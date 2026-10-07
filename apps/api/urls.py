from django.urls import path
from . import views

urlpatterns = [
    # Public Customer Dine-In API
    path('public/tables/resolve/<str:qr_token>/', views.PublicTableResolveAPIView.as_view(), name='api-public-table-resolve'),
    path('public/tables/<str:qr_token>/request-activation/', views.PublicTableRequestActivationAPIView.as_view(), name='api-public-table-request-activation'),
    path('public/menu/', views.PublicMenuAPIView.as_view(), name='api-public-menu'),
    path('public/sessions/<str:session_token>/orders/', views.PublicOrderSubmitAPIView.as_view(), name='api-public-order-submit'),
    path('public/sessions/<str:session_token>/orders/list/', views.PublicOrderListAPIView.as_view(), name='api-public-order-list'),
    path('public/sessions/<str:session_token>/orders/<str:order_code>/', views.PublicOrderDetailAPIView.as_view(), name='api-public-order-detail'),
    path('public/sessions/<str:session_token>/request-bill/', views.PublicRequestBillAPIView.as_view(), name='api-public-request-bill'),
    path('public/sessions/<str:session_token>/bill/', views.PublicBillDetailAPIView.as_view(), name='api-public-bill-detail'),

    # Cashier API
    path('cashier/orders/', views.CashierPendingOrdersAPIView.as_view(), name='api-cashier-pending-orders'),
    path('cashier/orders/history/', views.CashierOrdersHistoryAPIView.as_view(), name='api-cashier-orders-history'),
    path('cashier/bills/history/', views.CashierBillsHistoryAPIView.as_view(), name='api-cashier-bills-history'),
    path('cashier/orders/<uuid:order_id>/confirm/', views.CashierConfirmOrderAPIView.as_view(), name='api-cashier-confirm-order'),
    path('cashier/orders/<uuid:order_id>/reject/', views.CashierRejectOrderAPIView.as_view(), name='api-cashier-reject-order'),
    path('cashier/tables/', views.CashierTablesListAPIView.as_view(), name='api-cashier-tables-list'),
    path('cashier/tables/<uuid:table_id>/open-session/', views.CashierOpenTableSessionAPIView.as_view(), name='api-cashier-open-session'),
    path('cashier/table-sessions/<uuid:session_id>/close/', views.CashierCloseTableSessionAPIView.as_view(), name='api-cashier-close-session'),
    path('cashier/activation-requests/', views.CashierActivationRequestsAPIView.as_view(), name='api-cashier-activation-requests'),
    path('cashier/activation-requests/<uuid:request_id>/approve/', views.CashierApproveActivationAPIView.as_view(), name='api-cashier-approve-activation'),
    path('cashier/activation-requests/<uuid:request_id>/reject/', views.CashierRejectActivationAPIView.as_view(), name='api-cashier-reject-activation'),
    path('cashier/table-sessions/<uuid:session_id>/payments/', views.CashierPaymentAPIView.as_view(), name='api-cashier-payments'),
    path('cashier/shift/summary/', views.CashierShiftSummaryAPIView.as_view(), name='api-cashier-shift-summary'),


    # Kitchen API
    path('kitchen/orders/', views.KitchenQueueAPIView.as_view(), name='api-kitchen-queue'),
    path('kitchen/orders/<uuid:order_id>/start/', views.KitchenStartOrderAPIView.as_view(), name='api-kitchen-start-order'),
    path('kitchen/orders/<uuid:order_id>/ready/', views.KitchenReadyOrderAPIView.as_view(), name='api-kitchen-ready-order'),
    path('kitchen/orders/<uuid:order_id>/served/', views.KitchenServedOrderAPIView.as_view(), name='api-kitchen-served-order'),

    # Admin API
    path('admin/categories/', views.AdminCategoryListCreateAPIView.as_view(), name='api-admin-categories-list-create'),
    path('admin/categories/<uuid:category_id>/', views.AdminCategoryDetailAPIView.as_view(), name='api-admin-category-detail'),
    path('admin/categories/<uuid:category_id>/toggle-status/', views.AdminCategoryToggleStatusAPIView.as_view(), name='api-admin-category-toggle-status'),
    path('admin/menu-items/', views.AdminMenuItemListCreateAPIView.as_view(), name='api-admin-menu-items-list-create'),
    path('admin/menu-items/<uuid:item_id>/', views.AdminMenuItemDetailAPIView.as_view(), name='api-admin-menu-item-detail'),
    path('admin/menu-items/<uuid:item_id>/toggle-availability/', views.AdminToggleMenuItemAvailabilityAPIView.as_view(), name='api-admin-toggle-menu'),
    path('admin/tables/<uuid:table_id>/rotate-qr/', views.AdminRotateTableQRAPIView.as_view(), name='api-admin-rotate-qr'),
    path('admin/users/create/', views.AdminUserCreateAPIView.as_view(), name='api-admin-user-create'),
]
