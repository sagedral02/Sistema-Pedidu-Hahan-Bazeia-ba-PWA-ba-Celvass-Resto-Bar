from decimal import Decimal
from typing import Dict, Any, List
from django.utils import timezone
from django.db.models import Sum, Count, F, Q

from apps.ordering.models import Order, OrderItem, OrderStatus
from apps.payments.models import Payment, PaymentStatus
from apps.tables.models import RestaurantTable, TableStatus, TableSession, SessionStatus

def get_dashboard_metrics() -> Dict[str, Any]:
    # Local business day (Asia/Dili), consistent with the cashier shift summary.
    today = timezone.localdate()

    # Sales
    today_payments = Payment.objects.filter(
        created_at__date=today,
        status=PaymentStatus.COMPLETED
    )
    today_sales = today_payments.aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
    payments_count = today_payments.count()

    # Orders today
    today_orders = Order.objects.filter(created_at__date=today)
    total_orders_count = today_orders.count()
    
    pending_count = today_orders.filter(status=OrderStatus.WAITING_CASHIER_CONFIRMATION).count()
    active_kitchen_count = today_orders.filter(status__in=[OrderStatus.CONFIRMED, OrderStatus.PREPARING, OrderStatus.READY]).count()
    completed_count = today_orders.filter(status=OrderStatus.COMPLETED).count()
    rejected_count = today_orders.filter(status=OrderStatus.REJECTED).count()

    # Tables
    total_tables = RestaurantTable.objects.filter(is_active=True).count()
    occupied_tables = RestaurantTable.objects.filter(is_active=True, status=TableStatus.OCCUPIED).count()

    # Top items sold
    top_items = OrderItem.objects.filter(
        order__created_at__date=today,
        order__status__in=[OrderStatus.CONFIRMED, OrderStatus.PREPARING, OrderStatus.READY, OrderStatus.SERVED, OrderStatus.COMPLETED]
    ).values('menu_name_snapshot').annotate(
        qty_sold=Sum('quantity'),
        revenue=Sum('subtotal')
    ).order_by('-qty_sold')[:8]

    # Recent transactions
    recent_transactions = Payment.objects.filter(
        status=PaymentStatus.COMPLETED
    ).select_related('table_session__table', 'received_by').order_by('-created_at')[:10]

    # Rejection list
    recent_rejections = Order.objects.filter(
        status=OrderStatus.REJECTED
    ).select_related('table_session__table', 'confirmed_by').order_by('-created_at')[:10]

    return {
        'today_sales': today_sales,
        'payments_count': payments_count,
        'total_orders_count': total_orders_count,
        'pending_count': pending_count,
        'active_kitchen_count': active_kitchen_count,
        'completed_count': completed_count,
        'rejected_count': rejected_count,
        'total_tables': total_tables,
        'occupied_tables': occupied_tables,
        'top_items': list(top_items),
        'recent_transactions': recent_transactions,
        'recent_rejections': recent_rejections,
    }
