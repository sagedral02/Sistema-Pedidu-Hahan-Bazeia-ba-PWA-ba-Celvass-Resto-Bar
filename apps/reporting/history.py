"""
Riwayat (history) of orders and transactions, with filters, sorting, pagination and detail.

Scope rules (enforced here, never in the browser):
  * Admin and superuser see everything.
  * A cashier sees only transactions they received, and orders they created, confirmed or acted on
    (status history). Orders still waiting for confirmation have no owner yet and stay out of the history.
    A detail outside the cashier's scope raises HistoryNotFound, which the views turn into a 404.

All time filters use [start, end) aware datetimes in the active timezone (Asia/Dili), never __date.
"""
import math
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, Iterator, List, Optional

from django.db.models import Count, Exists, IntegerField, OuterRef, Q, Subquery, Sum
from django.db.models.functions import Coalesce
from django.utils import timezone

from apps.ordering.models import Order, OrderItem, OrderSource, OrderStatus, OrderStatusHistory
from apps.payments.models import Payment, PaymentMethod, PaymentStatus
from apps.payments.services import calculate_session_bill

from .labels import label, normalize_lang
from .reports import (
    SOLD_STATUSES, ZERO, Column, Label, Period, ReportData, ReportError, SummaryItem, _money, is_scoped_user,
)

DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100

ORDER_SORTS = {
    'time': 'created_at',
    'code': 'order_code',
    'table': 'table_session__table__display_name',
    'status': 'status',
    'items': 'item_count',
    'total': 'grand_total',
}
TRANSACTION_SORTS = {
    'time': 'event_at',
    'code': 'payment_code',
    'table': 'table_session__table__display_name',
    'method': 'method',
    'amount': 'amount',
    'cashier': 'received_by__username',
    'status': 'status',
}
HISTORY_PAYMENT_STATUSES = (PaymentStatus.COMPLETED, PaymentStatus.VOIDED)
UNOWNED_ORDER_STATUSES = (OrderStatus.WAITING_CASHIER_CONFIRMATION, OrderStatus.DRAFT)

ORDER_TONES = {
    OrderStatus.COMPLETED: 'success',
    OrderStatus.SERVED: 'success',
    OrderStatus.CONFIRMED: 'info',
    OrderStatus.PREPARING: 'info',
    OrderStatus.READY: 'info',
    OrderStatus.WAITING_CASHIER_CONFIRMATION: 'warning',
    OrderStatus.REJECTED: 'danger',
    OrderStatus.CANCELLED: 'danger',
}
PAYMENT_TONES = {
    PaymentStatus.COMPLETED: 'success',
    PaymentStatus.VOIDED: 'danger',
    PaymentStatus.PENDING: 'warning',
}


class HistoryNotFound(Exception):
    """The record does not exist or is outside the requesting user's scope."""


# --------------------------------------------------------------------------- filters

@dataclass(frozen=True)
class HistoryFilters:
    status: str = ''
    source: str = ''
    method: str = ''
    order_type: str = ''
    cashier: Optional[uuid.UUID] = None
    q: str = ''

    def as_dict(self) -> Dict[str, str]:
        return {
            k: str(v) for k, v in (
                ('status', self.status), ('source', self.source), ('method', self.method), ('order_type', self.order_type),
                ('cashier', self.cashier or ''), ('q', self.q),
            ) if v
        }


def parse_filters(kind: str, params) -> HistoryFilters:
    """Validate filter query parameters. Unknown values raise ReportError('invalid_filter')."""
    status = (params.get('status') or '').strip()
    source = (params.get('source') or '').strip()
    method = (params.get('method') or '').strip()
    order_type = (params.get('order_type') or '').strip()
    if order_type and order_type not in ('DINE_IN', 'TAKEAWAY'):
        raise ReportError('invalid_filter')
    cashier_raw = (params.get('cashier') or '').strip()
    q = (params.get('q') or '').strip()[:80]

    if kind == 'orders':
        if status and status not in OrderStatus.values:
            raise ReportError('invalid_filter')
        if source and source not in OrderSource.values:
            raise ReportError('invalid_filter')
        method = ''
    else:
        if status and status not in [s.value for s in HISTORY_PAYMENT_STATUSES]:
            raise ReportError('invalid_filter')
        if method and method not in PaymentMethod.values:
            raise ReportError('invalid_filter')
        source = ''

    cashier = None
    if cashier_raw:
        try:
            cashier = uuid.UUID(cashier_raw)
        except ValueError:
            raise ReportError('invalid_filter')
    return HistoryFilters(status=status, source=source, method=method, order_type=order_type, cashier=cashier, q=q)


