import pytest
from decimal import Decimal
from apps.tables.models import TableStatus, SessionStatus
from apps.tables.services import open_table_session, close_table_session
from apps.ordering.models import OrderStatus
from apps.ordering.services import submit_customer_order, confirm_order_by_cashier, reject_order_by_cashier
from apps.kitchen.services import start_preparing_order, mark_order_ready, mark_order_served
from apps.payments.services import record_cash_payment

@pytest.mark.django_db
def test_table_and_session_lifecycle(restaurant, table_one, cashier_user):
    assert table_one.status == TableStatus.AVAILABLE
    
    # 1. Open Session
    session = open_table_session(table=table_one, opened_by=cashier_user, guest_count=3)
    table_one.refresh_from_db()
    assert session.status == SessionStatus.OPEN
    assert session.guest_count == 3
    assert table_one.status == TableStatus.OCCUPIED

    # 2. Close Session
    closed_session = close_table_session(session=session, closed_by=cashier_user, reason="Done")
    table_one.refresh_from_db()
    assert closed_session.status == SessionStatus.CLOSED
    assert table_one.status == TableStatus.AVAILABLE

@pytest.mark.django_db
def test_order_lifecycle_happy_path(active_session, menu_item_fish, cashier_user, kitchen_user):
    # 1. Submit Order
    order = submit_customer_order(
        table_session=active_session,
        items_data=[{'menu_item_id': menu_item_fish.id, 'quantity': 2}],
        idempotency_key="idem-key-123"
    )
    assert order.status == OrderStatus.WAITING_CASHIER_CONFIRMATION
    assert order.grand_total == Decimal("13.00")

    # 2. Cashier Confirm
    confirmed = confirm_order_by_cashier(order=order, confirmed_by=cashier_user)
    assert confirmed.status == OrderStatus.CONFIRMED
    assert confirmed.confirmed_by == cashier_user

    # 3. Kitchen Start Cooking
    preparing = start_preparing_order(order=confirmed, staff_user=kitchen_user)
    assert preparing.status == OrderStatus.PREPARING

    # 4. Kitchen Mark Ready
    ready = mark_order_ready(order=preparing, staff_user=kitchen_user)
    assert ready.status == OrderStatus.READY

    # 5. Mark Served
    served = mark_order_served(order=ready, staff_user=cashier_user)
    assert served.status == OrderStatus.SERVED

    # 6. Bill then payment
    from apps.payments.billing import request_bill
    request_bill(session=active_session)
    payment = record_cash_payment(
        session=active_session,
        cashier_user=cashier_user,
        tendered_amount=Decimal("15.00")
    )
    assert payment.amount == Decimal("13.00")
    assert payment.change_amount == Decimal("2.00")
    
    active_session.refresh_from_db()
    order.refresh_from_db()
    active_session.table.refresh_from_db()
    assert active_session.status == SessionStatus.CLOSED
    assert active_session.table.status == TableStatus.AVAILABLE
    assert order.status == OrderStatus.COMPLETED

@pytest.mark.django_db
def test_order_rejection_workflow(active_session, menu_item_fish, cashier_user):
    order = submit_customer_order(
        table_session=active_session,
        items_data=[{'menu_item_id': menu_item_fish.id, 'quantity': 1}],
        idempotency_key="idem-key-reject"
    )
    assert order.status == OrderStatus.WAITING_CASHIER_CONFIRMATION

    rejected = reject_order_by_cashier(order=order, rejected_by=cashier_user, reason="Tamu tidak ada di meja")
    assert rejected.status == OrderStatus.REJECTED
    assert rejected.rejection_reason == "Tamu tidak ada di meja"

@pytest.mark.django_db
def test_bill_request_strictly_requires_served_order(active_session, menu_item_fish, cashier_user, kitchen_user):
    from django.core.exceptions import ValidationError
    from apps.tables.services import request_bill_for_session

    # 1. Calling request_bill_for_session with 0 orders must fail
    with pytest.raises(ValidationError) as exc:
        request_bill_for_session(session=active_session)
    assert "seidauk bele husu konta" in str(exc.value)

    # 2. Place an order, but it is only CONFIRMED / PREPARING -> must still fail
    order = submit_customer_order(
        table_session=active_session,
        items_data=[{'menu_item_id': menu_item_fish.id, 'quantity': 1}],
        idempotency_key="bill-guard-order"
    )
    confirmed = confirm_order_by_cashier(order=order, confirmed_by=cashier_user)
    with pytest.raises(ValidationError) as exc2:
        request_bill_for_session(session=active_session)
    assert "seidauk bele husu konta" in str(exc2.value)

    # 3. Cook and Mark Served
    preparing = start_preparing_order(order=confirmed, staff_user=kitchen_user)
    ready = mark_order_ready(order=preparing, staff_user=kitchen_user)
    served = mark_order_served(order=ready, staff_user=cashier_user)
    assert served.status == OrderStatus.SERVED

    # 4. Now requesting bill MUST SUCCEED
    updated_session = request_bill_for_session(session=active_session)
    assert updated_session.status == SessionStatus.BILL_REQUESTED
    assert updated_session.bill_requested_at is not None
