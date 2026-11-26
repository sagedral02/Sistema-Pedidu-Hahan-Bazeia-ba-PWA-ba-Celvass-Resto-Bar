"""Take-away base: hidden virtual table, daily queue numbers, labels."""
from datetime import timedelta

import pytest
from django.core.exceptions import ValidationError
from django.test import Client
from rest_framework.test import APIClient

from apps.tables.models import RestaurantTable, TableSession
from apps.tables.services import open_table_session
from apps.tables.selectors import list_all_tables_for_admin, list_tables_with_status
from apps.tables.takeaway import create_takeaway_session, format_takeaway_number, get_takeaway_table


@pytest.mark.django_db
def test_numbers_follow_each_other_and_restart_next_business_day(restaurant, cashier_user):
    a = create_takeaway_session(restaurant=restaurant, opened_by=cashier_user, customer_name='Budi')
    b = create_takeaway_session(restaurant=restaurant, opened_by=cashier_user)
    assert (a.takeaway_number, b.takeaway_number) == (1, 2)
    assert format_takeaway_number(a.takeaway_number) == 'TA-001'
    assert a.label == 'Take away #001 - Budi' and b.label == 'Take away #002'
    TableSession.objects.filter(id__in=[a.id, b.id]).update(takeaway_date=a.takeaway_date - timedelta(days=1))
    c = create_takeaway_session(restaurant=restaurant, opened_by=cashier_user)
    assert c.takeaway_number == 1


@pytest.mark.django_db
def test_virtual_table_is_hidden_everywhere(restaurant, table_one, cashier_user, admin_user):
    create_takeaway_session(restaurant=restaurant, opened_by=cashier_user)
    virtual = get_takeaway_table(restaurant)
    assert virtual.is_takeaway and not virtual.is_active
    assert virtual not in list(list_tables_with_status())
    assert virtual not in list(list_all_tables_for_admin())
    c = APIClient()
    c.force_authenticate(user=admin_user)
    codes = [t['table_code'] for t in c.get('/api/v1/admin/tables/').json()['data']]
    assert 'TAKEAWAY' not in codes
    c.force_authenticate(user=cashier_user)
    codes = [t['table_code'] for t in c.get('/api/v1/cashier/tables/').json()['data']]
    assert 'TAKEAWAY' not in codes


@pytest.mark.django_db
def test_virtual_table_cannot_be_opened_by_qr_or_admin(restaurant, cashier_user, admin_user):
    virtual = get_takeaway_table(restaurant)
    assert Client().get(f'/t/{virtual.qr_token}/').status_code == 404
    with pytest.raises(ValidationError):
        open_table_session(table=virtual, opened_by=cashier_user)
    c = APIClient()
    c.force_authenticate(user=admin_user)
    assert c.delete(f'/api/v1/admin/tables/{virtual.id}/').status_code == 404
    assert c.patch(f'/api/v1/admin/tables/{virtual.id}/', {'is_active': True}, format='json').status_code == 404
    virtual.refresh_from_db()
    assert not virtual.is_active


@pytest.mark.django_db
def test_the_same_virtual_table_is_reused(restaurant, cashier_user):
    create_takeaway_session(restaurant=restaurant, opened_by=cashier_user)
    create_takeaway_session(restaurant=restaurant, opened_by=cashier_user)
    assert RestaurantTable.objects.filter(is_takeaway=True).count() == 1


@pytest.mark.django_db
def test_dine_in_label_is_the_table_name(active_session):
    assert active_session.label == active_session.table.display_name and not active_session.is_takeaway