def parse_sort(kind: str, raw: Optional[str]) -> str:
    """Return a whitelisted sort expression ('time' or '-time'); anything else falls back to newest first."""
    allowed = ORDER_SORTS if kind == 'orders' else TRANSACTION_SORTS
    raw = (raw or '').strip()
    key = raw.lstrip('-')
    if key not in allowed:
        return '-time'
    return raw


def parse_paging(page_raw, size_raw):
    def to_int(value, default):
        try:
            return int(value)
        except (TypeError, ValueError):
            return default
    size = min(max(to_int(size_raw, DEFAULT_PAGE_SIZE), 1), MAX_PAGE_SIZE)
    return max(to_int(page_raw, 1), 1), size


# --------------------------------------------------------------------------- querysets

def _order_by(sort: str, mapping: Dict[str, str]):
    desc = sort.startswith('-')
    field_name = mapping[sort.lstrip('-')]
    return [('-' if desc else '') + field_name, '-pk' if desc else 'pk']


def orders_queryset(period: Optional[Period], user, filters: HistoryFilters):
    """Orders visible to `user` that match the filters. `period=None` means no time limit (detail lookups)."""
    qs = Order.objects.all()
    if period is not None:
        start_dt, end_dt = period.bounds()
        qs = qs.filter(created_at__gte=start_dt, created_at__lt=end_dt)

    scoped = user is not None and is_scoped_user(user)
    if scoped:
        acted = OrderStatusHistory.objects.filter(order=OuterRef('pk'), actor_user=user)
        qs = (
            qs.annotate(_acted=Exists(acted))
            .filter(Q(confirmed_by=user) | Q(created_by=user) | Q(_acted=True))
            .exclude(status__in=UNOWNED_ORDER_STATUSES)
        )

    if filters.status:
        qs = qs.filter(status=filters.status)
    if filters.source:
        qs = qs.filter(source=filters.source)
    if filters.order_type:
        qs = qs.filter(table_session__table__is_takeaway=(filters.order_type == 'TAKEAWAY'))
    if filters.cashier and not scoped:
        acted_by = OrderStatusHistory.objects.filter(order=OuterRef('pk'), actor_user_id=filters.cashier)
        qs = qs.annotate(_by=Exists(acted_by)).filter(Q(confirmed_by_id=filters.cashier) | Q(created_by_id=filters.cashier) | Q(_by=True))
    if filters.q:
        has_menu = OrderItem.objects.filter(order=OuterRef('pk'), menu_name_snapshot__icontains=filters.q)
        qs = qs.annotate(_menu=Exists(has_menu)).filter(
            Q(order_code__icontains=filters.q)
            | Q(table_session__table__display_name__icontains=filters.q)
            | Q(table_session__table__table_code__icontains=filters.q)
            | Q(_menu=True)
        )

    return qs


def _with_order_extras(qs):
    """Row columns that need joins or subqueries. Kept apart so KPI aggregates stay cheap."""
    items_count = (
        OrderItem.objects.filter(order=OuterRef('pk')).order_by().values('order')
        .annotate(n=Sum('quantity')).values('n')
    )
    last_actor = OrderStatusHistory.objects.filter(order=OuterRef('pk'), actor_user__isnull=False).order_by('-created_at')
    return qs.annotate(
        item_count=Coalesce(Subquery(items_count, output_field=IntegerField()), 0),
        last_actor_name=Subquery(last_actor.values('actor_user__full_name')[:1]),
        last_actor_username=Subquery(last_actor.values('actor_user__username')[:1]),
    ).select_related('table_session__table', 'confirmed_by', 'created_by')


