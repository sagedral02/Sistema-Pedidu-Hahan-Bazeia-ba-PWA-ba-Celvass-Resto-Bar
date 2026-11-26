"""Admin Menu & Stock: rating column, latest reviews per dish and inline daily quota."""
import pytest
from rest_framework.test import APIClient

from apps.audit.models import AuditEvent
from apps.catalog.models import MenuRating
from apps.catalog.ratings import recompute_item_rating
from apps.ordering.models import OrderSource
from apps.ordering.services import submit_customer_order


def api(user):
    c = APIClient()
    if user is not None:
        c.force_authenticate(user=user)
    return c


def quota_url(item):
    return f'/api/v1/admin/menu-items/{item.id}/quota/'


def reviews_url(item):
    return f'/api/v1/admin/menu-items/{item.id}/reviews/'


@pytest.fixture
def rated_fish(menu_item_fish, active_session, session_two):
    MenuRating.objects.create(menu_item=menu_item_fish, table_session=active_session, stars=5, comment='Diak <b>loos</b>')
    MenuRating.objects.create(menu_item=menu_item_fish, table_session=session_two, stars=4)
    recompute_item_rating(menu_item_fish.id)
    menu_item_fish.refresh_from_db()
    return menu_item_fish


# ----------------------------------------------------------------------------- reviews

@pytest.mark.django_db
def test_reviews_endpoint_returns_summary_and_latest(admin_user, rated_fish):
    res = api(admin_user).get(reviews_url(rated_fish))
    assert res.status_code == 200, res.content
    data = res.json()['data']
    assert data['rating_count'] == 2 and data['rating_avg'] == pytest.approx(4.5)
    assert len(data['reviews']) == 2
    assert {r['stars'] for r in data['reviews']} == {4, 5}
    assert all('date' in r and 'comment' in r for r in data['reviews'])
    assert 'device_token' not in data['reviews'][0]
    one = api(admin_user).get(reviews_url(rated_fish) + '?limit=1').json()['data']
    assert len(one['reviews']) == 1


@pytest.mark.django_db
def test_reviews_endpoint_is_admin_only(cashier_user, kitchen_user, rated_fish):
    assert api(None).get(reviews_url(rated_fish)).status_code in (401, 403)
    assert api(cashier_user).get(reviews_url(rated_fish)).status_code == 403
    assert api(kitchen_user).get(reviews_url(rated_fish)).status_code == 403


@pytest.mark.django_db
def test_admin_menu_table_shows_rating_and_review_button(admin_user, rated_fish, menu_item_corn):
    from django.test import Client
    c = Client()
    c.force_login(admin_user)
    res = c.get('/admin-portal/')
    assert res.status_code == 200
    html = res.content.decode()
    table = html.split('id="admin-menu-table"', 1)[1].split('</table>', 1)[0]
    assert 'menu_th_rating' in table
    assert f"openMenuReviews('{rated_fish.id}'" in table and '4.5' in table and '(2)' in table
    assert 'menu_no_rating' in table  # corn has no ratings
    assert 'id="menuReviewsModal"' in html and 'id="menu-form-reviews"' in html


# ----------------------------------------------------------------------------- inline quota

@pytest.mark.django_db
def test_inline_quota_set_and_clear(admin_user, menu_item_fish):
    c = api(admin_user)
    res = c.patch(quota_url(menu_item_fish), {'daily_quota': 12}, format='json')
    assert res.status_code == 200, res.content
    data = res.json()['data']
    assert data['item']['daily_quota'] == 12
    assert data['availability']['quota'] == 12 and data['availability']['remaining'] == 12
    assert data['availability']['state'] == 'AVAILABLE'
    menu_item_fish.refresh_from_db()
    assert menu_item_fish.daily_quota == 12
    assert AuditEvent.objects.filter(action='MENU_UPDATE', after_data__daily_quota=12).exists()

    res = c.patch(quota_url(menu_item_fish), {'daily_quota': None}, format='json')
    assert res.status_code == 200
    assert res.json()['data']['availability']['remaining'] is None
    menu_item_fish.refresh_from_db()
    assert menu_item_fish.daily_quota is None


@pytest.mark.django_db
def test_inline_quota_counts_todays_sales(admin_user, cashier_user, active_session, menu_item_fish):
    submit_customer_order(table_session=active_session, idempotency_key='q1', source=OrderSource.CASHIER,
                          created_by=cashier_user, items_data=[{'menu_item_id': menu_item_fish.id, 'quantity': 3}])
    res = api(admin_user).patch(quota_url(menu_item_fish), {'daily_quota': '3'}, format='json')
    av = res.json()['data']['availability']
    assert av['sold'] == 3 and av['remaining'] == 0 and av['state'] == 'QUOTA_REACHED'


@pytest.mark.django_db
@pytest.mark.parametrize('bad', ['0', '-1', 'abc', '2.5'])
def test_inline_quota_rejects_bad_values(admin_user, menu_item_fish, bad):
    res = api(admin_user).patch(quota_url(menu_item_fish), {'daily_quota': bad}, format='json')
    assert res.status_code == 400
    menu_item_fish.refresh_from_db()
    assert menu_item_fish.daily_quota is None


@pytest.mark.django_db
def test_inline_quota_requires_the_field_and_admin(admin_user, cashier_user, menu_item_fish):
    assert api(admin_user).patch(quota_url(menu_item_fish), {}, format='json').status_code == 400
    assert api(cashier_user).patch(quota_url(menu_item_fish), {'daily_quota': 5}, format='json').status_code == 403
    assert api(None).patch(quota_url(menu_item_fish), {'daily_quota': 5}, format='json').status_code in (401, 403)
    menu_item_fish.refresh_from_db()
    assert menu_item_fish.daily_quota is None


@pytest.mark.django_db
def test_quota_columns_visible_on_mobile(admin_user, menu_item_fish):
    from django.test import Client
    menu_item_fish.daily_quota = 7
    menu_item_fish.save()
    c = Client()
    c.force_login(admin_user)
    html = c.get('/admin-portal/').content.decode()
    table = html.split('id="admin-menu-table"', 1)[1].split('</table>', 1)[0]
    assert f"saveInlineQuota('{menu_item_fish.id}'" in table
    assert 'name="daily_quota" value="7"' in table
    for key in ('stock_th_quota', 'stock_th_sold', 'stock_th_left'):
        assert f'data-i18n-label="{key}"' in table
    # The quota/sold/left cells are no longer hidden on phones.
    assert 'd-none d-md-table-cell' not in table.split('<tbody>', 1)[1].replace(
        'text-end tabular fw-semibold d-none d-md-table-cell', '')
