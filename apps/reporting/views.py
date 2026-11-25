import logging

from django.http import HttpResponse, JsonResponse, StreamingHttpResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_GET

from apps.accounts.models import Role
from apps.accounts.permissions import role_required

from .exporters import build_display, export_filename, format_period, iter_csv_chunks, to_csv_bytes, to_pdf_bytes
from .history import (
    HISTORY_EXPORT_TYPES, HistoryNotFound, history_report, order_detail, orders_history, page_display, parse_filters, parse_paging, parse_sort,
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


@require_GET
@role_required(REPORT_ROLES)
def report_export_view(request):
    export_format = request.GET.get('format', 'csv')
    if export_format not in ('csv', 'pdf'):
        return _error_response(request, ReportError('invalid_report'))

    requested = request.GET.get('type', REPORT_TYPES[0])
    if requested in HISTORY_EXPORT_TYPES:
        return _export_history(request, requested, export_format)

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
