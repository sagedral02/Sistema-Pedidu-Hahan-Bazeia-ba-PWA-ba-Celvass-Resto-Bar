"""Cashier order screen API: take away and table orders straight to the kitchen."""
import uuid

import pytest
from rest_framework.test import APIClient

from apps.catalog.daily import close_menu_for_today
from apps.ordering.models import Order, OrderSource, OrderStatus
from apps.outbox.models import OutboxEvent
from apps.tables.models import SessionStatus, TableSession, TableStatus

URL = '/api/v1/cashier/orders/create/'


def api(user):
    c = APIClient()
    if user is not None:
        c.force_authenticate(user=user)
    return c


def post(client, body, key=None):
    return client.post(URL, body, format='json', HTTP_IDEMPOTENCY_KEY=key or str(uuid.uuid4()))


@pytest.mark.django_db
def test_takeaway_order_opens_a_numbered_session_and_reaches_the_kitchen(cashier_user, menu_item_fish, restaurant):
    res = post(api(cashier_user), {'destination': 'TAKEAWAY', 'customer_name': 'Budi',
                                   'items': [{'menu_item_id': str(menu_item_fish.id), 'quantity': 2}]})
    assert res.status_code == 201, res.content
    data = res.json()['data']
    assert data['status'] == OrderStatus.CONFIRMED and data['source'] == OrderSource.CASHIER
    assert data['is_takeaway'] is True and data['takeaway_number'] == 1
    assert data['table_name'] == 'Take away #001 - Budi'
    assert OutboxEvent.objects.filter(event_type='ORDER_CONFIRMED').exists()


@pytest.mark.django_db
def test_table_order_needs_an_open_session_first(cashier_user, menu_item_fish, table_one):
    item = [{'menu_item_id': str(menu_item_fish.id), 'quantity': 1}]
    res = post(api(cashier_user), {'destination': 'TABLE', 'table_id': str(table_one.id), 'items': item})
    assert res.status_code == 409 and res.json()['error']['code'] == 'TABLE_SESSION_NOT_OPEN'
    assert not TableSession.objects.filter(table=table_one).exists()
    assert not Order.objects.exists()

    opened = api(cashier_user).post(f'/api/v1/cashier/tables/{table_one.id}/open-session/', {'guest_count': 3}, format='json')
    assert opened.status_code in (200, 201), opened.content
    res = post(api(cashier_user), {'destination': 'TABLE', 'table_id': str(table_one.id), 'items': item})
    assert res.status_code == 201, res.content
    session = TableSession.objects.get(table=table_one)
    assert session.guest_count == 3 and session.status == SessionStatus.OPEN
    table_one.refresh_from_db()
    assert table_one.status == TableStatus.OCCUPIED
    # second order goes to the same session
    post(api(cashier_user), {'destination': 'TABLE', 'table_id': str(table_one.id),
                             'items': [{'menu_item_id': str(menu_item_fish.id), 'quantity': 1}]})
    assert TableSession.objects.filter(table=table_one).count() == 1
    assert Order.objects.filter(table_session=session).count() == 2


@pytest.mark.django_db
def test_same_key_returns_the_first_order(cashier_user, menu_item_fish):
    body = {'destination': 'TAKEAWAY', 'items': [{'menu_item_id': str(menu_item_fish.id), 'quantity': 1}]}
    a = post(api(cashier_user), body, key='same')
    b = post(api(cashier_user), body, key='same')
    assert a.status_code == 201 and b.status_code == 200
    assert a.json()['data']['id'] == b.json()['data']['id']
    assert TableSession.objects.filter(takeaway_number__isnull=False).count() == 1


@pytest.mark.django_db
def test_quota_and_closed_dish_are_enforced_and_nothing_is_left_behind(cashier_user, menu_item_fish, menu_item_corn):
    menu_item_fish.daily_quota = 1
    menu_item_fish.save()
    res = post(api(cashier_user), {'destination': 'TAKEAWAY', 'items': [{'menu_item_id': str(menu_item_fish.id), 'quantity': 2}]})
    assert res.status_code == 400
    assert TableSession.objects.filter(takeaway_number__isnull=False).count() == 0, 'the take-away session is rolled back'
    close_menu_for_today(menu_item=menu_item_corn, staff_user=cashier_user)
    res = post(api(cashier_user), {'destination': 'TAKEAWAY', 'items': [{'menu_item_id': str(menu_item_corn.id), 'quantity': 1}]})
    assert res.status_code == 400


@pytest.mark.django_db
def test_validation(cashier_user, menu_item_fish):
    c = api(cashier_user)
    assert post(c, {'destination': 'TABLE', 'items': [{'menu_item_id': str(menu_item_fish.id), 'quantity': 1}]}).status_code == 400
    assert post(c, {'destination': 'TAKEAWAY', 'items': []}).status_code == 400
    assert post(c, {'destination': 'TABLE', 'table_id': str(uuid.uuid4()),
                    'items': [{'menu_item_id': str(menu_item_fish.id), 'quantity': 1}]}).status_code == 404
    assert c.post(URL, {'destination': 'TAKEAWAY', 'items': [{'menu_item_id': str(menu_item_fish.id), 'quantity': 1}]},
                  format='json').status_code == 400, 'Idempotency-Key is required'


@pytest.mark.django_db
def test_permissions(kitchen_user, admin_user, menu_item_fish):
    body = {'destination': 'TAKEAWAY', 'items': [{'menu_item_id': str(menu_item_fish.id), 'quantity': 1}]}
    assert post(api(kitchen_user), body).status_code == 403
    assert post(api(None), body).status_code in (401, 403)
    assert post(api(admin_user), body).status_code == 201
    assert api(kitchen_user).get('/api/v1/cashier/menu/').status_code == 403
    assert api(kitchen_user).get('/api/v1/cashier/takeaway/').status_code == 403


@pytest.mark.django_db
def test_menu_lists_stock_and_takeaway_list_flags_ready(cashier_user, kitchen_user, menu_item_fish):
    from apps.kitchen.services import change_items_status
    from apps.ordering.models import ItemStatus
    menu_item_fish.daily_quota = 5
    menu_item_fish.save()
    data = api(cashier_user).get('/api/v1/cashier/menu/').json()['data']
    fish = next(i for c in data for i in c['items'] if i['name'] == 'Ikan Saboko')
    assert fish['remaining'] == 5 and fish['orderable'] is True

    res = post(api(cashier_user), {'destination': 'TAKEAWAY', 'items': [{'menu_item_id': str(menu_item_fish.id), 'quantity': 1}]})
    order = Order.objects.get(id=res.json()['data']['id'])
    rows = api(cashier_user).get('/api/v1/cashier/takeaway/').json()['data']
    assert len(rows) == 1 and rows[0]['ready_for_pickup'] is False
    change_items_status(order=order, to_status=ItemStatus.COOKING, staff_user=kitchen_user)
    change_items_status(order=order, to_status=ItemStatus.READY, staff_user=kitchen_user)
    rows = api(cashier_user).get('/api/v1/cashier/takeaway/').json()['data']
    assert rows[0]['ready_for_pickup'] is True and rows[0]['bill']['grand_total'] == '6.50'
