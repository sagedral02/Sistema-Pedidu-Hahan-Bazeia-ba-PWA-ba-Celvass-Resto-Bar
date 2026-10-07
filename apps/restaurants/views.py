from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.conf import settings
from apps.accounts.permissions import role_required
from apps.accounts.models import Role
from apps.tables.models import RestaurantTable, TableSession, SessionStatus
from apps.tables.selectors import get_table_by_qr_token, get_active_session_for_table, list_tables_with_status
from apps.catalog.selectors import get_categories, get_categories_with_counts, get_all_menu_items
from apps.ordering.models import Order
from apps.reporting.selectors import get_dashboard_metrics
from apps.audit.selectors import get_recent_audit_events


def customer_dine_in_view(request, qr_token=None):
    """
    Public customer dine-in view — requires a valid QR token to access a table.
    Enforces strict device-locking: only the device that opened/joined the active session
    can order or view the table session. Foreign devices are blocked with occupied_locked.html.
    """
    # Root URL without a QR token → show neutral landing page only
    if not qr_token:
        return render(request, 'customer/landing.html')

    table = get_table_by_qr_token(qr_token)
    if not table or not table.is_active:
        return render(request, 'customer/invalid_qr.html', status=404)

    session = get_active_session_for_table(table)
    categories = get_categories(active_only=True)

    # Extract device token from cookie, header, or query
    device_token = (
        request.COOKIES.get('celvass_device_id') or
        request.headers.get('X-Device-Token') or
        request.GET.get('device_id')
    )

    # If session is active, verify device lock
    if session:
        if session.primary_device_token:
            # Table is locked to a device
            if device_token and device_token != session.primary_device_token:
                # Different device trying to access active table
                return render(request, 'customer/occupied_locked.html', {
                    'table': table,
                    'session': session,
                }, status=403)
        elif device_token:
            # First device to access active session claims it
            session.primary_device_token = device_token
            session.authorized_device_tokens = [device_token]
            session.save(update_fields=['primary_device_token', 'authorized_device_tokens'])

    return render(request, 'customer/index.html', {
        'table': table,
        'session': session,
        'qr_token': qr_token,
        'categories': categories,
    })


def customer_tracker_view(request, qr_token, order_code):
    table = get_table_by_qr_token(qr_token)
    order = get_object_or_404(Order, order_code=order_code)

    return render(request, 'customer/tracker.html', {
        'table': table,
        'order': order,
        'qr_token': qr_token,
    })


@login_required
@role_required([Role.CASHIER, Role.ADMIN])
def cashier_portal_view(request):
    tables = list_tables_with_status()
    categories = get_categories(active_only=True)
    return render(request, 'cashier/dashboard.html', {
        'tables': tables,
        'categories': categories,
    })


@login_required
@role_required([Role.KITCHEN, Role.ADMIN])
def kitchen_portal_view(request):
    return render(request, 'kitchen/kds.html')


@login_required
@role_required([Role.ADMIN])
def admin_portal_view(request):
    metrics = get_dashboard_metrics()
    menu_items = get_all_menu_items()
    categories = get_categories_with_counts(active_only=False)
    tables = list_tables_with_status()
    audit_events = get_recent_audit_events(limit=50)

    return render(request, 'admin_custom/dashboard.html', {
        'metrics': metrics,
        'menu_items': menu_items,
        'categories': categories,
        'tables': tables,
        'audit_events': audit_events,
    })


@login_required
@role_required([Role.ADMIN])
def qr_print_sheet_view(request):
    tables = list_tables_with_status()
    return render(request, 'admin_custom/qr_sheet.html', {
        'tables': tables,
        'host': request.get_host(),
        'scheme': request.scheme,
    })

