"""Bill lifecycle of a table session.

    OPEN --request bill--> BILL_REQUESTED --pay--> PAID --last item served--> CLOSED
                               |  ^
        customer orders again  v  |  request bill again
                          OPEN (bill REOPENED)

* A payment is only taken against a REQUESTED bill (see payments.services.record_cash_payment).
* A customer can only ask for the bill when nothing is left to prepare. A cashier may force it, after the UI
  has listed the unserved items (`UnservedItemsError.items`).
* Paying does not close the session while items still wait to be served. The session closes by itself when
  the last item is served (`settle_paid_session`), which also covers take-away pick-up.
"""
from decimal import Decimal
from typing import Dict, List

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.audit.services import log_audit_event
from apps.ordering.models import ItemStatus, Order, OrderItem, OrderStatus
from apps.outbox.services import create_outbox_event
from apps.tables.models import RestaurantTable, SessionStatus, TableSession, TableStatus

from .models import Bill, BillLine, BillStatus

PENDING_ITEM_STATUSES = (ItemStatus.QUEUED, ItemStatus.COOKING, ItemStatus.READY)
BILLABLE_ORDER_STATUSES = (
    OrderStatus.CONFIRMED, OrderStatus.PREPARING, OrderStatus.READY, OrderStatus.SERVED, OrderStatus.COMPLETED,
)
NOT_READY_MESSAGE = (
    "Ita-boot seidauk bele husu konta tanba seidauk iha pedidu ne'ebé entrega tiha ona ba meza (SERVED)."
)
STILL_COOKING_MESSAGE = (
    "Ita-boot seidauk bele husu konta tanba sei iha hahan ne'ebé seidauk entrega ba meza. "
    "Favór hein to'o hahan hotu to'o meza."
)


class UnservedItemsError(ValidationError):
    """Raised when a bill is requested while items are not served yet. `items` feeds the cashier confirmation."""

    def __init__(self, message, items: List[Dict]):
        super().__init__(message)
        self.items = items


def unserved_items(session: TableSession) -> List[OrderItem]:
    return list(
        OrderItem.objects.filter(
            order__table_session=session, order__status__in=BILLABLE_ORDER_STATUSES,
            status__in=PENDING_ITEM_STATUSES,
        ).select_related('order').order_by('order__created_at', 'created_at')
    )


def billable_lines(session: TableSession) -> List[OrderItem]:
    return list(
        OrderItem.objects.filter(order__table_session=session, order__status__in=BILLABLE_ORDER_STATUSES)
        .exclude(status=ItemStatus.UNAVAILABLE).select_related('order').order_by('order__created_at', 'created_at')
    )


def snapshot_bill(bill: Bill, session: TableSession) -> Bill:
    """(Re)write the bill's lines and totals from the live order lines."""
    from .services import calculate_session_bill
    totals = calculate_session_bill(session)
    bill.lines.all().delete()
    BillLine.objects.bulk_create([
        BillLine(bill=bill, order_item=i, order_code=i.order.order_code, name=i.menu_name_snapshot,
                 quantity=i.quantity, unit_price=i.unit_price, subtotal=i.subtotal, note=(i.note or '')[:500])
        for i in billable_lines(session)
    ])
    bill.subtotal, bill.tax_total = totals['subtotal'], totals['tax_total']
    bill.discount_total, bill.grand_total = totals['discount_total'], totals['grand_total']
    bill.save(update_fields=['subtotal', 'tax_total', 'discount_total', 'grand_total', 'updated_at'])
    return bill


def active_bill(session: TableSession):
    return Bill.objects.filter(table_session=session, status=BillStatus.REQUESTED).first()


