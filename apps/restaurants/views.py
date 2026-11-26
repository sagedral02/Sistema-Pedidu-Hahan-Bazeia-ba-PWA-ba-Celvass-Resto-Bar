import secrets

from django.shortcuts import render, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.utils import timezone

from apps.accounts.permissions import role_required
from apps.accounts.models import Role
from apps.tables.selectors import get_table_by_qr_token, get_active_session_for_table, list_tables_with_status
from apps.tables.device import (
    DEVICE_COOKIE, extract_device_token, is_device_authorized, claim_session_if_unclaimed,
)
from apps.catalog.selectors import get_categories, get_categories_with_counts, get_all_menu_items
from apps.ordering.models import Order
from apps.reporting.selectors import get_dashboard_metrics

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
        return _landing(request)

    table = get_table_by_qr_token(qr_token)
    if not table or not table.is_active:
        return render(request, 'customer/invalid_qr.html', status=404)

    device_token = _resolve_device_token(request)
    session = get_active_session_for_table(table)
    is_staff_preview = request.user.is_authenticated

    if session:
        # A table that another phone already uses shows the "occupied" page to everyone else, staff included:
        # rendering the menu here would make every API call fail with DEVICE_LOCKED (the old reload loop).
        if not is_device_authorized(session, device_token):
            response = render(request, 'customer/occupied_locked.html', {
                'table': table,
                'session': session,
                'is_staff_preview': is_staff_preview,
                'staff_role': getattr(request.user, 'role', '') if is_staff_preview else '',
            }, status=403)
            return _attach_device_cookie(request, response, device_token)
        # Staff opening the table link from a portal only preview it: they never claim
        # the device lock, otherwise the real guest would be locked out.
        if not is_staff_preview:
            claim_session_if_unclaimed(session, device_token)

    from apps.catalog.ratings import latest_finished_session_for_device
    from apps.restaurants.services import get_restaurant, opening_status
    from apps.tables.device import guest_limit
    from apps.tables.selectors import get_pending_activation_request_for_table

    recent = None if session else latest_finished_session_for_device(table, device_token)
    pending = None if session else get_pending_activation_request_for_table(table)
    response = render(request, 'customer/index.html', {
        'table': table,
        'session': session,
        'qr_token': qr_token,
        'guest_limit': guest_limit(table),
        'opening': opening_status(get_restaurant()),
        'recent_session': recent,
        'pending_mine': bool(pending and pending.device_token and pending.device_token == device_token),
        **_customer_menu_context(),
    })
    return _attach_device_cookie(request, response, device_token)


def _landing(request):
    """Home page: restaurant information, today's opening state, the read-only menu and the QR scanner.
    Ordering is only possible after scanning the QR code on a table."""
    from apps.restaurants.services import get_restaurant, hours_list, opening_status
    restaurant = get_restaurant()
    hours = hours_list(restaurant)
    today = timezone.localtime().weekday()
    context = {
        **_customer_menu_context(),
        'opening': opening_status(restaurant),
        'hours': hours,
        'hours_configured': any(h['configured'] for h in hours),
        'today_weekday': today,
        'today_hours': next((h for h in hours if h['weekday'] == today), None),
    }
    return render(request, 'customer/landing.html', context)


def _customer_menu_context():
    """Categories plus, per category, the visible dishes with today's availability (`item.eff`),
    and the highlight rows (best rated, most ordered, house picks) for "all" and for every category."""
    from apps.catalog.availability import availability_map
    from apps.catalog.highlights import highlight_sections
    categories = list(get_categories(active_only=True))
    sections, flat = [], []
    for cat in categories:
        items = [i for i in cat.items.order_by('sort_order', 'name') if i.availability != 'INACTIVE']
        sections.append((cat, items))
        flat.extend(items)
    highlights = highlight_sections(categories)
    highlighted = [i for h in highlights for key in ('top_rated', 'most_ordered', 'featured') for i in h[key]]
    states = availability_map(flat + highlighted)
    for item in flat + highlighted:
        item.eff = states[str(item.id)]
    return {
        'categories': categories,
        'menu_sections': [{'category': c, 'items': items} for c, items in sections],
        'highlights': highlights,
    }


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
    menu_items = list(get_all_menu_items())
    from apps.catalog.availability import availability_map
    states = availability_map(menu_items)
    for entry in menu_items:
        entry.eff = states[str(entry.id)]

    from django.db.models import Count, Exists, OuterRef
    from apps.accounts.models import User
    from apps.tables.models import RestaurantTable, TableSession
    from apps.tables.selectors import ACTIVE_SESSION_STATUSES
    from apps.payments.models import Payment

    admin_tables = RestaurantTable.objects.filter(is_takeaway=False).annotate(
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


def customer_thanks_view(request, qr_token, session_token):
    """Thank-you page after the table paid: summary, ratings, restaurant contact, then back to the home page.

    Only for the device that owned the session and only for RATING_WINDOW after it was paid or closed;
    anything else goes to the home page, so a closed table never asks to be opened again by itself."""
    from django.conf import settings
    from django.shortcuts import redirect
    from apps.catalog.ratings import device_owns, finished_session, ratings_payload
    from apps.payments.models import Payment, PaymentStatus
    from apps.payments.services import calculate_session_bill

    home = getattr(settings, 'CUSTOMER_HOME_URL', '/') or '/'
    table = get_table_by_qr_token(qr_token)
    session = finished_session(session_token)
    if not table or session is None or session.table_id != table.id or not device_owns(session, extract_device_token(request)):
        return redirect(home)
    payment = Payment.objects.filter(table_session=session, status=PaymentStatus.COMPLETED).order_by('-paid_at').first()
    response = render(request, 'customer/thanks.html', {
        'table': table,
        'session': session,
        'qr_token': qr_token,
        'payment': payment,
        'bill': calculate_session_bill(session),
        'ratings': ratings_payload(session),
        'home_url': home,
        'countdown': 25,
    })
    response['Cache-Control'] = 'no-store'
    return response