def transactions_queryset(period: Optional[Period], user, filters: HistoryFilters):
    qs = Payment.objects.filter(status__in=HISTORY_PAYMENT_STATUSES)
    if period is not None:
        start_dt, end_dt = period.bounds()
        qs = qs.filter(
            Q(paid_at__gte=start_dt, paid_at__lt=end_dt)
            | Q(paid_at__isnull=True, created_at__gte=start_dt, created_at__lt=end_dt)
        )

    scoped = user is not None and is_scoped_user(user)
    if scoped:
        qs = qs.filter(received_by=user)
    elif filters.cashier:
        qs = qs.filter(received_by_id=filters.cashier)

    if filters.status:
        qs = qs.filter(status=filters.status)
    if filters.method:
        qs = qs.filter(method=filters.method)
    if filters.order_type:
        qs = qs.filter(table_session__table__is_takeaway=(filters.order_type == 'TAKEAWAY'))
    if filters.q:
        q = Q(payment_code__icontains=filters.q) | Q(table_session__table__display_name__icontains=filters.q) \
            | Q(table_session__table__table_code__icontains=filters.q)
        if not scoped:
            q |= Q(received_by__username__icontains=filters.q) | Q(received_by__full_name__icontains=filters.q)
        qs = qs.filter(q)
    return qs


def _with_transaction_extras(qs):
    return qs.annotate(event_at=Coalesce('paid_at', 'created_at')).select_related('table_session__table', 'received_by')


# --------------------------------------------------------------------------- pages

@dataclass
class HistoryPage:
    kind: str
    rows: List[Dict[str, Any]]
    total: int
    page: int
    pages: int
    page_size: int
    kpi: Dict[str, Any] = field(default_factory=dict)


def _person(user_obj, username=None, full_name=None) -> str:
    if user_obj is not None:
        return (user_obj.full_name or user_obj.username or '').strip() or '-'
    return (full_name or username or '').strip() or '-'


def _order_row(o: Order) -> Dict[str, Any]:
    if o.confirmed_by_id:
        staff = _person(o.confirmed_by)
    elif o.last_actor_name or o.last_actor_username:
        staff = _person(None, o.last_actor_username, o.last_actor_name)
    elif o.created_by_id:
        staff = _person(o.created_by)
    else:
        staff = '-'
    return {
        'id': str(o.id), 'created_at': o.created_at, 'code': o.order_code,
        'table': o.table_session.label, 'status': o.status, 'source': o.source,
        'item_count': o.item_count, 'grand_total': _money(o.grand_total), 'staff': staff,
    }


def _transaction_row(p: Payment) -> Dict[str, Any]:
    return {
        'id': str(p.id), 'event_at': p.event_at, 'code': p.payment_code,
        'table': p.table_session.label, 'method': p.method, 'status': p.status,
        'amount': _money(p.amount),
        'tendered': _money(p.tendered_amount if p.tendered_amount is not None else p.amount),
        'change': _money(p.change_amount),
        'cashier': _person(p.received_by),
    }


def _paginate(qs, page: int, page_size: int):
    total = qs.count()
    pages = max(1, math.ceil(total / page_size))
    page = min(page, pages)
    offset = (page - 1) * page_size
    return total, page, pages, qs[offset:offset + page_size]


def _orders_kpi(qs) -> Dict[str, Any]:
    agg = qs.aggregate(
        n=Count('pk'),
        sold=Sum('grand_total', filter=Q(status__in=SOLD_STATUSES)),
        sold_n=Count('pk', filter=Q(status__in=SOLD_STATUSES)),
        exc=Count('pk', filter=Q(status__in=[OrderStatus.REJECTED, OrderStatus.CANCELLED])),
    )
    sold = _money(agg['sold'])
    return {
        'orders': agg['n'], 'sold_total': sold,
        'average': _money(sold / agg['sold_n']) if agg['sold_n'] else ZERO,
        'exceptions': agg['exc'],
    }


