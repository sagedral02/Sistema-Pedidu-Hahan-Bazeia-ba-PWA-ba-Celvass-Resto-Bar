"""Daily production quota and effective availability of menu items."""
from datetime import datetime, timezone as dt_timezone

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.catalog.availability import (
    AVAILABLE, CLOSED_TODAY, QUOTA_REACHED, SOLD_OUT, availability_map, business_date, business_day_bounds,
    menu_availability,
)
from apps.catalog.models import DailyMenuState, ItemAvailability
from apps.ordering.models import ItemStatus, Order, OrderItem, OrderSource, OrderStatus
from apps.ordering.services import (
    confirm_order_by_cashier, reject_order_by_cashier, submit_customer_order,
)
from apps.kitchen.services import mark_item_unavailable


def order_of(session, item, qty, key, **kw):
    kw.setdefault('source', OrderSource.CASHIER)  # customer orders are limited to one waiting order
    return submit_customer_order(
        table_session=session, items_data=[{'menu_item_id': item.id, 'quantity': qty}],
        idempotency_key=key, **kw)


@pytest.fixture
def fish3(menu_item_fish):
    menu_item_fish.daily_quota = 3
    menu_item_fish.save()
    return menu_item_fish


# ----------------------------------------------------------------------------- availability

@pytest.mark.django_db
def test_no_quota_means_unlimited(menu_item_fish):
    a = menu_availability(menu_item_fish)
    assert a.state == AVAILABLE and a.orderable and a.remaining is None and a.quota is None


@pytest.mark.django_db
def test_remaining_shrinks_with_orders_and_closes_at_zero(active_session, fish3):
    order_of(active_session, fish3, 2, 'a')
    a = menu_availability(fish3)
    assert (a.sold, a.remaining, a.state) == (2, 1, AVAILABLE)
    order_of(active_session, fish3, 1, 'b')
    a = menu_availability(fish3)
    assert (a.sold, a.remaining, a.state) == (3, 0, QUOTA_REACHED) and not a.orderable


@pytest.mark.django_db
def test_order_over_the_remaining_is_refused_with_the_number_left(active_session, fish3):
    order_of(active_session, fish3, 2, 'a')
    with pytest.raises(ValidationError) as exc:
        order_of(active_session, fish3, 2, 'b')
    assert '1 portion' in str(exc.value)
    assert Order.objects.count() == 1


@pytest.mark.django_db
def test_fourth_portion_is_refused_when_quota_used(active_session, fish3):
    order_of(active_session, fish3, 3, 'a')
    with pytest.raises(ValidationError) as exc:
        order_of(active_session, fish3, 1, 'b')
    assert 'quota' in str(exc.value)


@pytest.mark.django_db
def test_split_lines_of_the_same_dish_are_summed(active_session, fish3):
    with pytest.raises(ValidationError):
        submit_customer_order(
            table_session=active_session, idempotency_key='x',
            items_data=[{'menu_item_id': fish3.id, 'quantity': 2}, {'menu_item_id': fish3.id, 'quantity': 2}])


@pytest.mark.django_db
def test_rejected_order_frees_its_portions(active_session, fish3, cashier_user):
    o = order_of(active_session, fish3, 3, 'a', source=OrderSource.CUSTOMER)
    assert menu_availability(fish3).state == QUOTA_REACHED
    reject_order_by_cashier(order=o, rejected_by=cashier_user, reason='fake')
    assert menu_availability(fish3).remaining == 3


@pytest.mark.django_db
def test_unavailable_line_frees_its_portions(active_session, fish3, cashier_user, kitchen_user):
    o = confirm_order_by_cashier(order=order_of(active_session, fish3, 3, 'a'), confirmed_by=cashier_user)
    line = OrderItem.objects.get(order=o)
    mark_item_unavailable(item=line, staff_user=kitchen_user, reason='habis')
    assert menu_availability(fish3).remaining == 3


