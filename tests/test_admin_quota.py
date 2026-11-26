"""Admin sets the daily production quota and closes/reopens dishes for the day."""
import pytest
from rest_framework.test import APIClient

from apps.audit.models import AuditEvent
from apps.catalog.availability import CLOSED_TODAY, menu_availability
from apps.catalog.models import MenuItem
from apps.ordering.models import OrderSource
from apps.ordering.services import submit_customer_order


def api(user):
    c = APIClient()
    if user is not None:
        c.force_authenticate(user=user)
    return c


def order(session, item, qty, key, user=None):
    return submit_customer_order(
        table_session=session, idempotency_key=key, source=OrderSource.CASHIER, created_by=user,
        items_data=[{'menu_item_id': item.id, 'quantity': qty}])


@pytest.mark.django_db
def test_create_with_quota_and_edit_it(admin_user, category_main):
    c = api(admin_user)
    res = c.post('/api/v1/admin/menu-items/', {
        'name': 'Sup Ikan', 'category_id': str(category_main.id), 'price': '4.00', 'daily_quota': '20'}, format='json')
    assert res.status_code == 201, res.content
    item_id = res.json()['data']['id']
    assert res.json()['data']['daily_quota'] == 20 and res.json()['data']['remaining'] == 20
    res = c.patch(f'/api/v1/admin/menu-items/{item_id}/', {'daily_quota': '5'}, format='json')
    assert res.status_code == 200 and res.json()['data']['daily_quota'] == 5
    assert AuditEvent.objects.filter(action='MENU_UPDATE', after_data__daily_quota=5).exists()


@pytest.mark.django_db
def test_blank_quota_removes_the_limit_and_missing_key_keeps_it(admin_user, menu_item_fish):
    menu_item_fish.daily_quota = 8
    menu_item_fish.save()
    c = api(admin_user)
    c.patch(f'/api/v1/admin/menu-items/{menu_item_fish.id}/', {'price': '7.00'}, format='json')
    menu_item_fish.refresh_from_db()
    assert menu_item_fish.daily_quota == 8, 'a patch without the key leaves the quota alone'
    res = c.patch(f'/api/v1/admin/menu-items/{menu_item_fish.id}/', {'daily_quota': ''}, format='multipart')
    assert res.status_code == 200, res.content
    menu_item_fish.refresh_from_db()
    assert menu_item_fish.daily_quota is None
    res = c.patch(f'/api/v1/admin/menu-items/{menu_item_fish.id}/', {'daily_quota': None}, format='json')
    assert res.status_code == 200


@pytest.mark.django_db
@pytest.mark.parametrize('bad', ['0', '-3', 'abc', '1.5'])
def test_invalid_quota_is_rejected(admin_user, menu_item_fish, bad):
    res = api(admin_user).patch(f'/api/v1/admin/menu-items/{menu_item_fish.id}/', {'daily_quota': bad}, format='json')
    assert res.status_code == 400
    menu_item_fish.refresh_from_db()
    assert menu_item_fish.daily_quota is None


@pytest.mark.django_db
def test_list_shows_quota_sold_and_remaining(admin_user, menu_item_fish, active_session, cashier_user):
    menu_item_fish.daily_quota = 20
    menu_item_fish.save()
    order(active_session, menu_item_fish, 6, 'a', cashier_user)
    rows = {r['name']: r for r in api(admin_user).get('/api/v1/admin/menu-items/').json()['data']}
    fish = rows['Ikan Saboko']
    assert (fish['daily_quota'], fish['sold'], fish['remaining'], fish['state']) == (20, 6, 14, 'AVAILABLE')


@pytest.mark.django_db
def test_close_today_and_reopen_endpoints(admin_user, menu_item_fish, active_session, cashier_user):
    order(active_session, menu_item_fish, 2, 'a', cashier_user)
    c = api(admin_user)
    res = c.post(f'/api/v1/admin/menu-items/{menu_item_fish.id}/close-today/', {'reason': 'no fish'}, format='json')
    assert res.status_code == 200, res.content
    data = res.json()['data']
    assert data['cancelled_items'] == 2 and data['item']['state'] == CLOSED_TODAY
    assert data['item']['closed_reason'] == 'no fish'
    res = c.post(f'/api/v1/admin/menu-items/{menu_item_fish.id}/reopen/')
    assert res.status_code == 200 and res.json()['data']['was_closed'] is True
    assert menu_availability(menu_item_fish).orderable


@pytest.mark.django_db
def test_only_admin_can_use_the_endpoints(menu_item_fish, cashier_user, kitchen_user):
    for user in (cashier_user, kitchen_user, None):
        c = api(user)
        for suffix in ('close-today', 'reopen'):
            res = c.post(f'/api/v1/admin/menu-items/{menu_item_fish.id}/{suffix}/')
            assert res.status_code in (401, 403), (user, suffix)
        assert c.patch(f'/api/v1/admin/menu-items/{menu_item_fish.id}/', {'daily_quota': '3'}, format='json').status_code in (401, 403)


@pytest.mark.django_db
def test_unknown_menu_item_is_404(admin_user):
    import uuid
    assert api(admin_user).post(f'/api/v1/admin/menu-items/{uuid.uuid4()}/close-today/').status_code == 404


@pytest.mark.django_db
def test_admin_page_lists_quota_columns_and_close_button(client, admin_user, menu_item_fish, active_session, cashier_user):
    menu_item_fish.daily_quota = 10
    menu_item_fish.save()
    order(active_session, menu_item_fish, 4, 'a', cashier_user)
    client.force_login(admin_user)
    html = client.get('/admin-portal/').content.decode()
    assert 'data-i18n="stock_th_quota"' in html and 'data-i18n="stock_th_left"' in html
    assert 'closeMenuItemToday(' in html and 'id="menu-form-quota"' in html
    assert 'toggleMenuItem(' not in html.split('admin-menu-table', 1)[1].split('</table>', 1)[0]