def _transactions_kpi(qs) -> Dict[str, Any]:
    done = Q(status=PaymentStatus.COMPLETED)
    agg = qs.aggregate(
        n=Count('pk'), collected=Sum('amount', filter=done), done_n=Count('pk', filter=done),
        voided=Count('pk', filter=Q(status=PaymentStatus.VOIDED)),
    )
    collected = _money(agg['collected'])
    return {
        'transactions': agg['n'], 'collected': collected,
        'average': _money(collected / agg['done_n']) if agg['done_n'] else ZERO,
        'voided': agg['voided'],
    }


def orders_history(period: Period, user, filters: HistoryFilters = HistoryFilters(), sort: str = '-time',
                   page: int = 1, page_size: int = DEFAULT_PAGE_SIZE) -> HistoryPage:
    qs = orders_queryset(period, user, filters)
    kpi = _orders_kpi(qs)
    ordered = _with_order_extras(qs).order_by(*_order_by(sort, ORDER_SORTS))
    total, page, pages, window = _paginate(ordered, page, page_size)
    return HistoryPage('orders', [_order_row(o) for o in window], total, page, pages, page_size, kpi)


def transactions_history(period: Period, user, filters: HistoryFilters = HistoryFilters(), sort: str = '-time',
                         page: int = 1, page_size: int = DEFAULT_PAGE_SIZE) -> HistoryPage:
    qs = transactions_queryset(period, user, filters)
    kpi = _transactions_kpi(qs)
    ordered = _with_transaction_extras(qs).order_by(*_order_by(sort, TRANSACTION_SORTS))
    total, page, pages, window = _paginate(ordered, page, page_size)
    return HistoryPage('transactions', [_transaction_row(p) for p in window], total, page, pages, page_size, kpi)


def iter_order_rows(period: Period, user, filters: HistoryFilters, sort: str = '-time') -> Iterator[Dict[str, Any]]:
    """Every matching order, one at a time (CSV export and PDF without pagination)."""
    qs = _with_order_extras(orders_queryset(period, user, filters)).order_by(*_order_by(sort, ORDER_SORTS))
    for o in qs.iterator(chunk_size=500):
        yield _order_row(o)


def iter_transaction_rows(period: Period, user, filters: HistoryFilters, sort: str = '-time') -> Iterator[Dict[str, Any]]:
    qs = _with_transaction_extras(transactions_queryset(period, user, filters)).order_by(
        *_order_by(sort, TRANSACTION_SORTS))
    for p in qs.iterator(chunk_size=500):
        yield _transaction_row(p)


# --------------------------------------------------------------------------- details

def _status_obj(lang: str, prefix: str, code: Optional[str], tones: Dict[str, str]) -> Dict[str, str]:
    if not code:
        return {'code': '', 'label': '-', 'tone': ''}
    text = label(lang, f'{prefix}{code}')
    return {'code': code, 'label': code if text == f'{prefix}{code}' else text, 'tone': tones.get(code, '')}


def _fmt_dt(value: Optional[datetime]) -> str:
    return timezone.localtime(value).strftime('%d/%m/%Y %H:%M') if value else ''


def _fmt_money(value) -> str:
    return f'${_money(value):,.2f}'


def order_status_obj(lang: str, code: Optional[str]):
    return _status_obj(lang, 'rpt_status_', code, ORDER_TONES)


def payment_status_obj(lang: str, code: Optional[str]):
    return _status_obj(lang, 'rpt_pstatus_', code, PAYMENT_TONES)


