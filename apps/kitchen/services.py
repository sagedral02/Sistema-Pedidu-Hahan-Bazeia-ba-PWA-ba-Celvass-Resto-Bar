"""
Order-level kitchen actions. They keep their original contract (one call moves the whole order) but now work
by moving every active line and letting `recompute_order` derive the order status, so the order and its
lines can never disagree. Per-line actions live next to them (start_item, ready_item, ...).
"""
from django.db import transaction
from django.core.exceptions import ValidationError

from apps.ordering.items import recompute_order, set_items_status
from apps.ordering.models import ItemStatus, Order, OrderItem, OrderStatus
from apps.audit.services import log_audit_event
from apps.outbox.services import create_outbox_event


def _order_event(event_type: str, order: Order, extra=None) -> None:
    payload = {
        'order_id': str(order.id),
        'order_code': order.order_code,
        'table_code': order.table_session.table.table_code,
        'status': order.status,
        'session_token': order.table_session.public_token,
    }
    if extra:
        payload.update(extra)
    create_outbox_event(
        aggregate_type='ORDER',
        aggregate_id=order.id,
        event_type=event_type,
        payload=payload,
    )


def _audit(order: Order, old_status: str, action: str, staff_user, default_role: str, request_id: str) -> None:
    log_audit_event(
        actor_user=staff_user,
        actor_role=getattr(staff_user, 'role', default_role) if staff_user else default_role,
        action=action,
        entity_type='Order',
        entity_id=str(order.id),
        before_data={'status': old_status},
        after_data={'status': order.status},
        request_id=request_id
    )


def start_preparing_order(*, order: Order, staff_user=None, request_id: str = "") -> Order:
    with transaction.atomic():
        locked_order = Order.objects.select_for_update().get(id=order.id)

        if locked_order.status != OrderStatus.CONFIRMED:
            if locked_order.status == OrderStatus.PREPARING:
                return locked_order
            raise ValidationError(f"Order must be CONFIRMED before preparing. Current status: '{locked_order.status}'.")

        old_status = locked_order.status
        set_items_status(locked_order.items.filter(status=ItemStatus.QUEUED), ItemStatus.COOKING, actor_user=staff_user)
        locked_order = recompute_order(
            locked_order, actor_user=staff_user, reason="Kitchen started preparation", request_id=request_id)

        _audit(locked_order, old_status, 'KITCHEN_START_PREP', staff_user, 'KITCHEN', request_id)
        _order_event('ORDER_PREPARING', locked_order)
        return locked_order


def mark_order_ready(*, order: Order, staff_user=None, request_id: str = "") -> Order:
    with transaction.atomic():
        locked_order = Order.objects.select_for_update().get(id=order.id)

        if locked_order.status not in [OrderStatus.CONFIRMED, OrderStatus.PREPARING]:
            if locked_order.status == OrderStatus.READY:
                return locked_order
            raise ValidationError(f"Cannot mark order ready from status '{locked_order.status}'.")

        old_status = locked_order.status
        set_items_status(
            locked_order.items.filter(status__in=[ItemStatus.QUEUED, ItemStatus.COOKING]),
            ItemStatus.READY, actor_user=staff_user)
        locked_order = recompute_order(
            locked_order, actor_user=staff_user, reason="Kitchen marked order ready", request_id=request_id)

        _audit(locked_order, old_status, 'KITCHEN_MARK_READY', staff_user, 'KITCHEN', request_id)
        _order_event('ORDER_READY', locked_order)
        return locked_order


def mark_order_served(*, order: Order, staff_user=None, request_id: str = "") -> Order:
    with transaction.atomic():
        locked_order = Order.objects.select_for_update().get(id=order.id)

        if locked_order.status not in [OrderStatus.PREPARING, OrderStatus.READY]:
            if locked_order.status == OrderStatus.SERVED:
                return locked_order
            raise ValidationError(f"Cannot mark order served from status '{locked_order.status}'.")

        old_status = locked_order.status
        set_items_status(
            locked_order.items.filter(status__in=[ItemStatus.QUEUED, ItemStatus.COOKING, ItemStatus.READY]),
            ItemStatus.SERVED, actor_user=staff_user)
        locked_order = recompute_order(
            locked_order, actor_user=staff_user, reason="Order served to table", request_id=request_id)

        _audit(locked_order, old_status, 'ORDER_SERVED', staff_user, 'STAFF', request_id)
        _order_event('ORDER_SERVED', locked_order)
        return locked_order


