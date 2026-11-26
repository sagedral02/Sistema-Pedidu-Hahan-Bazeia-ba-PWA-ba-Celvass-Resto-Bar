"""Closing a menu item for today cascades to queued lines; reopening restores ordering."""
import pytest
from django.core.exceptions import ValidationError

from apps.audit.models import AuditEvent
from apps.catalog.availability import CLOSED_TODAY, menu_availability
from apps.catalog.daily import close_menu_for_today, reopen_menu
from apps.catalog.models import ItemAvailability
from apps.kitchen.services import start_item
from apps.ordering.models import ItemStatus, Order, OrderItem, OrderSource, OrderStatus
from apps.ordering.services import confirm_order_by_cashier, submit_customer_order
from apps.outbox.models import OutboxEvent


def make(session, lines, key, source=OrderSource.CASHIER, user=None):
    return submit_customer_order(
        table_session=session, idempotency_key=key, source=source, created_by=user,
        items_data=[{'menu_item_id': i.id, 'quantity': q} for i, q in lines])


def line(order, menu_item):
    return OrderItem.objects.get(order=order, menu_item=menu_item)


@pytest.mark.django_db
def test_closing_cancels_queued_lines_in_several_orders(active_session, session_two, menu_item_fish, menu_item_corn, cashier_user):
    o1 = make(active_session, [(menu_item_fish, 2), (menu_item_corn, 1)], 'a', user=cashier_user)  # 13.00 + 3.50
    o2 = make(session_two, [(menu_item_fish, 1)], 'b', user=cashier_user)
    result = close_menu_for_today(menu_item=menu_item_fish, staff_user=cashier_user, reason='no fish')
    assert result['cancelled_items'] == 3 and sorted(result['orders']) == sorted([o1.order_code, o2.order_code])
    o1.refresh_from_db(), o2.refresh_from_db()
    assert line(o1, menu_item_fish).status == ItemStatus.UNAVAILABLE
    assert line(o1, menu_item_fish).unavailable_reason == 'no fish'
    assert str(o1.grand_total) == '3.50' and o1.status == OrderStatus.CONFIRMED
    assert o2.status == OrderStatus.CANCELLED
    assert menu_availability(menu_item_fish).state == CLOSED_TODAY


@pytest.mark.django_db
def test_lines_already_cooking_carry_on(active_session, menu_item_fish, cashier_user, kitchen_user):
    o = make(active_session, [(menu_item_fish, 1)], 'a', user=cashier_user)
    start_item(item=line(o, menu_item_fish), staff_user=kitchen_user)
    result = close_menu_for_today(menu_item=menu_item_fish, staff_user=cashier_user)
    assert result['cancelled_items'] == 0
    assert line(o, menu_item_fish).status == ItemStatus.COOKING
    o.refresh_from_db()
    assert str(o.grand_total) == '6.50'


@pytest.mark.django_db
def test_waiting_orders_are_left_for_the_cashier_to_confirm(active_session, menu_item_fish, menu_item_corn, cashier_user):
    o = make(active_session, [(menu_item_fish, 1), (menu_item_corn, 1)], 'a', source=OrderSource.CUSTOMER)
    close_menu_for_today(menu_item=menu_item_fish, staff_user=cashier_user)
    o.refresh_from_db()
    assert o.status == OrderStatus.WAITING_CASHIER_CONFIRMATION
    assert line(o, menu_item_fish).status == ItemStatus.QUEUED
    o = confirm_order_by_cashier(order=o, confirmed_by=cashier_user)
    assert o.status == OrderStatus.CONFIRMED
    assert line(o, menu_item_fish).status == ItemStatus.UNAVAILABLE
    assert line(o, menu_item_corn).status == ItemStatus.QUEUED
    assert str(o.grand_total) == '3.50'
    sent = OutboxEvent.objects.filter(event_type='ORDER_CONFIRMED').latest('created_at').payload
    assert [i['name'] for i in sent['items']] == ["Batar Da'an"], 'the kitchen is not told about the closed dish'


@pytest.mark.django_db
def test_confirm_refuses_an_order_whose_items_are_all_closed(active_session, menu_item_fish, cashier_user):
    o = make(active_session, [(menu_item_fish, 1)], 'a', source=OrderSource.CUSTOMER)
    close_menu_for_today(menu_item=menu_item_fish, staff_user=cashier_user)
    with pytest.raises(ValidationError) as exc:
        confirm_order_by_cashier(order=o, confirmed_by=cashier_user)
    assert 'Ikan Saboko' in str(exc.value)
    o.refresh_from_db()
    assert o.status == OrderStatus.WAITING_CASHIER_CONFIRMATION


@pytest.mark.django_db
def test_reopen_allows_ordering_but_cancelled_lines_stay_cancelled(active_session, session_two, menu_item_fish, cashier_user):
    o = make(active_session, [(menu_item_fish, 1)], 'a', user=cashier_user)
    close_menu_for_today(menu_item=menu_item_fish, staff_user=cashier_user)
    with pytest.raises(ValidationError):
        make(session_two, [(menu_item_fish, 1)], 'b', user=cashier_user)
    assert reopen_menu(menu_item=menu_item_fish, staff_user=cashier_user) is True
    assert menu_availability(menu_item_fish).orderable
    make(session_two, [(menu_item_fish, 1)], 'c', user=cashier_user)
    assert line(o, menu_item_fish).status == ItemStatus.UNAVAILABLE


@pytest.mark.django_db
def test_reopen_when_not_closed_does_nothing(menu_item_fish, cashier_user):
    assert reopen_menu(menu_item=menu_item_fish, staff_user=cashier_user) is False


@pytest.mark.django_db
def test_closing_twice_is_harmless(active_session, menu_item_fish, cashier_user):
    make(active_session, [(menu_item_fish, 1)], 'a', user=cashier_user)
    close_menu_for_today(menu_item=menu_item_fish, staff_user=cashier_user)
    again = close_menu_for_today(menu_item=menu_item_fish, staff_user=cashier_user)
    assert again['cancelled_items'] == 0


@pytest.mark.django_db
def test_inactive_menu_cannot_be_closed(menu_item_fish, cashier_user):
    menu_item_fish.availability = ItemAvailability.INACTIVE
    menu_item_fish.save()
    with pytest.raises(ValidationError):
        close_menu_for_today(menu_item=menu_item_fish, staff_user=cashier_user)


@pytest.mark.django_db
def test_audit_and_events(active_session, menu_item_fish, cashier_user):
    make(active_session, [(menu_item_fish, 2)], 'a', user=cashier_user)
    close_menu_for_today(menu_item=menu_item_fish, staff_user=cashier_user, reason='no fish')
    close = AuditEvent.objects.get(action='MENU_CLOSE_TODAY')
    assert close.after_data['cancelled_portions'] == 2 and close.after_data['reason'] == 'no fish'
    assert AuditEvent.objects.filter(action='ITEM_UNAVAILABLE').count() == 1
    assert OutboxEvent.objects.filter(event_type='MENU_AVAILABILITY_CHANGED', payload__closed=True).exists()
    assert OutboxEvent.objects.filter(event_type='ITEM_STATUS_CHANGED', payload__status='UNAVAILABLE').exists()
    reopen_menu(menu_item=menu_item_fish, staff_user=cashier_user)
    assert AuditEvent.objects.filter(action='MENU_REOPEN').exists()
    assert OutboxEvent.objects.filter(event_type='MENU_AVAILABILITY_CHANGED', payload__closed=False).exists()
