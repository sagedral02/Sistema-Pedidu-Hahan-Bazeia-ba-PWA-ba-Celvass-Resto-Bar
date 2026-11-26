"""Overview dashboard data: periods, KPI deltas, day/hour bucketing in Dili time, bounds and permissions."""
from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.test import Client
from django.utils import timezone
from rest_framework.test import APIClient

from apps.catalog.models import Category, MenuItem
from apps.ordering.models import OrderStatus
from apps.payments.models import PaymentMethod, PaymentStatus
from apps.reporting.overview import (
    RECENT_ROWS, TOP_CASHIERS, TOP_ITEMS, build_overview, parse_range,
)
from apps.reporting.reports import Period, ReportError, daily_sales_report, menu_sales_report, order_types_report
from reporting_helpers import add_item, make_order, make_payment

URL = '/api/v1/admin/overview/'


@pytest.fixture
def today():
    return timezone.localdate()


# ----------------------------------------------------------------------------- ranges

def test_parse_range_builds_equal_length_previous_periods():
    today = date(2026, 11, 25)
    cur, prev, key = parse_range('7d', today)
    assert (cur.start, cur.end, key) == (date(2026, 11, 19), today, '7d')
    assert (prev.start, prev.end) == (date(2026, 11, 12), date(2026, 11, 18))

    cur, prev, _ = parse_range('today', today)
    assert (cur.start, cur.end, prev.start, prev.end) == (today, today, date(2026, 11, 24), date(2026, 11, 24))

    cur, prev, _ = parse_range(None, today)
    assert cur.start == today

    cur, prev, _ = parse_range('30d', today)
    assert (cur.end - cur.start).days == 29 and (prev.end - prev.start).days == 29
    assert prev.end == cur.start - timedelta(days=1)


def test_parse_range_rejects_unknown_values():
    for bad in ('90d', 'week', "7d; DROP"):
        with pytest.raises(ReportError) as exc:
            parse_range(bad)
        assert exc.value.code == 'invalid_range'


# ----------------------------------------------------------------------------- KPI

@pytest.mark.django_db
def test_kpi_compares_with_the_previous_period(restaurant, active_session, cashier_user, today):
    make_payment(restaurant, active_session, cashier_user, '30.00', paid_day=today)
    make_payment(restaurant, active_session, cashier_user, '10.00', paid_day=today - timedelta(days=1))
    make_payment(restaurant, active_session, cashier_user, '20.00', paid_day=today - timedelta(days=9))
    make_payment(restaurant, active_session, cashier_user, '99.00', paid_day=today, status=PaymentStatus.VOIDED)

    data = build_overview('7d')
    kpi = data['kpi']
    assert kpi['sales'] == {'value': 40.0, 'previous': 20.0, 'delta_pct': 100.0}
    assert kpi['transactions'] == {'value': 2, 'previous': 1, 'delta_pct': 100.0}
    assert kpi['average'] == {'value': 20.0, 'previous': 20.0, 'delta_pct': 0.0}


@pytest.mark.django_db
def test_delta_is_null_when_there_is_nothing_to_compare_with(restaurant, active_session, cashier_user, today):
    make_payment(restaurant, active_session, cashier_user, '12.00', paid_day=today)
    kpi = build_overview('today')['kpi']
    assert kpi['sales']['previous'] == 0.0 and kpi['sales']['delta_pct'] is None
    assert kpi['transactions']['delta_pct'] is None


@pytest.mark.django_db
def test_rejection_rate_and_points_delta(restaurant, active_session, today):
    for status in (OrderStatus.COMPLETED, OrderStatus.COMPLETED, OrderStatus.COMPLETED, OrderStatus.REJECTED):
        make_order(restaurant, active_session, status, '5.00', day=today)
    make_order(restaurant, active_session, OrderStatus.REJECTED, '5.00', day=today - timedelta(days=1))
    make_order(restaurant, active_session, OrderStatus.COMPLETED, '5.00', day=today - timedelta(days=1))

    rate = build_overview('today')['kpi']['rejection_rate']
    assert rate == {'value': 25.0, 'previous': 50.0, 'delta_points': -25.0}
    assert build_overview('today')['kpi']['orders'] == {'value': 4, 'previous': 2, 'delta_pct': 100.0}


