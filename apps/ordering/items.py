"""
Per-item kitchen workflow.

An order's status is *derived* from the statuses of its items, and its money (subtotal, tax, total) is the
sum of the items that are still available. Every code path that changes an item goes through
`set_items_status` and then `recompute_order`, so the order can never disagree with its lines.

Item lifecycle:  QUEUED -> COOKING -> READY -> SERVED   (UNAVAILABLE from QUEUED or COOKING: out of stock)
Order status:    CONFIRMED (all active QUEUED) -> PREPARING (mixed) -> READY -> SERVED,
                 CANCELLED when no active item is left. The status never moves backwards.
"""
from decimal import Decimal
from typing import Iterable, Optional

from django.db import transaction
from django.utils import timezone

from .models import ActorType, ItemStatus, Order, OrderItem, OrderStatus, OrderStatusHistory

ACTIVE_ITEM_STATUSES = (ItemStatus.QUEUED, ItemStatus.COOKING, ItemStatus.READY, ItemStatus.SERVED)
# Orders whose status follows their items (the kitchen lifecycle).
KITCHEN_ORDER_STATUSES = (OrderStatus.CONFIRMED, OrderStatus.PREPARING, OrderStatus.READY, OrderStatus.SERVED)
# Orders whose money follows their items (a waiting order loses unavailable items before it is confirmed).
MONEY_ORDER_STATUSES = (OrderStatus.WAITING_CASHIER_CONFIRMATION,) + KITCHEN_ORDER_STATUSES

ITEM_TRANSITIONS = {
    ItemStatus.QUEUED: {ItemStatus.COOKING, ItemStatus.READY, ItemStatus.UNAVAILABLE},
    ItemStatus.COOKING: {ItemStatus.READY, ItemStatus.UNAVAILABLE},
    ItemStatus.READY: {ItemStatus.SERVED},
    ItemStatus.SERVED: set(),
    ItemStatus.UNAVAILABLE: set(),
}

_TIMESTAMP_FIELD = {
    ItemStatus.COOKING: 'cooking_started_at',
    ItemStatus.READY: 'ready_at',
    ItemStatus.SERVED: 'served_at',
}
_ORDER_RANK = {
    OrderStatus.CONFIRMED: 0,
    OrderStatus.PREPARING: 1,
    OrderStatus.READY: 2,
    OrderStatus.SERVED: 3,
}


def can_transition(current: str, target: str) -> bool:
    return target in ITEM_TRANSITIONS.get(current, set())


def derive_order_status(item_statuses: Iterable[str]) -> Optional[str]:
    """Order status implied by its items, or None when every item is unavailable (nothing left to cook)."""
    active = [s for s in item_statuses if s != ItemStatus.UNAVAILABLE]
    if not active:
        return None
    if all(s == ItemStatus.QUEUED for s in active):
        return OrderStatus.CONFIRMED
    if all(s == ItemStatus.SERVED for s in active):
        return OrderStatus.SERVED
    if all(s in (ItemStatus.READY, ItemStatus.SERVED) for s in active):
        return OrderStatus.READY
    return OrderStatus.PREPARING


def order_totals(items: Iterable[OrderItem], tax_percentage, discount_total: Decimal):
    """(subtotal, tax, grand) of the active items, with the same rounding as order submission."""
    subtotal = sum((i.subtotal for i in items if i.status != ItemStatus.UNAVAILABLE), Decimal('0.00'))
    subtotal = subtotal.quantize(Decimal('0.01'))
    rate = Decimal(str(tax_percentage or '0.00')) / Decimal('100.00')
    tax = (subtotal * rate).quantize(Decimal('0.01'))
    grand = max(Decimal('0.00'), (subtotal + tax - (discount_total or Decimal('0.00'))).quantize(Decimal('0.01')))
    return subtotal, tax, grand


def set_items_status(items, to_status: str, *, actor_user=None, reason: str = "") -> int:
    """
    Bulk-set the status of order lines (a queryset or a list) and stamp who/when.
    No transition validation: callers decide which lines to pass (per-line rules live in ITEM_TRANSITIONS).
    The caller must call `recompute_order` afterwards.
    """
    now = timezone.now()
    values = {
        'status': to_status,
        'status_changed_at': now,
        'status_changed_by': actor_user if getattr(actor_user, 'pk', None) else None,
    }
    stamp = _TIMESTAMP_FIELD.get(to_status)
    if stamp:
        values[stamp] = now
    if to_status == ItemStatus.UNAVAILABLE:
        values['unavailable_reason'] = (reason or '')[:255]
    if isinstance(items, (list, tuple)):
        ids = [i.pk for i in items]
        return OrderItem.objects.filter(pk__in=ids).update(**values) if ids else 0
    return items.update(**values)


def recompute_order(order: Order, *, actor_user=None, reason: str = "", request_id: str = "",
                    actor_type: str = ActorType.STAFF) -> Order:
    """
    Bring an order in line with its items: money from the active lines, status from all lines.
    Writes one status-history entry when the status changes. Safe to call repeatedly (idempotent).
    """
    with transaction.atomic():
        locked = Order.objects.select_for_update().select_related('restaurant').get(pk=order.pk)
        items = list(locked.items.all())
        update_fields = []

        if locked.status in MONEY_ORDER_STATUSES:
            subtotal, tax, grand = order_totals(items, locked.restaurant.tax_percentage, locked.discount_total)
            for field, value in (('subtotal', subtotal), ('tax_total', tax), ('grand_total', grand)):
                if getattr(locked, field) != value:
                    setattr(locked, field, value)
                    update_fields.append(field)

        old_status = locked.status
        new_status = None
        if locked.status in KITCHEN_ORDER_STATUSES:
            derived = derive_order_status(i.status for i in items)
            if derived is None:
                new_status = OrderStatus.CANCELLED
                locked.cancellation_reason = (reason or 'All items are unavailable.')[:1000]
                update_fields.append('cancellation_reason')
            elif _ORDER_RANK[derived] > _ORDER_RANK[locked.status]:
                new_status = derived

        if new_status:
            locked.status = new_status
            update_fields.append('status')

        if update_fields:
            locked.save(update_fields=update_fields + ['updated_at'])

        if new_status:
            OrderStatusHistory.objects.create(
                order=locked,
                from_status=old_status,
                to_status=new_status,
                actor_user=actor_user if getattr(actor_user, 'pk', None) else None,
                actor_type=actor_type,
                reason=reason or '',
                request_id=request_id,
            )
        return locked
