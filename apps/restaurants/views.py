import secrets

from django.shortcuts import render, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.http import Http404

from apps.accounts.permissions import role_required
from apps.accounts.models import Role
from apps.tables.selectors import get_table_by_qr_token, get_active_session_for_table, list_tables_with_status
from apps.tables.device import (
    DEVICE_COOKIE, extract_device_token, is_device_authorized, claim_session_if_unclaimed,
)
from apps.catalog.selectors import get_categories, get_categories_with_counts, get_all_menu_items
from apps.ordering.models import Order
from apps.reporting.selectors import get_dashboard_metrics
from apps.audit.selectors import get_recent_audit_events

DEVICE_COOKIE_MAX_AGE = 60 * 60 * 24 * 365


def _attach_device_cookie(request, response, device_token):
    """Persist the device id as an httpOnly cookie so page scripts cannot read or change it."""
    response.set_cookie(
        DEVICE_COOKIE,
        device_token,
        max_age=DEVICE_COOKIE_MAX_AGE,
        httponly=True,
        samesite='Lax',
        secure=request.is_secure(),
    )
    return response


def _resolve_device_token(request):
    token = extract_device_token(request)
    if token:
        return token
    return secrets.token_urlsafe(24)


def customer_dine_in_view(request, qr_token=None):
    """
    Customer menu for a table. Only the device that claimed the active session may use it;
    other devices (including devices without an id) see the occupied page.
    """
    if not qr_token:
        return render(request, 'customer/landing.html')

    table = get_table_by_qr_token(qr_token)
    if not table or not table.is_active:
        return render(request, 'customer/invalid_qr.html', status=404)

    device_token = _resolve_device_token(request)
    session = get_active_session_for_table(table)
    is_staff_preview = request.user.is_authenticated

    if session and not is_staff_preview:
        # Staff opening the table link from a portal only preview it: they never claim
        # the device lock, otherwise the real guest would be locked out.
        if not is_device_authorized(session, device_token):
            response = render(request, 'customer/occupied_locked.html', {
                'table': table,
                'session': session,
            }, status=403)
            return _attach_device_cookie(request, response, device_token)
        claim_session_if_unclaimed(session, device_token)

    response = render(request, 'customer/index.html', {
        'table': table,
        'session': session,
        'qr_token': qr_token,
        'categories': get_categories(active_only=True),
    })
    return _attach_device_cookie(request, response, device_token)


def customer_tracker_view(request, qr_token, order_code):
    table = get_table_by_qr_token(qr_token)
    if not table:
        raise Http404("Meza la hetan.")

    order = get_object_or_404(
        Order.objects.select_related('table_session__table'),
        order_code=order_code,
        table_session__table=table,
    )

    device_token = extract_device_token(request)
    if not is_device_authorized(order.table_session, device_token):
        return render(request, 'customer/occupied_locked.html', {
            'table': table,
            'session': order.table_session,
        }, status=403)

    return render(request, 'customer/tracker.html', {
        'table': table,
        'order': order,
        'qr_token': qr_token,
    })


@login_required
@role_required([Role.CASHIER, Role.ADMIN])
def cashier_portal_view(request):
    return render(request, 'cashier/dashboard.html', {
        'tables': list_tables_with_status(),
        'categories': get_categories(active_only=True),
    })


@login_required
@role_required([Role.KITCHEN, Role.ADMIN])
def kitchen_portal_view(request):
    return render(request, 'kitchen/kds.html')


@login_required
@role_required([Role.ADMIN])
def admin_portal_view(request):
    categories = list(get_categories_with_counts(active_only=False))
    menu_items = get_all_menu_items()

    from django.db.models import Count, Exists, OuterRef
    from apps.accounts.models import User
    from apps.tables.models import RestaurantTable, TableSession
    from apps.tables.selectors import ACTIVE_SESSION_STATUSES
    from apps.payments.models import Payment

    admin_tables = RestaurantTable.objects.annotate(
        has_active_session=Exists(
            TableSession.objects.filter(table=OuterRef('pk'), status__in=ACTIVE_SESSION_STATUSES)
        ),
        sessions_count=Count('sessions', distinct=True),
    ).order_by('-is_active', 'sort_order', 'table_code')

    return render(request, 'admin_custom/dashboard.html', {
        'admin_tables': admin_tables,
        'metrics': get_dashboard_metrics(),
        'menu_items': menu_items,
        'categories': categories,
        'active_category_count': sum(1 for c in categories if c.is_active),
        'tables': list_tables_with_status(),
        'audit_events': get_recent_audit_events(limit=50),
        'staff_users': User.objects.annotate(
            hist_sessions=Exists(TableSession.objects.filter(opened_by=OuterRef('pk'))),
            hist_payments=Exists(Payment.objects.filter(received_by=OuterRef('pk'))),
        ).order_by('-is_active', 'role', 'username'),
    })


@login_required
@role_required([Role.ADMIN])
def qr_print_sheet_view(request):
    return render(request, 'admin_custom/qr_sheet.html', {
        'tables': list_tables_with_status(),
        'host': request.get_host(),
        'scheme': request.scheme,
    })