@pytest.mark.django_db
def test_empty_database_gives_zeros_and_nulls(restaurant):
    data = build_overview('30d')
    assert data['kpi']['sales'] == {'value': 0.0, 'previous': 0.0, 'delta_pct': None}
    assert data['kpi']['rejection_rate'] == {'value': None, 'previous': None, 'delta_points': None}
    assert data['trend']['current'] == [0.0] * 30
    assert data['busy_hours'] == [0] * 24
    assert data['top_items'] == {'by_qty': [], 'by_revenue': []}
    assert data['categories'] == [] and data['cashiers'] == []
    assert data['recent_payments'] == [] and data['recent_rejections'] == []
    assert data['statuses'] == {'waiting': 0, 'active': 0, 'completed': 0, 'rejected': 0, 'cancelled': 0}
    assert [m['total'] for m in data['order_types']] == [0.0, 0.0]


@pytest.mark.django_db
def test_tables_card_counts_active_tables(restaurant, table_one, table_two):
    from apps.tables.models import TableStatus
    table_two.status = TableStatus.OCCUPIED
    table_two.save()
    assert build_overview('today')['tables'] == {'occupied': 1, 'total': 2}


# ----------------------------------------------------------------------------- trend (day and hour buckets)

@pytest.mark.django_db
def test_daily_trend_fills_empty_days_and_aligns_the_previous_period(restaurant, active_session, cashier_user, today):
    make_payment(restaurant, active_session, cashier_user, '10.00', paid_day=today - timedelta(days=6))
    make_payment(restaurant, active_session, cashier_user, '5.00', paid_day=today)
    make_payment(restaurant, active_session, cashier_user, '7.00', paid_day=today - timedelta(days=7))

    trend = build_overview('7d')['trend']
    assert trend['granularity'] == 'day'
    assert trend['labels'] == [(today - timedelta(days=6 - i)).isoformat() for i in range(7)]
    assert trend['current'] == [10.0, 0.0, 0.0, 0.0, 0.0, 0.0, 5.0]
    assert trend['previous'][-1] == 7.0 and sum(trend['previous']) == 7.0
    assert len(trend['previous_labels']) == 7


@pytest.mark.django_db
def test_daily_trend_puts_late_evening_and_after_midnight_payments_on_the_right_day(
        restaurant, active_session, cashier_user, today):
    make_payment(restaurant, active_session, cashier_user, '4.00', paid_day=today - timedelta(days=1), hour=23, minute=30)
    make_payment(restaurant, active_session, cashier_user, '6.00', paid_day=today, hour=0, minute=10)

    trend = build_overview('7d')['trend']
    assert trend['current'][-2:] == [4.0, 6.0]


@pytest.mark.django_db
def test_hourly_trend_for_today_uses_dili_hours(restaurant, active_session, cashier_user, today):
    make_payment(restaurant, active_session, cashier_user, '6.00', paid_day=today, hour=0, minute=10)
    make_payment(restaurant, active_session, cashier_user, '2.00', paid_day=today, hour=23, minute=30)
    make_payment(restaurant, active_session, cashier_user, '4.00', paid_day=today - timedelta(days=1), hour=23, minute=30)

    trend = build_overview('today')['trend']
    assert trend['granularity'] == 'hour' and trend['labels'] == list(range(24))
    assert trend['current'][0] == 6.0 and trend['current'][23] == 2.0 and sum(trend['current']) == 8.0
    assert trend['previous'][23] == 4.0 and sum(trend['previous']) == 4.0


@pytest.mark.django_db
def test_busy_hours_bucket_orders_by_local_hour(restaurant, active_session, today):
    make_order(restaurant, active_session, OrderStatus.COMPLETED, '1.00', day=today, hour=0, minute=10)
    make_order(restaurant, active_session, OrderStatus.COMPLETED, '1.00', day=today, hour=23, minute=30)
    make_order(restaurant, active_session, OrderStatus.REJECTED, '1.00', day=today, hour=12)
    make_order(restaurant, active_session, OrderStatus.COMPLETED, '1.00', day=today - timedelta(days=3), hour=12)

    today_hours = build_overview('today')['busy_hours']
    assert today_hours[0] == 1 and today_hours[12] == 1 and today_hours[23] == 1 and sum(today_hours) == 3
    assert build_overview('7d')['busy_hours'][12] == 2