# --------------------------------------------------------------------------- per-line actions

# Action name used by the API -> (target line status, who may not be skipped, audit action, history reason)
ITEM_ACTIONS = {
    'start': (ItemStatus.COOKING, 'KITCHEN_ITEM_START', 'Kitchen started cooking items'),
    'ready': (ItemStatus.READY, 'KITCHEN_ITEM_READY', 'Kitchen marked items ready'),
    'served': (ItemStatus.SERVED, 'ITEM_SERVED', 'Items served'),
    'unavailable': (ItemStatus.UNAVAILABLE, 'ITEM_UNAVAILABLE', 'Items are out of stock'),
}
_ORDER_EVENT_FOR_STATUS = {
    OrderStatus.PREPARING: 'ORDER_PREPARING',
    OrderStatus.READY: 'ORDER_READY',
    OrderStatus.SERVED: 'ORDER_SERVED',
    OrderStatus.CANCELLED: 'ORDER_CANCELLED',
}


def change_items_status(*, order: Order, to_status: str, item_ids=None, staff_user=None, reason: str = "",
                        request_id: str = "", default_role: str = 'KITCHEN') -> Order:
    """
    Move lines of one order to `to_status` in a single transaction (a bulk "all ready" is one call).

    `item_ids=None` means every line that can legally make this move. Explicit ids must all belong to the
    order and be able to make the move (lines already at the target are skipped, so repeating a request is
    harmless). Afterwards the order status and totals are recomputed from its lines, an audit entry is written
    per line, one ITEM_STATUS_CHANGED event is sent for the batch, and the order-level event follows when the
    order itself changed state.
    """
    from apps.ordering.items import KITCHEN_ORDER_STATUSES, can_transition

    audit_action, history_reason = None, None
    for target, action, why in ITEM_ACTIONS.values():
        if target == to_status:
            audit_action, history_reason = action, why
    if audit_action is None:
        raise ValidationError(f"Unsupported line status '{to_status}'.")

    with transaction.atomic():
        locked_order = Order.objects.select_for_update().get(id=order.id)
        if locked_order.status not in KITCHEN_ORDER_STATUSES:
            raise ValidationError(
                f"Order is not in the kitchen (status '{locked_order.status}'): its items cannot be changed.")

        lines = list(OrderItem.objects.select_for_update().filter(order=locked_order).order_by('created_at', 'id'))
        by_id = {str(line.id): line for line in lines}

        if item_ids is None:
            chosen = [l for l in lines if can_transition(l.status, to_status)]
        else:
            chosen, seen = [], set()
            for raw in item_ids:
                key = str(raw)
                if key in seen:
                    continue
                seen.add(key)
                line = by_id.get(key)
                if line is None:
                    raise ValidationError("Item does not belong to this order.")
                if line.status == to_status:
                    continue  # already there: repeating the request is harmless
                if not can_transition(line.status, to_status):
                    raise ValidationError(
                        f"'{line.menu_name_snapshot}' cannot move from {line.status} to {to_status}.")
                chosen.append(line)

        if not chosen:
            return locked_order

        old_order_status = locked_order.status
        before = {str(l.id): l.status for l in chosen}
        set_items_status(chosen, to_status, actor_user=staff_user, reason=reason)

        order_reason = reason.strip() if (to_status == ItemStatus.UNAVAILABLE and reason and reason.strip()) else history_reason
        locked_order = recompute_order(
            locked_order, actor_user=staff_user, reason=order_reason, request_id=request_id)

        role = getattr(staff_user, 'role', default_role) if staff_user else default_role
        for line in chosen:
            log_audit_event(
                actor_user=staff_user,
                actor_role=role,
                action=audit_action,
                entity_type='OrderItem',
                entity_id=str(line.id),
                before_data={'status': before[str(line.id)], 'order_code': locked_order.order_code},
                after_data={'status': to_status, 'name': line.menu_name_snapshot,
                            **({'reason': reason} if reason else {})},
                request_id=request_id,
            )

        table = locked_order.table_session.table
        create_outbox_event(
            aggregate_type='ORDER',
            aggregate_id=locked_order.id,
            event_type='ITEM_STATUS_CHANGED',
            payload={
                'order_id': str(locked_order.id),
                'order_code': locked_order.order_code,
                'table_code': table.table_code,
                'table_name': table.display_name,
                'order_status': locked_order.status,
                'status': to_status,
                'reason': reason or '',
                'session_token': locked_order.table_session.public_token,
                'items': [{'id': str(l.id), 'name': l.menu_name_snapshot, 'quantity': l.quantity,
                           'previous_status': before[str(l.id)], 'status': to_status} for l in chosen],
                'grand_total': str(locked_order.grand_total),
            }
        )
        if locked_order.status != old_order_status and locked_order.status in _ORDER_EVENT_FOR_STATUS:
            _order_event(_ORDER_EVENT_FOR_STATUS[locked_order.status], locked_order,
                         extra={'reason': locked_order.cancellation_reason} if locked_order.status == OrderStatus.CANCELLED else None)
        session = locked_order.table_session
        if session.is_takeaway and to_status in (ItemStatus.READY, ItemStatus.UNAVAILABLE):
            _notify_takeaway_ready(session)
        if to_status in (ItemStatus.SERVED, ItemStatus.UNAVAILABLE):
            from apps.payments.billing import settle_paid_session
            settle_paid_session(locked_order.table_session_id, actor=staff_user, request_id=request_id)
        return locked_order


