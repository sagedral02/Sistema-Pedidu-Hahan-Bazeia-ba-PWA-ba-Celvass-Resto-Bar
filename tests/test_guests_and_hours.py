"""Guest count per table capacity, and opening hours blocking customer orders."""
from datetime import time, timedelta

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.restaurants.models import OpeningHours
from apps.tables.models import TableActivationRequest, TableSession

REQ = '/api/v1/public/tables/{}/request-activation/'


def staff(user):
    c = APIClient()
    c.force_authenticate(user=user)
    return c


def close_now(restaurant):
    """Opening hours that exclude the current moment on every day."""
    now = timezone.localtime()
    start = (now + timedelta(hours=2)).time().replace(second=0, microsecond=0)
    end = (now + timedelta(hours=3)).time().replace(second=0, microsecond=0)
    for day in range(7):
        OpeningHours.objects.create(restaurant=restaurant, weekday=day, opens_at=start, closes_at=end)


# ----------------------------------------------------------------------------- guest count

@pytest.mark.django_db
def test_activation_needs_a_guest_count_within_capacity(restaurant, table_one):
    c = APIClient()
    assert c.post(REQ.format(table_one.qr_token), {}, format='json').status_code == 400
    assert c.post(REQ.format(table_one.qr_token), {'guest_count': 5}, format='json').status_code == 400  # capacity 4
    res = c.post(REQ.format(table_one.qr_token), {'guest_count': 4}, format='json')
    assert res.status_code == 201
    assert TableActivationRequest.objects.get().guest_count == 4


@pytest.mark.django_db
def test_table_without_capacity_allows_up_to_25(restaurant, table_one):
    table_one.capacity = None
    table_one.save()
    c = APIClient()
    assert c.post(REQ.format(table_one.qr_token), {'guest_count': 26}, format='json').status_code == 400
    assert c.post(REQ.format(table_one.qr_token), {'guest_count': 25}, format='json').status_code == 201


@pytest.mark.django_db
def test_cashier_open_session_respects_capacity(table_one, cashier_user):
    url = f'/api/v1/cashier/tables/{table_one.id}/open-session/'
    assert staff(cashier_user).post(url, {'guest_count': 6}, format='json').status_code == 400
    assert staff(cashier_user).post(url, {'guest_count': 3}, format='json').status_code == 201


@pytest.mark.django_db
def test_cashier_can_correct_the_guest_count_when_approving(restaurant, table_one, cashier_user):
    APIClient().post(REQ.format(table_one.qr_token), {'guest_count': 2}, format='json')
    req = TableActivationRequest.objects.get()
    url = f'/api/v1/cashier/activation-requests/{req.id}/approve/'
    assert staff(cashier_user).post(url, {'guest_count': 9}, format='json').status_code == 400
    res = staff(cashier_user).post(url, {'guest_count': 3}, format='json')
    assert res.status_code == 200 and res.json()['data']['guest_count'] == 3


@pytest.mark.django_db
def test_cashier_table_order_never_opens_a_session_but_take_away_needs_no_guests(table_one, cashier_user, menu_item_fish):
    url = '/api/v1/cashier/orders/create/'
    item = [{'menu_item_id': str(menu_item_fish.id), 'quantity': 1}]
    res = staff(cashier_user).post(url, {'destination': 'TABLE', 'table_id': str(table_one.id), 'guest_count': 7, 'items': item},
                                   format='json', HTTP_IDEMPOTENCY_KEY='g1')
    assert res.status_code == 409 and res.json()['error']['code'] == 'TABLE_SESSION_NOT_OPEN'
    assert not TableSession.objects.filter(table=table_one).exists()
    res = staff(cashier_user).post(url, {'destination': 'TAKEAWAY', 'items': item}, format='json', HTTP_IDEMPOTENCY_KEY='g2')
    assert res.status_code == 201


