import uuid
import secrets
from decimal import Decimal
from typing import List, Dict, Any, Optional
from django.db import transaction
from django.utils import timezone
from django.core.exceptions import ValidationError

from apps.tables.models import TableSession, SessionStatus
from apps.catalog.models import MenuItem, ItemAvailability
from apps.catalog.availability import business_date, menu_availability, QUOTA_REACHED
from apps.catalog.daily import out_of_stock_menu_ids
from .models import ItemStatus, Order, OrderItem, OrderStatus, OrderSource, OrderStatusHistory, ActorType, IdempotencyRecord
from apps.audit.services import log_audit_event
from apps.outbox.services import create_outbox_event

def generate_order_code() -> str:
    today_str = timezone.now().strftime("%Y%m%d")
    random_suffix = secrets.token_hex(3).upper()
    return f"ORD-{today_str}-{random_suffix}"

def submit_customer_order(
    *,
    table_session: TableSession,
    items_data: List[Dict[str, Any]],
    idempotency_key: str,
    customer_note: str = "",
    source: str = OrderSource.CUSTOMER,
    created_by=None,
    request_id: str = ""
) -> Order:
    """
    Submits a new order from customer PWA or cashier POS atomically.
    Implements server-side pricing, idempotency, and pending order gating.
    """
    if not idempotency_key:
        raise ValidationError("Idempotency-Key is required.")
        
    if not items_data:
        raise ValidationError("Order must contain at least one item.")

    with transaction.atomic():
        locked_session = TableSession.objects.select_for_update().get(id=table_session.id)
        
        if not locked_session.is_active:
            raise ValidationError("Table session is closed or inactive.")
        if locked_session.status == SessionStatus.PAID:
            raise ValidationError("This table session is already paid. Please ask the cashier to open a new one.")

        # Idempotency Check: Return existing order if key matches for this session
        existing_order = Order.objects.filter(
            table_session=locked_session,
            idempotency_key=idempotency_key
        ).first()
        if existing_order:
            return existing_order

        # Pending Order Limit: Only 1 order in WAITING_CASHIER_CONFIRMATION per session
        if source == OrderSource.CUSTOMER:
            has_pending = Order.objects.filter(
                table_session=locked_session,
                status=OrderStatus.WAITING_CASHIER_CONFIRMATION
            ).exists()
            if has_pending:
                raise ValidationError("You already have an order waiting for cashier verification. Please wait until the cashier confirms it before placing another order.")

        # Load menu items and calculate totals strictly using database prices
        subtotal = Decimal('0.00')
        order_items_to_create = []

        item_ids = [item['menu_item_id'] for item in items_data]
        # Lock the menu rows in a fixed (id) order so two orders for the same dish are serialised without
        # deadlocking, then check the daily quota against what is already sold.
        menu_items_map = {
            str(mi.id): mi for mi in MenuItem.objects.select_for_update().filter(id__in=item_ids).order_by('id')
        }
        requested = {}
        for item_dict in items_data:
            try:
                requested[str(item_dict.get('menu_item_id'))] = (
                    requested.get(str(item_dict.get('menu_item_id')), 0) + int(item_dict.get('quantity', 1)))
            except (TypeError, ValueError):
                raise ValidationError("Invalid quantity.")
        day = business_date()

        for item_dict in items_data:
            menu_id = str(item_dict.get('menu_item_id'))
            quantity = int(item_dict.get('quantity', 1))
            note = item_dict.get('note', '')

            if quantity < 1 or quantity > 99:
                raise ValidationError(f"Invalid quantity {quantity}. Must be between 1 and 99.")

            menu_item = menu_items_map.get(menu_id)
            if not menu_item:
                raise ValidationError(f"Menu item '{menu_id}' not found.")
            state = menu_availability(menu_item, day)
            if not state.orderable:
                if state.state == QUOTA_REACHED:
                    raise ValidationError(f"Menu item '{menu_item.name}' is sold out for today (daily quota reached).")
                raise ValidationError(f"Menu item '{menu_item.name}' is currently unavailable/sold out.")
            if state.remaining is not None and requested[menu_id] > state.remaining:
                raise ValidationError(
                    f"Only {state.remaining} portion(s) of '{menu_item.name}' left today.")

            unit_price = menu_item.price
            item_subtotal = (unit_price * Decimal(quantity)).quantize(Decimal('0.01'))
            subtotal += item_subtotal

            order_items_to_create.append({
                'menu_item': menu_item,
                'menu_name_snapshot': menu_item.name,
                'sku_snapshot': menu_item.sku or '',
                'unit_price': unit_price,
                'quantity': quantity,
                'subtotal': item_subtotal,
                'note': note[:500] if note else ''
            })

        # Calculate tax / totals (USD context: tax configured on restaurant)
        tax_rate = Decimal(str(locked_session.restaurant.tax_percentage or '0.00')) / Decimal('100.00')
        tax_total = (subtotal * tax_rate).quantize(Decimal('0.01'))
        discount_total = Decimal('0.00')
        grand_total = (subtotal + tax_total - discount_total).quantize(Decimal('0.01'))

        # Create Order
        initial_status = OrderStatus.WAITING_CASHIER_CONFIRMATION if source == OrderSource.CUSTOMER else OrderStatus.CONFIRMED
        
        order = Order.objects.create(
            restaurant=locked_session.restaurant,
            table_session=locked_session,
            order_code=generate_order_code(),
            source=source,
            status=initial_status,
            idempotency_key=idempotency_key,
            subtotal=subtotal,
            tax_total=tax_total,
            discount_total=discount_total,
            grand_total=grand_total,
            customer_note=customer_note,
            created_by=created_by,
            confirmed_by=created_by if source == OrderSource.CASHIER else None,
            confirmed_at=timezone.now() if source == OrderSource.CASHIER else None,
        )

        # Ordering again after asking for the bill withdraws the bill: the guest is still eating.
        from apps.payments.billing import reopen_bill_if_requested
        reopen_bill_if_requested(locked_session, actor=created_by, request_id=request_id)

        for oi in order_items_to_create:
            OrderItem.objects.create(
                order=order,
                menu_item=oi['menu_item'],
                menu_name_snapshot=oi['menu_name_snapshot'],
                sku_snapshot=oi['sku_snapshot'],
                unit_price=oi['unit_price'],
                quantity=oi['quantity'],
                subtotal=oi['subtotal'],
                note=oi['note']
            )

        OrderStatusHistory.objects.create(
            order=order,
            from_status=None,
            to_status=order.status,
            actor_user=created_by,
            actor_type=ActorType.CUSTOMER if source == OrderSource.CUSTOMER else ActorType.STAFF,
            reason="Order submitted",
            request_id=request_id
        )

        # Audit
        log_audit_event(
            actor_user=created_by,
            actor_role='CUSTOMER' if source == OrderSource.CUSTOMER else getattr(created_by, 'role', 'CASHIER'),
            action='ORDER_SUBMIT',
            entity_type='Order',
            entity_id=str(order.id),
            after_data={
                'order_code': order.order_code,
                'grand_total': str(order.grand_total),
                'items_count': len(order_items_to_create)
            },
            request_id=request_id
        )

        # Outbox event for WebSockets
        create_outbox_event(
            aggregate_type='ORDER',
            aggregate_id=order.id,
            event_type='ORDER_CREATED' if order.status == OrderStatus.WAITING_CASHIER_CONFIRMATION else 'ORDER_CONFIRMED',
            payload={
                'order_id': str(order.id),
                'order_code': order.order_code,
                'table_code': locked_session.table.table_code,
                'table_name': locked_session.table.display_name,
                'status': order.status,
                'grand_total': str(order.grand_total),
                'session_token': locked_session.public_token,
                'items_summary': [f"{i['quantity']}x {i['menu_name_snapshot']}" for i in order_items_to_create],
                'customer_note': order.customer_note,
                'submitted_at': order.submitted_at.isoformat(),
            }
        )

        return order

