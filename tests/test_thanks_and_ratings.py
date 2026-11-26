"""After payment: thank-you page (no new opening request) and dish ratings."""
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from django.test import Client
from django.utils import timezone

from apps.catalog.highlights import catalog_highlights, most_ordered, top_rated
from apps.catalog.models import MenuRating
from apps.kitchen.notices import report_out_of_stock
from apps.kitchen.services import change_items_status
from apps.ordering.models import ItemStatus, OrderItem, OrderSource
from apps.ordering.services import submit_customer_order
from apps.payments.billing import request_bill
from apps.payments.services import record_cash_payment
from apps.tables.models import SessionStatus, TableActivationRequest, TableSession

ROOT = Path(__file__).resolve().parent.parent
DEVICE = 'phone-abc'


def guest():
    c = Client()
    c.cookies['celvass_device_id'] = DEVICE
    return c


@pytest.fixture
def paid(active_session, menu_item_fish, menu_item_corn, cashier_user, kitchen_user):
    """A table owned by DEVICE that ate fish (corn ran out), paid and was closed."""
    active_session.primary_device_token = DEVICE
    active_session.authorized_device_tokens = [DEVICE]
    active_session.save()
    o = submit_customer_order(table_session=active_session, idempotency_key='t1', source=OrderSource.CASHIER,
                              created_by=cashier_user,
                              items_data=[{'menu_item_id': menu_item_fish.id, 'quantity': 2},
                                          {'menu_item_id': menu_item_corn.id, 'quantity': 1}])
    report_out_of_stock(item=OrderItem.objects.get(order=o, menu_item=menu_item_corn), staff_user=kitchen_user)
    for step in (ItemStatus.COOKING, ItemStatus.READY, ItemStatus.SERVED):
        change_items_status(order=o, to_status=step, staff_user=kitchen_user)
    request_bill(session=active_session)
    record_cash_payment(session=active_session, cashier_user=cashier_user, tendered_amount=Decimal('20'))
    active_session.refresh_from_db()
    assert active_session.status == SessionStatus.CLOSED
    return active_session


def thanks_url(session):
    return f'/t/{session.table.qr_token}/thanks/{session.public_token}/'


def ratings_url(session):
    return f'/api/v1/public/sessions/{session.public_token}/ratings/'


# ----------------------------------------------------------------------------- thank-you page

@pytest.mark.django_db
def test_thanks_page_for_the_owning_device(paid):
    res = guest().get(thanks_url(paid))
    assert res.status_code == 200
    html = res.content.decode()
    assert 'Ikan Saboko' in html and 'data-menu-item=' in html and "Batar Da" not in html.split('rate-list', 1)[1].split('</section>', 1)[0]
    assert 'Thanks.init' in html


@pytest.mark.django_db
def test_thanks_page_redirects_other_devices_and_expired_or_active_sessions(paid, session_two):
    other = Client()
    other.cookies['celvass_device_id'] = 'someone-else'
    assert other.get(thanks_url(paid)).status_code == 302
    TableSession.objects.filter(id=paid.id).update(closed_at=timezone.now() - timedelta(hours=7))
    assert guest().get(thanks_url(paid)).status_code == 302
    assert guest().get(thanks_url(session_two)).status_code == 302  # still open


@pytest.mark.django_db
def test_returning_to_the_qr_after_paying_does_not_request_a_new_session(paid):
    html = guest().get(f'/t/{paid.table.qr_token}/').content.decode()
    assert 'id="recent-session-box"' in html and thanks_url(paid) in html
    assert 'guest-step d-none' in html
    assert TableActivationRequest.objects.count() == 0


def test_customer_script_never_requests_activation_on_load():
    js = (ROOT / 'static' / 'js' / 'customer.js').read_text(encoding='utf-8')
    flow = js.split('startActivationFlow() {', 1)[1].split('\n  }', 1)[0]
    assert 'requestTableActivation' not in flow
    assert 'goToThanks' in js and "guest_count: guests" in js
    assert 'guest_count: 2' not in js


# ----------------------------------------------------------------------------- ratings

@pytest.mark.django_db
def test_rate_only_received_dishes_and_resubmit_updates(paid, menu_item_fish, menu_item_corn):
    c = guest()
    listed = c.get(ratings_url(paid)).json()['data']
    assert [r['name'] for r in listed] == ['Ikan Saboko']
    bad = c.post(ratings_url(paid), {'ratings': [{'menu_item_id': str(menu_item_corn.id), 'stars': 5}]}, content_type='application/json')
    assert bad.status_code == 400
    ok = c.post(ratings_url(paid), {'ratings': [{'menu_item_id': str(menu_item_fish.id), 'stars': 4, 'comment': ' enak  sekali '}]},
                content_type='application/json')
    assert ok.status_code == 200
    c.post(ratings_url(paid), {'ratings': [{'menu_item_id': str(menu_item_fish.id), 'stars': 2}]}, content_type='application/json')
    assert MenuRating.objects.count() == 1
    menu_item_fish.refresh_from_db()
    assert menu_item_fish.rating_count == 1 and menu_item_fish.rating_avg == Decimal('2.00')


