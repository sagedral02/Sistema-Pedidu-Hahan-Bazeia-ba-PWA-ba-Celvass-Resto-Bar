"""Bill lifecycle: request, reopen when ordering again, pay, auto-close when everything is served."""
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from rest_framework.test import APIClient

from apps.kitchen.services import change_items_status
from apps.kitchen.notices import report_out_of_stock
from apps.ordering.models import ItemStatus, Order, OrderItem, OrderSource, OrderStatus
from apps.ordering.services import confirm_order_by_cashier, submit_customer_order
from apps.outbox.models import OutboxEvent
from apps.payments.billing import UnservedItemsError, active_bill, request_bill
from apps.payments.models import Bill, BillStatus, Payment
from apps.payments.services import record_cash_payment
from apps.tables.models import SessionStatus, TableStatus

I = ItemStatus


def order(session, lines, key, user=None, source=OrderSource.CASHIER):
    return submit_customer_order(
        table_session=session, idempotency_key=key, source=source, created_by=user,
        items_data=[{'menu_item_id': m.id, 'quantity': q} for m, q in lines])


def serve_all(o, kitchen_user):
    for step in (I.COOKING, I.READY, I.SERVED):
        change_items_status(order=o, to_status=step, staff_user=kitchen_user)


def api(user):
    c = APIClient()
    c.force_authenticate(user=user)
    return c


@pytest.mark.django_db
def test_bill_sums_every_order_and_skips_unavailable_lines(active_session, menu_item_fish, menu_item_corn, cashier_user, kitchen_user):
    o1 = order(active_session, [(menu_item_fish, 2)], 'a', cashier_user)        # 13.00
    o2 = order(active_session, [(menu_item_corn, 1), (menu_item_fish, 1)], 'b', cashier_user)
    report_out_of_stock(item=OrderItem.objects.get(order=o2, menu_item=menu_item_corn), staff_user=kitchen_user)
    serve_all(o1, kitchen_user)
    serve_all(o2, kitchen_user)
    bill = request_bill(session=active_session)
    assert bill.grand_total == Decimal('19.50')
    assert sorted((l.name, l.quantity) for l in bill.lines.all()) == [('Ikan Saboko', 1), ('Ikan Saboko', 2)]
    active_session.refresh_from_db()
    assert active_session.status == SessionStatus.BILL_REQUESTED


@pytest.mark.django_db
def test_customer_cannot_ask_while_items_are_cooking_but_cashier_can_force(active_session, menu_item_fish, cashier_user, kitchen_user):
    o = order(active_session, [(menu_item_fish, 1)], 'a', cashier_user)
    change_items_status(order=o, to_status=I.COOKING, staff_user=kitchen_user)
    with pytest.raises(UnservedItemsError) as exc:
        request_bill(session=active_session)
    assert exc.value.items[0]['name'] == 'Ikan Saboko' and exc.value.items[0]['status'] == I.COOKING
    bill = request_bill(session=active_session, requested_by=cashier_user, force=True)
    assert bill.forced is True


@pytest.mark.django_db
def test_no_bill_without_orders_or_with_a_waiting_order(active_session, menu_item_fish):
    with pytest.raises(ValidationError):
        request_bill(session=active_session)
    order(active_session, [(menu_item_fish, 1)], 'a', source=OrderSource.CUSTOMER)
    with pytest.raises(ValidationError):
        request_bill(session=active_session, force=True)


@pytest.mark.django_db
def test_ordering_again_reopens_the_bill_and_a_new_request_has_the_new_total(active_session, menu_item_fish, menu_item_corn, cashier_user, kitchen_user):
    o1 = order(active_session, [(menu_item_fish, 1)], 'a', cashier_user)
    serve_all(o1, kitchen_user)
    first = request_bill(session=active_session)
    assert first.grand_total == Decimal('6.50')

    order(active_session, [(menu_item_corn, 1)], 'b', cashier_user)
    first.refresh_from_db()
    active_session.refresh_from_db()
    assert first.status == BillStatus.REOPENED
    assert active_session.status == SessionStatus.OPEN
    assert OutboxEvent.objects.filter(event_type='BILL_REOPENED').exists()

    with pytest.raises(ValidationError):
        record_cash_payment(session=active_session, cashier_user=cashier_user, tendered_amount=Decimal('50'))

    second = request_bill(session=active_session, requested_by=cashier_user, force=True)
    assert second.id != first.id and second.grand_total == Decimal('10.00')


@pytest.mark.django_db
def test_payment_without_a_bill_is_refused(active_session, menu_item_fish, cashier_user):
    order(active_session, [(menu_item_fish, 1)], 'a', cashier_user)
    with pytest.raises(ValidationError) as exc:
        record_cash_payment(session=active_session, cashier_user=cashier_user, tendered_amount=Decimal('10'))
    assert 'Request the bill' in str(exc.value)
    assert Payment.objects.count() == 0