def request_bill(*, session: TableSession, requested_by=None, force: bool = False, request_id: str = "") -> Bill:
    """Create (or return) the REQUESTED bill of a session. Customers pass force=False."""
    with transaction.atomic():
        locked = TableSession.objects.select_for_update().get(id=session.id)
        if locked.status not in (SessionStatus.OPEN, SessionStatus.BILL_REQUESTED):
            raise ValidationError("A bill can only be requested for an open table session.")
        existing = active_bill(locked)
        if existing and locked.status == SessionStatus.BILL_REQUESTED:
            return existing

        if Order.objects.filter(table_session=locked, status=OrderStatus.WAITING_CASHIER_CONFIRMATION).exists():
            raise ValidationError(STILL_COOKING_MESSAGE)
        if not billable_lines(locked):
            raise ValidationError(NOT_READY_MESSAGE)
        pending = unserved_items(locked)
        if pending and not force:
            raise UnservedItemsError(STILL_COOKING_MESSAGE, [
                {'id': str(i.id), 'name': i.menu_name_snapshot, 'quantity': i.quantity, 'status': i.status,
                 'order_code': i.order.order_code} for i in pending])

        bill = Bill.objects.create(
            restaurant=locked.restaurant, table_session=locked, status=BillStatus.REQUESTED,
            requested_by=requested_by, forced=bool(pending))
        snapshot_bill(bill, locked)

        locked.status = SessionStatus.BILL_REQUESTED
        locked.bill_requested_at = timezone.now()
        locked.save(update_fields=['status', 'bill_requested_at', 'updated_at'])

        log_audit_event(
            actor_user=requested_by, actor_role=getattr(requested_by, 'role', 'CUSTOMER') if requested_by else 'CUSTOMER',
            action='BILL_REQUEST', entity_type='Bill', entity_id=str(bill.id),
            after_data={'total': str(bill.grand_total), 'forced': bill.forced, 'unserved': len(pending)},
            request_id=request_id)
        create_outbox_event(
            aggregate_type='TABLE_SESSION', aggregate_id=locked.id, event_type='BILL_REQUESTED',
            payload={
                'session_id': str(locked.id), 'bill_id': str(bill.id), 'public_token': locked.public_token,
                'table_code': locked.table.table_code, 'table_name': locked.label,
                'total': str(bill.grand_total), 'forced': bill.forced,
                'requested_by_staff': bool(requested_by),
            })
        return bill


def reopen_bill_if_requested(session: TableSession, *, actor=None, request_id: str = "") -> bool:
    """A guest ordered again after asking for the bill: the bill is withdrawn and the session is open again.

    The caller already holds the session row lock.
    """
    if session.status != SessionStatus.BILL_REQUESTED:
        return False
    bill = active_bill(session)
    if bill:
        bill.status = BillStatus.REOPENED
        bill.reopened_at = timezone.now()
        bill.save(update_fields=['status', 'reopened_at', 'updated_at'])
    session.status = SessionStatus.OPEN
    session.bill_requested_at = None
    session.save(update_fields=['status', 'bill_requested_at', 'updated_at'])
    log_audit_event(
        actor_user=actor, actor_role=getattr(actor, 'role', 'CUSTOMER') if actor else 'CUSTOMER',
        action='BILL_REOPEN', entity_type='Bill', entity_id=str(bill.id) if bill else '',
        after_data={'session': str(session.id)}, request_id=request_id)
    create_outbox_event(
        aggregate_type='TABLE_SESSION', aggregate_id=session.id, event_type='BILL_REOPENED',
        payload={
            'session_id': str(session.id), 'bill_id': str(bill.id) if bill else '',
            'public_token': session.public_token, 'table_code': session.table.table_code,
            'table_name': session.label,
        })
    return True


def settle_paid_session(session_id, *, actor=None, request_id: str = "") -> bool:
    """Close a PAID session once nothing is left to serve. Returns True when it was closed."""
    if not TableSession.objects.filter(id=session_id, status=SessionStatus.PAID).exists():
        return False
    with transaction.atomic():
        locked = TableSession.objects.select_for_update().get(id=session_id)
        if locked.status != SessionStatus.PAID:
            return False
        if OrderItem.objects.filter(
            order__table_session=locked, order__status__in=BILLABLE_ORDER_STATUSES,
            status__in=PENDING_ITEM_STATUSES,
        ).exists():
            return False

        Order.objects.filter(table_session=locked, status__in=BILLABLE_ORDER_STATUSES).update(
            status=OrderStatus.COMPLETED, updated_at=timezone.now())
        now = timezone.now()
        locked.status = SessionStatus.CLOSED
        locked.closed_at = now
        locked.closed_by = actor
        locked.close_reason = "Payment Completed"
        locked.save(update_fields=['status', 'closed_at', 'closed_by', 'close_reason', 'updated_at'])

        table = RestaurantTable.objects.select_for_update().get(id=locked.table_id)
        if not table.is_takeaway:
            table.status = TableStatus.AVAILABLE
            table.save(update_fields=['status', 'updated_at'])

        log_audit_event(
            actor_user=actor, actor_role=getattr(actor, 'role', 'SYSTEM') if actor else 'SYSTEM',
            action='SESSION_SETTLED', entity_type='TableSession', entity_id=str(locked.id),
            after_data={'table_freed': None if table.is_takeaway else table.table_code}, request_id=request_id)
        create_outbox_event(
            aggregate_type='TABLE_SESSION', aggregate_id=locked.id, event_type='SESSION_CLOSED',
            payload={
                'session_id': str(locked.id), 'public_token': locked.public_token, 'table_id': str(table.id),
                'table_code': table.table_code, 'status': locked.status,
            })
        return True
