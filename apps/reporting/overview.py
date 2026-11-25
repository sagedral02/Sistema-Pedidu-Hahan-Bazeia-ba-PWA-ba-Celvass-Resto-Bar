"""
Data for the admin Overview dashboard (charts and KPI cards).

Definitions match the reports so the numbers agree everywhere:
  * Sales are Payment rows with status COMPLETED, dated by `paid_at` in the local business day (Asia/Dili).
  * Items sold come from OrderItem rows of orders in CONFIRMED..COMPLETED, dated by order.created_at.

Bucketing by day or hour is done in the database with the active timezone (TruncDate / ExtractHour),
never in Python from UTC. The previous period has the same length and ends the day before the current one.
Everything is bounded: top 8 menu items, top 10 categories and cashiers, 8 recent rows.
"""
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Dict, List, Optional

from django.db.models import Count, Q, Sum
from django.db.models.functions import ExtractHour, TruncDate
from django.utils import timezone

from apps.ordering.models import Order, OrderItem, OrderStatus
from apps.payments.models import Payment, PaymentMethod, PaymentStatus
from apps.tables.models import RestaurantTable, TableStatus

from .reports import SOLD_STATUSES, ZERO, Period, ReportError, _money

RANGE_DAYS = {'today': 1, '7d': 7, '30d': 30}
TOP_ITEMS = 8
TOP_CATEGORIES = 10
TOP_CASHIERS = 10
RECENT_ROWS = 8

ACTIVE_STATUSES = (OrderStatus.CONFIRMED, OrderStatus.PREPARING, OrderStatus.READY, OrderStatus.SERVED)


def parse_range(raw: Optional[str], today: Optional[date] = None):
    """Return (current Period, previous Period, key). Raises ReportError('invalid_range')."""
    key = (raw or 'today').strip()
    if key not in RANGE_DAYS:
        raise ReportError('invalid_range')
    today = today or timezone.localdate()
    days = RANGE_DAYS[key]
    current = Period(today - timedelta(days=days - 1), today, key)
    previous_end = current.start - timedelta(days=1)
    previous = Period(previous_end - timedelta(days=days - 1), previous_end, key)
    return current, previous, key


def _pct_change(current, previous) -> Optional[float]:
    """Percent change versus the previous period; None when there is nothing to compare against."""
    if not previous:
        return None
    return round(float((Decimal(current) - Decimal(previous)) / Decimal(previous) * 100), 1)


def _num(value) -> float:
    return float(_money(value))


def _payments(period: Period):
    start_dt, end_dt = period.bounds()
    return Payment.objects.filter(status=PaymentStatus.COMPLETED, paid_at__gte=start_dt, paid_at__lt=end_dt)


def _orders(period: Period):
    start_dt, end_dt = period.bounds()
    return Order.objects.filter(created_at__gte=start_dt, created_at__lt=end_dt)


def _period_totals(period: Period) -> Dict[str, Any]:
    pay = _payments(period).aggregate(total=Sum('amount'), n=Count('id'))
    total = pay['total'] or ZERO
    orders = _orders(period).aggregate(
        n=Count('id'),
        rejected=Count('id', filter=_status_filter(OrderStatus.REJECTED)),
    )
    return {
        'sales': total, 'transactions': pay['n'],
        'average': (total / pay['n']) if pay['n'] else ZERO,
        'orders': orders['n'], 'rejected': orders['rejected'],
        'rejection_rate': round(orders['rejected'] * 100 / orders['n'], 1) if orders['n'] else None,
    }


def _status_filter(*statuses):
    return Q(status__in=statuses)


def _kpi(current: Dict[str, Any], previous: Dict[str, Any]) -> Dict[str, Any]:
    def card(key, as_money=False):
        cur, prev = current[key], previous[key]
        return {
            'value': _num(cur) if as_money else cur,
            'previous': _num(prev) if as_money else prev,
            'delta_pct': _pct_change(cur, prev),
        }

    rate_cur, rate_prev = current['rejection_rate'], previous['rejection_rate']
    return {
        'sales': card('sales', True),
        'transactions': card('transactions'),
        'average': card('average', True),
        'orders': card('orders'),
        'rejection_rate': {
            'value': rate_cur, 'previous': rate_prev,
            'delta_points': round(rate_cur - rate_prev, 1) if rate_cur is not None and rate_prev is not None else None,
        },
    }


def _daily_sales(period: Period) -> Dict[date, float]:
    rows = _payments(period).annotate(day=TruncDate('paid_at')).values('day').annotate(total=Sum('amount'))
    return {r['day']: _num(r['total']) for r in rows}


def _hourly_sales(period: Period) -> List[float]:
    rows = _payments(period).annotate(hour=ExtractHour('paid_at')).values('hour').annotate(total=Sum('amount'))
    by_hour = {r['hour']: _num(r['total']) for r in rows}
    return [by_hour.get(h, 0.0) for h in range(24)]


def _trend(current: Period, previous: Period) -> Dict[str, Any]:
    if current.start == current.end:
        return {
            'granularity': 'hour',
            'labels': list(range(24)),
            'current': _hourly_sales(current),
            'previous': _hourly_sales(previous),
        }
    days = (current.end - current.start).days + 1
    cur_map, prev_map = _daily_sales(current), _daily_sales(previous)
    cur_days = [current.start + timedelta(days=i) for i in range(days)]
    prev_days = [previous.start + timedelta(days=i) for i in range(days)]
    return {
        'granularity': 'day',
        'labels': [d.isoformat() for d in cur_days],
        'previous_labels': [d.isoformat() for d in prev_days],
        'current': [cur_map.get(d, 0.0) for d in cur_days],
        'previous': [prev_map.get(d, 0.0) for d in prev_days],
    }


