from django.db import transaction
from django.utils import timezone
from django.core.exceptions import ValidationError
from .models import RestaurantTable, TableSession, TableStatus, SessionStatus, TableActivationRequest, ActivationRequestStatus

ACTIVE_SESSION_STATUSES = [
    SessionStatus.OPEN,
    SessionStatus.BILL_REQUESTED,
    SessionStatus.PAYMENT_PENDING,
    SessionStatus.PAID,
]

def open_table_session(*, table: RestaurantTable, opened_by, guest_count: int = 1, request_id: str = "", device_token: str = "") -> TableSession:
    """
    Opens a new dine-in table session atomically.
    Ensures that only ONE active session exists per table.
    Links and approves any pending TableActivationRequest for this table.
    """
    with transaction.atomic():
        locked_table = RestaurantTable.objects.select_for_update().get(id=table.id)

        if locked_table.is_takeaway:
            raise ValidationError("Take-away orders get their own session (create_takeaway_session).")
        if not locked_table.is_active:
            raise ValidationError("Table is not active.")
        from .device import guest_limit
        if guest_count > guest_limit(locked_table):
            raise ValidationError(
                f"{locked_table.display_name}: maximum {guest_limit(locked_table)} guests (table capacity).")
        if locked_table.status == TableStatus.MAINTENANCE:
            raise ValidationError("Table is currently under maintenance.")
            
        existing_active = TableSession.objects.filter(
            table=locked_table,
            status__in=ACTIVE_SESSION_STATUSES
        ).first()
        
        if existing_active:
            raise ValidationError(f"Table already has an active session (Session ID: {existing_active.id}).")
            
        # Check and resolve any pending activation requests for this table
        pending_reqs = TableActivationRequest.objects.filter(
            table=locked_table,
            status=ActivationRequestStatus.PENDING
        )
        resolved_device_token = device_token
        for req in pending_reqs:
            req.status = ActivationRequestStatus.APPROVED
            req.resolved_at = timezone.now()
            req.resolved_by = opened_by
            req.save(update_fields=['status', 'resolved_at', 'resolved_by'])
            if not resolved_device_token and req.device_token:
                resolved_device_token = req.device_token
            if guest_count <= 1 and 1 < req.guest_count <= guest_limit(locked_table):
                guest_count = req.guest_count

        session = TableSession.objects.create(
            restaurant=locked_table.restaurant,
            table=locked_table,
            opened_by=opened_by,
            guest_count=max(1, guest_count),
            status=SessionStatus.OPEN,
            primary_device_token=resolved_device_token or None,
            authorized_device_tokens=[resolved_device_token] if resolved_device_token else []
        )
        
        locked_table.status = TableStatus.OCCUPIED
        locked_table.save(update_fields=['status', 'updated_at'])
        
        # Outbox & Audit integration
        from apps.audit.services import log_audit_event
        from apps.outbox.services import create_outbox_event
        
        log_audit_event(
            actor_user=opened_by,
            actor_role=getattr(opened_by, 'role', 'CASHIER'),
            action='TABLE_OPEN',
            entity_type='TableSession',
            entity_id=str(session.id),
            after_data={'table_code': locked_table.table_code, 'guest_count': session.guest_count},
            request_id=request_id
        )
        
        create_outbox_event(
            aggregate_type='TABLE_SESSION',
            aggregate_id=session.id,
            event_type='SESSION_OPENED',
            payload={
                'session_id': str(session.id),
                'public_token': session.public_token,
                'table_id': str(locked_table.id),
                'table_code': locked_table.table_code,
                'table_name': locked_table.display_name,
                'qr_token': locked_table.qr_token,
                'status': session.status,
            }
        )
        
        return session


