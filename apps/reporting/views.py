import logging

from django.http import HttpResponse, JsonResponse, StreamingHttpResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_GET

from apps.accounts.models import Role
from apps.accounts.permissions import role_required

from apps.audit.selectors import AuditNotFound, audit_event_detail, audit_events_page, parse_audit_sort

from .exporters import build_display, export_filename, format_period, iter_csv_chunks, to_csv_bytes, to_pdf_bytes
from .history import (
    HISTORY_EXPORT_TYPES, HistoryNotFound, _fmt_dt, history_report, order_detail, orders_history, page_display, parse_filters, parse_paging, parse_sort,
    transaction_detail, transactions_history,
)
from .labels import label, normalize_lang
from .reports import REPORT_TYPES, MAX_RANGE_DAYS, ReportError, build_report, is_scoped_user, parse_period

logger = logging.getLogger(__name__)

REPORT_ROLES = [Role.CASHIER, Role.ADMIN]


def _resolve(request):
    """Validate the query string. Returns (report_type, period, lang) or raises ReportError."""
    lang = normalize_lang(request.GET.get('lang'))
    report_type = request.GET.get('type', REPORT_TYPES[0])
    if report_type not in REPORT_TYPES:
        raise ReportError('invalid_report')
    period = parse_period(request.GET.get('preset'), request.GET.get('start'), request.GET.get('end'))
    return report_type, period, lang


def _error_response(request, exc: ReportError, status=400):
    lang = normalize_lang(request.GET.get('lang'))
    return JsonResponse({'error': {'code': exc.code.upper(), 'message': label(lang, f'rpt_err_{exc.code}')}}, status=status)


@role_required(REPORT_ROLES)
def reports_page_view(request):
    return render(request, 'reports/index.html', {
        'reports_page': True,
        'report_types': REPORT_TYPES,
        'max_range_days': MAX_RANGE_DAYS,
        'today': timezone.localdate().isoformat(),
        'is_admin_view': request.user.is_admin,
        'cashier_choices': _history_cashier_choices(request),
    })


def _history_cashier_choices(request):
    """Admins can filter by cashier; cashiers only ever see themselves, so the filter is hidden."""
    if not request.user.is_admin:
        return []
    from apps.accounts.models import User
    users = User.objects.filter(role__in=[Role.CASHIER, Role.ADMIN]).order_by('full_name', 'username')
    return [{'id': str(u.id), 'name': u.get_full_name()} for u in users]


def _history_params(request, kind):
    """Validated (period, filters, sort, page, page_size, lang). Raises ReportError."""
    lang = normalize_lang(request.GET.get('lang'))
    period = parse_period(request.GET.get('preset'), request.GET.get('start'), request.GET.get('end'))
    filters = parse_filters(kind, request.GET)
    sort = parse_sort(kind, request.GET.get('sort'))
    page, page_size = parse_paging(request.GET.get('page'), request.GET.get('page_size'))
    return period, filters, sort, page, page_size, lang


def _history_list(request, kind):
    try:
        period, filters, sort, page, page_size, lang = _history_params(request, kind)
    except ReportError as exc:
        return _error_response(request, exc)

    build = orders_history if kind == 'orders' else transactions_history
    result = build(period, request.user, filters, sort, page, page_size)
    body = page_display(result, lang, scoped=is_scoped_user(request.user))
    body['period_label'] = format_period(period)
    body['sort'] = sort
    body['cashiers'] = _history_cashier_choices(request)
    response = JsonResponse(body)
    response['Cache-Control'] = 'no-store'
    return response


@require_GET
@role_required(REPORT_ROLES)
def history_orders_view(request):
    return _history_list(request, 'orders')


@require_GET
@role_required(REPORT_ROLES)
def history_transactions_view(request):
    return _history_list(request, 'transactions')


def _audit_actor(row, lang):
    return row['actor'] or row['actor_role'] or label(lang, 'rpt_audit_system')