@pytest.mark.django_db
@pytest.mark.parametrize('stars', [0, 6, 'x', None])
def test_invalid_stars(paid, menu_item_fish, stars):
    res = guest().post(ratings_url(paid), {'ratings': [{'menu_item_id': str(menu_item_fish.id), 'stars': stars}]},
                       content_type='application/json')
    assert res.status_code == 400


@pytest.mark.django_db
def test_ratings_refused_for_other_devices_active_and_expired_sessions(paid, session_two, menu_item_fish):
    other = Client()
    other.cookies['celvass_device_id'] = 'intruder'
    body = {'ratings': [{'menu_item_id': str(menu_item_fish.id), 'stars': 5}]}
    assert other.post(ratings_url(paid), body, content_type='application/json').status_code == 403
    assert guest().post(ratings_url(session_two), body, content_type='application/json').status_code == 404
    TableSession.objects.filter(id=paid.id).update(closed_at=timezone.now() - timedelta(hours=7))
    assert guest().post(ratings_url(paid), body, content_type='application/json').status_code == 404


# ----------------------------------------------------------------------------- highlights

def rate(item, values):
    item.rating_avg = Decimal(str(sum(values) / len(values))).quantize(Decimal('0.01'))
    item.rating_count = len(values)
    item.save()


@pytest.mark.django_db
def test_top_rated_needs_three_reviews_and_sorts(menu_item_fish, menu_item_corn):
    rate(menu_item_fish, [5, 5])
    assert top_rated() == []
    rate(menu_item_fish, [4, 4, 4])
    rate(menu_item_corn, [5, 5, 5])
    assert [i.name for i in top_rated()] == ["Batar Da'an", 'Ikan Saboko']


@pytest.mark.django_db
def test_most_ordered_skips_cancelled_lines_rejected_orders_and_old_orders(active_session, session_two, menu_item_fish,
                                                                            menu_item_corn, cashier_user, kitchen_user):
    from apps.ordering.models import Order
    from apps.ordering.services import reject_order_by_cashier
    o = submit_customer_order(table_session=active_session, idempotency_key='a', source=OrderSource.CASHIER, created_by=cashier_user,
                              items_data=[{'menu_item_id': menu_item_corn.id, 'quantity': 3}, {'menu_item_id': menu_item_fish.id, 'quantity': 1}])
    report_out_of_stock(item=OrderItem.objects.get(order=o, menu_item=menu_item_corn), staff_user=kitchen_user)
    r = submit_customer_order(table_session=session_two, idempotency_key='b',
                              items_data=[{'menu_item_id': menu_item_corn.id, 'quantity': 5}])
    reject_order_by_cashier(order=r, rejected_by=cashier_user, reason='x')
    ranked = most_ordered()
    assert [i.name for i in ranked] == ['Ikan Saboko'] and ranked[0].ordered_count == 1
    Order.objects.filter(id=o.id).update(created_at=timezone.now() - timedelta(days=31))
    assert most_ordered() == []


@pytest.mark.django_db
def test_highlights_follow_the_category(restaurant, menu_item_fish):
    from apps.catalog.models import Category, MenuItem
    drinks = Category.objects.create(restaurant=restaurant, name='Hemu', slug='drinks', is_active=True)
    tea = MenuItem.objects.create(restaurant=restaurant, category=drinks, name='Teh', slug='teh', price=1, is_featured=True)
    menu_item_fish.is_featured = True
    menu_item_fish.save()
    assert {i.name for i in catalog_highlights()['featured']} == {'Teh', 'Ikan Saboko'}
    assert [i.name for i in catalog_highlights('drinks')['featured']] == ['Teh']


@pytest.mark.django_db
def test_menu_page_renders_highlights_and_stars(client, table_one, menu_item_fish):
    rate(menu_item_fish, [5, 4, 5])
    menu_item_fish.is_featured = True
    menu_item_fish.save()
    html = client.get(f'/t/{table_one.qr_token}/').content.decode()
    assert 'data-highlight-for="all"' in html and 'data-highlight-for="main"' in html
    assert 'data-i18n="cust_hl_top_rated"' in html and 'data-i18n="cust_hl_featured"' in html
    assert 'menu-card-rating' in html and '4.7' in html