def confirm_order_by_cashier(*, order: Order, confirmed_by, request_id: str = "") -> Order:
    """
    Cashier confirmation gate: Approves a pending order so it is sent to the kitchen.
    """
    with transaction.atomic():
        locked_order = Order.objects.select_for_update().get(id=order.id)
        
        if locked_order.status != OrderStatus.WAITING_CASHIER_CONFIRMATION:
            if locked_order.status == OrderStatus.CONFIRMED:
                return locked_order
            raise ValidationError(f"Order cannot be confirmed from status '{locked_order.status}'.")

        # Dishes closed for today (or switched off) since the customer ordered cannot be sent to the kitchen.
        lines = list(locked_order.items.select_related('menu_item'))
        out_ids = out_of_stock_menu_ids([l.menu_item for l in lines if l.menu_item_id])
        dead = [l for l in lines if l.menu_item_id and str(l.menu_item_id) in out_ids and l.status == ItemStatus.QUEUED]
        live = [l for l in lines if l.status != ItemStatus.UNAVAILABLE]
        if dead and len(dead) == len(live):
            names = ', '.join(sorted({l.menu_name_snapshot for l in dead}))
            raise ValidationError(f"Every item is out of stock today ({names}). Reject this order instead.")

        old_status = locked_order.status
        locked_order.status = OrderStatus.CONFIRMED
        locked_order.confirmed_by = confirmed_by
        locked_order.confirmed_at = timezone.now()
        locked_order.save(update_fields=['status', 'confirmed_by', 'confirmed_at', 'updated_at'])

        OrderStatusHistory.objects.create(
            order=locked_order,
            from_status=old_status,
            to_status=OrderStatus.CONFIRMED,
            actor_user=confirmed_by,
            actor_type=ActorType.STAFF,
            reason="Confirmed by cashier",
            request_id=request_id
        )

        log_audit_event(
            actor_user=confirmed_by,
            actor_role=getattr(confirmed_by, 'role', 'CASHIER'),
            action='ORDER_CONFIRM',
            entity_type='Order',
            entity_id=str(locked_order.id),
            before_data={'status': old_status},
            after_data={'status': OrderStatus.CONFIRMED},
            request_id=request_id
        )

        if dead:
            from apps.kitchen.services import change_items_status
            locked_order = change_items_status(
                order=locked_order, to_status=ItemStatus.UNAVAILABLE, item_ids=[l.id for l in dead],
                staff_user=confirmed_by, reason="Out of stock today", request_id=request_id, default_role='CASHIER')

        # Outbox event to notify Kitchen & Customer
        items = [i for i in locked_order.items.all() if i.status != ItemStatus.UNAVAILABLE]
        create_outbox_event(
            aggregate_type='ORDER',
            aggregate_id=locked_order.id,
            event_type='ORDER_CONFIRMED',
            payload={
                'order_id': str(locked_order.id),
                'order_code': locked_order.order_code,
                'table_code': locked_order.table_session.table.table_code,
                'table_name': locked_order.table_session.table.display_name,
                'status': locked_order.status,
                'session_token': locked_order.table_session.public_token,
                'grand_total': str(locked_order.grand_total),
                'customer_note': locked_order.customer_note,
                'items': [
                    {'name': item.menu_name_snapshot, 'quantity': item.quantity, 'note': item.note}
                    for item in items
                ],
                'confirmed_at': locked_order.confirmed_at.isoformat(),
            }
        )

        return locked_order