def request_table_activation(*, table: RestaurantTable, device_token: str = "", guest_count: int = 2) -> TableActivationRequest:
    """
    Submits a table activation request from customer scanning an unopened/locked table QR.
    Emits TABLE_ACTIVATION_REQUESTED outbox event to notify cashier immediately.
    """
    with transaction.atomic():
        locked_table = RestaurantTable.objects.select_for_update().get(id=table.id)
        if not locked_table.is_active:
            raise ValidationError("Meza la ativu.")
        from .device import guest_limit
        if guest_count > guest_limit(locked_table):
            raise ValidationError(f"Maximum {guest_limit(locked_table)} guests for this table.")
        if locked_table.status == TableStatus.MAINTENANCE:
            raise ValidationError("Meza iha manutensaun nia laran.")

        # If table already has an active session, return None
        existing_active = TableSession.objects.filter(
            table=locked_table,
            status__in=ACTIVE_SESSION_STATUSES
        ).first()
        if existing_active:
            return None

        # Re-use or update existing pending request
        req = TableActivationRequest.objects.filter(
            table=locked_table,
            status=ActivationRequestStatus.PENDING
        ).first()

        if req:
            req.requested_at = timezone.now()
            if device_token:
                req.device_token = device_token
            if guest_count > 0:
                req.guest_count = guest_count
            req.save(update_fields=['requested_at', 'device_token', 'guest_count'])
        else:
            req = TableActivationRequest.objects.create(
                restaurant=locked_table.restaurant,
                table=locked_table,
                device_token=device_token or "",
                guest_count=max(1, guest_count),
                status=ActivationRequestStatus.PENDING
            )

        from apps.outbox.services import create_outbox_event
        create_outbox_event(
            aggregate_type='TABLE',
            aggregate_id=locked_table.id,
            event_type='TABLE_ACTIVATION_REQUESTED',
            payload={
                'request_id': str(req.id),
                'table_id': str(locked_table.id),
                'table_code': locked_table.table_code,
                'table_name': locked_table.display_name,
                'guest_count': req.guest_count,
                'device_token': req.device_token,
                'requested_at': req.requested_at.isoformat(),
            }
        )

        return req


def approve_table_activation(*, request_id: str, approved_by, request_id_header: str = "", guest_count=None) -> TableSession:
    """
    Approves a table activation request by opening the table session and claiming the requesting device.
    """
    with transaction.atomic():
        req = TableActivationRequest.objects.select_for_update().get(id=request_id)
        if req.status != ActivationRequestStatus.PENDING:
            existing_active = TableSession.objects.filter(
                table=req.table,
                status__in=ACTIVE_SESSION_STATUSES
            ).first()
            if existing_active:
                return existing_active
            raise ValidationError(f"Pedidu loke meza la válidu tanba status '{req.status}'.")

        if guest_count is not None:
            req.guest_count = guest_count
            req.save(update_fields=['guest_count'])
        session = open_table_session(
            table=req.table,
            opened_by=approved_by,
            guest_count=req.guest_count,
            request_id=request_id_header,
            device_token=req.device_token or ""
        )
        return session


def reject_table_activation(*, request_id: str, rejected_by, reason: str = "") -> TableActivationRequest:
    """
    Rejects a table activation request.
    """
    with transaction.atomic():
        req = TableActivationRequest.objects.select_for_update().get(id=request_id)
        req.status = ActivationRequestStatus.REJECTED
        req.resolved_at = timezone.now()
        req.resolved_by = rejected_by
        req.save(update_fields=['status', 'resolved_at', 'resolved_by'])
        return req


def close_table_session(*, session: TableSession, closed_by, reason: str = "Completed", request_id: str = "") -> TableSession:
    """
    Closes an active table session atomically.
    Sets table to AVAILABLE.
    """
    with transaction.atomic():
        locked_session = TableSession.objects.select_for_update().get(id=session.id)
        
        if locked_session.status == SessionStatus.CLOSED:
            return locked_session
            
        locked_session.status = SessionStatus.CLOSED
        locked_session.closed_by = closed_by
        locked_session.closed_at = timezone.now()
        locked_session.close_reason = reason
        locked_session.save(update_fields=['status', 'closed_by', 'closed_at', 'close_reason', 'updated_at'])
        
        table = RestaurantTable.objects.select_for_update().get(id=locked_session.table_id)
        table.status = TableStatus.AVAILABLE
        table.save(update_fields=['status', 'updated_at'])
        
        # Outbox & Audit integration
        from apps.audit.services import log_audit_event
        from apps.outbox.services import create_outbox_event
        
        log_audit_event(
            actor_user=closed_by,
            actor_role=getattr(closed_by, 'role', 'CASHIER') if closed_by else 'SYSTEM',
            action='TABLE_CLOSE',
            entity_type='TableSession',
            entity_id=str(locked_session.id),
            after_data={'close_reason': reason},
            request_id=request_id
        )
        
        create_outbox_event(
            aggregate_type='TABLE_SESSION',
            aggregate_id=locked_session.id,
            event_type='SESSION_CLOSED',
            payload={
                'session_id': str(locked_session.id),
                'public_token': locked_session.public_token,
                'table_id': str(table.id),
                'table_code': table.table_code,
                'status': locked_session.status,
            }
        )
        
        return locked_session

WALKOUT_REASON_MIN = 3