def order_detail(order_id, user, lang: str = 'tet') -> Dict[str, Any]:
    lang = normalize_lang(lang)
    try:
        order_uuid = uuid.UUID(str(order_id))
    except ValueError:
        raise HistoryNotFound()

    qs = orders_queryset(None, user, HistoryFilters()).filter(pk=order_uuid)
    order = qs.select_related('table_session__table', 'confirmed_by', 'created_by').first()
    if order is None:
        raise HistoryNotFound()

    scoped = user is not None and is_scoped_user(user)
    items = list(order.items.all().order_by('created_at'))
    history = list(order.status_history.select_related('actor_user').order_by('created_at'))
    payments_qs = Payment.objects.filter(table_session=order.table_session, status__in=HISTORY_PAYMENT_STATUSES) \
        .select_related('received_by').order_by('paid_at', 'created_at')
    if scoped:
        payments_qs = payments_qs.filter(received_by=user)

    return {
        'id': str(order.id),
        'code': order.order_code,
        'status': order_status_obj(lang, order.status),
        'source': label(lang, f'rpt_source_{order.source}'),
        'table': order.table_session.label,
        'session_id': str(order.table_session_id),
        'session_code': f'SES-{str(order.table_session_id)[:6].upper()}',
        'created_at': _fmt_dt(order.created_at),
        'confirmed_at': _fmt_dt(order.confirmed_at),
        'confirmed_by': _person(order.confirmed_by) if order.confirmed_by_id else '',
        'created_by': _person(order.created_by) if order.created_by_id else '',
        'customer_note': order.customer_note or '',
        'rejection_reason': order.rejection_reason or '',
        'cancellation_reason': order.cancellation_reason or '',
        'items': [{
            'name': i.menu_name_snapshot, 'quantity': i.quantity, 'unit_price': _fmt_money(i.unit_price),
            'subtotal': _fmt_money(i.subtotal), 'note': i.note or '',
            'status_code': i.status, 'status': label(lang, f'rpt_item_{i.status}'),
            'unavailable_reason': i.unavailable_reason or '',
        } for i in items],
        'money': {
            'subtotal': _fmt_money(order.subtotal), 'discount': _fmt_money(order.discount_total),
            'tax': _fmt_money(order.tax_total), 'total': _fmt_money(order.grand_total),
        },
        'timeline': [{
            'from': order_status_obj(lang, h.from_status) if h.from_status else None,
            'to': order_status_obj(lang, h.to_status),
            'actor': _person(h.actor_user) if h.actor_user_id else '',
            'actor_type': h.actor_type,
            'reason': h.reason or '',
            'at': _fmt_dt(h.created_at),
        } for h in history],
        'payments': [{
            'id': str(p.id), 'code': p.payment_code, 'method': label(lang, f'rpt_method_{p.method}'),
            'amount': _fmt_money(p.amount), 'status': payment_status_obj(lang, p.status),
            'paid_at': _fmt_dt(p.paid_at or p.created_at),
        } for p in payments_qs],
    }


def transaction_detail(payment_id, user, lang: str = 'tet') -> Dict[str, Any]:
    lang = normalize_lang(lang)
    try:
        payment_uuid = uuid.UUID(str(payment_id))
    except ValueError:
        raise HistoryNotFound()

    qs = transactions_queryset(None, user, HistoryFilters()).filter(pk=payment_uuid)
    payment = qs.select_related('table_session__table', 'received_by', 'voided_by').first()
    if payment is None:
        raise HistoryNotFound()

    session = payment.table_session
    paid_at = payment.paid_at or payment.created_at
    data = {
        'id': str(payment.id),
        'code': payment.payment_code,
        'status': payment_status_obj(lang, payment.status),
        'method': label(lang, f'rpt_method_{payment.method}'),
        'amount': _fmt_money(payment.amount),
        'tendered': _fmt_money(payment.tendered_amount if payment.tendered_amount is not None else payment.amount),
        'change': _fmt_money(payment.change_amount),
        'cashier': _person(payment.received_by),
        'paid_at': _fmt_dt(paid_at),
        'void_reason': payment.void_reason or '',
        'voided_by': _person(payment.voided_by) if payment.voided_by_id else '',
        'voided_at': _fmt_dt(payment.updated_at) if payment.status == PaymentStatus.VOIDED else '',
        'table': session.label,
        'session_id': str(session.id),
        'session_code': f'SES-{str(session.id)[:6].upper()}',
        'receipt': None,
    }
    if payment.status == PaymentStatus.COMPLETED:
        bill = calculate_session_bill(session)
        data['receipt'] = {
            'payment_code': payment.payment_code,
            'amount': str(payment.amount),
            'tendered_amount': str(payment.tendered_amount if payment.tendered_amount is not None else payment.amount),
            'change_amount': str(payment.change_amount or Decimal('0.00')),
            'paid_at': paid_at.isoformat() if paid_at else None,
            'table_name': session.label,
            'table_code': session.table.table_code,
            'items': bill.get('items', []),
            'subtotal': str(bill.get('subtotal', payment.amount)),
        }
    return data


