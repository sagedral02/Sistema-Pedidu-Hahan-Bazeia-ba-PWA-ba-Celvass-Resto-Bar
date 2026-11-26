"""Take away: every take-away order gets its own session on one hidden virtual table.

The virtual table (`is_takeaway=True`, `is_active=False`) never shows up in table lists, QR sheets or table
counts, cannot be opened through a QR code and is not editable from the admin table manager. Each session has
a queue number that restarts every business day (TA-001) and an optional customer name.
"""
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Max

from apps.audit.services import log_audit_event
from apps.outbox.services import create_outbox_event

from .models import RestaurantTable, SessionStatus, TableSession, TableStatus

TAKEAWAY_TABLE_CODE = 'TAKEAWAY'


def get_takeaway_table(restaurant) -> RestaurantTable:
    table, _ = RestaurantTable.objects.get_or_create(
        restaurant=restaurant, table_code=TAKEAWAY_TABLE_CODE,
        defaults={'display_name': 'Take away', 'is_takeaway': True, 'is_active': False,
                  'status': TableStatus.AVAILABLE, 'sort_order': 9999},
    )
    changed = []
    if not table.is_takeaway:
        table.is_takeaway = True
        changed.append('is_takeaway')
    if table.is_active:
        table.is_active = False
        changed.append('is_active')
    if changed:
        table.save(update_fields=changed + ['updated_at'])
    return table


def format_takeaway_number(number) -> str:
    return f"TA-{int(number or 0):03d}"


def create_takeaway_session(*, restaurant, opened_by, customer_name: str = "", request_id: str = "") -> TableSession:
    """Open a new take-away session with the next queue number of the business day."""
    from apps.catalog.availability import business_date

    name = (customer_name or '').strip()[:80]
    with transaction.atomic():
        table = get_takeaway_table(restaurant)
        # Lock the virtual table so two cashiers never get the same number.
        table = RestaurantTable.objects.select_for_update().get(id=table.id)
        day = business_date()
        last = TableSession.objects.filter(table=table, takeaway_date=day).aggregate(n=Max('takeaway_number'))['n'] or 0
        session = TableSession.objects.create(
            restaurant=restaurant, table=table, opened_by=opened_by, guest_count=1,
            status=SessionStatus.OPEN, takeaway_number=last + 1, takeaway_date=day, customer_name=name,
        )
        log_audit_event(
            actor_user=opened_by, actor_role=getattr(opened_by, 'role', 'CASHIER'), action='TAKEAWAY_OPEN',
            entity_type='TableSession', entity_id=str(session.id),
            after_data={'number': format_takeaway_number(session.takeaway_number), 'customer_name': name},
            request_id=request_id)
        create_outbox_event(
            aggregate_type='TABLE_SESSION', aggregate_id=session.id, event_type='SESSION_OPENED',
            payload={
                'session_id': str(session.id), 'public_token': session.public_token, 'table_id': str(table.id),
                'table_code': table.table_code, 'table_name': session.label, 'status': session.status,
                'takeaway': True,
            })
        return session


def ensure_not_takeaway(table: RestaurantTable) -> None:
    if table.is_takeaway:
        raise ValidationError("The take-away table is managed by the system.")
