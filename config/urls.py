from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static

from apps.accounts import views as auth_views
from apps.restaurants import views as resto_views
from apps.pwa import views as pwa_views
from apps.reporting import views as report_views

urlpatterns = [
    path('admin/', admin.site.urls),

    # PWA Endpoints
    path('manifest.json', pwa_views.manifest_json, name='pwa-manifest'),
    path('sw.js', pwa_views.service_worker, name='pwa-sw'),
    path('offline/', pwa_views.offline_view, name='pwa-offline'),
    path('pwa/icons/<str:name>', pwa_views.brand_icon, name='pwa-icon'),
    path('favicon.ico', pwa_views.brand_icon, {'name': 'favicon.ico'}, name='favicon'),

    # REST API v1
    path('api/v1/', include('apps.api.urls')),

    # Customer Dine-In Web App (QR-gated)
    path('', resto_views.customer_dine_in_view, name='customer-home'),
    path('t/<str:qr_token>/', resto_views.customer_dine_in_view, name='customer-table'),
    path('t/<str:qr_token>/order/<str:order_code>/', resto_views.customer_tracker_view, name='customer-tracker'),
    path('t/<str:qr_token>/thanks/<str:session_token>/', resto_views.customer_thanks_view, name='customer-thanks'),

    # Staff Portals (protected by @login_required + @role_required)
    path('cashier/', resto_views.cashier_portal_view, name='cashier-portal'),
    path('kitchen/', resto_views.kitchen_portal_view, name='kitchen-portal'),
    path('admin-portal/', resto_views.admin_portal_view, name='admin-portal'),
    path('admin-portal/qr-sheet/', resto_views.qr_print_sheet_view, name='admin-qr-sheet'),

    # Reports (Admin and Cashier)
    path('reports/', report_views.reports_page_view, name='reports'),
    path('reports/data/', report_views.report_data_view, name='reports-data'),
    path('reports/export/', report_views.report_export_view, name='reports-export'),
    path('reports/audit/', report_views.audit_list_view, name='reports-audit'),
    path('reports/audit/<str:event_id>/', report_views.audit_detail_view, name='reports-audit-detail'),
    path('reports/history/orders/', report_views.history_orders_view, name='reports-history-orders'),
    path('reports/history/transactions/', report_views.history_transactions_view, name='reports-history-transactions'),
    path('reports/history/orders/<str:order_id>/', report_views.history_order_detail_view, name='reports-history-order'),
    path('reports/history/transactions/<str:payment_id>/', report_views.history_transaction_detail_view,
         name='reports-history-transaction'),

    # Authentication
    path('login/', auth_views.login_view, name='login'),
    path('logout/', auth_views.logout_view, name='logout'),
]

# Demo login only available in DEBUG mode (development)
if settings.DEBUG:
    urlpatterns += [
        path('demo-login/<str:role>/', auth_views.demo_login_view, name='demo-login'),
    ]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