# ----------------------------------------------------------------------------- breakdowns agree with the reports

@pytest.mark.django_db
def test_breakdowns_match_the_summary_reports(restaurant, active_session, session_two, cashier_user, cashier_two,
                                              category_main, today):
    drinks = Category.objects.create(restaurant=restaurant, name='Minuman', slug='drinks', is_active=True)
    tea = MenuItem.objects.create(restaurant=restaurant, category=drinks, name='Teh', slug='teh', price=Decimal('1.00'))
    fish = MenuItem.objects.create(restaurant=restaurant, category=category_main, name='Ikan', slug='ikan',
                                   price=Decimal('6.50'))
    order = make_order(restaurant, active_session, OrderStatus.COMPLETED, '25.00', day=today)
    add_item(order, 'Ikan', 3, '6.50', fish)
    add_item(order, 'Teh', 5, '1.00', tea)
    make_payment(restaurant, active_session, cashier_user, '20.00', paid_day=today)
    session_two.table.is_takeaway = True
    session_two.table.save()
    make_payment(restaurant, session_two, cashier_two, '30.00', paid_day=today)

    data = build_overview('7d')
    period = Period(today - timedelta(days=6), today)

    sales = {s.label_key: s.value for s in daily_sales_report(period).summary}
    assert data['kpi']['sales']['value'] == float(sales['rpt_sum_total_sales']) == 50.0
    assert data['kpi']['transactions']['value'] == sales['rpt_sum_transactions'] == 2

    types = {m['order_type']: m for m in data['order_types']}
    report = {r[0].key: r for r in order_types_report(period).rows}
    assert types['DINE_IN']['total'] == float(report['rpt_ot_DINE_IN'][2]) == 20.0
    assert types['TAKEAWAY']['count'] == report['rpt_ot_TAKEAWAY'][1] == 1

    menu = {r[1]: (r[2], float(r[3])) for r in menu_sales_report(period).rows}
    by_qty = {i['name']: (i['qty'], i['revenue']) for i in data['top_items']['by_qty']}
    assert by_qty == menu == {'Ikan': (3, 19.5), 'Teh': (5, 5.0)}
    assert [i['name'] for i in data['top_items']['by_qty']] == ['Teh', 'Ikan']
    assert [i['name'] for i in data['top_items']['by_revenue']] == ['Ikan', 'Teh']

    assert data['categories'] == [
        {'name': 'Makanan Utama', 'revenue': 19.5, 'qty': 3}, {'name': 'Minuman', 'revenue': 5.0, 'qty': 5}]
    assert [(c['name'], c['total'], c['count']) for c in data['cashiers']] == [
        ('Maria Cashier', 30.0, 1), ('cashier_test', 20.0, 1)]


@pytest.mark.django_db
def test_items_without_a_menu_link_have_an_empty_category(restaurant, active_session, today):
    order = make_order(restaurant, active_session, OrderStatus.COMPLETED, '2.00', day=today)
    add_item(order, 'Menu Lama', 2, '1.00', menu_item=None)
    assert build_overview('today')['categories'] == [{'name': '', 'revenue': 2.0, 'qty': 2}]


@pytest.mark.django_db
def test_order_statuses_are_grouped(restaurant, active_session, today):
    for status in (OrderStatus.WAITING_CASHIER_CONFIRMATION, OrderStatus.CONFIRMED, OrderStatus.PREPARING,
                   OrderStatus.READY, OrderStatus.SERVED, OrderStatus.COMPLETED, OrderStatus.COMPLETED,
                   OrderStatus.REJECTED, OrderStatus.CANCELLED):
        make_order(restaurant, active_session, status, '1.00', day=today)
    assert build_overview('today')['statuses'] == {
        'waiting': 1, 'active': 4, 'completed': 2, 'rejected': 1, 'cancelled': 1}


# ----------------------------------------------------------------------------- bounds

