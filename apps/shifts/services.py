"""Cashier shifts. One open shift per cashier; a payment needs one (see payments.services)."""
from decimal import Decimal, InvalidOperation
from typing import Optional

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Count, Sum
from django.utils import timezone

from apps.audit.services import log_audit_event

from .models import Shift, ShiftStatus

CENT = Decimal('0.01')


def _money(value, field: str) -> Decimal:
    try:
        amount = Decimal(str(value)).quantize(CENT)
    except (InvalidOperation, TypeError, ValueError):
        raise ValidationError(f"{field} must be an amount.")
    if amount < 0:
        raise ValidationError(f"{field} cannot be negative.")
    return amount


def current_shift(user) -> Optional[Shift]:
    if user is None or not getattr(user, 'is_authenticated', False):
        return None
    return Shift.objects.filter(cashier=user, status=ShiftStatus.OPEN).first()


def _cash_payments(shift: Shift):
    """Completed cash payments taken in this shift. Voided and old non-cash rows never count as cash."""
    from apps.payments.models import PaymentMethod, PaymentStatus
    return shift.payments.filter(status=PaymentStatus.COMPLETED, method=PaymentMethod.CASH)


def shift_figures(shift: Shift) -> dict:
    agg = _cash_payments(shift).aggregate(total=Sum('amount'), n=Count('id'))
    cash_sales = (agg['total'] or Decimal('0.00')).quantize(CENT)
    expected = (shift.opening_cash + cash_sales).quantize(CENT)
    return {'cash_sales': cash_sales, 'payments_count': agg['n'] or 0, 'expected_cash': expected}


def open_shift(*, cashier, opening_cash, restaurant=None, request_id: str = "") -> Shift:
    from apps.restaurants.models import Restaurant
    amount = _money(opening_cash, "Opening cash")
    restaurant = restaurant or Restaurant.objects.first()
    if current_shift(cashier):
        raise ValidationError("You already have an open shift.")
    try:
        with transaction.atomic():
            shift = Shift.objects.create(restaurant=restaurant, cashier=cashier, opening_cash=amount)
    except IntegrityError:
        raise ValidationError("You already have an open shift.")
    log_audit_event(
        actor_user=cashier, actor_role=getattr(cashier, 'role', 'CASHIER'), action='SHIFT_OPEN',
        entity_type='Shift', entity_id=str(shift.id), after_data={'opening_cash': str(amount)},
        request_id=request_id)
    return shift


def close_shift(*, shift: Shift, counted_cash, note: str = "", actor=None, request_id: str = "") -> Shift:
    from apps.payments.models import BillStatus
    counted = _money(counted_cash, "Counted cash")
    note = (note or '').strip()
    with transaction.atomic():
        locked = Shift.objects.select_for_update().get(id=shift.id)
        if locked.status == ShiftStatus.CLOSED:
            raise ValidationError("This shift is already closed.")
        from apps.payments.models import Bill
        pending = Bill.objects.filter(status=BillStatus.REQUESTED, requested_by=locked.cashier,
                                      requested_at__gte=locked.opened_at).count()
        if pending:
            raise ValidationError(f"{pending} bill(s) you requested are still unpaid. Take the payment first.")
        figures = shift_figures(locked)
        difference = (counted - figures['expected_cash']).quantize(CENT)
        if difference != 0 and not note:
            raise ValidationError("The counted cash differs from the expected cash: add a note.")
        locked.counted_cash = counted
        locked.expected_cash = figures['expected_cash']
        locked.cash_sales = figures['cash_sales']
        locked.payments_count = figures['payments_count']
        locked.difference = difference
        locked.closing_note = note
        locked.status = ShiftStatus.CLOSED
        locked.closed_at = timezone.now()
        locked.save()
        log_audit_event(
            actor_user=actor or locked.cashier, actor_role=getattr(actor or locked.cashier, 'role', 'CASHIER'),
            action='SHIFT_CLOSE', entity_type='Shift', entity_id=str(locked.id),
            after_data={'expected': str(locked.expected_cash), 'counted': str(counted),
                        'difference': str(difference), 'note': note},
            request_id=request_id)
        return locked


def shift_dict(shift: Optional[Shift]) -> Optional[dict]:
    if shift is None:
        return None
    live = shift.status == ShiftStatus.OPEN
    figures = shift_figures(shift) if live else {
        'cash_sales': shift.cash_sales, 'payments_count': shift.payments_count, 'expected_cash': shift.expected_cash}
    name = (getattr(shift.cashier, 'full_name', '') or shift.cashier.username)
    return {
        'id': str(shift.id), 'status': shift.status, 'cashier': name,
        'opened_at': timezone.localtime(shift.opened_at).strftime('%d/%m/%Y %H:%M'),
        'closed_at': timezone.localtime(shift.closed_at).strftime('%d/%m/%Y %H:%M') if shift.closed_at else None,
        'opening_cash': str(shift.opening_cash),
        'cash_sales': str(figures['cash_sales']),
        'payments_count': figures['payments_count'],
        'expected_cash': str(figures['expected_cash']),
        'counted_cash': str(shift.counted_cash) if shift.counted_cash is not None else None,
        'difference': str(shift.difference) if shift.difference is not None else None,
        'closing_note': shift.closing_note,
    }
