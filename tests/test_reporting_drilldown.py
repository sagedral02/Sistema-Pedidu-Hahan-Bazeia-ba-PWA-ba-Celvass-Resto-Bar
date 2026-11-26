"""Drill-down targets of the summary reports must open Riwayat lists that agree with the report totals."""
from datetime import timedelta

import pytest
from django.test import Client
from django.utils import timezone

from apps.ordering.models import OrderStatus
from apps.payments.models import PaymentMethod, PaymentStatus
from reporting_helpers import add_item, make_history, make_order, make_payment

DATA = '/reports/data/'
TRANSACTIONS = '/reports/history/transactions/'
ORDERS = '/reports/history/orders/'


def login(user):
    client = Client()
    client.force_login(user)
    return client


@pytest.fixture
def today():
    return timezone.localdate()


@pytest.fixture
def data(restaurant, active_session, session_two, cashier_user, cashier_two, admin_user, today):
    yesterday = today - timedelta(days=1)
    make_payment(restaurant, active_session, cashier_user, '10.00', paid_day=yesterday)
    make_payment(restaurant, active_session, cashier_user, '5.00', paid_day=today, hour=9)
    from apps.tables.takeaway import create_takeaway_session
    takeaway = create_takeaway_session(restaurant=restaurant, opened_by=cashier_two)
    make_payment(restaurant, takeaway, cashier_two, '30.00', paid_day=today, hour=10)
    make_payment(restaurant, session_two, cashier_two, '7.00', paid_day=today, hour=11,
                 status=PaymentStatus.VOIDED, void_reason='Salah input', voided_by=admin_user)
    order = make_order(restaurant, active_session, OrderStatus.COMPLETED, '12.00', day=today, confirmed_by=cashier_user)
    add_item(order, 'Ikan Saboko', 2, '6.00')
    rejected = make_order(restaurant, session_two, OrderStatus.REJECTED, '9.00', day=today, hour=13,
                          confirmed_by=cashier_two)
    make_history(rejected, OrderStatus.REJECTED, cashier_two, 'Stok habis', day=today)
    return {'yesterday': yesterday, 'order': order, 'rejected': rejected}


def drills(client, report_type, **extra):
    body = client.get(DATA, {'type': report_type, 'preset': '7days', **extra}).json()
    assert len(body['drills']) == len(body['rows'])
    return body


@pytest.mark.django_db
def test_daily_sales_rows_open_that_days_completed_transactions(admin_user, data, today):
    client = login(admin_user)
    body = drills(client, 'daily_sales')
    assert len(body['rows']) == 2
    for row, drill in zip(body['rows'], body['drills']):
        assert drill['kind'] == 'transactions' and drill['status'] == 'COMPLETED'
        assert drill['start'] == drill['end']
        listed = client.get(TRANSACTIONS, {'preset': 'custom', 'start': drill['start'], 'end': drill['end'],
                                           'status': drill['status']}).json()
        assert str(listed['total']) == row[1]


@pytest.mark.django_db
def test_order_type_rows_open_transactions_of_that_type(admin_user, data):
    client = login(admin_user)
    body = drills(client, 'order_types')
    by_type = {d['order_type']: (row, d) for row, d in zip(body['rows'], body['drills'])}
    row, drill = by_type['TAKEAWAY']
    listed = client.get(TRANSACTIONS, {'preset': '7days', 'order_type': drill['order_type'], 'status': drill['status']}).json()
    assert listed['total'] == 1 and listed['rows'][0]['amount'] == '$30.00'


@pytest.mark.django_db
def test_cashier_rows_open_that_cashiers_transactions(admin_user, data, cashier_two):
    client = login(admin_user)
    body = drills(client, 'cashiers')
    drill = next(d for row, d in zip(body['rows'], body['drills']) if row[0] == 'Maria Cashier')
    assert drill == {'kind': 'transactions', 'cashier': str(cashier_two.id)}
    listed = client.get(TRANSACTIONS, {'preset': '7days', 'cashier': drill['cashier']}).json()
    assert {r['cashier'] for r in listed['rows']} == {'Maria Cashier'}


@pytest.mark.django_db
def test_exception_rows_find_their_own_record(admin_user, data):
    client = login(admin_user)
    body = drills(client, 'exceptions')
    kinds = {d['kind'] for d in body['drills']}
    assert kinds == {'orders', 'transactions'}
    for drill in body['drills']:
        url = TRANSACTIONS if drill['kind'] == 'transactions' else ORDERS
        params = {'preset': '7days', 'q': drill['q']}
        if drill.get('status'):
            params['status'] = drill['status']
        assert client.get(url, params).json()['total'] == 1


@pytest.mark.django_db
def test_menu_rows_open_orders_that_contain_the_item(admin_user, data):
    client = login(admin_user)
    body = drills(client, 'menu_sales')
    drill = next(d for row, d in zip(body['rows'], body['drills']) if row[1] == 'Ikan Saboko')
    listed = client.get(ORDERS, {'preset': '7days', 'q': drill['q']}).json()
    assert [r['code'] for r in listed['rows']] == [data['order'].order_code]


@pytest.mark.django_db
def test_table_rows_open_orders_of_that_table(admin_user, data):
    client = login(admin_user)
    body = drills(client, 'table_usage')
    assert all(d['kind'] == 'orders' for d in body['drills'])
    drill = next(d for row, d in zip(body['rows'], body['drills']) if row[0] == 'Meja 02')
    listed = client.get(ORDERS, {'preset': '7days', 'q': drill['q']}).json()
    assert data['rejected'].order_code in {r['code'] for r in listed['rows']}


@pytest.mark.django_db
def test_drill_targets_never_widen_a_cashiers_scope(cashier_user, cashier_two, data):
    client = login(cashier_user)
    body = drills(client, 'cashiers')
    assert [r[0] for r in body['rows']] == ['cashier_test']
    listed = client.get(TRANSACTIONS, {'preset': '7days', **{k: v for k, v in body['drills'][0].items() if k != 'kind'}}).json()
    assert {r['cashier'] for r in listed['rows']} == {'cashier_test'}


@pytest.mark.django_db
def test_drill_targets_do_not_leak_into_csv(admin_user, data):
    res = login(admin_user).get('/reports/export/', {'type': 'daily_sales', 'format': 'csv', 'preset': '7days'})
    assert b'drill' not in res.content and b'kind' not in res.content
