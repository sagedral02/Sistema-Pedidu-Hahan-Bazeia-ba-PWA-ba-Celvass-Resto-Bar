"""Take away end to end: order, kitchen, ready for pick-up, bill, pay, hand over."""
import uuid
from decimal import Decimal

import pytest
from rest_framework.test import APIClient

from apps.kitchen.notices import report_out_of_stock
from apps.kitchen.services import change_items_status
from apps.ordering.models import ItemStatus, Order, OrderItem, OrderStatus
from apps.outbox.models import OutboxEvent
from apps.payments.billing import request_bill
from apps.payments.services import record_cash_payment
from apps.tables.models import SessionStatus, TableStatus

I = ItemStatus


def api(user):
    c = APIClient()
    c.force_authenticate(user=user)
    return c


def takeaway(cashier_user, lines):
    res = api(cashier_user).post('/api/v1/cashier/orders/create/', {
        'destination': 'TAKEAWAY', 'customer_name': 'Ana',
        'items': [{'menu_item_id': str(m.id), 'quantity': q} for m, q in lines]},
        format='json', HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()))
    assert res.status_code == 201, res.content
    return Order.objects.get(id=res.json()['data']['id'])


def cook(order, kitchen_user):
    change_items_status(order=order, to_status=I.COOKING, staff_user=kitchen_user)
    change_items_status(order=order, to_status=I.READY, staff_user=kitchen_user)


@pytest.mark.django_db
def test_full_cycle_pay_then_hand_over(cashier_user, kitchen_user, menu_item_fish):
    o = takeaway(cashier_user, [(menu_item_fish, 2)])
    session = o.table_session
    cook(o, kitchen_user)
    assert OutboxEvent.objects.filter(event_type='TAKEAWAY_READY').count() == 1

    res = api(cashier_user).post(f'/api/v1/cashier/table-sessions/{session.id}/request-bill/', {}, format='json')
    assert res.status_code == 400 and res.json()['error']['code'] == 'BILL_UNSERVED_ITEMS'
    api(cashier_user).post(f'/api/v1/cashier/table-sessions/{session.id}/request-bill/', {'force': True}, format='json')
    record_cash_payment(session=session, cashier_user=cashier_user, tendered_amount=Decimal('20'))
    session.refresh_from_db()
    assert session.status == SessionStatus.PAID, 'paid but the food is still on the counter'

    res = api(cashier_user).post(f'/api/v1/cashier/table-sessions/{session.id}/hand-over/')
    assert res.status_code == 200 and res.json()['data']['handed_over'] == 1
    session.refresh_from_db()
    assert session.status == SessionStatus.CLOSED
    assert Order.objects.get(id=o.id).status == OrderStatus.COMPLETED
    assert session.table.status == TableStatus.AVAILABLE, 'the virtual table never changes status'


@pytest.mark.django_db
def test_hand_over_before_payment_keeps_the_session_open(cashier_user, kitchen_user, menu_item_fish):
    o = takeaway(cashier_user, [(menu_item_fish, 1)])
    cook(o, kitchen_user)
    api(cashier_user).post(f'/api/v1/cashier/table-sessions/{o.table_session_id}/hand-over/')
    o.table_session.refresh_from_db()
    assert o.table_session.status == SessionStatus.OPEN
    request_bill(session=o.table_session)
    record_cash_payment(session=o.table_session, cashier_user=cashier_user, tendered_amount=Decimal('10'))
    o.table_session.refresh_from_db()
    assert o.table_session.status == SessionStatus.CLOSED


@pytest.mark.django_db
def test_out_of_stock_item_lowers_the_bill_and_can_complete_readiness(cashier_user, kitchen_user, menu_item_fish, menu_item_corn):
    o = takeaway(cashier_user, [(menu_item_fish, 1), (menu_item_corn, 1)])
    fish = OrderItem.objects.get(order=o, menu_item=menu_item_fish)
    change_items_status(order=o, to_status=I.COOKING, item_ids=[fish.id], staff_user=kitchen_user)
    change_items_status(order=o, to_status=I.READY, item_ids=[fish.id], staff_user=kitchen_user)
    assert not OutboxEvent.objects.filter(event_type='TAKEAWAY_READY').exists()
    report_out_of_stock(item=OrderItem.objects.get(order=o, menu_item=menu_item_corn), staff_user=kitchen_user)
    assert OutboxEvent.objects.filter(event_type='TAKEAWAY_READY').exists()
    bill = request_bill(session=o.table_session, requested_by=cashier_user, force=True)
    assert bill.grand_total == Decimal('6.50')


@pytest.mark.django_db
def test_hand_over_rules(cashier_user, kitchen_user, menu_item_fish, active_session):
    o = takeaway(cashier_user, [(menu_item_fish, 1)])
    res = api(cashier_user).post(f'/api/v1/cashier/table-sessions/{o.table_session_id}/hand-over/')
    assert res.status_code == 400, 'nothing ready yet'
    res = api(cashier_user).post(f'/api/v1/cashier/table-sessions/{active_session.id}/hand-over/')
    assert res.status_code == 400, 'dine-in is served by the kitchen'
    assert api(kitchen_user).post(f'/api/v1/cashier/table-sessions/{o.table_session_id}/hand-over/').status_code == 403


@pytest.mark.django_db
def test_kitchen_ticket_carries_the_takeaway_label(cashier_user, kitchen_user, menu_item_fish):
    takeaway(cashier_user, [(menu_item_fish, 1)])
    rows = api(kitchen_user).get('/api/v1/kitchen/orders/').json()['data']
    assert rows[0]['is_takeaway'] is True and rows[0]['table_name'] == 'Take away #001 - Ana'