@require_GET
@role_required([Role.ADMIN])
def audit_list_view(request):
    """Audit events (admin only): filtered by period, free text and entity type; server-side pagination."""
    lang = normalize_lang(request.GET.get('lang'))
    try:
        period = parse_period(request.GET.get('preset'), request.GET.get('start'), request.GET.get('end'))
    except ReportError as exc:
        return _error_response(request, exc)

    sort = parse_audit_sort(request.GET.get('sort'))
    page, page_size = parse_paging(request.GET.get('page'), request.GET.get('page_size'))
    result = audit_events_page(
        period, q=request.GET.get('q', ''), entity=(request.GET.get('entity') or '').strip()[:80],
        sort=sort, page=page, page_size=page_size,
    )
    kpi = result['kpi']
    body = {
        'kind': 'audit',
        'total': result['total'], 'page': result['page'], 'pages': result['pages'], 'page_size': result['page_size'],
        'period_label': format_period(period),
        'sort': sort,
        'entity_types': result['entity_types'],
        'kpi': [
            {'label': label(lang, 'rpt_sum_events'), 'value': str(kpi['events'])},
            {'label': label(lang, 'rpt_sum_actors'), 'value': str(kpi['actors'])},
            {'label': label(lang, 'rpt_sum_entities'), 'value': str(kpi['entities'])},
        ],
        'columns': [
            {'key': 'time', 'label': label(lang, 'rpt_col_time'), 'align': 'start', 'sortable': True},
            {'key': 'action', 'label': label(lang, 'rpt_col_action'), 'align': 'start', 'sortable': True},
            {'key': 'actor', 'label': label(lang, 'rpt_col_actor'), 'align': 'start', 'sortable': True},
            {'key': 'entity', 'label': label(lang, 'rpt_col_entity'), 'align': 'start', 'sortable': True},
        ],
        'rows': [{
            'id': r['id'],
            'time': _fmt_dt(r['created_at']),
            'action': r['action'],
            'actor': _audit_actor(r, lang),
            'entity': f"{r['entity_type']} \u00b7 {r['entity_id'][:12]}",
        } for r in result['rows']],
    }
    response = JsonResponse(body)
    response['Cache-Control'] = 'no-store'
    return response


@require_GET
@role_required([Role.ADMIN])
def audit_detail_view(request, event_id):
    lang = normalize_lang(request.GET.get('lang'))
    try:
        data = audit_event_detail(event_id)
    except AuditNotFound:
        return JsonResponse({'error': {'code': 'NOT_FOUND', 'message': label(lang, 'rpt_err_not_found')}}, status=404)
    data['created_at'] = _fmt_dt(data['created_at'])
    data['actor'] = _audit_actor(data, lang)
    response = JsonResponse(data)
    response['Cache-Control'] = 'no-store'
    return response


def _history_detail(request, loader, record_id):
    lang = normalize_lang(request.GET.get('lang'))
    try:
        data = loader(record_id, request.user, lang)
    except HistoryNotFound:
        return JsonResponse({'error': {'code': 'NOT_FOUND', 'message': label(lang, 'rpt_err_not_found')}}, status=404)
    response = JsonResponse(data)
    response['Cache-Control'] = 'no-store'
    return response


@require_GET
@role_required(REPORT_ROLES)
def history_order_detail_view(request, order_id):
    return _history_detail(request, order_detail, order_id)


@require_GET
@role_required(REPORT_ROLES)
def history_transaction_detail_view(request, payment_id):
    return _history_detail(request, transaction_detail, payment_id)


@require_GET
@role_required(REPORT_ROLES)
def report_data_view(request):
    try:
        report_type, period, lang = _resolve(request)
    except ReportError as exc:
        return _error_response(request, exc)

    report = build_report(report_type, period, request.user)
    response = JsonResponse(build_display(report, lang))
    response['Cache-Control'] = 'no-store'
    return response


def _download_headers(response, report, extension):
    response['Content-Disposition'] = f'attachment; filename="{export_filename(report, extension)}"'
    response['Cache-Control'] = 'no-store'
    return response


def _export_history(request, report_type, export_format):
    """CSV streams row by row from the database; the PDF prints at most PDF_MAX_ROWS rows."""
    kind = HISTORY_EXPORT_TYPES[report_type]
    try:
        period, filters, sort, _page, _size, lang = _history_params(request, kind)
    except ReportError as exc:
        return _error_response(request, exc)

    report = history_report(kind, period, request.user, filters, sort, lang)
    if export_format == 'csv':
        response = StreamingHttpResponse(iter_csv_chunks(report, lang), content_type='text/csv; charset=utf-8')
        return _download_headers(response, report, 'csv')

    try:
        body = to_pdf_bytes(report, lang, _restaurant_name(), request.user.get_full_name())
    except Exception:
        logger.exception('History PDF generation failed')
        return JsonResponse({'error': {'code': 'PDF_FAILED', 'message': 'PDF generation failed.'}}, status=500)
    return _download_headers(HttpResponse(body, content_type='application/pdf'), report, 'pdf')


