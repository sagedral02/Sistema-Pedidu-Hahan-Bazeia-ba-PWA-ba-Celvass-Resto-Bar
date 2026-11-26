import uuid
import logging
from django.db import transaction
from django.utils import timezone
from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync
from .models import OutboxEvent, OutboxStatus

logger = logging.getLogger(__name__)

# Broadcast routing. Customers are reached through the session token in the payload, whatever the event.
# New events must be listed here, otherwise they are stored but never delivered.
CASHIER_EVENTS = frozenset([
    'ORDER_CREATED', 'BILL_REQUESTED', 'SESSION_OPENED', 'SESSION_CLOSED', 'PAYMENT_COMPLETED',
    'TABLE_ACTIVATION_REQUESTED', 'ITEM_STATUS_CHANGED', 'MENU_AVAILABILITY_CHANGED',
    'KITCHEN_NOTICE', 'KITCHEN_NOTICE_UPDATED', 'BILL_REOPENED', 'TAKEAWAY_READY',
])
KITCHEN_EVENTS = frozenset([
    'ORDER_CONFIRMED', 'ORDER_PREPARING', 'ORDER_READY', 'ORDER_SERVED', 'ORDER_CANCELLED', 'ITEM_STATUS_CHANGED', 'MENU_AVAILABILITY_CHANGED',
])

def create_outbox_event(
    *,
    aggregate_type: str,
    aggregate_id: uuid.UUID,
    event_type: str,
    payload: dict,
    event_version: int = 1
) -> OutboxEvent:
    """
    Creates an OutboxEvent row inside the active transaction.
    Schedules immediate dispatch after transaction commit.
    """
    event = OutboxEvent.objects.create(
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        event_type=event_type,
        payload=payload,
        event_version=event_version,
        status=OutboxStatus.PENDING,
        next_attempt_at=timezone.now()
    )
    
    # Trigger dispatch immediately upon transaction commit
    transaction.on_commit(lambda: dispatch_single_event(event.id))
    # Web Push (phones with the app closed); a no-op when VAPID keys are not configured.
    transaction.on_commit(lambda: _push(event_type, payload))
    return event


def _push(event_type, payload):
    try:
        from apps.pwa.push import notify_for_event
        notify_for_event(event_type, payload)
    except Exception as exc:  # push must never break the business transaction
        logger.error(f"Web push for {event_type} failed: {exc}")

def dispatch_single_event(event_id: uuid.UUID):
    """
    Dispatches a single outbox event to the relevant Channels WebSocket groups.
    """
    try:
        event = OutboxEvent.objects.get(id=event_id, status=OutboxStatus.PENDING)
    except OutboxEvent.DoesNotExist:
        return

    channel_layer = get_channel_layer()
    if not channel_layer:
        return

    try:
        event.status = OutboxStatus.PROCESSING
        event.locked_at = timezone.now()
        event.save(update_fields=['status', 'locked_at'])

        event_type = event.event_type
        payload = event.payload

        # Broadcast routing:
        # 1. Cashier group receives all order creations, session open/close, bill requests, payments, and activation requests
        if event_type in CASHIER_EVENTS:
            async_to_sync(channel_layer.group_send)(
                'cashier_channel',
                {
                    'type': 'cashier_message',
                    'event': event_type,
                    'data': payload
                }
            )

        # 2. Kitchen group receives confirmed orders, prep status, ready status, served status
        if event_type in KITCHEN_EVENTS:
            async_to_sync(channel_layer.group_send)(
                'kitchen_channel',
                {
                    'type': 'kitchen_message',
                    'event': event_type,
                    'data': payload
                }
            )

        # 3. Customer group per table session
        session_token = payload.get('public_token') or payload.get('session_token')
        if session_token:
            async_to_sync(channel_layer.group_send)(
                f'customer_{session_token}',
                {
                    'type': 'customer_message',
                    'event': event_type,
                    'data': payload
                }
            )

        # 4. Table group per QR token (for tables waiting to be activated)
        qr_token = payload.get('qr_token')
        if qr_token:
            async_to_sync(channel_layer.group_send)(
                f'table_{qr_token}',
                {
                    'type': 'customer_message',
                    'event': event_type,
                    'data': payload
                }
            )


        event.status = OutboxStatus.DISPATCHED
        event.dispatched_at = timezone.now()
        event.save(update_fields=['status', 'dispatched_at'])

    except Exception as exc:
        logger.error(f"Error dispatching outbox event {event_id}: {exc}")
        event.attempt_count += 1
        event.last_error = str(exc)
        if event.attempt_count >= 5:
            event.status = OutboxStatus.DEAD
        else:
            event.status = OutboxStatus.PENDING
            event.next_attempt_at = timezone.now() + timezone.timedelta(seconds=2 ** event.attempt_count)
        event.save(update_fields=['status', 'attempt_count', 'last_error', 'next_attempt_at'])
