"""Cashier Menu & Stock: can only close a dish for today or reopen it."""
import pytest
from django.test import Client
from rest_framework.test import APIClient

from apps.catalog.availability import CLOSED_TODAY, menu_availability


def api(user):
    c = APIClient()
    c.force_authenticate(user=user)
    return c


@pytest.mark.django_db
def test_list_shows_stock(cashier_user, menu_item_fish):
    menu_item_fish.daily_quota = 4
    menu_item_fish.save()
    rows = api(cashier_user).get('/api/v1/cashier/menu-stock/').json()['data']
    fish = next(r for r in rows if r['name'] == 'Ikan Saboko')
    assert (fish['quota'], fish['sold'], fish['remaining'], fish['state']) == (4, 0, 4, 'AVAILABLE')


@pytest.mark.django_db
def test_mark_sold_out_reaches_the_public_menu_and_reopen(cashier_user, menu_item_fish, table_one, active_session):
    res = api(cashier_user).post(f'/api/v1/cashier/menu-items/{menu_item_fish.id}/sold-out/', {'closed': True}, format='json')
    assert res.status_code == 200
    assert menu_availability(menu_item_fish).state == CLOSED_TODAY
    items = {i['name']: i for c in Client().get('/api/v1/public/menu/').json()['data'] for i in c['items']}
    assert items['Ikan Saboko']['orderable'] is False
    res = api(cashier_user).post(f'/api/v1/cashier/menu-items/{menu_item_fish.id}/sold-out/', {'closed': False}, format='json')
    assert res.status_code == 200 and res.json()['data']['was_closed'] is True
    assert menu_availability(menu_item_fish).orderable


@pytest.mark.django_db
def test_cashier_cannot_edit_or_delete_dishes(cashier_user, menu_item_fish):
    c = api(cashier_user)
    assert c.patch(f'/api/v1/admin/menu-items/{menu_item_fish.id}/', {'price': '1.00'}, format='json').status_code == 403
    assert c.delete(f'/api/v1/admin/menu-items/{menu_item_fish.id}/').status_code == 403
    assert c.post('/api/v1/admin/menu-items/', {'name': 'x'}, format='json').status_code == 403
    menu_item_fish.refresh_from_db()
    assert str(menu_item_fish.price) == '6.50'


@pytest.mark.django_db
def test_kitchen_and_anonymous_are_refused(kitchen_user, menu_item_fish):
    assert api(kitchen_user).get('/api/v1/cashier/menu-stock/').status_code == 403
    assert api(kitchen_user).post(f'/api/v1/cashier/menu-items/{menu_item_fish.id}/sold-out/').status_code == 403
    assert APIClient().get('/api/v1/cashier/menu-stock/').status_code in (401, 403)
