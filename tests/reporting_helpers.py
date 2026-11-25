"""Data builders shared by the reporting tests."""
import itertools
from datetime import date, datetime, time
from decimal import Decimal

from django.utils import timezone

from apps.ordering.models import Order, OrderItem, OrderStatus, OrderStatusHistory
from apps.payments.models import Payment, PaymentMethod, PaymentStatus

_counter = itertools.count(1)


def local_dt(day: date, hour=12, minute=0):
    """Aware datetime in the restaurant timezone (Asia/Dili)."""
    return timezone.make_aware(datetime.combine(day, time(hour, minute)), timezone.get_current_timezone())


def make_payment(restaurant, session, cashier, amount, *, paid_day, hour=12, minute=0,
                 status=PaymentStatus.COMPLETED, method=PaymentMethod.CASH, void_reason=None, voided_by=None):
    n = next(_counter)
    payment = Payment.objects.create(
        restaurant=restaurant, table_session=session, payment_code=f'PAY-T{n:05d}', method=method,
        amount=Decimal(str(amount)), status=status, received_by=cashier,
        paid_at=local_dt(paid_day, hour, minute), void_reason=void_reason, voided_by=voided_by,
    )
    if status == PaymentStatus.VOIDED:
        # `updated_at` is the void time; auto_now would otherwise stamp the test run time.
        Payment.objects.filter(pk=payment.pk).update(updated_at=local_dt(paid_day, hour, minute))
    return payment


def make_order(restaurant, session, status, total, *, day, created_by=None, confirmed_by=None, hour=12, minute=0,
               source='CUSTOMER'):
    n = next(_counter)
    order = Order.objects.create(
        restaurant=restaurant, table_session=session, order_code=f'ORD-T{n:05d}', status=status,
        idempotency_key=f'idem-{n}', subtotal=Decimal(str(total)), grand_total=Decimal(str(total)),
        created_by=created_by, confirmed_by=confirmed_by, source=source,
    )
    Order.objects.filter(pk=order.pk).update(created_at=local_dt(day, hour, minute))
    return order


def add_item(order, name, qty, unit_price, menu_item=None):
    return OrderItem.objects.create(
        order=order, menu_item=menu_item, menu_name_snapshot=name, unit_price=Decimal(str(unit_price)),
        quantity=qty, subtotal=Decimal(str(unit_price)) * qty,
    )


def make_history(order, to_status, actor, reason='', *, day):
    entry = OrderStatusHistory.objects.create(
        order=order, from_status=OrderStatus.WAITING_CASHIER_CONFIRMATION, to_status=to_status,
        actor_user=actor, reason=reason,
    )
    OrderStatusHistory.objects.filter(pk=entry.pk).update(created_at=local_dt(day))
    return entry