def reject_order_by_cashier(*, order: Order, rejected_by, reason: str, request_id: str = "") -> Order:
    """
    Cashier rejects a suspicious / fake / unverified order with reason.
    Order will NEVER reach the kitchen.
    """
    if not reason or not reason.strip():
        raise ValidationError("Rejection reason is required.")

    with transaction.atomic():
        locked_order = Order.objects.select_for_update().get(id=order.id)
        
        if locked_order.status != OrderStatus.WAITING_CASHIER_CONFIRMATION:
            raise ValidationError(f"Cannot reject order with status '{locked_order.status}'.")

        old_status = locked_order.status
        locked_order.status = OrderStatus.REJECTED
        locked_order.rejection_reason = reason.strip()
        locked_order.save(update_fields=['status', 'rejection_reason', 'updated_at'])

        OrderStatusHistory.objects.create(
            order=locked_order,
            from_status=old_status,
            to_status=OrderStatus.REJECTED,
            actor_user=rejected_by,
            actor_type=ActorType.STAFF,
            reason=reason.strip(),
            request_id=request_id
        )

        log_audit_event(
            actor_user=rejected_by,
            actor_role=getattr(rejected_by, 'role', 'CASHIER'),
            action='ORDER_REJECT',
            entity_type='Order',
            entity_id=str(locked_order.id),
            before_data={'status': old_status},
            after_data={'status': OrderStatus.REJECTED, 'reason': reason},
            request_id=request_id
        )

        # Notify customer
        create_outbox_event(
            aggregate_type='ORDER',
            aggregate_id=locked_order.id,
            event_type='ORDER_REJECTED',
            payload={
                'order_id': str(locked_order.id),
                'order_code': locked_order.order_code,
                'status': locked_order.status,
                'session_token': locked_order.table_session.public_token,
                'reason': reason.strip(),
            }
        )

        return locked_order

def cancel_order_by_staff(*, order: Order, actor_user, reason: str = "Staff cancellation", request_id: str = "") -> Order:
    """
    Cancels an order before it is served.
    """
    with transaction.atomic():
        locked_order = Order.objects.select_for_update().get(id=order.id)
        
        if locked_order.status in [OrderStatus.SERVED, OrderStatus.COMPLETED, OrderStatus.CANCELLED, OrderStatus.REJECTED]:
            raise ValidationError(f"Cannot cancel order in status '{locked_order.status}'.")

        old_status = locked_order.status
        locked_order.status = OrderStatus.CANCELLED
        locked_order.cancellation_reason = reason
        locked_order.save(update_fields=['status', 'cancellation_reason', 'updated_at'])

        OrderStatusHistory.objects.create(
            order=locked_order,
            from_status=old_status,
            to_status=OrderStatus.CANCELLED,
            actor_user=actor_user,
            actor_type=ActorType.STAFF,
            reason=reason,
            request_id=request_id
        )

        log_audit_event(
            actor_user=actor_user,
            actor_role=getattr(actor_user, 'role', 'STAFF'),
            action='ORDER_CANCEL',
            entity_type='Order',
            entity_id=str(locked_order.id),
            before_data={'status': old_status},
            after_data={'status': OrderStatus.CANCELLED, 'reason': reason},
            request_id=request_id
        )

        create_outbox_event(
            aggregate_type='ORDER',
            aggregate_id=locked_order.id,
            event_type='ORDER_CANCELLED',
            payload={
                'order_id': str(locked_order.id),
                'order_code': locked_order.order_code,
                'status': locked_order.status,
                'session_token': locked_order.table_session.public_token,
                'reason': reason,
            }
        )

        return locked_order