def _busy_hours(period: Period) -> List[int]:
    rows = _orders(period).exclude(status=OrderStatus.DRAFT).annotate(hour=ExtractHour('created_at')) \
        .values('hour').annotate(n=Count('id'))
    by_hour = {r['hour']: r['n'] for r in rows}
    return [by_hour.get(h, 0) for h in range(24)]


def _methods(period: Period) -> List[Dict[str, Any]]:
    rows = {r['method']: r for r in _payments(period).values('method').annotate(n=Count('id'), total=Sum('amount'))}
    return [
        {'method': m, 'count': rows[m]['n'] if m in rows else 0, 'total': _num(rows[m]['total']) if m in rows else 0.0}
        for m in PaymentMethod.values
    ]


def _sold_items(period: Period):
    start_dt, end_dt = period.bounds()
    return OrderItem.objects.filter(
        order__status__in=SOLD_STATUSES, order__created_at__gte=start_dt, order__created_at__lt=end_dt)


def _top_items(period: Period) -> Dict[str, List[Dict[str, Any]]]:
    base = _sold_items(period).values('menu_name_snapshot').annotate(qty=Sum('quantity'), revenue=Sum('subtotal'))

    def shape(qs):
        return [{'name': r['menu_name_snapshot'], 'qty': r['qty'] or 0, 'revenue': _num(r['revenue'])}
                for r in qs[:TOP_ITEMS]]

    return {
        'by_qty': shape(base.order_by('-qty', 'menu_name_snapshot')),
        'by_revenue': shape(base.order_by('-revenue', 'menu_name_snapshot')),
    }


def _categories(period: Period) -> List[Dict[str, Any]]:
    rows = (
        _sold_items(period).values('menu_item__category__name')
        .annotate(revenue=Sum('subtotal'), qty=Sum('quantity')).order_by('-revenue')[:TOP_CATEGORIES]
    )
    # An empty name means the menu item was deleted; the page shows the translated "No category" label.
    return [{'name': r['menu_item__category__name'] or '', 'revenue': _num(r['revenue']), 'qty': r['qty'] or 0}
            for r in rows]


def _statuses(period: Period) -> Dict[str, int]:
    counts = {r['status']: r['n'] for r in _orders(period).values('status').annotate(n=Count('id'))}
    return {
        'waiting': counts.get(OrderStatus.WAITING_CASHIER_CONFIRMATION, 0),
        'active': sum(counts.get(s, 0) for s in ACTIVE_STATUSES),
        'completed': counts.get(OrderStatus.COMPLETED, 0),
        'rejected': counts.get(OrderStatus.REJECTED, 0),
        'cancelled': counts.get(OrderStatus.CANCELLED, 0),
    }


def _cashiers(period: Period) -> List[Dict[str, Any]]:
    rows = (
        _payments(period)
        .values('received_by_id', 'received_by__full_name', 'received_by__username')
        .annotate(total=Sum('amount'), n=Count('id')).order_by('-total')[:TOP_CASHIERS]
    )
    return [{
        'id': str(r['received_by_id']),
        'name': (r['received_by__full_name'] or r['received_by__username'] or '-').strip() or '-',
        'total': _num(r['total']), 'count': r['n'],
    } for r in rows]


def _fmt_dt(value) -> str:
    return timezone.localtime(value).strftime('%d/%m %H:%M') if value else ''


def _iso_day(value) -> str:
    """Local (Dili) calendar day of a timestamp, used to deep-link into the history list."""
    return timezone.localtime(value).date().isoformat() if value else ''


def _recent_payments() -> List[Dict[str, Any]]:
    rows = Payment.objects.filter(status=PaymentStatus.COMPLETED).select_related('table_session__table') \
        .order_by('-paid_at', '-created_at')[:RECENT_ROWS]
    return [{
        'id': str(p.id), 'code': p.payment_code, 'table': p.table_session.table.display_name,
        'amount': _num(p.amount), 'method': p.method, 'at': _fmt_dt(p.paid_at or p.created_at),
        'date': _iso_day(p.paid_at or p.created_at),
    } for p in rows]


def _recent_rejections() -> List[Dict[str, Any]]:
    rows = Order.objects.filter(status=OrderStatus.REJECTED).select_related('table_session__table') \
        .order_by('-created_at')[:RECENT_ROWS]
    return [{
        'id': str(o.id), 'code': o.order_code, 'table': o.table_session.table.display_name,
        'reason': o.rejection_reason or '', 'at': _fmt_dt(o.created_at), 'date': _iso_day(o.created_at),
    } for o in rows]


def build_overview(range_key: Optional[str] = 'today', today: Optional[date] = None) -> Dict[str, Any]:
    current, previous, key = parse_range(range_key, today)
    tables = RestaurantTable.objects.filter(is_active=True).aggregate(
        total=Count('id'), occupied=Count('id', filter=Q(status=TableStatus.OCCUPIED)),
    )

    return {
        'range': key,
        'period': {
            'start': current.start.isoformat(), 'end': current.end.isoformat(),
            'previous_start': previous.start.isoformat(), 'previous_end': previous.end.isoformat(),
        },
        'kpi': _kpi(_period_totals(current), _period_totals(previous)),
        'tables': {'occupied': tables['occupied'], 'total': tables['total']},
        'trend': _trend(current, previous),
        'busy_hours': _busy_hours(current),
        'methods': _methods(current),
        'top_items': _top_items(current),
        'categories': _categories(current),
        'statuses': _statuses(current),
        'cashiers': _cashiers(current),
        'recent_payments': _recent_payments(),
        'recent_rejections': _recent_rejections(),
    }