@pytest.mark.django_db
def test_tables_api_reports_the_guest_limit(table_one, cashier_user):
    rows = staff(cashier_user).get('/api/v1/cashier/tables/').json()['data']
    assert next(r for r in rows if r['id'] == str(table_one.id))['guest_limit'] == 4


# ----------------------------------------------------------------------------- opening hours

@pytest.mark.django_db
def test_closed_restaurant_refuses_customer_activation_and_orders(restaurant, table_one, active_session, menu_item_fish, table_two):
    close_now(restaurant)
    res = APIClient().post(REQ.format(table_two.qr_token), {'guest_count': 2}, format='json')
    assert res.status_code == 409 and res.json()['error']['code'] == 'RESTAURANT_CLOSED'
    res = APIClient().post(f'/api/v1/public/sessions/{active_session.public_token}/orders/',
                           {'items': [{'menu_item_id': str(menu_item_fish.id), 'quantity': 1}]},
                           format='json', HTTP_IDEMPOTENCY_KEY='c1')
    assert res.status_code == 409


@pytest.mark.django_db
def test_cashier_still_orders_when_closed(restaurant, table_one, active_session, cashier_user, menu_item_fish):
    close_now(restaurant)
    res = staff(cashier_user).post('/api/v1/cashier/orders/create/', {
        'destination': 'TABLE', 'table_id': str(table_one.id), 'guest_count': 2,
        'items': [{'menu_item_id': str(menu_item_fish.id), 'quantity': 1}]}, format='json', HTTP_IDEMPOTENCY_KEY='c2')
    assert res.status_code == 201


@pytest.mark.django_db
def test_open_restaurant_accepts_customers(restaurant, table_one):
    for day in range(7):
        OpeningHours.objects.create(restaurant=restaurant, weekday=day, opens_at=time(0, 0), closes_at=time(23, 59))
    now = timezone.localtime()
    if now.hour == 23 and now.minute == 59:
        pytest.skip('boundary minute')
    assert APIClient().post(REQ.format(table_one.qr_token), {'guest_count': 2}, format='json').status_code == 201


@pytest.mark.django_db
def test_qr_page_shows_closed_banner_and_no_guest_form(client, restaurant, table_one):
    close_now(restaurant)
    html = client.get(f'/t/{table_one.qr_token}/').content.decode()
    assert 'id="restaurant-closed-banner"' in html and 'id="guest-step"' not in html


@pytest.mark.django_db
def test_qr_page_asks_how_many_people_instead_of_auto_requesting(client, restaurant, table_one):
    html = client.get(f'/t/{table_one.qr_token}/').content.decode()
    assert 'id="guest-step"' in html and 'guestLimit: 4' in html
    assert TableActivationRequest.objects.count() == 0


@pytest.mark.django_db
def test_activation_queue_shows_capacity_and_limit(restaurant, table_one, cashier_user):
    APIClient().post(REQ.format(table_one.qr_token), {'guest_count': 3}, format='json')
    rows = staff(cashier_user).get('/api/v1/cashier/activation-requests/').json()['data']
    row = rows[0] if isinstance(rows, list) else rows['results'][0]
    assert row['guest_count'] == 3 and row['table_capacity'] == 4 and row['guest_limit'] == 4
    assert 'device_token' not in row


@pytest.mark.django_db
def test_cashier_guest_inputs_are_driven_by_the_table_limit(cashier_user):
    from django.test import Client
    from pathlib import Path
    c = Client()
    c.force_login(cashier_user)
    html = c.get('/cashier/').content.decode()
    assert 'id="open-session-guests-hint"' in html
    # Orders never open a table, so the order screen has no guest field any more.
    assert 'id="pos-order-guests"' not in html
    root = Path(__file__).resolve().parent.parent / 'static' / 'js'
    cashier_js = (root / 'cashier.js').read_text(encoding='utf-8')
    assert 'table.guest_limit' in cashier_js and "type: 'number'" in cashier_js
