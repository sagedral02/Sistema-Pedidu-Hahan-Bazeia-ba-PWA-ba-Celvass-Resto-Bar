import pytest
from decimal import Decimal
from apps.ordering.services import submit_customer_order
from apps.payments.services import record_cash_payment
from django.core.exceptions import ValidationError
from apps.payments.billing import request_bill

@pytest.mark.django_db
def test_server_authoritative_pricing(active_session, menu_item_fish, menu_item_corn):
    # DB prices: fish = 6.50, corn = 3.50
    # Customer order: 2 fish ($13.00) + 3 corn ($10.50) = $23.50
    order = submit_customer_order(
        table_session=active_session,
        items_data=[
            {'menu_item_id': menu_item_fish.id, 'quantity': 2},
            {'menu_item_id': menu_item_corn.id, 'quantity': 3},
        ],
        idempotency_key="price-calc-test"
    )

    assert order.subtotal == Decimal("23.50")
    assert order.grand_total == Decimal("23.50")

@pytest.mark.django_db
def test_cash_tender_and_change_calculation(active_session, menu_item_fish, cashier_user):
    from apps.ordering.services import confirm_order_by_cashier
    order = submit_customer_order(
        table_session=active_session,
        items_data=[{'menu_item_id': menu_item_fish.id, 'quantity': 2}],
        idempotency_key="pay-calc-1"
    )
    # Confirm order so it is billable
    confirm_order_by_cashier(order=order, confirmed_by=cashier_user)

    request_bill(session=active_session, requested_by=cashier_user, force=True)
    # Bill = $13.00, Tendered = $20.00 -> Change = $7.00
    payment = record_cash_payment(
        session=active_session,
        cashier_user=cashier_user,
        tendered_amount=Decimal("20.00")
    )
    assert payment.amount == Decimal("13.00")
    assert payment.tendered_amount == Decimal("20.00")
    assert payment.change_amount == Decimal("7.00")

@pytest.mark.django_db
def test_insufficient_cash_tender_rejected(active_session, menu_item_fish, cashier_user):
    from apps.ordering.services import confirm_order_by_cashier
    order = submit_customer_order(
        table_session=active_session,
        items_data=[{'menu_item_id': menu_item_fish.id, 'quantity': 2}],
        idempotency_key="pay-calc-insufficient"
    )
    # Confirm order so it is billable
    confirm_order_by_cashier(order=order, confirmed_by=cashier_user)

    request_bill(session=active_session, requested_by=cashier_user, force=True)
    # Bill = $13.00, Tendered = $10.00 -> Must raise ValidationError
    with pytest.raises(ValidationError) as exc:
        record_cash_payment(
            session=active_session,
            cashier_user=cashier_user,
            tendered_amount=Decimal("10.00")
        )
    assert "less than bill total" in str(exc.value)