@pytest.mark.django_db
def test_lists_are_bounded(restaurant, active_session, cashier_user, today):
    from apps.accounts.models import Role, User
    order = make_order(restaurant, active_session, OrderStatus.COMPLETED, '100.00', day=today)
    for i in range(TOP_ITEMS + 5):
        add_item(order, f'Menu {i:02d}', i + 1, '1.00')
    for i in range(TOP_CASHIERS + 4):
        staff = User.objects.create_user(username=f'staff{i:02d}', password='x', role=Role.CASHIER, is_staff=True)
        make_payment(restaurant, active_session, staff, '1.00', paid_day=today)
    for i in range(RECENT_ROWS + 4):
        make_order(restaurant, active_session, OrderStatus.REJECTED, '1.00', day=today, minute=i)

    data = build_overview('today')
    assert len(data['top_items']['by_qty']) == TOP_ITEMS
    assert data['top_items']['by_qty'][0]['name'] == f'Menu {TOP_ITEMS + 4:02d}'
    assert len(data['cashiers']) == TOP_CASHIERS
    assert len(data['recent_payments']) == RECENT_ROWS
    assert len(data['recent_rejections']) == RECENT_ROWS


@pytest.mark.django_db
def test_recent_lists_carry_ids_for_deep_links(restaurant, active_session, cashier_user, today):
    pay = make_payment(restaurant, active_session, cashier_user, '9.00', paid_day=today, hour=9)
    order = make_order(restaurant, active_session, OrderStatus.REJECTED, '4.00', day=today)
    order.rejection_reason = 'Stok habis'
    order.save()
    data = build_overview('today')
    assert data['recent_payments'][0]['id'] == str(pay.id)
    assert data['recent_payments'][0]['method'] == 'CASH'
    assert data['recent_rejections'][0] == {
        'id': str(order.id), 'code': order.order_code, 'table': 'Meja 01', 'reason': 'Stok habis',
        'at': data['recent_rejections'][0]['at'], 'date': today.isoformat(),
    }
    assert data['recent_payments'][0]['date'] == today.isoformat()


@pytest.mark.django_db
def test_query_count_stays_small_with_plenty_of_data(restaurant, active_session, cashier_user, today,
                                                    django_assert_max_num_queries):
    for i in range(40):
        order = make_order(restaurant, active_session, OrderStatus.COMPLETED, '5.00', day=today, minute=i % 59)
        add_item(order, f'Menu {i % 9}', 2, '2.50')
        make_payment(restaurant, active_session, cashier_user, '5.00', paid_day=today, minute=i % 59)

    for key in ('today', '7d', '30d'):
        with django_assert_max_num_queries(24):
            build_overview(key)


# ----------------------------------------------------------------------------- endpoint

@pytest.mark.django_db
def test_endpoint_returns_the_dashboard_payload_for_admins(admin_user, restaurant, active_session, cashier_user, today):
    make_payment(restaurant, active_session, cashier_user, '15.00', paid_day=today)
    client = APIClient()
    client.force_authenticate(user=admin_user)
    res = client.get(URL, {'range': '7d'})
    assert res.status_code == 200
    assert res['Cache-Control'] == 'no-store'
    body = res.json()['data']
    assert body['range'] == '7d'
    assert body['kpi']['sales']['value'] == 15.0
    assert set(body) >= {'kpi', 'tables', 'trend', 'busy_hours', 'order_types', 'top_items', 'categories', 'statuses',
                         'cashiers', 'recent_payments', 'recent_rejections', 'period'}


@pytest.mark.django_db
def test_endpoint_defaults_to_today_and_rejects_unknown_ranges(admin_user, restaurant):
    client = APIClient()
    client.force_authenticate(user=admin_user)
    assert client.get(URL).json()['data']['range'] == 'today'
    res = client.get(URL, {'range': '1y'})
    assert res.status_code == 400
    assert res.json()['error']['code'] == 'INVALID_INPUT'


@pytest.mark.django_db
def test_endpoint_is_admin_only(cashier_user, kitchen_user, restaurant):
    anonymous = Client().get(URL)
    assert anonymous.status_code in (401, 403)
    for user in (cashier_user, kitchen_user):
        client = APIClient()
        client.force_authenticate(user=user)
        assert client.get(URL).status_code == 403


@pytest.mark.django_db
def test_endpoint_only_accepts_get(admin_user, restaurant):
    client = APIClient()
    client.force_authenticate(user=admin_user)
    assert client.post(URL, {}, format='json').status_code == 405