@pytest.mark.django_db
def test_yesterdays_orders_do_not_count(active_session, fish3):
    o = order_of(active_session, fish3, 3, 'a')
    Order.objects.filter(id=o.id).update(submitted_at=timezone.now() - timezone.timedelta(days=2))
    assert menu_availability(fish3).remaining == 3


@pytest.mark.django_db
def test_closed_today_is_not_orderable_and_reopening_restores(active_session, menu_item_fish):
    state = DailyMenuState.objects.create(menu_item=menu_item_fish, business_date=business_date(), reason='no fish')
    a = menu_availability(menu_item_fish)
    assert a.state == CLOSED_TODAY and a.closed_reason == 'no fish'
    with pytest.raises(ValidationError):
        order_of(active_session, menu_item_fish, 1, 'a')
    state.is_closed = False
    state.save()
    assert menu_availability(menu_item_fish).orderable


@pytest.mark.django_db
def test_a_closed_state_of_another_day_is_ignored(menu_item_fish):
    DailyMenuState.objects.create(menu_item=menu_item_fish, business_date=business_date().replace(year=2020))
    assert menu_availability(menu_item_fish).orderable


@pytest.mark.django_db
def test_permanent_sold_out_stays_sold_out(menu_item_fish, active_session):
    menu_item_fish.availability = ItemAvailability.SOLD_OUT
    menu_item_fish.save()
    assert menu_availability(menu_item_fish).state == SOLD_OUT
    with pytest.raises(ValidationError):
        order_of(active_session, menu_item_fish, 1, 'a')


@pytest.mark.django_db
def test_cashier_orders_use_the_same_quota(active_session, fish3, cashier_user):
    order_of(active_session, fish3, 3, 'a', source=OrderSource.CASHIER, created_by=cashier_user)
    with pytest.raises(ValidationError):
        order_of(active_session, fish3, 1, 'b', source=OrderSource.CASHIER, created_by=cashier_user)


@pytest.mark.django_db
def test_availability_map_matches_single_lookups(active_session, fish3, menu_item_corn):
    order_of(active_session, fish3, 1, 'a')
    got = availability_map([fish3, menu_item_corn])
    assert got[str(fish3.id)] == menu_availability(fish3)
    assert got[str(menu_item_corn.id)] == menu_availability(menu_item_corn)


# ----------------------------------------------------------------------------- business day

def utc(y, m, d, h, mi=0):
    return datetime(y, m, d, h, mi, tzinfo=dt_timezone.utc)


def test_business_day_changes_at_midnight_in_dili():
    # Dili is UTC+9: 14:59 UTC is still 23:59 on the 10th, 15:00 UTC is already the 11th.
    assert business_date(utc(2026, 3, 10, 14, 59)).isoformat() == '2026-03-10'
    assert business_date(utc(2026, 3, 10, 15, 0)).isoformat() == '2026-03-11'


def test_business_day_start_hour_shifts_the_boundary(settings):
    settings.BUSINESS_DAY_START_HOUR = 4
    # 02:00 Dili on the 11th (17:00 UTC on the 10th) still belongs to the 10th.
    assert business_date(utc(2026, 3, 10, 17, 0)).isoformat() == '2026-03-10'
    assert business_date(utc(2026, 3, 10, 19, 0)).isoformat() == '2026-03-11'
    start, end = business_day_bounds(business_date(utc(2026, 3, 10, 19, 0)))
    assert (end - start).total_seconds() == 24 * 3600
    assert timezone.localtime(start).hour == 4


# ----------------------------------------------------------------------------- API

@pytest.mark.django_db
def test_public_menu_reports_remaining_and_state(client, active_session, fish3, category_main):
    order_of(active_session, fish3, 3, 'a')
    res = client.get('/api/v1/public/menu/')
    assert res.status_code == 200
    items = {i['name']: i for c in res.json()['data'] for i in c['items']}
    fish = items['Ikan Saboko']
    assert fish['remaining'] == 0 and fish['orderable'] is False and fish['state'] == QUOTA_REACHED