AUDIT_EXPORT_MAX_ROWS = 20000


def _audit_report(request, lang):
    """The Audit tab as a downloadable report (same period, search and entity filters as the list)."""
    from apps.audit.selectors import AUDIT_SORTS, audit_queryset
    from .reports import Column, ReportData, SummaryItem

    period = parse_period(request.GET.get('preset'), request.GET.get('start'), request.GET.get('end'))
    q = request.GET.get('q', '')
    entity = (request.GET.get('entity') or '').strip()[:80]
    sort = parse_audit_sort(request.GET.get('sort'))
    desc = sort.startswith('-')
    qs = audit_queryset(period, q, entity).select_related('actor_user').order_by(
        ('-' if desc else '') + AUDIT_SORTS[sort.lstrip('-')], '-pk' if desc else 'pk')
    total = qs.count()

    def rows():
        for e in qs[:AUDIT_EXPORT_MAX_ROWS].iterator(chunk_size=500):
            actor = ((e.actor_user.full_name or e.actor_user.username) if e.actor_user else '') or e.actor_role or label(lang, 'rpt_audit_system')
            yield [e.created_at, e.action, actor, e.actor_role or '', e.entity_type, e.entity_id]

    parts = []
    if entity:
        parts.append(f"{label(lang, 'rpt_col_entity')}: {entity}")
    if q.strip():
        parts.append(f"{label(lang, 'rpt_pdf_search')}: {q.strip()[:80]}")
    return ReportData(
        report_type='audit',
        title_key='rpt_type_audit_log',
        period=period,
        columns=[
            Column('rpt_col_time', 'datetime'), Column('rpt_col_action'), Column('rpt_col_actor'),
            Column('rpt_col_role'), Column('rpt_col_entity'), Column('rpt_col_entity_id'),
        ],
        rows=rows(),
        summary=[SummaryItem('rpt_sum_events', total, 'int')],
        filters_text=' \u00b7 '.join(parts),
    )


def _export_audit(request, export_format):
    if not request.user.is_admin:
        return JsonResponse({'error': {'code': 'FORBIDDEN', 'message': 'Forbidden'}}, status=403)
    lang = normalize_lang(request.GET.get('lang'))
    try:
        report = _audit_report(request, lang)
    except ReportError as exc:
        return _error_response(request, exc)
    if export_format == 'csv':
        response = StreamingHttpResponse(iter_csv_chunks(report, lang), content_type='text/csv; charset=utf-8')
        return _download_headers(response, report, 'csv')
    try:
        body = to_pdf_bytes(report, lang, _restaurant_name(), request.user.get_full_name())
    except Exception:
        logger.exception('Audit PDF generation failed')
        return JsonResponse({'error': {'code': 'PDF_FAILED', 'message': 'PDF generation failed.'}}, status=500)
    return _download_headers(HttpResponse(body, content_type='application/pdf'), report, 'pdf')


@require_GET
@role_required(REPORT_ROLES)
def report_export_view(request):
    export_format = request.GET.get('format', 'csv')
    if export_format not in ('csv', 'pdf'):
        return _error_response(request, ReportError('invalid_report'))

    requested = request.GET.get('type', REPORT_TYPES[0])
    if requested in HISTORY_EXPORT_TYPES:
        return _export_history(request, requested, export_format)
    if requested == 'audit':
        return _export_audit(request, export_format)

    try:
        report_type, period, lang = _resolve(request)
    except ReportError as exc:
        return _error_response(request, exc)

    report = build_report(report_type, period, request.user)

    if export_format == 'csv':
        body, content_type = to_csv_bytes(report, lang), 'text/csv; charset=utf-8'
    else:
        try:
            body = to_pdf_bytes(report, lang, _restaurant_name(), request.user.get_full_name())
        except Exception:
            logger.exception('Report PDF generation failed')
            return JsonResponse({'error': {'code': 'PDF_FAILED', 'message': 'PDF generation failed.'}}, status=500)
        content_type = 'application/pdf'

    response = HttpResponse(body, content_type=content_type)
    response['Content-Disposition'] = f'attachment; filename="{export_filename(report, export_format)}"'
    response['Cache-Control'] = 'no-store'
    return response


def _restaurant_name() -> str:
    from apps.restaurants.models import Restaurant
    restaurant = Restaurant.objects.filter(is_active=True).first()
    return restaurant.name if restaurant else 'Restaurante Sabor Dili'
