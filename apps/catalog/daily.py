"""Closing a menu item for the rest of the business day (kitchen ran out) and reopening it.

Closing cascades: lines of this dish that nobody has started cooking yet (QUEUED) in orders already sent to the
kitchen become UNAVAILABLE, so the bill shrinks and the customer is told. Lines that are being cooked or are
ready carry on. Orders still waiting for the cashier are handled when the cashier confirms them.
"""
from typing import Iterable, Set

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.audit.services import log_audit_event
from apps.outbox.services import create_outbox_event

from .availability import (
    CLOSED_TODAY, INACTIVE, SOLD_OUT, availability_map, business_date,
)
from .models import DailyMenuState, ItemAvailability, MenuItem

DEFAULT_REASON = "Out of stock today"
OUT_OF_STOCK_STATES = (CLOSED_TODAY, SOLD_OUT, INACTIVE)


def out_of_stock_menu_ids(menu_items: Iterable[MenuItem]) -> Set[str]:
    """Ids (as str) of the given menu items that cannot be served today (closed, switched off or inactive)."""
    states = availability_map([m for m in menu_items if m is not None])
    return {mid for mid, a in states.items() if a.state in OUT_OF_STOCK_STATES}


def _role(user) -> str:
    return getattr(user, 'role', 'STAFF') if user else 'SYSTEM'


def _broadcast(menu_item: MenuItem, closed: bool, reason: str, cancelled: int) -> None:
    create_outbox_event(
        aggregate_type='MENU_ITEM',
        aggregate_id=menu_item.id,
        event_type='MENU_AVAILABILITY_CHANGED',
        payload={
            'menu_item_id': str(menu_item.id),
            'name': menu_item.name,
            'closed': closed,
            'reason': reason,
            'cancelled_items': cancelled,
        },
    )


def close_menu_for_today(*, menu_item: MenuItem, staff_user=None, reason: str = "", request_id: str = "") -> dict:
    """Close `menu_item` for today. Returns {'cancelled_items': n, 'orders': [order codes]}."""
    from apps.kitchen.services import change_items_status
    from apps.ordering.models import ItemStatus, OrderItem, OrderStatus

    reason = (reason or '').strip()[:255] or DEFAULT_REASON
    with transaction.atomic():
        locked = MenuItem.objects.select_for_update().get(id=menu_item.id)
        if locked.availability == ItemAvailability.INACTIVE:
            raise ValidationError("Menu item is inactive.")

        day = business_date()
        state, created = DailyMenuState.objects.get_or_create(
            menu_item=locked, business_date=day,
            defaults={'is_closed': True, 'reason': reason, 'closed_by': staff_user, 'closed_at': timezone.now()},
        )
        already_closed = (not created) and state.is_closed
        if not created:
            state.is_closed = True
            state.reason = reason
            state.closed_by = staff_user
            state.closed_at = timezone.now()
            state.save()

        lines = list(
            OrderItem.objects.filter(
                menu_item=locked, status=ItemStatus.QUEUED,
                order__status__in=[OrderStatus.CONFIRMED, OrderStatus.PREPARING, OrderStatus.READY],
            ).select_related('order').order_by('order__created_at', 'created_at')
        )
        by_order = {}
        for line in lines:
            by_order.setdefault(line.order_id, []).append(line)

        codes = []
        cancelled = 0
        for group in by_order.values():
            order = group[0].order
            change_items_status(
                order=order, to_status=ItemStatus.UNAVAILABLE, item_ids=[l.id for l in group],
                staff_user=staff_user, reason=reason, request_id=request_id,
                default_role='CASHIER',
            )
            codes.append(order.order_code)
            cancelled += sum(l.quantity for l in group)

        log_audit_event(
            actor_user=staff_user, actor_role=_role(staff_user), action='MENU_CLOSE_TODAY',
            entity_type='MenuItem', entity_id=str(locked.id),
            before_data={'closed': already_closed},
            after_data={'closed': True, 'reason': reason, 'cancelled_portions': cancelled, 'orders': codes},
            request_id=request_id,
        )
        _broadcast(locked, True, reason, cancelled)
        from apps.kitchen.notices import resolve_notices_for_menu
        resolve_notices_for_menu(locked, staff_user=staff_user, request_id=request_id)
        return {'cancelled_items': cancelled, 'orders': codes}


def reopen_menu(*, menu_item: MenuItem, staff_user=None, request_id: str = "") -> bool:
    """Undo today's closing. Lines that were already cancelled stay cancelled. Returns True when it was closed."""
    with transaction.atomic():
        locked = MenuItem.objects.select_for_update().get(id=menu_item.id)
        state = DailyMenuState.objects.filter(menu_item=locked, business_date=business_date(), is_closed=True).first()
        if state is None:
            return False
        state.is_closed = False
        state.save(update_fields=['is_closed', 'updated_at'])
        log_audit_event(
            actor_user=staff_user, actor_role=_role(staff_user), action='MENU_REOPEN',
            entity_type='MenuItem', entity_id=str(locked.id),
            before_data={'closed': True}, after_data={'closed': False}, request_id=request_id,
        )
        _broadcast(locked, False, '', 0)
        return True
