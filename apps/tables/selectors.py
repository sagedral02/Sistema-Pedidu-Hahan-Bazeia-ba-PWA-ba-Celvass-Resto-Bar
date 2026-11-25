from typing import Optional, List
from .models import RestaurantTable, TableSession, SessionStatus, TableStatus, TableActivationRequest, ActivationRequestStatus

ACTIVE_SESSION_STATUSES = [
    SessionStatus.OPEN,
    SessionStatus.BILL_REQUESTED,
    SessionStatus.PAYMENT_PENDING,
    SessionStatus.PAID,
]

def get_table_by_qr_token(qr_token: str) -> Optional[RestaurantTable]:
    return RestaurantTable.objects.filter(qr_token=qr_token, is_active=True).first()

def get_active_session_for_table(table: RestaurantTable) -> Optional[TableSession]:
    return TableSession.objects.filter(
        table=table,
        status__in=ACTIVE_SESSION_STATUSES
    ).first()

def get_session_by_public_token(public_token: str) -> Optional[TableSession]:
    return TableSession.objects.filter(
        public_token=public_token,
        status__in=ACTIVE_SESSION_STATUSES
    ).select_related('table', 'restaurant').first()

def list_tables_with_status():
    return RestaurantTable.objects.filter(is_active=True).prefetch_related(
        'sessions', 'activation_requests'
    ).order_by('sort_order', 'table_code')

def list_all_tables_for_admin():
    """All tables including inactive ones, for the admin table manager."""
    return RestaurantTable.objects.all().prefetch_related('sessions').order_by('-is_active', 'sort_order', 'table_code')

def get_pending_activation_requests_for_restaurant(restaurant=None) -> List[TableActivationRequest]:
    try:
        qs = TableActivationRequest.objects.filter(status=ActivationRequestStatus.PENDING).select_related('table')
        if restaurant:
            qs = qs.filter(restaurant=restaurant)
        return list(qs.order_by('-requested_at'))
    except Exception:
        return []

def get_pending_activation_request_for_table(table: RestaurantTable) -> Optional[TableActivationRequest]:
    try:
        return TableActivationRequest.objects.filter(
            table=table,
            status=ActivationRequestStatus.PENDING
        ).first()
    except Exception:
        return None