@pytest.mark.django_db
def test_paying_a_fully_served_session_closes_it_and_frees_the_table(active_session, menu_item_fish, cashier_user, kitchen_user):
    o = order(active_session, [(menu_item_fish, 1)], 'a', cashier_user)
    serve_all(o, kitchen_user)
    bill = request_bill(session=active_session)
    p = record_cash_payment(session=active_session, cashier_user=cashier_user, tendered_amount=Decimal('10'))
    bill.refresh_from_db()
    active_session.refresh_from_db()
    assert p.bill_id == bill.id and bill.status == BillStatus.PAID
    assert active_session.status == SessionStatus.CLOSED
    assert active_session.table.status == TableStatus.AVAILABLE
    assert Order.objects.get(id=o.id).status == OrderStatus.COMPLETED


@pytest.mark.django_db
def test_paid_session_closes_when_the_last_item_is_served(active_session, menu_item_fish, cashier_user, kitchen_user):
    o = order(active_session, [(menu_item_fish, 1)], 'a', cashier_user)
    request_bill(session=active_session, requested_by=cashier_user, force=True)
    record_cash_payment(session=active_session, cashier_user=cashier_user, tendered_amount=Decimal('10'))
    active_session.refresh_from_db()
    assert active_session.status == SessionStatus.PAID
    active_session.table.refresh_from_db()
    assert active_session.table.status == TableStatus.OCCUPIED
    serve_all(o, kitchen_user)
    active_session.refresh_from_db()
    active_session.table.refresh_from_db()
    assert active_session.status == SessionStatus.CLOSED
    assert active_session.table.status == TableStatus.AVAILABLE


@pytest.mark.django_db
def test_paid_session_refuses_new_orders(active_session, menu_item_fish, cashier_user):
    order(active_session, [(menu_item_fish, 1)], 'a', cashier_user)
    request_bill(session=active_session, requested_by=cashier_user, force=True)
    record_cash_payment(session=active_session, cashier_user=cashier_user, tendered_amount=Decimal('10'))
    with pytest.raises(ValidationError):
        order(active_session, [(menu_item_fish, 1)], 'b', cashier_user)


@pytest.mark.django_db
def test_payment_is_idempotent(active_session, menu_item_fish, cashier_user, kitchen_user):
    o = order(active_session, [(menu_item_fish, 1)], 'a', cashier_user)
    serve_all(o, kitchen_user)
    request_bill(session=active_session)
    p1 = record_cash_payment(session=active_session, cashier_user=cashier_user, tendered_amount=Decimal('10'), idempotency_key='k')
    p2 = record_cash_payment(session=active_session, cashier_user=cashier_user, tendered_amount=Decimal('10'), idempotency_key='k')
    assert p1.id == p2.id and Payment.objects.count() == 1


@pytest.mark.django_db
def test_legacy_bill_requested_session_gets_a_bill_when_paid(active_session, menu_item_fish, cashier_user):
    order(active_session, [(menu_item_fish, 1)], 'a', cashier_user)
    active_session.status = SessionStatus.BILL_REQUESTED
    active_session.save()
    assert Bill.objects.count() == 0
    p = record_cash_payment(session=active_session, cashier_user=cashier_user, tendered_amount=Decimal('10'))
    assert p.bill is not None and p.bill.status == BillStatus.PAID and p.bill.grand_total == Decimal('6.50')


@pytest.mark.django_db
def test_requesting_twice_returns_the_same_bill(active_session, menu_item_fish, cashier_user, kitchen_user):
    o = order(active_session, [(menu_item_fish, 1)], 'a', cashier_user)
    serve_all(o, kitchen_user)
    assert request_bill(session=active_session).id == request_bill(session=active_session).id


# ----------------------------------------------------------------------------- API

@pytest.mark.django_db
def test_cashier_request_bill_api_lists_unserved_items_then_forces(active_session, menu_item_fish, cashier_user):
    order(active_session, [(menu_item_fish, 1)], 'a', cashier_user)
    url = f'/api/v1/cashier/table-sessions/{active_session.id}/request-bill/'
    res = api(cashier_user).post(url, {}, format='json')
    assert res.status_code == 400
    body = res.json()['error']
    assert body['code'] == 'BILL_UNSERVED_ITEMS' and body['details']['items'][0]['name'] == 'Ikan Saboko'
    res = api(cashier_user).post(url, {'force': True}, format='json')
    assert res.status_code == 200 and res.json()['data']['forced'] is True
    assert active_bill(active_session) is not None


@pytest.mark.django_db
def test_cashier_request_bill_api_permissions(active_session, kitchen_user):
    url = f'/api/v1/cashier/table-sessions/{active_session.id}/request-bill/'
    assert api(kitchen_user).post(url, {}, format='json').status_code == 403
    assert APIClient().post(url, {}, format='json').status_code in (401, 403)


@pytest.mark.django_db
def test_public_request_bill_still_works_for_customers(client, active_session, menu_item_fish, cashier_user, kitchen_user):
    o = order(active_session, [(menu_item_fish, 1)], 'a', cashier_user)
    serve_all(o, kitchen_user)
    res = client.post(f'/api/v1/public/sessions/{active_session.public_token}/request-bill/')
    assert res.status_code == 200
    assert active_bill(active_session) is not None