def _single(item: OrderItem, to_status: str, **kwargs) -> Order:
    return change_items_status(order=item.order, to_status=to_status, item_ids=[item.id], **kwargs)


def start_item(*, item: OrderItem, staff_user=None, request_id: str = "") -> Order:
    return _single(item, ItemStatus.COOKING, staff_user=staff_user, request_id=request_id)


def ready_item(*, item: OrderItem, staff_user=None, request_id: str = "") -> Order:
    return _single(item, ItemStatus.READY, staff_user=staff_user, request_id=request_id)


def serve_item(*, item: OrderItem, staff_user=None, request_id: str = "", default_role: str = 'KITCHEN') -> Order:
    return _single(item, ItemStatus.SERVED, staff_user=staff_user, request_id=request_id, default_role=default_role)


def mark_item_unavailable(*, item: OrderItem, staff_user=None, reason: str = "", request_id: str = "") -> Order:
    return _single(item, ItemStatus.UNAVAILABLE, staff_user=staff_user, reason=reason, request_id=request_id)


def _notify_takeaway_ready(session) -> None:
    """Tell the cashier once every remaining take-away item is ready for pick-up."""
    lines = list(OrderItem.objects.filter(
        order__table_session=session,
        order__status__in=[OrderStatus.CONFIRMED, OrderStatus.PREPARING, OrderStatus.READY, OrderStatus.SERVED],
    ).exclude(status=ItemStatus.UNAVAILABLE))
    if not lines or any(l.status in (ItemStatus.QUEUED, ItemStatus.COOKING) for l in lines):
        return
    if not any(l.status == ItemStatus.READY for l in lines):
        return
    create_outbox_event(
        aggregate_type='TABLE_SESSION',
        aggregate_id=session.id,
        event_type='TAKEAWAY_READY',
        payload={
            'session_id': str(session.id),
            'table_name': session.label,
            'takeaway_number': session.takeaway_number,
            'customer_name': session.customer_name,
        },
    )


def hand_over_takeaway(*, session, staff_user=None, request_id: str = "") -> int:
    """Cashier hands the ready take-away items to the customer (READY -> SERVED). Returns lines handed over."""
    if not session.is_takeaway:
        raise ValidationError("Only take-away orders are handed over at the counter.")
    count = 0
    orders = Order.objects.filter(
        table_session=session, status__in=[OrderStatus.PREPARING, OrderStatus.READY],
    ).order_by('created_at')
    for order in orders:
        ready_ids = list(order.items.filter(status=ItemStatus.READY).values_list('id', flat=True))
        if not ready_ids:
            continue
        change_items_status(order=order, to_status=ItemStatus.SERVED, item_ids=ready_ids, staff_user=staff_user,
                            request_id=request_id, default_role='CASHIER')
        count += len(ready_ids)
    if count == 0:
        raise ValidationError("Nothing is ready to hand over yet.")
    return count
