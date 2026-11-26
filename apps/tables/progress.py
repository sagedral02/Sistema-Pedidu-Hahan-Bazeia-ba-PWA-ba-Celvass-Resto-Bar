"""What is happening at a table: orders, the kitchen state of every line, and whether the table has gone quiet.

Used by the cashier table cards (expandable detail, "ready for the bill" button) and by the idle-table alert.
"""
from datetime import timedelta
from typing import Any, Dict, Optional

from django.utils import timezone

from apps.ordering.models import ItemStatus, OrderStatus

# Orders shown on the card: everything the cashier still has to care about.
VISIBLE_ORDER_STATUSES = (
    OrderStatus.WAITING_CASHIER_CONFIRMATION, OrderStatus.CONFIRMED, OrderStatus.PREPARING,
    OrderStatus.READY, OrderStatus.SERVED, OrderStatus.COMPLETED,
)
PENDING_ITEM_STATUSES = (ItemStatus.QUEUED, ItemStatus.COOKING)
UNPAID_SESSION_STATUSES = ('OPEN', 'BILL_REQUESTED', 'PAYMENT_PENDING')


def idle_minutes_setting(restaurant=None) -> int:
    """Minutes without activity before a table is flagged (0 = alert off)."""
    if restaurant is None:
        from apps.restaurants.services import get_restaurant
        restaurant = get_restaurant()
    return int(getattr(restaurant, 'idle_alert_minutes', 0) or 0)


def session_progress(session, idle_minutes: Optional[int] = None, now=None) -> Dict[str, Any]:
    """Orders and line states of one session plus the delivery summary and the idle flag."""
    now = now or timezone.now()
    orders = list(
        session.orders.filter(status__in=VISIBLE_ORDER_STATUSES)
        .prefetch_related('items').order_by('created_at')
    )
    out_orders = []
    counts = {'pending': 0, 'ready': 0, 'served': 0, 'unavailable': 0, 'waiting': 0}
    last = session.opened_at
    for order in orders:
        last = max(last, order.updated_at or order.created_at)
        waiting = order.status == OrderStatus.WAITING_CASHIER_CONFIRMATION
        lines = []
        for item in order.items.all():
            if item.status_changed_at:
                last = max(last, item.status_changed_at)
            if waiting:
                counts['waiting'] += item.quantity
            elif item.status in PENDING_ITEM_STATUSES:
                counts['pending'] += item.quantity
            elif item.status == ItemStatus.READY:
                counts['ready'] += item.quantity
            elif item.status == ItemStatus.SERVED:
                counts['served'] += item.quantity
            elif item.status == ItemStatus.UNAVAILABLE:
                counts['unavailable'] += item.quantity
            lines.append({
                'name': item.menu_name_snapshot, 'quantity': item.quantity,
                'status': 'WAITING' if waiting else item.status, 'note': item.note or '',
            })
        out_orders.append({
            'id': str(order.id), 'code': order.order_code, 'status': order.status,
            'created_at': order.created_at.isoformat(), 'items': lines,
        })
    for stamp in (session.bill_requested_at, session.paid_at):
        if stamp:
            last = max(last, stamp)

    has_food = counts['served'] > 0
    all_delivered = has_food and not (counts['pending'] or counts['ready'] or counts['waiting'])
    if idle_minutes is None:
        idle_minutes = idle_minutes_setting()
    idle_for = int((now - last).total_seconds() // 60)
    is_idle = bool(
        idle_minutes and session.status in UNPAID_SESSION_STATUSES
        and not (counts['pending'] or counts['ready'] or counts['waiting'])
        and now - last >= timedelta(minutes=idle_minutes)
    )
    return {
        'orders': out_orders,
        'pending_items': counts['pending'], 'ready_items': counts['ready'], 'served_items': counts['served'],
        'unavailable_items': counts['unavailable'], 'waiting_items': counts['waiting'],
        'all_delivered': all_delivered,
        'last_activity_at': last.isoformat(),
        'idle_minutes': idle_for,
        'is_idle': is_idle,
    }


def raise_idle_alerts(table_rows) -> int:
    """
    Mark sessions that just went idle (once per session) and push a notification to the cashiers.
    `table_rows` is the serialized cashier table list. Returns the number of new alerts.
    """
    from .models import TableSession
    idle = {row['active_session']['id']: row['active_session']['idle_minutes']
            for row in table_rows if row.get('active_session') and row['active_session'].get('is_idle')}
    if not idle:
        return 0
    fresh = list(TableSession.objects.filter(id__in=idle.keys(), idle_alerted_at__isnull=True).select_related('table'))
    if not fresh:
        return 0
    TableSession.objects.filter(id__in=[s.id for s in fresh]).update(idle_alerted_at=timezone.now())
    from apps.pwa.push import notify_idle_table
    for session in fresh:
        notify_idle_table(session, idle[str(session.id)])
    return len(fresh)
