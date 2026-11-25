import pytest
from django.test import Client
from rest_framework.test import APIClient

from apps.accounts.models import Role, User
from apps.audit.models import AuditEvent

URL = '/api/v1/admin/users/'
GOOD_PW = 'Tasi-Dili-2026'


def api(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def make(username, role=Role.CASHIER, **extra):
    return User.objects.create_user(username=username, password=GOOD_PW, role=role, **extra)


@pytest.mark.django_db
def test_only_admin_can_manage_staff(cashier_user):
    assert APIClient().get(URL).status_code in (401, 403)
    assert api(cashier_user).get(URL).status_code == 403


@pytest.mark.django_db
def test_create_list_update_staff(admin_user):
    client = api(admin_user)
    res = client.post(URL, {'username': 'dapur2', 'password': GOOD_PW, 'role': 'KITCHEN', 'full_name': 'Maria'}, format='json')
    assert res.status_code == 201, res.json()
    data = res.json()['data']
    assert data['role'] == 'KITCHEN' and data['full_name'] == 'Maria' and data['is_active']
    assert 'password' not in data
    assert AuditEvent.objects.filter(action='USER_CREATE', entity_id=data['id']).exists()

    listed = client.get(URL).json()['data']
    assert any(u['id'] == data['id'] for u in listed)
    me = next(u for u in listed if u['username'] == admin_user.username)
    assert me['is_self'] is True

    upd = client.patch(f"{URL}{data['id']}/", {'full_name': 'Maria Soares', 'role': 'CASHIER'}, format='json')
    assert upd.status_code == 200, upd.json()
    assert upd.json()['data']['full_name'] == 'Maria Soares'
    assert upd.json()['data']['role'] == 'CASHIER'


@pytest.mark.django_db
def test_password_change_validates_and_allows_new_login(admin_user):
    staff = make('kaixa5')
    client = api(admin_user)
    weak = client.patch(f'{URL}{staff.id}/', {'password': '123'}, format='json')
    assert weak.status_code == 400
    assert weak.json()['error']['code'] == 'WEAK_PASSWORD'

    ok = client.patch(f'{URL}{staff.id}/', {'password': 'Ikan-Saboko-77'}, format='json')
    assert ok.status_code == 200
    assert Client().login(username='kaixa5', password='Ikan-Saboko-77')
    assert not Client().login(username='kaixa5', password=GOOD_PW)
    # Leaving the password empty keeps the current one.
    assert client.patch(f'{URL}{staff.id}/', {'password': '', 'full_name': 'X'}, format='json').status_code == 200
    assert Client().login(username='kaixa5', password='Ikan-Saboko-77')


@pytest.mark.django_db
def test_username_must_be_unique_and_valid(admin_user, cashier_user):
    client = api(admin_user)
    dup = client.post(URL, {'username': cashier_user.username.upper(), 'password': GOOD_PW}, format='json')
    assert dup.status_code == 400
    bad = client.post(URL, {'username': 'kaixa ida', 'password': GOOD_PW}, format='json')
    assert bad.status_code == 400
    other = make('dapur9')
    clash = client.patch(f'{URL}{other.id}/', {'username': cashier_user.username}, format='json')
    assert clash.status_code == 400


@pytest.mark.django_db
def test_admin_cannot_lock_themselves_out(admin_user):
    second_admin = make('admin2', role=Role.ADMIN)
    client = api(second_admin)
    me = f'{URL}{second_admin.id}/'
    assert client.patch(me, {'role': 'CASHIER'}, format='json').status_code == 400
    assert client.patch(me, {'is_active': False}, format='json').status_code == 400
    assert client.delete(me).status_code == 400
    # Editing own name is allowed.
    assert client.patch(me, {'full_name': 'Jerente'}, format='json').status_code == 200
    second_admin.refresh_from_db()
    assert second_admin.role == Role.ADMIN and second_admin.is_active


@pytest.mark.django_db
def test_superuser_is_protected_from_other_admins(admin_user):
    other_admin = make('admin3', role=Role.ADMIN)
    client = api(other_admin)
    target = f'{URL}{admin_user.id}/'
    assert client.patch(target, {'role': 'KITCHEN'}, format='json').status_code == 400
    assert client.patch(target, {'is_active': False}, format='json').status_code == 400
    assert client.delete(target).status_code == 400


@pytest.mark.django_db
def test_delete_staff_without_history_removes_account(admin_user):
    staff = make('temp1')
    res = api(admin_user).delete(f'{URL}{staff.id}/')
    assert res.status_code == 200
    assert res.json()['data'] == {'deleted': True, 'deactivated': False}
    assert not User.objects.filter(pk=staff.pk).exists()
    assert AuditEvent.objects.filter(action='USER_DELETE', entity_id=str(staff.pk)).exists()


@pytest.mark.django_db
def test_delete_staff_with_history_deactivates_and_blocks_login(admin_user, cashier_user, active_session):
    res = api(admin_user).delete(f'{URL}{cashier_user.id}/')
    assert res.status_code == 200
    assert res.json()['data'] == {'deleted': False, 'deactivated': True}
    cashier_user.refresh_from_db()
    assert cashier_user.is_active is False
    assert not Client().login(username=cashier_user.username, password='password123')


@pytest.mark.django_db
def test_deactivated_staff_is_signed_out_of_existing_session(admin_user, cashier_user):
    browser = Client()
    browser.force_login(cashier_user)
    assert browser.get('/cashier/').status_code == 200
    assert api(admin_user).patch(f'{URL}{cashier_user.id}/', {'is_active': False}, format='json').status_code == 200
    res = browser.get('/cashier/')
    assert res.status_code == 302
    assert '/login/' in res.url


@pytest.mark.django_db
def test_admin_panel_renders_staff_crud_controls(admin_user, cashier_user):
    client = Client()
    client.force_login(admin_user)
    html = client.get('/admin-portal/').content.decode()
    assert 'id="admin-staff-tbody"' in html
    assert 'id="staffModal"' in html
    assert 'openCreateStaffModal()' in html
    assert f"openEditStaffModal('{cashier_user.id}')" in html
