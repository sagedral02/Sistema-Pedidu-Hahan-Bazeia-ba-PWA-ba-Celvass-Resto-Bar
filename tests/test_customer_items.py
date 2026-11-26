"""Customer view: item status in the tracker and API, remaining stock on the menu."""
import re

import pytest
from django.test import Client

from apps.catalog.daily import close_menu_for_today
from apps.kitchen.services import start_item
from apps.kitchen.notices import report_out_of_stock
from apps.ordering.models import ItemStatus, OrderItem, OrderSource
from apps.ordering.services import submit_customer_order


def make(session, lines, key, user=None):
    return submit_customer_order(
        table_session=session, idempotency_key=key, source=OrderSource.CASHIER, created_by=user,
        items_data=[{'menu_item_id': i.id, 'quantity': q} for i, q in lines])


@pytest.fixture
def mixed_order(active_session, menu_item_fish, menu_item_corn, cashier_user, kitchen_user):
    o = make(active_session, [(menu_item_fish, 2), (menu_item_corn, 1)], 'm', cashier_user)
    start_item(item=OrderItem.objects.get(order=o, menu_item=menu_item_fish), staff_user=kitchen_user)
    report_out_of_stock(item=OrderItem.objects.get(order=o, menu_item=menu_item_corn), staff_user=kitchen_user, reason='habis')
    o.refresh_from_db()
    return o


@pytest.mark.django_db
def test_order_api_exposes_each_item_status(mixed_order, active_session):
    res = Client().get(f'/api/v1/public/sessions/{active_session.public_token}/orders/{mixed_order.order_code}/')
    assert res.status_code == 200
    items = {i['menu_name_snapshot']: i for i in res.json()['data']['items']}
    assert items['Ikan Saboko']['status'] == ItemStatus.COOKING
    assert items["Batar Da'an"]['status'] == ItemStatus.UNAVAILABLE
    assert items["Batar Da'an"]['unavailable_reason'] == 'habis'
    assert res.json()['data']['grand_total'] == '13.00'


@pytest.mark.django_db
def test_tracker_shows_status_per_item_and_marks_unavailable_as_not_billed(mixed_order, table_one):
    html = Client().get(f'/t/{table_one.qr_token}/order/{mixed_order.order_code}/').content.decode()
    assert len(re.findall(r'<div class="cart-line[^"]*" data-line-id=', html)) == 2
    assert 'data-i18n="cust_item_COOKING"' in html
    assert 'data-i18n="cust_item_UNAVAILABLE"' in html
    assert 'data-i18n="cust_item_not_billed"' in html
    unavailable = re.search(r'data-line-status="UNAVAILABLE".*?</div>\s*</div>', html, re.S)
    assert unavailable and 'is-unavailable' in html
    assert '$13.00' in html and 'id="order-grand-total"' in html


@pytest.mark.django_db
def test_tracker_updates_items_in_place(mixed_order, table_one):
    html = Client().get(f'/t/{table_one.qr_token}/order/{mixed_order.order_code}/').content.decode()
    assert '/api/v1/public/sessions/' in html and 'data-line-id' in html
    assert 'refresh(' in html


@pytest.mark.django_db
def test_menu_page_shows_remaining_and_sold_out(client, table_one, active_session, menu_item_fish, menu_item_corn, cashier_user):
    menu_item_fish.daily_quota = 4
    menu_item_fish.save()
    make(active_session, [(menu_item_fish, 1)], 'a', cashier_user)
    html = client.get(f'/t/{table_one.qr_token}/').content.decode()
    fish_card = re.search(r'<button[^>]*data-item-name="Ikan Saboko"[^>]*>', html, re.S).group(0)
    assert 'data-remaining="3"' in fish_card and 'data-available="1"' in fish_card
    assert 'cust_stock_left' in html
    corn_card = re.search(r'<button[^>]*data-item-name="Batar Da\'an"[^>]*>|<button[^>]*data-item-name="Batar Da&#x27;an"[^>]*>', html, re.S).group(0)
    assert 'data-remaining=""' in corn_card


@pytest.mark.django_db
def test_menu_page_flags_a_closed_dish_as_sold_out(client, table_one, active_session, menu_item_fish, cashier_user):
    close_menu_for_today(menu_item=menu_item_fish, staff_user=cashier_user)
    html = client.get(f'/t/{table_one.qr_token}/').content.decode()
    fish_card = re.search(r'<button[^>]*data-item-name="Ikan Saboko"[^>]*>', html, re.S).group(0)
    assert 'data-available="0"' in fish_card and 'is-unavailable' in fish_card


def test_customer_script_limits_the_cart_to_remaining_stock():
    from pathlib import Path
    js = (Path(__file__).resolve().parent.parent / 'static' / 'js' / 'customer.js').read_text(encoding='utf-8')
    for needle in ('maxAddable', 'cust_max_portions', 'refreshMenu', 'ITEM_STATUS_CHANGED', 'cust_item_'):
        assert needle in js, needle
