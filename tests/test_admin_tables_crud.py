import pytest
from django.test import Client
from rest_framework.test import APIClient

from apps.audit.models import AuditEvent
from apps.tables.models import RestaurantTable, TableStatus

URL = '/api/v1/admin/tables/'


def admin_client(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


@pytest.mark.django_db
def test_only_admin_can_manage_tables(restaurant, cashier_user):
    anon = APIClient()
    assert anon.get(URL).status_code in (401, 403)
    assert admin_client(cashier_user).post(URL, {'table_code': 'T-9', 'display_name': 'X'}, format='json').status_code == 403


@pytest.mark.django_db
def test_create_list_update_table(restaurant, admin_user):
    client = admin_client(admin_user)
    res = client.post(URL, {'table_code': ' t-10 ', 'display_name': 'Meza 10', 'capacity': 6, 'sort_order': 10}, format='json')
    assert res.status_code == 201, res.json()
    data = res.json()['data']
    assert data['table_code'] == 'T-10'
    assert data['capacity'] == 6
    assert data['status'] == 'AVAILABLE'
    assert data['qr_token']
    assert AuditEvent.objects.filter(action='TABLE_CREATE', entity_id=data['id']).exists()

    listed = client.get(URL).json()['data']
    assert any(t['id'] == data['id'] for t in listed)

    upd = client.patch(f"{URL}{data['id']}/", {'display_name': 'Meza Varanda', 'capacity': None, 'status': 'MAINTENANCE'}, format='json')
    assert upd.status_code == 200, upd.json()
    body = upd.json()['data']
    assert body['display_name'] == 'Meza Varanda'
    assert body['capacity'] is None
    assert body['status'] == 'MAINTENANCE'
    assert body['table_code'] == 'T-10'


@pytest.mark.django_db
def test_table_code_must_be_unique_case_insensitive(restaurant, admin_user, table_one):
    res = admin_client(admin_user).post(URL, {'table_code': 't-01', 'display_name': 'Copy'}, format='json')
    assert res.status_code == 400
    assert res.json()['error']['code'] == 'TABLE_ERROR'


@pytest.mark.django_db
@pytest.mark.parametrize('payload', [
    {'table_code': '', 'display_name': 'A'},
    {'table_code': 'T-5', 'display_name': ''},
    {'table_code': 'T-5', 'display_name': 'A', 'capacity': 0},
    {'table_code': 'T-5', 'display_name': 'A', 'capacity': 51},
    {'table_code': 'T-5', 'display_name': 'A', 'status': 'OCCUPIED'},
])
def test_invalid_table_input_is_rejected(restaurant, admin_user, payload):
    res = admin_client(admin_user).post(URL, payload, format='json')
    assert res.status_code == 400


@pytest.mark.django_db
def test_busy_table_cannot_change_status_deactivate_or_delete(admin_user, active_session, table_one):
    client = admin_client(admin_user)
    assert client.patch(f'{URL}{table_one.id}/', {'status': 'MAINTENANCE'}, format='json').status_code == 400
    assert client.patch(f'{URL}{table_one.id}/', {'is_active': False}, format='json').status_code == 400
    assert client.delete(f'{URL}{table_one.id}/').status_code == 400
    # Renaming a busy table is still allowed.
    assert client.patch(f'{URL}{table_one.id}/', {'display_name': 'Meza Prinsipal'}, format='json').status_code == 200
    table_one.refresh_from_db()
    assert table_one.status == TableStatus.OCCUPIED
    assert table_one.is_active


@pytest.mark.django_db
def test_delete_table_without_history_removes_it(restaurant, admin_user):
    table = RestaurantTable.objects.create(restaurant=restaurant, table_code='T-77', display_name='Meza 77')
    res = admin_client(admin_user).delete(f'{URL}{table.id}/')
    assert res.status_code == 200
    assert res.json()['data'] == {'deleted': True, 'deactivated': False}
    assert not RestaurantTable.objects.filter(id=table.id).exists()


@pytest.mark.django_db
def test_delete_table_with_history_deactivates_and_invalidates_qr(admin_user, active_session, table_one, cashier_user):
    from apps.tables.services import close_table_session
    close_table_session(session=active_session, closed_by=cashier_user)

    res = admin_client(admin_user).delete(f'{URL}{table_one.id}/')
    assert res.status_code == 200
    assert res.json()['data'] == {'deleted': False, 'deactivated': True}
    table_one.refresh_from_db()
    assert table_one.is_active is False
    assert Client().get(f'/t/{table_one.qr_token}/').status_code == 404


@pytest.mark.django_db
def test_admin_panel_lists_inactive_tables_with_crud_controls(admin_user, restaurant):
    RestaurantTable.objects.create(restaurant=restaurant, table_code='T-OFF', display_name='Meza Off', is_active=False)
    client = Client()
    client.force_login(admin_user)
    html = client.get('/admin-portal/').content.decode()
    assert 'Meza Off' in html
    assert 'openCreateTableModal()' in html
    assert 'id="tableModal"' in html


@pytest.mark.django_db
def test_table_list_can_be_refreshed_without_page_reload(admin_user, restaurant):
    """The admin page re-renders the table list from the API after create/delete."""
    client = Client()
    client.force_login(admin_user)
    html = client.get('/admin-portal/').content.decode()
    assert 'id="admin-tables-tbody"' in html

    api = admin_client(admin_user)
    created = api.post(URL, {'table_code': 'T-50', 'display_name': 'Meza 50'}, format='json').json()['data']
    assert api.delete(f"{URL}{created['id']}/").status_code == 200
    again = api.post(URL, {'table_code': 'T-50', 'display_name': 'Meza 50'}, format='json')
    assert again.status_code == 201
    listed = api.get(URL).json()['data']
    assert [t['table_code'] for t in listed].count('T-50') == 1
    assert {'has_active_session', 'sessions_count', 'is_active', 'qr_token'} <= set(listed[0])


@pytest.mark.django_db
@pytest.mark.parametrize('value', ['CLEANING', 'OCCUPIED'])
def test_manual_status_is_only_available_or_unavailable(restaurant, admin_user, value):
    client = admin_client(admin_user)
    table = client.post(URL, {'table_code': 'T-60', 'display_name': 'Meza 60'}, format='json').json()['data']
    res = client.patch(f"{URL}{table['id']}/", {'status': value}, format='json')
    assert res.status_code == 400
    ok = client.patch(f"{URL}{table['id']}/", {'status': 'MAINTENANCE'}, format='json')
    assert ok.status_code == 200
    assert client.patch(f"{URL}{table['id']}/", {'status': 'AVAILABLE'}, format='json').status_code == 200


@pytest.mark.django_db
def test_table_form_offers_two_status_options(admin_user):
    client = Client()
    client.force_login(admin_user)
    html = client.get('/admin-portal/').content.decode()
    assert 'value="CLEANING"' not in html
    assert 'value="MAINTENANCE" data-i18n="table_status_unavailable"' in html
