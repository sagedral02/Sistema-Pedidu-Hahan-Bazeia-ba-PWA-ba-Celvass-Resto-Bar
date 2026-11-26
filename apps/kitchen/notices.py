"""Kitchen -> cashier notices. For now one kind: a dish ran out ("Bahan habis")."""
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.audit.services import log_audit_event
from apps.outbox.services import create_outbox_event

from .models import KitchenNotice, NoticeKind, NoticeStatus

ACTIVE_NOTICE_STATUSES = (NoticeStatus.OPEN, NoticeStatus.ACKNOWLEDGED)


def _role(user) -> str:
    return getattr(user, 'role', 'KITCHEN') if user else 'SYSTEM'


def _event(notice: KitchenNotice, kind: str) -> None:
    create_outbox_event(
        aggregate_type='KITCHEN_NOTICE',
        aggregate_id=notice.id,
        event_type=kind,
        payload={
            'notice_id': str(notice.id),
            'kind': notice.kind,
            'status': notice.status,
            'menu_item_id': str(notice.menu_item_id) if notice.menu_item_id else '',
            'menu_name': notice.menu_name_snapshot,
            'order_code': notice.order_code,
            'table_label': notice.table_label,
            'message': notice.message,
        },
    )


def list_active_notices(restaurant=None):
    qs = KitchenNotice.objects.filter(status__in=ACTIVE_NOTICE_STATUSES)
    if restaurant is not None:
        qs = qs.filter(restaurant=restaurant)
    return qs.order_by('created_at')


def report_out_of_stock(*, item, staff_user=None, reason: str = "", request_id: str = ""):
    """The kitchen cannot make this order line any more.

    The line becomes UNAVAILABLE (bill and customer are updated) and the cashier gets a notice, once per dish
    while it is still waiting for a decision. Returns (order, notice or None).
    """
    from apps.catalog.availability import CLOSED_TODAY, menu_availability
    from apps.kitchen.services import mark_item_unavailable

    reason = (reason or '').strip()[:255]
    with transaction.atomic():
        order = mark_item_unavailable(
            item=item, staff_user=staff_user, reason=reason or "Out of stock", request_id=request_id)
        item.refresh_from_db()
        menu_item = item.menu_item
        if menu_item is None or menu_item.id is None:
            return order, None
        if menu_availability(menu_item).state == CLOSED_TODAY:
            return order, None  # the cashier already closed this dish for today
        existing = KitchenNotice.objects.select_for_update().filter(
            menu_item=menu_item, status__in=ACTIVE_NOTICE_STATUSES, kind=NoticeKind.OUT_OF_STOCK).first()
        if existing:
            return order, existing
        table = order.table_session.table
        notice = KitchenNotice.objects.create(
            restaurant=order.restaurant, kind=NoticeKind.OUT_OF_STOCK, menu_item=menu_item,
            menu_name_snapshot=item.menu_name_snapshot, order_item=item, order_code=order.order_code,
            table_label=getattr(order.table_session, 'label', None) or table.display_name,
            message=reason, created_by=staff_user,
        )
        log_audit_event(
            actor_user=staff_user, actor_role=_role(staff_user), action='KITCHEN_NOTICE_CREATE',
            entity_type='KitchenNotice', entity_id=str(notice.id),
            after_data={'menu': notice.menu_name_snapshot, 'order_code': order.order_code, 'reason': reason},
            request_id=request_id,
        )
        _event(notice, 'KITCHEN_NOTICE')
        return order, notice


def acknowledge_notice(*, notice: KitchenNotice, staff_user=None, request_id: str = "") -> KitchenNotice:
    with transaction.atomic():
        locked = KitchenNotice.objects.select_for_update().get(id=notice.id)
        if locked.status != NoticeStatus.OPEN:
            return locked
        locked.status = NoticeStatus.ACKNOWLEDGED
        locked.acknowledged_by = staff_user
        locked.acknowledged_at = timezone.now()
        locked.save(update_fields=['status', 'acknowledged_by', 'acknowledged_at'])
        log_audit_event(
            actor_user=staff_user, actor_role=_role(staff_user), action='KITCHEN_NOTICE_ACK',
            entity_type='KitchenNotice', entity_id=str(locked.id), request_id=request_id)
        _event(locked, 'KITCHEN_NOTICE_UPDATED')
        return locked


def resolve_notice(*, notice: KitchenNotice, staff_user=None, resolution: str = 'DISMISSED',
                   request_id: str = "") -> KitchenNotice:
    with transaction.atomic():
        locked = KitchenNotice.objects.select_for_update().get(id=notice.id)
        if locked.status == NoticeStatus.RESOLVED:
            return locked
        locked.status = NoticeStatus.RESOLVED
        locked.resolved_by = staff_user
        locked.resolved_at = timezone.now()
        locked.resolution = resolution
        locked.save(update_fields=['status', 'resolved_by', 'resolved_at', 'resolution'])
        log_audit_event(
            actor_user=staff_user, actor_role=_role(staff_user), action='KITCHEN_NOTICE_RESOLVE',
            entity_type='KitchenNotice', entity_id=str(locked.id),
            after_data={'resolution': resolution}, request_id=request_id)
        _event(locked, 'KITCHEN_NOTICE_UPDATED')
        return locked


def mark_sold_out_from_notice(*, notice: KitchenNotice, staff_user=None, request_id: str = "") -> dict:
    """Cashier presses "Tandai habis": close the dish for today and settle the notice."""
    from apps.catalog.daily import close_menu_for_today

    with transaction.atomic():
        locked = KitchenNotice.objects.select_for_update().select_related('menu_item').get(id=notice.id)
        if locked.menu_item is None:
            raise ValidationError("This menu item no longer exists.")
        result = close_menu_for_today(
            menu_item=locked.menu_item, staff_user=staff_user,
            reason=locked.message or "Out of stock today", request_id=request_id)
        # close_menu_for_today already settles every open notice of this dish.
        return result


def resolve_notices_for_menu(menu_item, *, staff_user=None, resolution: str = 'MENU_CLOSED', request_id: str = "") -> int:
    count = 0
    for notice in KitchenNotice.objects.filter(menu_item=menu_item, status__in=ACTIVE_NOTICE_STATUSES):
        resolve_notice(notice=notice, staff_user=staff_user, resolution=resolution, request_id=request_id)
        count += 1
    return count
