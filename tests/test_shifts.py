"""Cashier shifts: opening cash, payments need a shift, closing count and difference."""
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from rest_framework.test import APIClient

from apps.ordering.models import OrderSource
from apps.ordering.services import submit_customer_order
from apps.payments.billing import request_bill
from apps.payments.models import Payment, PaymentMethod, PaymentStatus
from apps.payments.services import record_cash_payment
from apps.shifts.models import Shift, ShiftStatus
from apps.shifts.services import close_shift, current_shift, open_shift, shift_figures

pytestmark = [pytest.mark.shift_required, pytest.mark.django_db]


def billed(session, item, user, key='a', qty=1):
    submit_customer_order(table_session=session, idempotency_key=key, source=OrderSource.CASHIER, created_by=user,
                          items_data=[{'menu_item_id': item.id, 'quantity': qty}])
    request_bill(session=session, requested_by=user, force=True)
    return session


def api(user):
    c = APIClient()
    c.force_authenticate(user=user)
    return c


def test_payment_without_a_shift_is_refused(active_session, menu_item_fish, cashier_user):
    billed(active_session, menu_item_fish, cashier_user)
    with pytest.raises(ValidationError) as exc:
        record_cash_payment(session=active_session, cashier_user=cashier_user, tendered_amount=Decimal('10'))
    assert 'shift' in str(exc.value)


def test_payment_is_linked_to_the_open_shift(active_session, menu_item_fish, cashier_user):
    shift = open_shift(cashier=cashier_user, opening_cash='50')
    billed(active_session, menu_item_fish, cashier_user)
    p = record_cash_payment(session=active_session, cashier_user=cashier_user, tendered_amount=Decimal('10'))
    assert p.shift_id == shift.id


def test_one_open_shift_per_cashier_and_cashiers_are_separate(cashier_user, cashier_two, restaurant):
    open_shift(cashier=cashier_user, opening_cash='10')
    with pytest.raises(ValidationError):
        open_shift(cashier=cashier_user, opening_cash='10')
    other = open_shift(cashier=cashier_two, opening_cash='20')
    assert current_shift(cashier_two).id == other.id
    assert Shift.objects.filter(status=ShiftStatus.OPEN).count() == 2


def test_expected_cash_ignores_voided_and_legacy_non_cash(active_session, session_two, menu_item_fish, cashier_user):
    shift = open_shift(cashier=cashier_user, opening_cash='50.00')
    billed(active_session, menu_item_fish, cashier_user, qty=2)              # 13.00 cash
    record_cash_payment(session=active_session, cashier_user=cashier_user, tendered_amount=Decimal('20'))
    Payment.objects.create(restaurant=active_session.restaurant, table_session=session_two, payment_code='OLD-1',
                           method=PaymentMethod.MANUAL_OTHER, amount=Decimal('9'), status=PaymentStatus.COMPLETED,
                           received_by=cashier_user, shift=shift)
    Payment.objects.create(restaurant=active_session.restaurant, table_session=session_two, payment_code='VOID-1',
                           method=PaymentMethod.CASH, amount=Decimal('4'), status=PaymentStatus.VOIDED,
                           received_by=cashier_user, shift=shift)
    figures = shift_figures(shift)
    assert figures['cash_sales'] == Decimal('13.00') and figures['expected_cash'] == Decimal('63.00')
    assert figures['payments_count'] == 1


def test_close_with_exact_count_needs_no_note(cashier_user, restaurant):
    shift = open_shift(cashier=cashier_user, opening_cash='25')
    closed = close_shift(shift=shift, counted_cash='25.00')
    assert closed.status == ShiftStatus.CLOSED and closed.difference == Decimal('0.00')


def test_a_difference_needs_a_note(cashier_user, restaurant):
    shift = open_shift(cashier=cashier_user, opening_cash='25')
    with pytest.raises(ValidationError):
        close_shift(shift=shift, counted_cash='20')
    closed = close_shift(shift=shift, counted_cash='20', note='kembalian salah')
    assert closed.difference == Decimal('-5.00') and closed.closing_note == 'kembalian salah'


def test_closing_twice_is_refused(cashier_user, restaurant):
    shift = open_shift(cashier=cashier_user, opening_cash='5')
    close_shift(shift=shift, counted_cash='5')
    with pytest.raises(ValidationError):
        close_shift(shift=shift, counted_cash='5')


def test_cannot_close_with_my_requested_bill_unpaid(active_session, menu_item_fish, cashier_user):
    shift = open_shift(cashier=cashier_user, opening_cash='0')
    billed(active_session, menu_item_fish, cashier_user)
    with pytest.raises(ValidationError) as exc:
        close_shift(shift=shift, counted_cash='0')
    assert 'unpaid' in str(exc.value)


@pytest.mark.parametrize('bad', ['-1', 'abc', ''])
def test_invalid_amounts(cashier_user, restaurant, bad):
    with pytest.raises(ValidationError):
        open_shift(cashier=cashier_user, opening_cash=bad)


def test_api_open_status_close(cashier_user, restaurant):
    c = api(cashier_user)
    assert c.get('/api/v1/cashier/shift/').json()['data']['current'] is None
    res = c.post('/api/v1/cashier/shift/open/', {'opening_cash': '30'}, format='json')
    assert res.status_code == 200 and res.json()['data']['expected_cash'] == '30.00'
    assert c.post('/api/v1/cashier/shift/open/', {'opening_cash': '30'}, format='json').status_code == 400
    res = c.post('/api/v1/cashier/shift/close/', {'counted_cash': '28', 'note': 'kurang'}, format='json')
    assert res.status_code == 200 and res.json()['data']['difference'] == '-2.00'
    data = c.get('/api/v1/cashier/shift/').json()['data']
    assert data['current'] is None and data['last_closed']['counted_cash'] == '28.00'
    assert c.post('/api/v1/cashier/shift/close/', {'counted_cash': '1'}, format='json').status_code == 404


def test_api_permissions(kitchen_user):
    assert api(kitchen_user).get('/api/v1/cashier/shift/').status_code == 403
    assert api(kitchen_user).post('/api/v1/cashier/shift/open/', {'opening_cash': '1'}, format='json').status_code == 403
