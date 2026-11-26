"""Payments are cash only; old non-cash rows stay readable."""
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from rest_framework.test import APIClient

from apps.kitchen.services import change_items_status
from apps.ordering.models import ItemStatus, OrderSource
from apps.ordering.services import submit_customer_order
from apps.payments.models import Payment, PaymentMethod
from apps.payments.services import record_cash_payment


@pytest.fixture
def billable(active_session, menu_item_fish, cashier_user):
    o = submit_customer_order(
        table_session=active_session, idempotency_key='c', source=OrderSource.CASHIER, created_by=cashier_user,
        items_data=[{'menu_item_id': menu_item_fish.id, 'quantity': 1}])
    from apps.payments.billing import request_bill
    request_bill(session=active_session, requested_by=cashier_user, force=True)
    return active_session


@pytest.mark.django_db
def test_service_rejects_non_cash(billable, cashier_user):
    with pytest.raises(ValidationError):
        record_cash_payment(session=billable, cashier_user=cashier_user, tendered_amount=Decimal('10'),
                            method=PaymentMethod.MANUAL_OTHER)
    assert Payment.objects.count() == 0


@pytest.mark.django_db
def test_api_rejects_non_cash_and_defaults_to_cash(billable, cashier_user):
    c = APIClient()
    c.force_authenticate(user=cashier_user)
    url = f'/api/v1/cashier/table-sessions/{billable.id}/payments/'
    assert c.post(url, {'tendered_amount': '10.00', 'method': 'MANUAL_OTHER'}, format='json').status_code == 400
    res = c.post(url, {'tendered_amount': '10.00'}, format='json')
    assert res.status_code in (200, 201), res.content
    assert Payment.objects.get().method == PaymentMethod.CASH


@pytest.mark.django_db
def test_cash_change_is_computed(billable, cashier_user):
    p = record_cash_payment(session=billable, cashier_user=cashier_user, tendered_amount=Decimal('10.00'))
    assert p.amount == Decimal('6.50') and p.change_amount == Decimal('3.50')


@pytest.mark.django_db
def test_legacy_non_cash_payment_is_still_readable(restaurant, active_session, cashier_user):
    from reporting_helpers import make_payment
    from django.utils import timezone
    make_payment(restaurant, active_session, cashier_user, '9.00', paid_day=timezone.localdate(),
                 method=PaymentMethod.MANUAL_OTHER)
    assert Payment.objects.get().get_method_display()


def test_payment_modal_has_no_method_picker():
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    html = (root / 'templates' / 'cashier' / 'dashboard.html').read_text(encoding='utf-8')
    assert 'pos-payment-method' not in html and 'MANUAL_OTHER' not in html
    js = (root / 'static' / 'js' / 'cashier.js').read_text(encoding='utf-8')
    assert 'pos-payment-method' not in js and 'onMethodChange' not in js