def close_session_by_staff(*, session: TableSession, closed_by, mode: str = 'normal', reason: str = "",
                           request_id: str = "") -> TableSession:
    """
    The cashier's padlock button.

    * normal  - only when nothing is left to pay (fully paid, or nothing billable was ordered).
    * walkout - the guests left without paying: a reason is required, orders still waiting for the cashier
                are cancelled, the open bill is marked WALKOUT and the remaining balance is kept as the loss.
    Both free the table.
    """
    from apps.ordering.models import Order, OrderStatus
    from apps.ordering.services import cancel_order_by_staff
    from apps.payments.models import Bill, BillStatus
    from apps.payments.services import calculate_session_bill
    from .models import CloseKind

    mode = (mode or 'normal').strip().lower()
    if mode not in ('normal', 'walkout'):
        raise ValidationError("mode must be 'normal' or 'walkout'.")
    reason = (reason or '').strip()

    with transaction.atomic():
        locked = TableSession.objects.select_for_update().get(id=session.id)
        if locked.status in (SessionStatus.CLOSED, SessionStatus.CANCELLED):
            raise ValidationError("This session is already closed.")
        if locked.table.is_takeaway:
            raise ValidationError("Take-away sessions close when the order is handed over.")

        if mode == 'normal':
            remaining = calculate_session_bill(locked)['remaining_balance']
            if remaining > 0:
                err = ValidationError(f"There is still ${remaining} to pay. Take the payment, or close it as "
                                      f"'left without paying'.")
                err.code = 'SESSION_UNPAID'
                raise err
            closed = close_table_session(session=locked, closed_by=closed_by, reason=reason or "Completed",
                                         request_id=request_id)
            TableSession.objects.filter(id=closed.id).update(close_kind=CloseKind.NORMAL)
            closed.close_kind = CloseKind.NORMAL
            return closed

        if len(reason) < WALKOUT_REASON_MIN:
            err = ValidationError("Write why the table is closed without payment.")
            err.code = 'WALKOUT_REASON_REQUIRED'
            raise err
        for order in Order.objects.filter(table_session=locked, status=OrderStatus.WAITING_CASHIER_CONFIRMATION):
            cancel_order_by_staff(order=order, actor_user=closed_by, reason=f"Walkout: {reason}", request_id=request_id)
        bill = calculate_session_bill(locked)
        unpaid = bill['remaining_balance']
        Bill.objects.filter(table_session=locked, status__in=[BillStatus.REQUESTED, BillStatus.REOPENED]) \
            .update(status=BillStatus.WALKOUT, updated_at=timezone.now())

        closed = close_table_session(session=locked, closed_by=closed_by, reason=reason, request_id=request_id)
        TableSession.objects.filter(id=closed.id).update(close_kind=CloseKind.WALKOUT, unpaid_amount=unpaid)
        closed.close_kind = CloseKind.WALKOUT
        closed.unpaid_amount = unpaid

        from apps.audit.services import log_audit_event
        log_audit_event(
            actor_user=closed_by,
            actor_role=getattr(closed_by, 'role', 'CASHIER') if closed_by else 'SYSTEM',
            action='TABLE_WALKOUT',
            entity_type='TableSession',
            entity_id=str(closed.id),
            after_data={'table': closed.table.display_name, 'unpaid_amount': str(unpaid), 'reason': reason,
                        'bill_total': str(bill['grand_total']), 'total_paid': str(bill['total_paid'])},
            request_id=request_id,
        )
        return closed


def request_bill_for_session(*, session: TableSession, requested_by=None, force: bool = False, request_id: str = "") -> TableSession:
    """
    Customer or cashier requests the bill for the table session (creates the Bill, see payments.billing).
    A customer needs every item served; a cashier may pass force=True after confirming the unserved items.
    """
    from apps.payments.billing import request_bill
    request_bill(session=session, requested_by=requested_by, force=force, request_id=request_id)
    return TableSession.objects.get(id=session.id)

def rotate_table_qr(*, table: RestaurantTable, actor_user, request_id: str = "") -> str:
    with transaction.atomic():
        locked_table = RestaurantTable.objects.select_for_update().get(id=table.id)
        old_token = locked_table.qr_token
        new_token = locked_table.rotate_qr()
        
        from apps.audit.services import log_audit_event
        log_audit_event(
            actor_user=actor_user,
            actor_role=getattr(actor_user, 'role', 'ADMIN'),
            action='QR_ROTATE',
            entity_type='RestaurantTable',
            entity_id=str(locked_table.id),
            before_data={'qr_token': old_token},
            after_data={'qr_token': new_token},
            request_id=request_id
        )
        return new_token
