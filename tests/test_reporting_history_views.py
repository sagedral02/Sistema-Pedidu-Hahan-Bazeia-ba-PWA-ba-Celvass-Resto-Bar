"""Riwayat endpoints: access control, scope enforcement on the server, validation and translations."""
import uuid

import pytest
from django.test import Client
from django.utils import timezone

from apps.ordering.models import OrderStatus
from apps.payments.models import PaymentMethod, PaymentStatus
from reporting_helpers import add_item, make_history, make_order, make_payment

ORDERS = '/reports/history/orders/'
TRANSACTIONS = '/reports/history/transactions/'


def login(user):
    client = Client()
    client.force_login(user)
    return client


@pytest.fixture
def today():
    return timezone.localdate()


@pytest.fixture
def data(restaurant, active_session, session_two, cashier_user, cashier_two, admin_user, today):
    mine = make_order(restaurant, active_session, OrderStatus.COMPLETED, '20.00', day=today, hour=10,
                      confirmed_by=cashier_user)
    add_item(mine, 'Ikan Saboko', 2, '10.00')
    make_history(mine, OrderStatus.CONFIRMED, cashier_user, day=today)
    theirs = make_order(restaurant, session_two, OrderStatus.REJECTED, '9.00', day=today, hour=11,
                        confirmed_by=cashier_two)
    make_history(theirs, OrderStatus.REJECTED, cashier_two, 'Stok habis', day=today)
    waiting = make_order(restaurant, session_two, OrderStatus.WAITING_CASHIER_CONFIRMATION, '4.00', day=today)
    pay_mine = make_payment(restaurant, active_session, cashier_user, '20.00', paid_day=today)
    pay_theirs = make_payment(restaurant, session_two, cashier_two, '30.00', paid_day=today,
                              method=PaymentMethod.MANUAL_OTHER)
    make_payment(restaurant, session_two, cashier_two, '7.00', paid_day=today, status=PaymentStatus.VOIDED,
                 void_reason='Salah input', voided_by=admin_user)
    return {'mine': mine, 'theirs': theirs, 'waiting': waiting, 'pay_mine': pay_mine, 'pay_theirs': pay_theirs}


# ----------------------------------------------------------------------------- access

@pytest.mark.django_db
@pytest.mark.parametrize('url', [ORDERS, TRANSACTIONS, ORDERS + 'x/', TRANSACTIONS + 'x/'])
def test_anonymous_users_are_sent_to_login(url):
    res = Client().get(url)
    assert res.status_code == 302 and res['Location'].startswith('/login/')


@pytest.mark.django_db
@pytest.mark.parametrize('url', [ORDERS, TRANSACTIONS, ORDERS + 'x/', TRANSACTIONS + 'x/'])
def test_kitchen_staff_get_403(kitchen_user, url):
    assert login(kitchen_user).get(url).status_code == 403


@pytest.mark.django_db
@pytest.mark.parametrize('url', [ORDERS, TRANSACTIONS])
def test_history_only_accepts_get(admin_user, url):
    assert login(admin_user).post(url).status_code == 405


# ----------------------------------------------------------------------------- lists

@pytest.mark.django_db
def test_admin_orders_list_shape_and_translation(admin_user, data):
    res = login(admin_user).get(ORDERS, {'lang': 'en'})
    assert res.status_code == 200
    assert res['Cache-Control'] == 'no-store'
    body = res.json()
    assert body['kind'] == 'orders' and body['total'] == 3 and body['page'] == 1 and body['pages'] == 1
    assert body['scope_own'] is False
    assert [c['key'] for c in body['columns']] == ['time', 'code', 'table', 'status', 'items', 'total', 'staff']
    assert {r['status']['label'] for r in body['rows']} == {'Completed', 'Rejected', 'Waiting for cashier'}
    assert body['kpi'][0] == {'label': 'Orders', 'value': '3'}
    assert {c['name'] for c in body['cashiers']} >= {'cashier_test', 'Maria Cashier'}


@pytest.mark.django_db
def test_cashier_gets_only_their_orders_and_no_cashier_choices(cashier_user, data):
    body = login(cashier_user).get(ORDERS).json()
    assert [r['id'] for r in body['rows']] == [str(data['mine'].id)]
    assert body['scope_own'] is True
    assert body['cashiers'] == []


@pytest.mark.django_db
def test_cashier_transactions_are_limited_to_their_own(cashier_user, data):
    body = login(cashier_user).get(TRANSACTIONS, {'lang': 'pt'}).json()
    assert [r['id'] for r in body['rows']] == [str(data['pay_mine'].id)]
    assert body['rows'][0]['method'] == 'Dinheiro'
    assert body['rows'][0]['status']['label'] == 'Concluído'


