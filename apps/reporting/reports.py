"""
Report builders.

Each builder returns a ReportData: one structure that the page, the CSV export and the PDF export
all render, so the numbers can never differ between formats.

Definitions (shared with the dashboard in selectors.py):
  * A sale is a Payment with status COMPLETED, dated by `paid_at` in the local business day.
  * Items sold come from OrderItem rows of orders in CONFIRMED..COMPLETED, dated by order.created_at.
"""
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Callable, Dict, Iterable, List, Optional

from django.db.models import Count, Q, Sum
from django.db.models.functions import TruncDate
from django.utils import timezone

from apps.ordering.models import OrderItem, OrderStatus, OrderStatusHistory
from apps.payments.models import Payment, PaymentMethod, PaymentStatus
from apps.tables.models import SessionStatus, TableSession

MAX_RANGE_DAYS = 366
PRESETS = ('today', '7days', 'month', 'custom')
ZERO = Decimal('0.00')

SOLD_STATUSES = [
    OrderStatus.CONFIRMED,
    OrderStatus.PREPARING,
    OrderStatus.READY,
    OrderStatus.SERVED,
    OrderStatus.COMPLETED,
]


class ReportError(ValueError):
    """Raised for invalid report requests. `code` maps to a localized `rpt_err_<code>` label."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class Label:
    """A cell or summary value that must be translated when rendered."""
    key: str


@dataclass(frozen=True)
class Period:
    start: date
    end: date
    preset: str = 'custom'

    def bounds(self):
        """Aware [start, end) datetimes in the active (local) timezone."""
        tz = timezone.get_current_timezone()
        start_dt = timezone.make_aware(datetime.combine(self.start, time.min), tz)
        end_dt = timezone.make_aware(datetime.combine(self.end + timedelta(days=1), time.min), tz)
        return start_dt, end_dt


@dataclass(frozen=True)
class Column:
    label_key: str
    kind: str = 'text'  # text, int, decimal (one place), money, percent, date, datetime
    align: str = 'start'  # start or end


@dataclass(frozen=True)
class SummaryItem:
    label_key: str
    value: Any
    kind: str = 'text'


@dataclass
class ReportData:
    report_type: str
    title_key: str
    period: Period
    columns: List[Column]
    rows: Iterable[list]  # a list, or a generator for exports that stream from the database
    summary: List[SummaryItem] = field(default_factory=list)
    scoped_to_user: bool = False
    filters_text: str = ''
    drills: Optional[List[Optional[Dict[str, str]]]] = None  # one drill-down target per row (page only)


# --------------------------------------------------------------------------- periods

def _parse_iso_date(raw: Optional[str]) -> date:
    try:
        return date.fromisoformat((raw or '').strip())
    except ValueError:
        raise ReportError('invalid_date')


def parse_period(preset: Optional[str], start_raw: Optional[str] = None, end_raw: Optional[str] = None,
                 today: Optional[date] = None) -> Period:
    """Resolve a preset or a custom range into a Period of local dates (both ends inclusive)."""
    today = today or timezone.localdate()
    preset = (preset or 'today').strip()
    if preset not in PRESETS:
        raise ReportError('invalid_date')

    if preset == 'today':
        return Period(today, today, preset)
    if preset == '7days':
        return Period(today - timedelta(days=6), today, preset)
    if preset == 'month':
        return Period(today.replace(day=1), today, preset)

    start = _parse_iso_date(start_raw)
    end = _parse_iso_date(end_raw)
    if end < start:
        raise ReportError('end_before_start')
    if (end - start).days + 1 > MAX_RANGE_DAYS:
        raise ReportError('range_too_long')
    return Period(start, end, preset)


# --------------------------------------------------------------------------- helpers

def is_scoped_user(user) -> bool:
    """Cashiers only see their own rows in the per-cashier and exceptions reports."""
    return not getattr(user, 'is_admin', False)


def _display_name(full_name: Optional[str], username: Optional[str]) -> str:
    return (full_name or username or '').strip() or '-'


def _user_name(user) -> str:
    if user is None:
        return '-'
    return _display_name(getattr(user, 'full_name', ''), getattr(user, 'username', ''))


def _money(value) -> Decimal:
    return (value or ZERO).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


def _place(session) -> str:
    """Table name, or the take-away label (Take away #012 - Budi)."""
    return session.label


def _completed_payments(period: Period):
    start_dt, end_dt = period.bounds()
    return Payment.objects.filter(status=PaymentStatus.COMPLETED, paid_at__gte=start_dt, paid_at__lt=end_dt)


# --------------------------------------------------------------------------- builders

def daily_sales_report(period: Period, user=None) -> ReportData:
    grouped = (
        _completed_payments(period)
        .annotate(day=TruncDate('paid_at'))
        .values('day')
        .annotate(n=Count('id'), total=Sum('amount'))
        .order_by('day')
    )
    rows, total_sales, total_count = [], ZERO, 0
    for g in grouped:
        total = _money(g['total'])
        total_sales += total
        total_count += g['n']
        rows.append([g['day'], g['n'], total, _money(total / g['n'])])

    average = _money(total_sales / total_count) if total_count else ZERO
    drills = [
        {'kind': 'transactions', 'start': r[0].isoformat(), 'end': r[0].isoformat(), 'status': PaymentStatus.COMPLETED}
        for r in rows
    ]
    return ReportData(
        report_type='daily_sales',
        drills=drills,
        title_key='rpt_type_daily_sales',
        period=period,
        columns=[
            Column('rpt_col_date', 'date'),
            Column('rpt_col_transactions', 'int', 'end'),
            Column('rpt_col_total_sales', 'money', 'end'),
            Column('rpt_col_average', 'money', 'end'),
        ],
        rows=rows,
        summary=[
            SummaryItem('rpt_sum_total_sales', total_sales, 'money'),
            SummaryItem('rpt_sum_transactions', total_count, 'int'),
            SummaryItem('rpt_sum_average', average, 'money'),
        ],
    )


def menu_sales_report(period: Period, user=None) -> ReportData:
    start_dt, end_dt = period.bounds()
    grouped = (
        OrderItem.objects.filter(
            order__status__in=SOLD_STATUSES,
            order__created_at__gte=start_dt,
            order__created_at__lt=end_dt,
        )
        .exclude(status='UNAVAILABLE')
        .values('menu_item__category__name', 'menu_name_snapshot')
        .annotate(qty=Sum('quantity'), revenue=Sum('subtotal'))
    )
    entries = [
        (g['menu_item__category__name'], g['menu_name_snapshot'], g['qty'] or 0, _money(g['revenue']))
        for g in grouped
    ]
    # Named categories first (alphabetical), then items without a category; best sellers first inside each.
    entries.sort(key=lambda e: (e[0] is None, (e[0] or '').lower(), -e[2], e[1].lower()))

    rows = [[e[0] if e[0] else Label('rpt_uncategorized'), e[1], e[2], e[3]] for e in entries]
    # Lists every order in the period that contains the item (the report itself counts sold statuses only).
    drills = [{'kind': 'orders', 'q': e[1]} for e in entries]
    return ReportData(
        report_type='menu_sales',
        drills=drills,
        title_key='rpt_type_menu_sales',
        period=period,
        columns=[
            Column('rpt_col_category'),
            Column('rpt_col_menu'),
            Column('rpt_col_qty', 'int', 'end'),
            Column('rpt_col_revenue', 'money', 'end'),
        ],
        rows=rows,
        summary=[
            SummaryItem('rpt_sum_items_sold', sum(e[2] for e in entries), 'int'),
            SummaryItem('rpt_sum_revenue', sum((e[3] for e in entries), ZERO), 'money'),
        ],
    )


ORDER_TYPES = ('DINE_IN', 'TAKEAWAY')


def order_types_report(period: Period, user=None) -> ReportData:
    """Sales split by order type: dine-in (a real table) and take away (the hidden virtual table)."""
    grouped = {
        g['table_session__table__is_takeaway']: g
        for g in _completed_payments(period).values('table_session__table__is_takeaway').annotate(n=Count('id'), total=Sum('amount'))
    }
    entries = []
    for kind in ORDER_TYPES:
        g = grouped.get(kind == 'TAKEAWAY')
        entries.append([kind, g['n'] if g else 0, _money(g['total']) if g else ZERO])

    grand_total = sum((e[2] for e in entries), ZERO)
    percents = [Decimal('0.0')] * len(entries)
    if grand_total > 0:
        percents = [(e[2] * 100 / grand_total).quantize(Decimal('0.1'), rounding=ROUND_HALF_UP) for e in entries]
        # Put the rounding remainder on the largest row so the column always adds up to 100.0.
        remainder = Decimal('100.0') - sum(percents)
        if remainder:
            percents[max(range(len(entries)), key=lambda i: entries[i][2])] += remainder

    rows = [[Label(f'rpt_ot_{e[0]}'), e[1], e[2], percents[i]] for i, e in enumerate(entries)]
    drills = [{'kind': 'transactions', 'order_type': e[0], 'status': PaymentStatus.COMPLETED} for e in entries]
    return ReportData(
        report_type='order_types',
        drills=drills,
        title_key='rpt_type_order_types',
        period=period,
        columns=[
            Column('rpt_col_order_type'),
            Column('rpt_col_transactions', 'int', 'end'),
            Column('rpt_col_amount', 'money', 'end'),
            Column('rpt_col_percent', 'percent', 'end'),
        ],
        rows=rows,
        summary=[
            SummaryItem('rpt_sum_total_sales', grand_total, 'money'),
            SummaryItem('rpt_sum_transactions', sum(e[1] for e in entries), 'int'),
        ],
    )


def cashiers_report(period: Period, user=None) -> ReportData:
    scoped = user is not None and is_scoped_user(user)
    start_dt, end_dt = period.bounds()

    completed = _completed_payments(period)
    voided = Payment.objects.filter(
        status=PaymentStatus.VOIDED, updated_at__gte=start_dt, updated_at__lt=end_dt
    )
    if scoped:
        completed = completed.filter(received_by=user)
        voided = voided.filter(received_by=user)

    people: Dict[Any, Dict[str, Any]] = {}
    for g in completed.values('received_by_id', 'received_by__full_name', 'received_by__username').annotate(
        n=Count('id'), total=Sum('amount')
    ):
        people[g['received_by_id']] = {
            'name': _display_name(g['received_by__full_name'], g['received_by__username']),
            'n': g['n'], 'total': _money(g['total']), 'voids': 0,
        }
    for g in voided.values('received_by_id', 'received_by__full_name', 'received_by__username').annotate(n=Count('id')):
        entry = people.setdefault(g['received_by_id'], {
            'name': _display_name(g['received_by__full_name'], g['received_by__username']),
            'n': 0, 'total': ZERO, 'voids': 0,
        })
        entry['voids'] = g['n']

    entries = sorted(people.items(), key=lambda kv: (-kv[1]['total'], kv[1]['name'].lower()))
    rows = [[p['name'], p['n'], p['total'], p['voids']] for _, p in entries]
    drills = [{'kind': 'transactions', 'cashier': str(person_id)} for person_id, _ in entries]
    entries = [p for _, p in entries]
    return ReportData(
        report_type='cashiers',
        drills=drills,
        title_key='rpt_type_cashiers',
        period=period,
        columns=[
            Column('rpt_col_cashier'),
            Column('rpt_col_transactions', 'int', 'end'),
            Column('rpt_col_received', 'money', 'end'),
            Column('rpt_col_voids', 'int', 'end'),
        ],
        rows=rows,
        summary=[
            SummaryItem('rpt_sum_total_sales', sum((p['total'] for p in entries), ZERO), 'money'),
            SummaryItem('rpt_sum_transactions', sum(p['n'] for p in entries), 'int'),
            SummaryItem('rpt_sum_voided', sum(p['voids'] for p in entries), 'int'),
        ],
        scoped_to_user=scoped,
    )


def exceptions_report(period: Period, user=None) -> ReportData:
    scoped = user is not None and is_scoped_user(user)
    start_dt, end_dt = period.bounds()

    history = OrderStatusHistory.objects.filter(
        to_status__in=[OrderStatus.REJECTED, OrderStatus.CANCELLED],
        created_at__gte=start_dt,
        created_at__lt=end_dt,
    ).select_related('order__table_session__table', 'actor_user')
    voided = Payment.objects.filter(
        status=PaymentStatus.VOIDED, updated_at__gte=start_dt, updated_at__lt=end_dt
    ).select_related('table_session__table', 'voided_by', 'received_by')

    unavailable = OrderItem.objects.filter(
        status='UNAVAILABLE', status_changed_at__gte=start_dt, status_changed_at__lt=end_dt,
    ).select_related('order__table_session__table', 'status_changed_by')

    walkouts = walkout_sessions(period).select_related('table', 'closed_by').prefetch_related('orders')

    if scoped:
        history = history.filter(actor_user=user)
        voided = voided.filter(Q(voided_by=user) | Q(received_by=user))
        unavailable = unavailable.filter(Q(status_changed_by=user) | Q(order__confirmed_by=user) | Q(order__created_by=user))
        walkouts = walkouts.filter(closed_by=user)

    entries = []  # (sort time, row)
    counts = {'rejected': 0, 'cancelled': 0, 'voided': 0, 'unavailable': 0, 'walkout': 0}
    walkout_total = ZERO
    for s in walkouts:
        counts['walkout'] += 1
        walkout_total += _money(s.unpaid_amount)
        codes = sorted(o.order_code for o in s.orders.all())
        entries.append((s.closed_at, [
            s.closed_at, Label('rpt_kind_walkout'), codes[0] if codes else '-',
            _place(s), _money(s.unpaid_amount), s.close_reason or '', _user_name(s.closed_by),
        ]))
    for line in unavailable:
        counts['unavailable'] += 1
        entries.append((line.status_changed_at, [
            line.status_changed_at, Label('rpt_kind_unavailable'), line.order.order_code,
            _place(line.order.table_session), _money(line.subtotal),
            f"{line.quantity}x {line.menu_name_snapshot}" + (f": {line.unavailable_reason}" if line.unavailable_reason else ''),
            _user_name(line.status_changed_by),
        ]))
    for h in history:
        kind = 'rejected' if h.to_status == OrderStatus.REJECTED else 'cancelled'
        counts[kind] += 1
        order = h.order
        reason = h.reason or (order.rejection_reason if kind == 'rejected' else order.cancellation_reason) or ''
        entries.append((h.created_at, [
            h.created_at, Label(f'rpt_kind_{kind}'), order.order_code,
            _place(order.table_session), _money(order.grand_total), reason, _user_name(h.actor_user),
        ]))
    for p in voided:
        counts['voided'] += 1
        entries.append((p.updated_at, [
            p.updated_at, Label('rpt_kind_voided'), p.payment_code,
            _place(p.table_session), _money(p.amount), p.void_reason or '',
            _user_name(p.voided_by or p.received_by),
        ]))

    entries.sort(key=lambda e: e[0], reverse=True)
    drills = [
        {'kind': 'transactions', 'status': PaymentStatus.VOIDED, 'q': e[1][2]}
        if e[1][1].key == 'rpt_kind_voided' else {'kind': 'orders', 'q': e[1][2]}
        for e in entries
    ]
    return ReportData(
        report_type='exceptions',
        drills=drills,
        title_key='rpt_type_exceptions',
        period=period,
        columns=[
            Column('rpt_col_time', 'datetime'),
            Column('rpt_col_kind'),
            Column('rpt_col_code'),
            Column('rpt_col_table'),
            Column('rpt_col_amount', 'money', 'end'),
            Column('rpt_col_reason'),
            Column('rpt_col_staff'),
        ],
        rows=[e[1] for e in entries],
        summary=[
            SummaryItem('rpt_sum_rejected', counts['rejected'], 'int'),
            SummaryItem('rpt_sum_cancelled', counts['cancelled'], 'int'),
            SummaryItem('rpt_sum_voided', counts['voided'], 'int'),
            SummaryItem('rpt_sum_unavailable', counts['unavailable'], 'int'),
            SummaryItem('rpt_sum_walkout', counts['walkout'], 'int'),
            SummaryItem('rpt_sum_walkout_amount', walkout_total, 'money'),
        ],
        scoped_to_user=scoped,
    )


def walkout_sessions(period: Period):
    """Table sessions closed in the period because the guests left without paying."""
    start_dt, end_dt = period.bounds()
    return TableSession.objects.filter(close_kind='WALKOUT', closed_at__gte=start_dt, closed_at__lt=end_dt)


def table_usage_report(period: Period, user=None) -> ReportData:
    start_dt, end_dt = period.bounds()
    sessions = (
        TableSession.objects.filter(opened_at__gte=start_dt, opened_at__lt=end_dt)
        .exclude(status=SessionStatus.CANCELLED)
        .values('table_id', 'table__display_name', 'table__sort_order', 'table__table_code', 'table__is_takeaway')
        .annotate(n=Count('id'), guests=Sum('guest_count'))
    )
    sales = (
        _completed_payments(period)
        .values('table_session__table_id', 'table_session__table__display_name',
                'table_session__table__sort_order', 'table_session__table__table_code')
        .annotate(total=Sum('amount'))
    )

    tables: Dict[Any, Dict[str, Any]] = {}
    for s in sessions:
        tables[s['table_id']] = {
            'name': Label('rpt_ot_TAKEAWAY') if s['table__is_takeaway'] else s['table__display_name'],
            'order': (s['table__sort_order'], s['table__table_code']),
            'n': s['n'], 'guests': s['guests'] or 0, 'total': ZERO,
        }
    for s in sales:
        tid = s['table_session__table_id']
        entry = tables.setdefault(tid, {
            'name': s['table_session__table__display_name'],
            'order': (s['table_session__table__sort_order'], s['table_session__table__table_code']),
            'n': 0, 'guests': 0, 'total': ZERO,
        })
        entry['total'] = _money(s['total'])

    entries = sorted(tables.values(), key=lambda t: t['order'])
    return ReportData(
        report_type='table_usage',
        drills=[{'kind': 'orders', 'order_type': 'TAKEAWAY'} if isinstance(t['name'], Label) else {'kind': 'orders', 'q': t['name']}
                for t in entries],
        title_key='rpt_type_table_usage',
        period=period,
        columns=[
            Column('rpt_col_table'),
            Column('rpt_col_sessions', 'int', 'end'),
            Column('rpt_col_guests', 'int', 'end'),
            Column('rpt_col_sales', 'money', 'end'),
        ],
        rows=[[t['name'], t['n'], t['guests'], t['total']] for t in entries],
        summary=[
            SummaryItem('rpt_sum_sessions', sum(t['n'] for t in entries), 'int'),
            SummaryItem('rpt_sum_guests', sum(t['guests'] for t in entries), 'int'),
            SummaryItem('rpt_sum_total_sales', sum((t['total'] for t in entries), ZERO), 'money'),
        ],
    )


def shifts_report(period: Period, user=None) -> ReportData:
    """Cashier shifts opened in the period: opening cash, cash sales, expected, counted and difference.
    Cashiers only see their own shifts."""
    from apps.shifts.models import Shift, ShiftStatus
    from apps.shifts.services import shift_figures
    scoped = user is not None and is_scoped_user(user)
    start_dt, end_dt = period.bounds()
    shifts = Shift.objects.filter(opened_at__gte=start_dt, opened_at__lt=end_dt).select_related('cashier').order_by('-opened_at')
    if scoped:
        shifts = shifts.filter(cashier=user)
    rows, drills = [], []
    total_diff = ZERO
    for s in shifts:
        live = s.status == ShiftStatus.OPEN
        figures = shift_figures(s) if live else {'cash_sales': s.cash_sales, 'expected_cash': s.expected_cash}
        diff = None if live else _money(s.difference)
        if diff is not None:
            total_diff += diff
        rows.append([
            s.opened_at, s.closed_at if s.closed_at else Label('rpt_shift_open'), _user_name(s.cashier),
            _money(s.opening_cash), _money(figures['cash_sales']), _money(figures['expected_cash']),
            _money(s.counted_cash) if s.counted_cash is not None else '-',
            diff if diff is not None else '-', s.closing_note or '',
        ])
        drills.append({'kind': 'transactions', 'cashier': str(s.cashier_id)})
    return ReportData(
        report_type='shifts',
        drills=drills,
        title_key='rpt_type_shifts',
        period=period,
        columns=[
            Column('rpt_col_opened', 'datetime'),
            Column('rpt_col_closed', 'datetime'),
            Column('rpt_col_cashier'),
            Column('rpt_col_opening_cash', 'money', 'end'),
            Column('rpt_col_cash_sales', 'money', 'end'),
            Column('rpt_col_expected_cash', 'money', 'end'),
            Column('rpt_col_counted_cash', 'money', 'end'),
            Column('rpt_col_difference', 'money', 'end'),
            Column('rpt_col_note'),
        ],
        rows=rows,
        summary=[
            SummaryItem('rpt_sum_shifts', len(rows), 'int'),
            SummaryItem('rpt_sum_difference', total_diff, 'money'),
        ],
        scoped_to_user=scoped,
    )


def dine_in_sessions(period: Period):
    """Dine-in table sessions opened in the period (cancelled openings and take away are not visits).
    Shared with the Overview so the visitor numbers agree everywhere."""
    start_dt, end_dt = period.bounds()
    return (TableSession.objects.filter(opened_at__gte=start_dt, opened_at__lt=end_dt, table__is_takeaway=False)
            .exclude(status=SessionStatus.CANCELLED))


def takeaway_orders(period: Period):
    """Take-away orders placed in the period that were accepted (CONFIRMED..COMPLETED)."""
    from apps.ordering.models import Order
    start_dt, end_dt = period.bounds()
    return Order.objects.filter(created_at__gte=start_dt, created_at__lt=end_dt, status__in=SOLD_STATUSES,
                                table_session__table__is_takeaway=True)


def visitors_report(period: Period, user=None) -> ReportData:
    """Per day: dine-in sessions, guests, average guests per session and take-away orders."""
    visits = {
        r['day']: r for r in dine_in_sessions(period).annotate(day=TruncDate('opened_at'))
        .values('day').annotate(n=Count('id'), guests=Sum('guest_count'))
    }
    takeaway = {
        r['day']: r['n'] for r in takeaway_orders(period).annotate(day=TruncDate('created_at'))
        .values('day').annotate(n=Count('id'))
    }
    rows, drills = [], []
    total_sessions = total_guests = total_takeaway = 0
    for day in sorted(set(visits) | set(takeaway)):
        v = visits.get(day) or {}
        n, guests, ta = v.get('n', 0), int(v.get('guests') or 0), takeaway.get(day, 0)
        total_sessions += n
        total_guests += guests
        total_takeaway += ta
        avg = (Decimal(guests) / n).quantize(Decimal('0.1'), rounding=ROUND_HALF_UP) if n else Decimal('0.0')
        rows.append([day, n, guests, avg, ta])
        drills.append({'kind': 'orders', 'start': day.isoformat(), 'end': day.isoformat()})
    overall_avg = (Decimal(total_guests) / total_sessions).quantize(Decimal('0.1'), rounding=ROUND_HALF_UP) \
        if total_sessions else Decimal('0.0')
    return ReportData(
        report_type='visitors',
        drills=drills,
        title_key='rpt_type_visitors',
        period=period,
        columns=[
            Column('rpt_col_date', 'date'),
            Column('rpt_col_sessions', 'int', 'end'),
            Column('rpt_col_guests', 'int', 'end'),
            Column('rpt_col_avg_guests', 'decimal', 'end'),
            Column('rpt_col_takeaway_orders', 'int', 'end'),
        ],
        rows=rows,
        summary=[
            SummaryItem('rpt_sum_guests', total_guests, 'int'),
            SummaryItem('rpt_sum_sessions', total_sessions, 'int'),
            SummaryItem('rpt_sum_avg_guests', overall_avg, 'decimal'),
            SummaryItem('rpt_sum_takeaway_orders', total_takeaway, 'int'),
        ],
    )


REPORT_BUILDERS: Dict[str, Callable[..., ReportData]] = {
    'daily_sales': daily_sales_report,
    'menu_sales': menu_sales_report,
    'order_types': order_types_report,
    'cashiers': cashiers_report,
    'exceptions': exceptions_report,
    'table_usage': table_usage_report,
    'shifts': shifts_report,
    'visitors': visitors_report,
}
REPORT_TYPES = tuple(REPORT_BUILDERS)


def build_report(report_type: str, period: Period, user=None) -> ReportData:
    builder = REPORT_BUILDERS.get(report_type)
    if builder is None:
        raise ReportError('invalid_report')
    return builder(period, user)