# --------------------------------------------------------------------------- display (JSON for the page)

ORDER_COLUMNS = (
    ('time', 'rpt_col_time', 'start', True),
    ('code', 'rpt_col_code', 'start', True),
    ('table', 'rpt_col_table', 'start', True),
    ('status', 'rpt_col_status', 'start', True),
    ('items', 'rpt_col_items', 'end', True),
    ('total', 'rpt_col_total', 'end', True),
    ('staff', 'rpt_col_staff', 'start', False),
)
TRANSACTION_COLUMNS = (
    ('time', 'rpt_col_time', 'start', True),
    ('code', 'rpt_col_code', 'start', True),
    ('table', 'rpt_col_table', 'start', True),
    ('method', 'rpt_col_method', 'start', True),
    ('amount', 'rpt_col_amount', 'end', True),
    ('tendered', 'rpt_col_tendered', 'end', False),
    ('cashier', 'rpt_col_cashier', 'start', True),
    ('status', 'rpt_col_status', 'start', True),
)


def page_display(page: HistoryPage, lang: str, scoped: bool) -> Dict[str, Any]:
    lang = normalize_lang(lang)
    if page.kind == 'orders':
        columns = ORDER_COLUMNS
        rows = [{
            'id': r['id'],
            'time': _fmt_dt(r['created_at']),
            'code': r['code'],
            'table': r['table'],
            'status': order_status_obj(lang, r['status']),
            'items': str(r['item_count']),
            'total': _fmt_money(r['grand_total']),
            'staff': r['staff'],
        } for r in page.rows]
        kpi = [
            {'label': label(lang, 'rpt_sum_orders'), 'value': str(page.kpi['orders'])},
            {'label': label(lang, 'rpt_sum_sold_total'), 'value': _fmt_money(page.kpi['sold_total'])},
            {'label': label(lang, 'rpt_sum_avg_order'), 'value': _fmt_money(page.kpi['average'])},
            {'label': label(lang, 'rpt_sum_exceptions'), 'value': str(page.kpi['exceptions'])},
        ]
    else:
        columns = TRANSACTION_COLUMNS
        rows = [{
            'id': r['id'],
            'time': _fmt_dt(r['event_at']),
            'code': r['code'],
            'table': r['table'],
            'method': label(lang, f"rpt_method_{r['method']}"),
            'amount': _fmt_money(r['amount']),
            'tendered': f"{_fmt_money(r['tendered'])} / {_fmt_money(r['change'])}",
            'cashier': r['cashier'],
            'status': payment_status_obj(lang, r['status']),
        } for r in page.rows]
        kpi = [
            {'label': label(lang, 'rpt_sum_transactions'), 'value': str(page.kpi['transactions'])},
            {'label': label(lang, 'rpt_sum_collected'), 'value': _fmt_money(page.kpi['collected'])},
            {'label': label(lang, 'rpt_sum_average'), 'value': _fmt_money(page.kpi['average'])},
            {'label': label(lang, 'rpt_sum_voided'), 'value': str(page.kpi['voided'])},
        ]
    return {
        'kind': page.kind,
        'scope_own': scoped,
        'total': page.total, 'page': page.page, 'pages': page.pages, 'page_size': page.page_size,
        'kpi': kpi,
        'columns': [
            {'key': key, 'label': label(lang, label_key), 'align': align, 'sortable': sortable}
            for key, label_key, align, sortable in columns
        ],
        'rows': rows,
    }


# --------------------------------------------------------------------------- export (CSV / PDF)