@pytest.mark.django_db
def test_cashier_cannot_widen_scope_with_a_cashier_filter(cashier_user, cashier_two, data):
    body = login(cashier_user).get(TRANSACTIONS, {'cashier': str(cashier_two.id)}).json()
    assert [r['id'] for r in body['rows']] == [str(data['pay_mine'].id)]


@pytest.mark.django_db
def test_filters_sort_and_paging_reach_the_selector(admin_user, data):
    client = login(admin_user)
    rejected = client.get(ORDERS, {'status': 'REJECTED'}).json()
    assert [r['id'] for r in rejected['rows']] == [str(data['theirs'].id)]

    asc = client.get(TRANSACTIONS, {'sort': 'amount'}).json()
    assert [r['amount'] for r in asc['rows']] == ['$7.00', '$20.00', '$30.00']
    assert asc['sort'] == 'amount'

    paged = client.get(TRANSACTIONS, {'page_size': 2, 'page': 2}).json()
    assert (paged['total'], paged['pages'], paged['page'], len(paged['rows'])) == (3, 2, 2, 1)


@pytest.mark.django_db
def test_unknown_sort_is_ignored_not_executed(admin_user, data):
    res = login(admin_user).get(ORDERS, {'sort': 'grand_total; DROP TABLE orders'})
    assert res.status_code == 200 and res.json()['sort'] == '-time'


@pytest.mark.django_db
@pytest.mark.parametrize('params,code', [
    ({'status': 'NOPE'}, 'INVALID_FILTER'),
    ({'cashier': 'zzz'}, 'INVALID_FILTER'),
    ({'preset': 'custom', 'start': 'x', 'end': 'y'}, 'INVALID_DATE'),
    ({'preset': 'custom', 'start': '2026-02-01', 'end': '2026-01-01'}, 'END_BEFORE_START'),
])
def test_invalid_parameters_give_localized_errors(admin_user, restaurant, params, code):
    res = login(admin_user).get(ORDERS, {**params, 'lang': 'id'})
    assert res.status_code == 400
    err = res.json()['error']
    assert err['code'] == code
    assert err['message'] and not err['message'].startswith('rpt_')


@pytest.mark.django_db
def test_every_list_works_in_every_language(admin_user, data):
    client = login(admin_user)
    for url in (ORDERS, TRANSACTIONS):
        for lang in ('tet', 'pt', 'en', 'id'):
            body = client.get(url, {'lang': lang}).json()
            assert all(not c['label'].startswith('rpt_') for c in body['columns'])
            assert all(not k['label'].startswith('rpt_') for k in body['kpi'])
            for row in body['rows']:
                assert not row['status']['label'].startswith('rpt_')


# ----------------------------------------------------------------------------- details

@pytest.mark.django_db
def test_admin_order_detail(admin_user, data):
    body = login(admin_user).get(f"{ORDERS}{data['mine'].id}/", {'lang': 'en'}).json()
    assert body['code'] == data['mine'].order_code
    assert body['items'][0]['name'] == 'Ikan Saboko'
    assert body['timeline'][0]['to']['label'] == 'Confirmed'
    assert body['money']['total'] == '$20.00'


@pytest.mark.django_db
def test_cashier_gets_404_for_orders_outside_their_scope(cashier_user, data):
    client = login(cashier_user)
    assert client.get(f"{ORDERS}{data['mine'].id}/").status_code == 200
    for key in ('theirs', 'waiting'):
        res = client.get(f"{ORDERS}{data[key].id}/", {'lang': 'en'})
        assert res.status_code == 404
        assert res.json()['error'] == {'code': 'NOT_FOUND', 'message': 'Record not found.'}


@pytest.mark.django_db
def test_cashier_gets_404_for_other_cashiers_transactions(cashier_user, data):
    client = login(cashier_user)
    assert client.get(f"{TRANSACTIONS}{data['pay_mine'].id}/").status_code == 200
    assert client.get(f"{TRANSACTIONS}{data['pay_theirs'].id}/").status_code == 404


@pytest.mark.django_db
def test_transaction_detail_includes_receipt_payload(admin_user, data):
    body = login(admin_user).get(f"{TRANSACTIONS}{data['pay_mine'].id}/", {'lang': 'id'}).json()
    assert body['method'] == 'Tunai'
    assert body['receipt']['payment_code'] == data['pay_mine'].payment_code
    assert body['receipt']['items'][0]['name'] == 'Ikan Saboko'


@pytest.mark.django_db
@pytest.mark.parametrize('bad', ['not-a-uuid', str(uuid.uuid4())])
def test_unknown_or_malformed_ids_are_404(admin_user, restaurant, bad):
    client = login(admin_user)
    assert client.get(f'{ORDERS}{bad}/').status_code == 404
    assert client.get(f'{TRANSACTIONS}{bad}/').status_code == 404