HISTORY_EXPORT_TYPES = {'orders_history': 'orders', 'transactions_history': 'transactions'}


def filters_text(filters: HistoryFilters, lang: str) -> str:
    """Human readable list of the active filters, printed on the PDF so a printout explains itself."""
    from apps.accounts.models import User

    parts = []
    if filters.status:
        prefix = 'rpt_status_' if filters.status in OrderStatus.values else 'rpt_pstatus_'
        parts.append(f"{label(lang, 'rpt_col_status')}: {label(lang, prefix + filters.status)}")
    if filters.source:
        parts.append(f"{label(lang, 'rpt_col_source')}: {label(lang, 'rpt_source_' + filters.source)}")
    if filters.method:
        parts.append(f"{label(lang, 'rpt_col_method')}: {label(lang, 'rpt_method_' + filters.method)}")
    if filters.cashier:
        person = User.objects.filter(pk=filters.cashier).first()
        parts.append(f"{label(lang, 'rpt_col_cashier')}: {person.get_full_name() if person else '-'}")
    if filters.q:
        parts.append(f"{label(lang, 'rpt_pdf_search')}: {filters.q}")
    return '; '.join(parts)


def history_report(kind: str, period: Period, user, filters: HistoryFilters, sort: str = '-time',
                   lang: str = 'tet') -> ReportData:
    """
    ReportData for a history export. `rows` is a generator, so a CSV of any size streams from the database
    one row at a time; the PDF takes only the first rows it can print.
    """
    scoped = user is not None and is_scoped_user(user)
    if kind == 'orders':
        kpi = _orders_kpi(orders_queryset(period, user, filters))
        rows = (
            [r['created_at'], r['code'], r['table'], Label(f"rpt_status_{r['status']}"), r['item_count'],
             r['grand_total'], r['staff']]
            for r in iter_order_rows(period, user, filters, sort)
        )
        return ReportData(
            report_type='orders_history', title_key='rpt_type_orders_history', period=period,
            columns=[
                Column('rpt_col_time', 'datetime'), Column('rpt_col_code'), Column('rpt_col_table'),
                Column('rpt_col_status'), Column('rpt_col_items', 'int', 'end'), Column('rpt_col_total', 'money', 'end'),
                Column('rpt_col_staff'),
            ],
            rows=rows,
            summary=[
                SummaryItem('rpt_sum_orders', kpi['orders'], 'int'),
                SummaryItem('rpt_sum_sold_total', kpi['sold_total'], 'money'),
                SummaryItem('rpt_sum_avg_order', kpi['average'], 'money'),
                SummaryItem('rpt_sum_exceptions', kpi['exceptions'], 'int'),
            ],
            scoped_to_user=scoped, filters_text=filters_text(filters, lang),
        )

    kpi = _transactions_kpi(transactions_queryset(period, user, filters))
    rows = (
        [r['event_at'], r['code'], r['table'], Label(f"rpt_method_{r['method']}"), r['amount'], r['tendered'],
         r['change'], r['cashier'], Label(f"rpt_pstatus_{r['status']}")]
        for r in iter_transaction_rows(period, user, filters, sort)
    )
    return ReportData(
        report_type='transactions_history', title_key='rpt_type_transactions_history', period=period,
        columns=[
            Column('rpt_col_time', 'datetime'), Column('rpt_col_code'), Column('rpt_col_table'),
            Column('rpt_col_method'), Column('rpt_col_amount', 'money', 'end'),
            Column('rpt_col_tendered', 'money', 'end'), Column('rpt_col_change', 'money', 'end'),
            Column('rpt_col_cashier'), Column('rpt_col_status'),
        ],
        rows=rows,
        summary=[
            SummaryItem('rpt_sum_transactions', kpi['transactions'], 'int'),
            SummaryItem('rpt_sum_collected', kpi['collected'], 'money'),
            SummaryItem('rpt_sum_average', kpi['average'], 'money'),
            SummaryItem('rpt_sum_voided', kpi['voided'], 'int'),
        ],
        scoped_to_user=scoped, filters_text=filters_text(filters, lang),
    )
