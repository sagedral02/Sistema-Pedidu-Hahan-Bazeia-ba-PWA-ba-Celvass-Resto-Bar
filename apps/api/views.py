import uuid
import logging
from decimal import Decimal
from django.core.exceptions import ValidationError
from django.utils import timezone
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.authentication import SessionAuthentication, BasicAuthentication

logger = logging.getLogger(__name__)

from apps.accounts.permissions import IsCashierRole, IsKitchenRole, IsAdminRole, IsCashierOrAdmin, IsKitchenOrAdmin
from apps.tables.models import RestaurantTable, TableSession, TableStatus, SessionStatus
from apps.tables.selectors import (
    get_table_by_qr_token, get_session_by_public_token, list_tables_with_status,
    get_pending_activation_requests_for_restaurant, get_pending_activation_request_for_table
)
from apps.tables.services import (
    open_table_session, close_table_session, request_bill_for_session, rotate_table_qr,
    request_table_activation, approve_table_activation, reject_table_activation
)
from apps.tables.device import (
    extract_device_token, is_device_authorized, claim_session_if_unclaimed, parse_guest_count, guest_limit,
)


def restaurant_closed_error():
    """Customers cannot open a table or order outside the opening hours (staff still can)."""
    from apps.restaurants.services import get_restaurant, opening_status
    st = opening_status(get_restaurant())
    if st['is_open']:
        return None
    message = "Restorante taka agora."
    if st['next_opening_label']:
        message += f" Loke fali {st['next_opening_label']}."
    return api_error("RESTAURANT_CLOSED", message, details=st, http_status=status.HTTP_409_CONFLICT)
from apps.restaurants.models import Restaurant
from apps.catalog.selectors import (
    get_categories, get_categories_with_counts, get_category_by_id,
    get_public_menu, get_all_menu_items, get_menu_item_by_id
)
from apps.catalog.models import Category, MenuItem, ItemAvailability
from apps.catalog.services import (
    set_menu_item_availability, update_menu_item_price,
    create_category, update_category, delete_category,
    create_menu_item, update_menu_item, delete_menu_item
)
from apps.ordering.models import Order, OrderStatus
from apps.ordering.services import submit_customer_order, confirm_order_by_cashier, reject_order_by_cashier, cancel_order_by_staff
from apps.ordering.selectors import get_pending_orders_for_cashier, get_orders_for_session, get_order_by_code
from apps.kitchen.services import start_preparing_order, mark_order_ready, mark_order_served
from apps.kitchen.selectors import get_kitchen_queue_orders
from apps.payments.models import Payment, PaymentStatus
from apps.payments.services import calculate_session_bill, record_cash_payment
from apps.payments.selectors import get_completed_payment_for_session

from .serializers import (
    RestaurantTableSerializer, CategorySerializer, MenuItemSerializer,
    AdminCategorySerializer, CategoryCreateUpdateSerializer,
    AdminMenuItemSerializer, MenuItemCreateUpdateSerializer,
    AdminTableSerializer, TableCreateUpdateSerializer, AdminUserSerializer, StaffUserWriteSerializer,
    OrderSerializer, OrderSubmitRequestSerializer, CashPaymentRequestSerializer,
    TableActivationRequestSerializer
)

def api_response(data=None, success=True, error=None, http_status=status.HTTP_200_OK, request_id=None):
    if request_id is None:
        request_id = str(uuid.uuid4())
    body = {
        "success": success,
        "meta": {
            "request_id": request_id,
            "timestamp": timezone.now().isoformat()
        }
    }
    if success:
        body["data"] = data
    else:
        body["error"] = error or {
            "code": "GENERAL_ERROR",
            "message": "An error occurred."
        }
    return Response(body, status=http_status)

GENERIC_SERVER_ERROR_MESSAGE = "Erro iha servidór. Favor koko fali ka kontaktu Kaixa."


def server_error(exc, context=""):
    """Log the full exception and return a generic message without internal details."""
    logger.exception("Unhandled API error %s: %s", context, exc)
    return api_error("SERVER_ERROR", GENERIC_SERVER_ERROR_MESSAGE, http_status=status.HTTP_500_INTERNAL_SERVER_ERROR)


def validation_message(exc):
    if hasattr(exc, 'message'):
        return str(exc.message)
    if getattr(exc, 'messages', None):
        return str(exc.messages[0])
    return str(exc)


def device_locked_error():
    return api_error(
        "DEVICE_LOCKED",
        "Meza ne'e okupadu hela hosi telemóvel seluk. Ita-boot labele uza meza ne'e.",
        http_status=status.HTTP_403_FORBIDDEN,
    )


def api_error(code, message, details=None, http_status=status.HTTP_400_BAD_REQUEST, request_id=None):
    return api_response(
        success=False,
        error={
            "code": code,
            "message": message,
            "details": details or {}
        },
        http_status=http_status,
        request_id=request_id
    )

# ==========================================
# PUBLIC API (Customer Dine-In PWA)
# ==========================================

class PublicTableResolveAPIView(APIView):
    permission_classes = [AllowAny]

    def get(self, request, qr_token):
        table = get_table_by_qr_token(qr_token)
        if not table:
            return api_error("TABLE_NOT_FOUND", "Meza ho QR code ne'e la hetan.", http_status=status.HTTP_404_NOT_FOUND)
        
        from apps.tables.selectors import get_active_session_for_table
        active_session = get_active_session_for_table(table)
        pending_req = get_pending_activation_request_for_table(table)

        return api_response({
            "table": {
                "id": str(table.id),
                "table_code": table.table_code,
                "display_name": table.display_name,
                "capacity": table.capacity,
                "guest_limit": guest_limit(table),
                "status": table.status,
            },
            "session": {
                "has_active_session": active_session is not None,
                "available_for_ordering": active_session is not None and active_session.is_active,
                "public_token": active_session.public_token if active_session else None,
                "status": active_session.status if active_session else None,
            },
            "activation_request": {
                "has_pending_activation": pending_req is not None,
                "request_id": str(pending_req.id) if pending_req else None,
            }
        })


class PublicTableRequestActivationAPIView(APIView):
    permission_classes = [AllowAny]

    def post(self, request, qr_token):
        table = get_table_by_qr_token(qr_token)
        if not table:
            return api_error("TABLE_NOT_FOUND", "Meza ho QR code ne'e la hetan.", http_status=status.HTTP_404_NOT_FOUND)

        from apps.tables.selectors import get_active_session_for_table
        active_session = get_active_session_for_table(table)
        if active_session:
            return api_response({
                "status": "ALREADY_OPEN",
                "message": "Sesi meza loke tiha ona.",
                "session_token": active_session.public_token,
            })

        closed = restaurant_closed_error()
        if closed:
            return closed

        device_token = extract_device_token(request, request.data)
        try:
            # The guest must say how many people sit at the table (no default): see the "How many?" step.
            guest_count = parse_guest_count(request.data.get('guest_count'), max_value=guest_limit(table))
        except ValidationError as e:
            return api_error("INVALID_INPUT", validation_message(e))

        try:
            req = request_table_activation(
                table=table,
                device_token=device_token,
                guest_count=guest_count
            )
            return api_response({
                "status": "PENDING",
                "message": "Pedidu loke meza haruka ona ba Kaixa.",
                "request_id": str(req.id) if req else None,
                "table_code": table.table_code,
                "table_name": table.display_name,
            }, http_status=status.HTTP_201_CREATED)
        except ValidationError as e:
            return api_error("ACTIVATION_ERROR", validation_message(e))
        except Exception as e:
            return server_error(e, "request-activation")


class PublicSessionRatingsAPIView(APIView):
    """GET / POST /public/sessions/<token>/ratings/ : rate the dishes of a paid table (owning device, 6 hours)."""
    permission_classes = [AllowAny]

    def _session(self, request, session_token):
        from apps.catalog.ratings import device_owns, finished_session
        session = finished_session(session_token)
        if session is None:
            return None, api_error("RATING_CLOSED", "Rating la loke ba sesaun ne'e.", http_status=status.HTTP_404_NOT_FOUND)
        if not device_owns(session, extract_device_token(request)):
            return None, device_locked_error()
        return session, None

    def get(self, request, session_token):
        from apps.catalog.ratings import ratings_payload
        session, error = self._session(request, session_token)
        if error:
            return error
        return api_response(ratings_payload(session))

    def post(self, request, session_token):
        from apps.catalog.ratings import submit_ratings
        session, error = self._session(request, session_token)
        if error:
            return error
        entries = request.data.get('ratings') if hasattr(request.data, 'get') else None
        if entries is None and hasattr(request.data, 'get') and request.data.get('menu_item_id'):
            entries = [request.data]
        try:
            data = submit_ratings(session=session, device_token=extract_device_token(request), entries=entries or [])
        except PermissionError:
            return device_locked_error()
        except ValidationError as e:
            return api_error("RATING_INVALID", validation_message(e))
        return api_response(data)


class PublicMenuAPIView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        categories = get_categories(active_only=True)
        serializer = CategorySerializer(categories, many=True)
        return api_response(serializer.data)


class PublicOrderSubmitAPIView(APIView):
    permission_classes = [AllowAny]

    def post(self, request, session_token):
        session = get_session_by_public_token(session_token)
        if not session:
            return api_error("TABLE_SESSION_NOT_OPEN", "Sesi meza la hetan ka taka tiha ona.", http_status=status.HTTP_404_NOT_FOUND)

        device_token = extract_device_token(request, request.data)
        if not is_device_authorized(session, device_token):
            return device_locked_error()
        claim_session_if_unclaimed(session, device_token)

        closed = restaurant_closed_error()
        if closed:
            return closed

        idempotency_key = request.headers.get('Idempotency-Key') or request.data.get('idempotency_key')
        if not idempotency_key:
            return api_error("IDEMPOTENCY_KEY_REQUIRED", "Header 'Idempotency-Key' obrigatóriu atu haruka pedidu.", http_status=status.HTTP_400_BAD_REQUEST)

        serializer = OrderSubmitRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return api_error("INVALID_INPUT", "Formatu pedidu la válidu.", details=serializer.errors, http_status=status.HTTP_400_BAD_REQUEST)

        try:
            order = submit_customer_order(
                table_session=session,
                items_data=serializer.validated_data['items'],
                idempotency_key=idempotency_key,
                customer_note=serializer.validated_data.get('customer_note', ''),
                source='CUSTOMER'
            )
            order_data = OrderSerializer(order).data
            return api_response(order_data, http_status=status.HTTP_201_CREATED)
        except ValidationError as e:
            return api_error("ORDER_VALIDATION_ERROR", validation_message(e))
        except Exception as e:
            return server_error(e, "order-submit")


class PublicOrderListAPIView(APIView):
    permission_classes = [AllowAny]

    def get(self, request, session_token):
        session = get_session_by_public_token(session_token)
        if not session:
            return api_error("TABLE_SESSION_NOT_OPEN", "Sesi meza la hetan.", http_status=status.HTTP_404_NOT_FOUND)
        if not is_device_authorized(session, extract_device_token(request)):
            return device_locked_error()

        orders = get_orders_for_session(session.id)
        serializer = OrderSerializer(orders, many=True)
        return api_response(serializer.data)


class PublicOrderDetailAPIView(APIView):
    permission_classes = [AllowAny]

    def get(self, request, session_token, order_code):
        order = get_order_by_code(order_code, session_token=session_token)
        if not order:
            return api_error("RESOURCE_NOT_FOUND", "Pedidu la hetan.", http_status=status.HTTP_404_NOT_FOUND)
        if not is_device_authorized(order.table_session, extract_device_token(request)):
            return device_locked_error()
        return api_response(OrderSerializer(order).data)


class PublicRequestBillAPIView(APIView):
    permission_classes = [AllowAny]

    def post(self, request, session_token):
        session = get_session_by_public_token(session_token)
        if not session:
            return api_error("TABLE_SESSION_NOT_OPEN", "Sesi meza la hetan ka taka tiha ona.", http_status=status.HTTP_404_NOT_FOUND)

        if not is_device_authorized(session, extract_device_token(request, request.data)):
            return device_locked_error()

        if session.status == SessionStatus.PAID:
            bill = calculate_session_bill(session)
            return api_response({
                "status": "PAID",
                "message": "Konta ba meza ne'e selu tiha ona.",
                "bill": bill
            })

        if session.status == SessionStatus.BILL_REQUESTED:
            bill = calculate_session_bill(session)
            return api_response({
                "status": "BILL_REQUESTED",
                "message": "Konta husu tiha ona, favor hein kaixa.",
                "bill_requested_at": session.bill_requested_at,
                "bill": bill
            })

        try:
            updated_session = request_bill_for_session(session=session)
            bill = calculate_session_bill(updated_session)
            return api_response({
                "status": updated_session.status,
                "message": "Konta haruka tiha ona ba kaixa.",
                "bill_requested_at": updated_session.bill_requested_at,
                "bill": bill
            })
        except ValidationError as e:
            return api_error("BILL_REQUEST_INVALID", validation_message(e))
        except Exception as e:
            return server_error(e, "request-bill")


class PublicBillDetailAPIView(APIView):
    permission_classes = [AllowAny]

    def get(self, request, session_token):
        session = get_session_by_public_token(session_token)
        if not session:
            return api_error("TABLE_SESSION_NOT_OPEN", "Sesi meza la hetan.", http_status=status.HTTP_404_NOT_FOUND)
        if not is_device_authorized(session, extract_device_token(request)):
            return device_locked_error()

        bill = calculate_session_bill(session)
        payment = get_completed_payment_for_session(session)
        
        return api_response({
            "session_status": session.status,
            "bill": bill,
            "payment": {
                "payment_code": payment.payment_code,
                "amount": str(payment.amount),
                "tendered_amount": str(payment.tendered_amount),
                "change_amount": str(payment.change_amount),
                "paid_at": payment.paid_at.isoformat() if payment.paid_at else None,
            } if payment else None
        })


# ==========================================
# CASHIER API
# ==========================================

class CashierPendingOrdersAPIView(APIView):
    authentication_classes = [SessionAuthentication, BasicAuthentication]
    permission_classes = [IsAuthenticated, IsCashierOrAdmin]

    def get(self, request):
        pending_orders = get_pending_orders_for_cashier()
        serializer = OrderSerializer(pending_orders, many=True)
        return api_response(serializer.data)


class CashierConfirmOrderAPIView(APIView):
    authentication_classes = [SessionAuthentication, BasicAuthentication]
    permission_classes = [IsAuthenticated, IsCashierRole | IsAdminRole]

    def post(self, request, order_id):
        try:
            order = Order.objects.get(id=order_id)
            user = request.user if request.user.is_authenticated else None
            confirmed_order = confirm_order_by_cashier(order=order, confirmed_by=user)
            return api_response(OrderSerializer(confirmed_order).data)
        except Order.DoesNotExist:
            return api_error("RESOURCE_NOT_FOUND", "Pedidu la hetan.", http_status=status.HTTP_404_NOT_FOUND)
        except ValidationError as e:
            return api_error("ORDER_INVALID_STATE", validation_message(e))


class CashierRejectOrderAPIView(APIView):
    authentication_classes = [SessionAuthentication, BasicAuthentication]
    permission_classes = [IsAuthenticated, IsCashierRole | IsAdminRole]

    def post(self, request, order_id):
        reason = request.data.get('reason', '').strip()
        if not reason:
            return api_error("INVALID_INPUT", "Razaun rekuza pedidu obrigatóriu atu preenxe.", http_status=status.HTTP_400_BAD_REQUEST)
        
        try:
            order = Order.objects.get(id=order_id)
            user = request.user if request.user.is_authenticated else None
            rejected_order = reject_order_by_cashier(order=order, rejected_by=user, reason=reason)
            return api_response(OrderSerializer(rejected_order).data)
        except Order.DoesNotExist:
            return api_error("RESOURCE_NOT_FOUND", "Pedidu la hetan.", http_status=status.HTTP_404_NOT_FOUND)
        except ValidationError as e:
            return api_error("ORDER_INVALID_STATE", validation_message(e))


class CashierTablesListAPIView(APIView):
    authentication_classes = [SessionAuthentication, BasicAuthentication]
    permission_classes = [IsAuthenticated, IsCashierRole | IsAdminRole]

    def get(self, request):
        from apps.tables.progress import idle_minutes_setting
        tables = list_tables_with_status()
        serializer = RestaurantTableSerializer(tables, many=True, context={'idle_minutes': idle_minutes_setting()})
        from apps.tables.progress import raise_idle_alerts
        try:
            raise_idle_alerts(serializer.data)
        except Exception as exc:  # an alert must never break the table list
            logger.error(f"Idle alert failed: {exc}")
        return api_response(serializer.data)


class CashierOpenTableSessionAPIView(APIView):
    authentication_classes = [SessionAuthentication, BasicAuthentication]
    permission_classes = [IsAuthenticated, IsCashierRole | IsAdminRole]

    def post(self, request, table_id):
        table = RestaurantTable.objects.filter(id=table_id).first()
        try:
            guest_count = parse_guest_count(request.data.get('guest_count'), default=1,
                                            max_value=guest_limit(table) if table else None)
        except ValidationError as e:
            return api_error("INVALID_INPUT", validation_message(e))
        try:
            table = RestaurantTable.objects.get(id=table_id)
            user = request.user if request.user.is_authenticated else None
            session = open_table_session(table=table, opened_by=user, guest_count=guest_count)
            return api_response({
                "session_id": str(session.id),
                "public_token": session.public_token,
                "status": session.status,
                "table_code": table.table_code,
                "guest_count": session.guest_count,
            }, http_status=status.HTTP_201_CREATED)
        except RestaurantTable.DoesNotExist:
            return api_error("RESOURCE_NOT_FOUND", "Meza la hetan.", http_status=status.HTTP_404_NOT_FOUND)
        except ValidationError as e:
            return api_error("TABLE_SESSION_ALREADY_OPEN", validation_message(e))


class CashierCloseTableSessionAPIView(APIView):
    authentication_classes = [SessionAuthentication, BasicAuthentication]
    permission_classes = [IsAuthenticated, IsCashierRole | IsAdminRole]

    def post(self, request, session_id):
        """{mode: 'normal' | 'walkout', reason}. Normal needs a settled bill (409 SESSION_UNPAID otherwise);
        walkout needs a reason and records the unpaid balance as a loss."""
        from apps.tables.services import close_session_by_staff
        data = request.data if hasattr(request.data, 'get') else {}
        try:
            session = TableSession.objects.select_related('table').get(id=session_id)
        except TableSession.DoesNotExist:
            return api_error("RESOURCE_NOT_FOUND", "Sesi meza la hetan.", http_status=status.HTTP_404_NOT_FOUND)
        try:
            closed = close_session_by_staff(session=session, closed_by=request.user,
                                            mode=str(data.get('mode') or 'normal'),
                                            reason=str(data.get('reason') or ''))
        except ValidationError as e:
            code = getattr(e, 'code', None) or 'INVALID_INPUT'
            http = status.HTTP_409_CONFLICT if code == 'SESSION_UNPAID' else status.HTTP_400_BAD_REQUEST
            return api_error(code if code in ('SESSION_UNPAID', 'WALKOUT_REASON_REQUIRED') else 'INVALID_INPUT',
                             validation_message(e), http_status=http)
        return api_response({
            "session_id": str(closed.id),
            "status": closed.status,
            "closed_at": closed.closed_at,
            "close_kind": closed.close_kind,
            "unpaid_amount": str(closed.unpaid_amount),
        })


class CashierActivationRequestsAPIView(APIView):
    authentication_classes = [SessionAuthentication, BasicAuthentication]
    permission_classes = [IsAuthenticated, IsCashierOrAdmin]

    def get(self, request):
        try:
            reqs = get_pending_activation_requests_for_restaurant()
            serializer = TableActivationRequestSerializer(reqs, many=True)
            return api_response(serializer.data)
        except Exception:
            return api_response([])


class CashierApproveActivationAPIView(APIView):
    authentication_classes = [SessionAuthentication, BasicAuthentication]
    permission_classes = [IsAuthenticated, IsCashierRole | IsAdminRole]

    def post(self, request, request_id):
        user = request.user if request.user.is_authenticated else None
        raw_guests = request.data.get('guest_count') if hasattr(request.data, 'get') else None
        try:
            guests = parse_guest_count(raw_guests, default=0) or None
        except ValidationError as e:
            return api_error("INVALID_INPUT", validation_message(e))
        try:
            session = approve_table_activation(request_id=str(request_id), approved_by=user, guest_count=guests)
            return api_response({
                "session_id": str(session.id),
                "public_token": session.public_token,
                "status": session.status,
                "table_code": session.table.table_code,
                "guest_count": session.guest_count,
            }, http_status=status.HTTP_200_OK)
        except ValidationError as e:
            return api_error("ACTIVATION_ERROR", validation_message(e))
        except Exception as e:
            return server_error(e, "approve-activation")


class CashierRejectActivationAPIView(APIView):
    authentication_classes = [SessionAuthentication, BasicAuthentication]
    permission_classes = [IsAuthenticated, IsCashierRole | IsAdminRole]

    def post(self, request, request_id):
        user = request.user if request.user.is_authenticated else None
        reason = request.data.get('reason', '')
        from apps.tables.models import TableActivationRequest
        try:
            req = reject_table_activation(request_id=str(request_id), rejected_by=user, reason=reason)
            return api_response({
                "request_id": str(req.id),
                "status": req.status,
            })
        except TableActivationRequest.DoesNotExist:
            return api_error("RESOURCE_NOT_FOUND", "Pedidu loke meza la hetan.", http_status=status.HTTP_404_NOT_FOUND)
        except ValidationError as e:
            return api_error("ACTIVATION_ERROR", validation_message(e))
        except Exception as e:
            return server_error(e, "reject-activation")


class CashierPaymentAPIView(APIView):

    authentication_classes = [SessionAuthentication, BasicAuthentication]
    permission_classes = [IsAuthenticated, IsCashierRole | IsAdminRole]

    def get(self, request, session_id):
        try:
            session = TableSession.objects.get(id=session_id)
            bill = calculate_session_bill(session)
            last_payment = Payment.objects.filter(table_session=session, status=PaymentStatus.COMPLETED).last()
            if not last_payment:
                return api_error("NO_PAYMENT_FOUND", "Seidauk iha pagamentu ba sesi ne'e.", http_status=status.HTTP_404_NOT_FOUND)
            return api_response({
                "payment_code": last_payment.payment_code,
                "amount": str(last_payment.amount),
                "tendered_amount": str(last_payment.tendered_amount),
                "change_amount": str(last_payment.change_amount),
                "paid_at": last_payment.paid_at,
                "table_name": session.label,
                "table_code": session.table.table_code,
                "items": bill.get("items", []),
                "subtotal": str(bill.get("subtotal", last_payment.amount)),
            })
        except TableSession.DoesNotExist:
            return api_error("RESOURCE_NOT_FOUND", "Sesi meza la hetan.", http_status=status.HTTP_404_NOT_FOUND)

    def post(self, request, session_id):
        serializer = CashPaymentRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return api_error("INVALID_INPUT", "Formatu pagamentu la válidu.", details=serializer.errors)

        idempotency_key = request.headers.get('Idempotency-Key') or request.data.get('idempotency_key', '')

        try:
            session = TableSession.objects.get(id=session_id)
            user = request.user if request.user.is_authenticated else None
            payment = record_cash_payment(
                session=session,
                cashier_user=user,
                tendered_amount=serializer.validated_data['tendered_amount'],
                method=serializer.validated_data['method'],
                idempotency_key=idempotency_key
            )
            bill = calculate_session_bill(session)
            return api_response({
                "payment_code": payment.payment_code,
                "amount": str(payment.amount),
                "tendered_amount": str(payment.tendered_amount),
                "change_amount": str(payment.change_amount),
                "status": payment.status,
                "paid_at": payment.paid_at,
                "table_name": session.label,
                "table_code": session.table.table_code,
                "items": bill.get("items", []),
                "subtotal": str(bill.get("subtotal", payment.amount)),
            }, http_status=status.HTTP_201_CREATED)
        except TableSession.DoesNotExist:
            return api_error("RESOURCE_NOT_FOUND", "Sesi meza la hetan.", http_status=status.HTTP_404_NOT_FOUND)
        except ValidationError as e:
            return api_error("PAYMENT_ERROR", validation_message(e))


class CashierRequestBillAPIView(APIView):
    """POST /cashier/table-sessions/<id>/request-bill/ : ask for the bill on behalf of the table.

    Without `force` the call is refused with the list of unserved items when something is still being
    prepared, so the cashier can confirm before the bill is raised anyway (`{"force": true}`).
    """
    authentication_classes = [SessionAuthentication, BasicAuthentication]
    permission_classes = [IsAuthenticated, IsCashierRole | IsAdminRole]

    def post(self, request, session_id):
        from apps.payments.billing import UnservedItemsError, active_bill
        try:
            session = TableSession.objects.get(id=session_id)
        except TableSession.DoesNotExist:
            return api_error("RESOURCE_NOT_FOUND", "Sesi meza la hetan.", http_status=status.HTTP_404_NOT_FOUND)
        force = str(request.data.get('force', '')).lower() in ('1', 'true', 'yes') if hasattr(request.data, 'get') else False
        try:
            session = request_bill_for_session(session=session, requested_by=request.user, force=force)
        except UnservedItemsError as e:
            return api_error("BILL_UNSERVED_ITEMS", validation_message(e), details={'items': e.items})
        except ValidationError as e:
            return api_error("BILL_REQUEST_INVALID", validation_message(e))
        bill = active_bill(session)
        return api_response({
            "status": session.status,
            "bill_id": str(bill.id) if bill else None,
            "forced": bool(bill and bill.forced),
            "bill": calculate_session_bill(session),
        })


class CashierMenuAPIView(APIView):
    """GET /cashier/menu/ : active dishes grouped by category with today's stock (for the order screen)."""
    authentication_classes = [SessionAuthentication, BasicAuthentication]
    permission_classes = [IsAuthenticated, IsCashierRole | IsAdminRole]

    def get(self, request):
        return api_response(CategorySerializer(get_categories(active_only=True), many=True).data)


class CashierCreateOrderAPIView(APIView):
    """POST /cashier/orders/create/ : the cashier orders for a take away or a table; it goes straight to the kitchen.

    Requires an Idempotency-Key. For a take away a new take-away session is opened; for a table without an
    active session one is opened with `guest_count`. A repeat with the same key returns the first order.
    """
    authentication_classes = [SessionAuthentication, BasicAuthentication]
    permission_classes = [IsAuthenticated, IsCashierRole | IsAdminRole]

    def post(self, request):
        from django.db import transaction
        from apps.ordering.models import OrderSource
        from apps.tables.selectors import get_active_session_for_table
        from apps.tables.takeaway import create_takeaway_session
        from .serializers import CashierOrderCreateSerializer

        key = (request.headers.get('Idempotency-Key') or request.data.get('idempotency_key') or '').strip()
        if not key:
            return api_error("IDEMPOTENCY_KEY_REQUIRED", "Header 'Idempotency-Key' obrigatóriu.")
        serializer = CashierOrderCreateSerializer(data=request.data)
        if not serializer.is_valid():
            return api_error("INVALID_INPUT", "Formatu pedidu la válidu.", details=serializer.errors)
        data = serializer.validated_data

        existing = Order.objects.filter(idempotency_key=f"pos:{key}", source=OrderSource.CASHIER).first()
        if existing:
            return api_response(OrderSerializer(existing).data, http_status=status.HTTP_200_OK)

        try:
            with transaction.atomic():
                if data['destination'] == 'TAKEAWAY':
                    restaurant = Restaurant.objects.first()
                    session = create_takeaway_session(
                        restaurant=restaurant, opened_by=request.user, customer_name=data.get('customer_name', ''))
                else:
                    table = RestaurantTable.objects.filter(id=data['table_id'], is_active=True, is_takeaway=False).first()
                    if table is None:
                        return api_error("RESOURCE_NOT_FOUND", "Meza la hetan.", http_status=status.HTTP_404_NOT_FOUND)
                    # The cashier opens the table first ("Loke sesaun"); orders never open a session.
                    session = get_active_session_for_table(table)
                    if session is None or session.status not in (SessionStatus.OPEN, SessionStatus.BILL_REQUESTED):
                        return api_error("TABLE_SESSION_NOT_OPEN",
                                         f"{table.display_name}: loke sesaun uluk molok halo pedidu.",
                                         http_status=status.HTTP_409_CONFLICT)
                order = submit_customer_order(
                    table_session=session, items_data=data['items'], idempotency_key=f"pos:{key}",
                    customer_note=data.get('customer_note', ''), source=OrderSource.CASHIER,
                    created_by=request.user,
                )
        except ValidationError as e:
            return api_error("ORDER_VALIDATION_ERROR", validation_message(e))
        return api_response(OrderSerializer(order).data, http_status=status.HTTP_201_CREATED)


class CashierTakeawayListAPIView(APIView):
    """GET /cashier/takeaway/ : take-away orders still in progress (not closed), newest number last."""
    authentication_classes = [SessionAuthentication, BasicAuthentication]
    permission_classes = [IsAuthenticated, IsCashierRole | IsAdminRole]

    def get(self, request):
        from apps.tables.selectors import ACTIVE_SESSION_STATUSES
        orders = (Order.objects.filter(table_session__table__is_takeaway=True,
                                       table_session__status__in=ACTIVE_SESSION_STATUSES)
                  .exclude(status__in=[OrderStatus.REJECTED])
                  .select_related('table_session__table').prefetch_related('items')
                  .order_by('table_session__takeaway_date', 'table_session__takeaway_number', 'created_at'))
        rows = []
        for o in orders:
            data = OrderSerializer(o).data
            live = [i for i in o.items.all() if i.status != 'UNAVAILABLE']
            data['ready_for_pickup'] = bool(live) and all(i.status in ('READY', 'SERVED') for i in live) \
                and any(i.status == 'READY' for i in live)
            data['bill'] = {k: str(v) for k, v in calculate_session_bill(o.table_session).items()
                            if k in ('grand_total', 'remaining_balance', 'total_paid')}
            rows.append(data)
        return api_response(rows)


class CashierHandOverAPIView(APIView):
    """POST /cashier/table-sessions/<id>/hand-over/ : give the ready take-away items to the customer."""
    authentication_classes = [SessionAuthentication, BasicAuthentication]
    permission_classes = [IsAuthenticated, IsCashierRole | IsAdminRole]

    def post(self, request, session_id):
        from apps.kitchen.services import hand_over_takeaway
        try:
            session = TableSession.objects.select_related('table').get(id=session_id)
        except TableSession.DoesNotExist:
            return api_error("RESOURCE_NOT_FOUND", "Sesi la hetan.", http_status=status.HTTP_404_NOT_FOUND)
        try:
            count = hand_over_takeaway(session=session, staff_user=request.user)
        except ValidationError as e:
            return api_error("HANDOVER_INVALID", validation_message(e))
        session.refresh_from_db()
        return api_response({'handed_over': count, 'session_status': session.status})


# ==========================================
# KITCHEN API
# ==========================================

class KitchenQueueAPIView(APIView):
    authentication_classes = [SessionAuthentication, BasicAuthentication]
    permission_classes = [IsAuthenticated, IsKitchenOrAdmin | IsCashierRole]

    def get(self, request):
        queue = get_kitchen_queue_orders()
        serializer = OrderSerializer(queue, many=True)
        return api_response(serializer.data)


class KitchenStartOrderAPIView(APIView):
    authentication_classes = [SessionAuthentication, BasicAuthentication]
    permission_classes = [IsAuthenticated, IsKitchenOrAdmin]

    def post(self, request, order_id):
        try:
            order = Order.objects.get(id=order_id)
            user = request.user if request.user.is_authenticated else None
            prep_order = start_preparing_order(order=order, staff_user=user)
            return api_response(OrderSerializer(prep_order).data)
        except Order.DoesNotExist:
            return api_error("RESOURCE_NOT_FOUND", "Pedidu la hetan.", http_status=status.HTTP_404_NOT_FOUND)
        except ValidationError as e:
            return api_error("ORDER_INVALID_STATE", validation_message(e))


class KitchenReadyOrderAPIView(APIView):
    authentication_classes = [SessionAuthentication, BasicAuthentication]
    permission_classes = [IsAuthenticated, IsKitchenRole | IsAdminRole]

    def post(self, request, order_id):
        try:
            order = Order.objects.get(id=order_id)
            user = request.user if request.user.is_authenticated else None
            ready_order = mark_order_ready(order=order, staff_user=user)
            return api_response(OrderSerializer(ready_order).data)
        except Order.DoesNotExist:
            return api_error("RESOURCE_NOT_FOUND", "Pedidu la hetan.", http_status=status.HTTP_404_NOT_FOUND)
        except ValidationError as e:
            return api_error("ORDER_INVALID_STATE", validation_message(e))


class KitchenServedOrderAPIView(APIView):
    authentication_classes = [SessionAuthentication, BasicAuthentication]
    permission_classes = [IsAuthenticated, IsKitchenRole | IsAdminRole]

    def post(self, request, order_id):
        try:
            order = Order.objects.get(id=order_id)
            user = request.user if request.user.is_authenticated else None
            served_order = mark_order_served(order=order, staff_user=user)
            return api_response(OrderSerializer(served_order).data)
        except Order.DoesNotExist:
            return api_error("RESOURCE_NOT_FOUND", "Pedidu la hetan.", http_status=status.HTTP_404_NOT_FOUND)
        except ValidationError as e:
            return api_error("ORDER_INVALID_STATE", validation_message(e))


_KITCHEN_ITEM_ACTIONS = {'start': 'COOKING', 'ready': 'READY', 'served': 'SERVED'}


def _item_ids_from_request(request):
    """Optional `item_ids` list in the body; None means "every eligible line"."""
    data = request.data if hasattr(request, 'data') else {}
    raw = data.get('item_ids') if hasattr(data, 'get') else None
    if raw is None:
        return None
    if not isinstance(raw, (list, tuple)):
        raise ValidationError("item_ids must be a list.")
    ids = []
    for value in raw:
        try:
            ids.append(uuid.UUID(str(value)))
        except (ValueError, AttributeError):
            raise ValidationError("item_ids contains an invalid id.")
    return ids


class KitchenItemActionAPIView(APIView):
    """POST /kitchen/items/<item_id>/<action>/ : move one order line (start, ready, served)."""
    authentication_classes = [SessionAuthentication, BasicAuthentication]
    permission_classes = [IsAuthenticated, IsKitchenRole | IsAdminRole]
    action = None

    def post(self, request, item_id):
        from apps.ordering.models import OrderItem
        from apps.kitchen.services import change_items_status
        try:
            item = OrderItem.objects.select_related('order').get(id=item_id)
            user = request.user if request.user.is_authenticated else None
            order = change_items_status(
                order=item.order, to_status=_KITCHEN_ITEM_ACTIONS[self.action],
                item_ids=[item.id], staff_user=user)
            return api_response(OrderSerializer(order).data)
        except OrderItem.DoesNotExist:
            return api_error("RESOURCE_NOT_FOUND", "Item la hetan.", http_status=status.HTTP_404_NOT_FOUND)
        except ValidationError as e:
            return api_error("ORDER_INVALID_STATE", validation_message(e))


class KitchenOrderItemsActionAPIView(APIView):
    """POST /kitchen/orders/<order_id>/items/<action>/ : bulk move (all eligible lines or `item_ids`)."""
    authentication_classes = [SessionAuthentication, BasicAuthentication]
    permission_classes = [IsAuthenticated, IsKitchenRole | IsAdminRole]
    action = None

    def post(self, request, order_id):
        from apps.kitchen.services import change_items_status
        try:
            order = Order.objects.get(id=order_id)
            user = request.user if request.user.is_authenticated else None
            order = change_items_status(
                order=order, to_status=_KITCHEN_ITEM_ACTIONS[self.action],
                item_ids=_item_ids_from_request(request), staff_user=user)
            return api_response(OrderSerializer(order).data)
        except Order.DoesNotExist:
            return api_error("RESOURCE_NOT_FOUND", "Pedidu la hetan.", http_status=status.HTTP_404_NOT_FOUND)
        except ValidationError as e:
            return api_error("ORDER_INVALID_STATE", validation_message(e))


class KitchenItemUnavailableAPIView(APIView):
    """POST /kitchen/items/<item_id>/unavailable/ : the kitchen ran out of this dish ("Bahan habis")."""
    authentication_classes = [SessionAuthentication, BasicAuthentication]
    permission_classes = [IsAuthenticated, IsKitchenRole | IsAdminRole]

    def post(self, request, item_id):
        from apps.ordering.models import OrderItem
        from apps.kitchen.notices import report_out_of_stock
        try:
            item = OrderItem.objects.select_related('order').get(id=item_id)
            reason = str(request.data.get('reason', '') if hasattr(request.data, 'get') else '')
            order, notice = report_out_of_stock(item=item, staff_user=request.user, reason=reason)
            data = OrderSerializer(order).data
            return api_response({'order': data, 'notice': _notice_dict(notice) if notice else None})
        except OrderItem.DoesNotExist:
            return api_error("RESOURCE_NOT_FOUND", "Item la hetan.", http_status=status.HTTP_404_NOT_FOUND)
        except ValidationError as e:
            return api_error("ORDER_INVALID_STATE", validation_message(e))


def _notice_dict(n):
    return {
        'id': str(n.id), 'kind': n.kind, 'status': n.status,
        'menu_item_id': str(n.menu_item_id) if n.menu_item_id else None,
        'menu_name': n.menu_name_snapshot, 'order_code': n.order_code, 'table_label': n.table_label,
        'message': n.message, 'created_at': n.created_at.isoformat(),
        'acknowledged_at': n.acknowledged_at.isoformat() if n.acknowledged_at else None,
    }


class CashierKitchenNoticesAPIView(APIView):
    """GET /cashier/kitchen-notices/ : notices still waiting for a decision (oldest first)."""
    authentication_classes = [SessionAuthentication, BasicAuthentication]
    permission_classes = [IsAuthenticated, IsCashierRole | IsAdminRole]

    def get(self, request):
        from apps.kitchen.notices import list_active_notices
        return api_response([_notice_dict(n) for n in list_active_notices()])


class CashierKitchenNoticeActionAPIView(APIView):
    """POST /cashier/kitchen-notices/<id>/<action>/ with action acknowledge, resolve or sold-out."""
    authentication_classes = [SessionAuthentication, BasicAuthentication]
    permission_classes = [IsAuthenticated, IsCashierRole | IsAdminRole]
    action = None

    def post(self, request, notice_id):
        from apps.kitchen.models import KitchenNotice
        from apps.kitchen import notices
        try:
            notice = KitchenNotice.objects.get(id=notice_id)
            if self.action == 'acknowledge':
                notice = notices.acknowledge_notice(notice=notice, staff_user=request.user)
                return api_response(_notice_dict(notice))
            if self.action == 'resolve':
                notice = notices.resolve_notice(notice=notice, staff_user=request.user)
                return api_response(_notice_dict(notice))
            result = notices.mark_sold_out_from_notice(notice=notice, staff_user=request.user)
            notice.refresh_from_db()
            return api_response({'notice': _notice_dict(notice), **result})
        except KitchenNotice.DoesNotExist:
            return api_error("RESOURCE_NOT_FOUND", "Info la hetan.", http_status=status.HTTP_404_NOT_FOUND)
        except ValidationError as e:
            return api_error("VALIDATION_ERROR", validation_message(e))


# ==========================================
# ADMIN API
# ==========================================

class AdminToggleMenuItemAvailabilityAPIView(APIView):
    authentication_classes = [SessionAuthentication, BasicAuthentication]
    permission_classes = [IsAuthenticated, IsAdminRole]

    def post(self, request, item_id):
        try:
            item = MenuItem.objects.get(id=item_id)
            target = request.data.get('availability')
            if not target:
                target = 'SOLD_OUT' if item.availability == 'AVAILABLE' else 'AVAILABLE'
            
            user = request.user if request.user.is_authenticated else None
            updated = set_menu_item_availability(item=item, availability=target, actor_user=user)
            return api_response(MenuItemSerializer(updated).data)
        except MenuItem.DoesNotExist:
            return api_error("RESOURCE_NOT_FOUND", "Item menu la hetan.", http_status=status.HTTP_404_NOT_FOUND)


class AdminTableListCreateAPIView(APIView):
    authentication_classes = [SessionAuthentication, BasicAuthentication]
    permission_classes = [IsAuthenticated, IsAdminRole]

    def get(self, request):
        from apps.tables.selectors import list_all_tables_for_admin
        return api_response(AdminTableSerializer(list_all_tables_for_admin(), many=True).data)

    def post(self, request):
        from apps.tables.admin_services import create_table
        serializer = TableCreateUpdateSerializer(data=request.data)
        if not serializer.is_valid():
            return api_error("INVALID_INPUT", "Dadus meza la válidu.", details=serializer.errors)
        restaurant = Restaurant.objects.first()
        if not restaurant:
            return api_error("RESTAURANT_NOT_FOUND", "Restorante seidauk konfigura.")
        data = serializer.validated_data
        try:
            table = create_table(
                restaurant=restaurant,
                table_code=data['table_code'],
                display_name=data['display_name'],
                capacity=data.get('capacity'),
                sort_order=data.get('sort_order', 0),
                is_active=data.get('is_active', True),
                actor_user=request.user,
            )
            return api_response(AdminTableSerializer(table).data, http_status=status.HTTP_201_CREATED)
        except ValidationError as e:
            return api_error("TABLE_ERROR", validation_message(e))
        except Exception as e:
            return server_error(e, request.path)


class AdminTableDetailAPIView(APIView):
    authentication_classes = [SessionAuthentication, BasicAuthentication]
    permission_classes = [IsAuthenticated, IsAdminRole]

    def _get(self, table_id):
        return RestaurantTable.objects.filter(id=table_id, is_takeaway=False).first()

    def get(self, request, table_id):
        table = self._get(table_id)
        if not table:
            return api_error("RESOURCE_NOT_FOUND", "Meza la hetan.", http_status=status.HTTP_404_NOT_FOUND)
        return api_response(AdminTableSerializer(table).data)

    def put(self, request, table_id):
        return self.patch(request, table_id)

    def patch(self, request, table_id):
        from apps.tables.admin_services import update_table
        table = self._get(table_id)
        if not table:
            return api_error("RESOURCE_NOT_FOUND", "Meza la hetan.", http_status=status.HTTP_404_NOT_FOUND)
        serializer = TableCreateUpdateSerializer(data=request.data, partial=True)
        if not serializer.is_valid():
            return api_error("INVALID_INPUT", "Dadus meza la válidu.", details=serializer.errors)
        data = serializer.validated_data
        try:
            updated = update_table(
                table=table,
                table_code=data.get('table_code'),
                display_name=data.get('display_name'),
                capacity=data.get('capacity'),
                capacity_provided='capacity' in data,
                sort_order=data.get('sort_order') if 'sort_order' in request.data else None,
                is_active=data.get('is_active') if 'is_active' in request.data else None,
                status=data.get('status'),
                actor_user=request.user,
            )
            return api_response(AdminTableSerializer(updated).data)
        except ValidationError as e:
            return api_error("TABLE_ERROR", validation_message(e))
        except Exception as e:
            return server_error(e, request.path)

    def delete(self, request, table_id):
        from apps.tables.admin_services import delete_table
        table = self._get(table_id)
        if not table:
            return api_error("RESOURCE_NOT_FOUND", "Meza la hetan.", http_status=status.HTTP_404_NOT_FOUND)
        try:
            return api_response(delete_table(table=table, actor_user=request.user))
        except ValidationError as e:
            return api_error("TABLE_ERROR", validation_message(e))
        except Exception as e:
            return server_error(e, request.path)


class AdminRotateTableQRAPIView(APIView):
    authentication_classes = [SessionAuthentication, BasicAuthentication]
    permission_classes = [IsAuthenticated, IsAdminRole]

    def post(self, request, table_id):
        try:
            table = RestaurantTable.objects.get(id=table_id)
            user = request.user if request.user.is_authenticated else None
            new_token = rotate_table_qr(table=table, actor_user=user)
            return api_response({"new_qr_token": new_token, "table_code": table.table_code})
        except RestaurantTable.DoesNotExist:
            return api_error("RESOURCE_NOT_FOUND", "Meza la hetan.", http_status=status.HTTP_404_NOT_FOUND)


class AdminCategoryListCreateAPIView(APIView):
    authentication_classes = [SessionAuthentication, BasicAuthentication]
    permission_classes = [IsAuthenticated, IsAdminRole]

    def get(self, request):
        categories = get_categories_with_counts(active_only=False)
        serializer = AdminCategorySerializer(categories, many=True)
        return api_response(serializer.data)

    def post(self, request):
        serializer = CategoryCreateUpdateSerializer(data=request.data)
        if not serializer.is_valid():
            return api_error("INVALID_INPUT", "Dadus kategoria la válidu.", details=serializer.errors)

        restaurant = Restaurant.objects.first()
        if not restaurant:
            return api_error("RESTAURANT_NOT_FOUND", "Restorante seidauk konfigura.")

        try:
            category = create_category(
                restaurant=restaurant,
                name=serializer.validated_data['name'],
                slug=serializer.validated_data.get('slug'),
                description=serializer.validated_data.get('description', ''),
                icon_name=serializer.validated_data.get('icon_name', 'utensils'),
                sort_order=serializer.validated_data.get('sort_order', 0),
                is_active=serializer.validated_data.get('is_active', True),
                actor_user=request.user
            )
            return api_response(AdminCategorySerializer(category).data, http_status=status.HTTP_201_CREATED)
        except ValidationError as e:
            msg = validation_message(e)
            return api_error("CATEGORY_ERROR", msg)
        except Exception as e:
            return server_error(e, request.path)


class AdminCategoryDetailAPIView(APIView):
    authentication_classes = [SessionAuthentication, BasicAuthentication]
    permission_classes = [IsAuthenticated, IsAdminRole]

    def get(self, request, category_id):
        category = get_category_by_id(category_id)
        if not category:
            return api_error("RESOURCE_NOT_FOUND", "Kategoria la hetan.", http_status=status.HTTP_404_NOT_FOUND)
        return api_response(AdminCategorySerializer(category).data)

    def put(self, request, category_id):
        return self.patch(request, category_id)

    def patch(self, request, category_id):
        category = get_category_by_id(category_id)
        if not category:
            return api_error("RESOURCE_NOT_FOUND", "Kategoria la hetan.", http_status=status.HTTP_404_NOT_FOUND)

        serializer = CategoryCreateUpdateSerializer(data=request.data, partial=True)
        if not serializer.is_valid():
            return api_error("INVALID_INPUT", "Dadus kategoria la válidu.", details=serializer.errors)

        try:
            updated = update_category(
                category=category,
                name=serializer.validated_data.get('name'),
                slug=serializer.validated_data.get('slug'),
                description=serializer.validated_data.get('description'),
                icon_name=serializer.validated_data.get('icon_name'),
                sort_order=serializer.validated_data.get('sort_order'),
                is_active=serializer.validated_data.get('is_active'),
                actor_user=request.user
            )
            return api_response(AdminCategorySerializer(updated).data)
        except ValidationError as e:
            msg = validation_message(e)
            return api_error("CATEGORY_ERROR", msg)
        except Exception as e:
            return server_error(e, request.path)

    def delete(self, request, category_id):
        category = get_category_by_id(category_id)
        if not category:
            return api_error("RESOURCE_NOT_FOUND", "Kategoria la hetan.", http_status=status.HTTP_404_NOT_FOUND)

        try:
            delete_category(category=category, actor_user=request.user)
            return api_response({"deleted": True, "message": "Kategoria hamos ho susesu!"})
        except ValidationError as e:
            msg = validation_message(e)
            return api_error("CATEGORY_DELETE_ERROR", msg)
        except Exception as e:
            return server_error(e, request.path)


class AdminCategoryToggleStatusAPIView(APIView):
    authentication_classes = [SessionAuthentication, BasicAuthentication]
    permission_classes = [IsAuthenticated, IsAdminRole]

    def post(self, request, category_id):
        category = get_category_by_id(category_id)
        if not category:
            return api_error("RESOURCE_NOT_FOUND", "Kategoria la hetan.", http_status=status.HTTP_404_NOT_FOUND)

        updated = update_category(
            category=category,
            is_active=not category.is_active,
            actor_user=request.user
        )
        return api_response(AdminCategorySerializer(updated).data)


class AdminMenuItemListCreateAPIView(APIView):
    authentication_classes = [SessionAuthentication, BasicAuthentication]
    permission_classes = [IsAuthenticated, IsAdminRole]

    def get(self, request):
        from apps.catalog.availability import availability_map
        items = list(get_all_menu_items())
        serializer = AdminMenuItemSerializer(items, many=True, context={'availability': availability_map(items)})
        return api_response(serializer.data)

    def post(self, request):
        data = request.data.copy() if hasattr(request.data, 'copy') else dict(request.data)
        serializer = MenuItemCreateUpdateSerializer(data=data)
        if not serializer.is_valid():
            return api_error("INVALID_INPUT", "Dadus menu la válidu.", details=serializer.errors)

        category = get_category_by_id(serializer.validated_data['category_id'])
        if not category:
            return api_error("RESOURCE_NOT_FOUND", "Kategoria ne'ebé hili la hetan.", http_status=status.HTTP_404_NOT_FOUND)

        restaurant = category.restaurant or Restaurant.objects.first()
        image_file = request.FILES.get('image') or serializer.validated_data.get('image')

        try:
            item = create_menu_item(
                restaurant=restaurant,
                category=category,
                name=serializer.validated_data['name'],
                price=serializer.validated_data['price'],
                slug=serializer.validated_data.get('slug'),
                sku=serializer.validated_data.get('sku', ''),
                description=serializer.validated_data.get('description', ''),
                image=image_file,
                image_url=serializer.validated_data.get('image_url', ''),
                availability=serializer.validated_data.get('availability', 'AVAILABLE'),
                is_featured=serializer.validated_data.get('is_featured', False),
                sort_order=serializer.validated_data.get('sort_order', 0),
                preparation_note=serializer.validated_data.get('preparation_note', ''),
                daily_quota=serializer.validated_data.get('daily_quota'),
                actor_user=request.user
            )
            return api_response(AdminMenuItemSerializer(item).data, http_status=status.HTTP_201_CREATED)
        except ValidationError as e:
            msg = validation_message(e)
            return api_error("MENU_ERROR", msg)
        except Exception as e:
            return server_error(e, request.path)


class AdminMenuItemCloseTodayAPIView(APIView):
    """POST /admin/menu-items/<id>/close-today/ : close the dish for the business day (reason optional)."""
    authentication_classes = [SessionAuthentication, BasicAuthentication]
    permission_classes = [IsAuthenticated, IsAdminRole]

    def post(self, request, item_id):
        from apps.catalog.daily import close_menu_for_today
        item = get_menu_item_by_id(item_id)
        if not item:
            return api_error("RESOURCE_NOT_FOUND", "Menu la hetan.", http_status=status.HTTP_404_NOT_FOUND)
        try:
            reason = str(request.data.get('reason', '') if hasattr(request.data, 'get') else '')
            result = close_menu_for_today(menu_item=item, staff_user=request.user, reason=reason)
        except ValidationError as e:
            return api_error("MENU_ERROR", validation_message(e))
        item.refresh_from_db()
        return api_response({'item': AdminMenuItemSerializer(item).data, **result})


class AdminMenuItemReopenAPIView(APIView):
    """POST /admin/menu-items/<id>/reopen/ : undo today's closing."""
    authentication_classes = [SessionAuthentication, BasicAuthentication]
    permission_classes = [IsAuthenticated, IsAdminRole]

    def post(self, request, item_id):
        from apps.catalog.daily import reopen_menu
        item = get_menu_item_by_id(item_id)
        if not item:
            return api_error("RESOURCE_NOT_FOUND", "Menu la hetan.", http_status=status.HTTP_404_NOT_FOUND)
        was_closed = reopen_menu(menu_item=item, staff_user=request.user)
        return api_response({'item': AdminMenuItemSerializer(item).data, 'was_closed': was_closed})


class AdminMenuItemQuotaAPIView(APIView):
    """PATCH /admin/menu-items/<id>/quota/ : set the daily quota inline ({"daily_quota": 20} or null/"" = no limit)."""
    authentication_classes = [SessionAuthentication, BasicAuthentication]
    permission_classes = [IsAuthenticated, IsAdminRole]

    def patch(self, request, item_id):
        from apps.catalog.availability import menu_availability
        item = get_menu_item_by_id(item_id)
        if not item:
            return api_error("RESOURCE_NOT_FOUND", "Menu la hetan.", http_status=status.HTTP_404_NOT_FOUND)
        if not hasattr(request.data, 'get') or 'daily_quota' not in request.data:
            return api_error("VALIDATION_ERROR", "daily_quota obrigatóriu.")
        try:
            updated = update_menu_item(item=item, daily_quota=request.data.get('daily_quota'), actor_user=request.user)
        except ValidationError as e:
            return api_error("MENU_ERROR", validation_message(e))
        return api_response({'item': AdminMenuItemSerializer(updated).data,
                             'availability': menu_availability(updated).as_dict()})

    put = patch


class AdminMenuItemReviewsAPIView(APIView):
    """GET /admin/menu-items/<id>/reviews/ : rating summary and the latest customer reviews of a dish."""
    authentication_classes = [SessionAuthentication, BasicAuthentication]
    permission_classes = [IsAuthenticated, IsAdminRole]

    def get(self, request, item_id):
        from apps.catalog.ratings import recent_reviews
        item = get_menu_item_by_id(item_id)
        if not item:
            return api_error("RESOURCE_NOT_FOUND", "Menu la hetan.", http_status=status.HTTP_404_NOT_FOUND)
        try:
            limit = max(1, min(int(request.query_params.get('limit', 5)), 20))
        except (TypeError, ValueError):
            limit = 5
        return api_response({
            'rating_avg': float(item.rating_avg or 0),
            'rating_count': int(item.rating_count or 0),
            'reviews': recent_reviews(item, limit=limit),
        })


class AdminMenuItemDetailAPIView(APIView):
    authentication_classes = [SessionAuthentication, BasicAuthentication]
    permission_classes = [IsAuthenticated, IsAdminRole]

    def get(self, request, item_id):
        item = get_menu_item_by_id(item_id)
        if not item:
            return api_error("RESOURCE_NOT_FOUND", "Menu la hetan.", http_status=status.HTTP_404_NOT_FOUND)
        return api_response(AdminMenuItemSerializer(item).data)

    def put(self, request, item_id):
        return self.patch(request, item_id)

    def patch(self, request, item_id):
        item = get_menu_item_by_id(item_id)
        if not item:
            return api_error("RESOURCE_NOT_FOUND", "Menu la hetan.", http_status=status.HTTP_404_NOT_FOUND)

        data = request.data.copy() if hasattr(request.data, 'copy') else dict(request.data)
        serializer = MenuItemCreateUpdateSerializer(data=data, partial=True)
        if not serializer.is_valid():
            return api_error("INVALID_INPUT", "Dadus menu la válidu.", details=serializer.errors)

        category = None
        if 'category_id' in serializer.validated_data:
            category = get_category_by_id(serializer.validated_data['category_id'])
            if not category:
                return api_error("RESOURCE_NOT_FOUND", "Kategoria ne'ebé hili la hetan.", http_status=status.HTTP_404_NOT_FOUND)

        image_file = request.FILES.get('image') or serializer.validated_data.get('image')

        try:
            updated = update_menu_item(
                item=item,
                name=serializer.validated_data.get('name'),
                category=category,
                price=serializer.validated_data.get('price'),
                slug=serializer.validated_data.get('slug'),
                sku=serializer.validated_data.get('sku'),
                description=serializer.validated_data.get('description'),
                image=image_file if image_file is not None else None,
                image_url=serializer.validated_data.get('image_url'),
                availability=serializer.validated_data.get('availability'),
                is_featured=serializer.validated_data.get('is_featured'),
                sort_order=serializer.validated_data.get('sort_order'),
                preparation_note=serializer.validated_data.get('preparation_note'),
                **({'daily_quota': serializer.validated_data.get('daily_quota')} if 'daily_quota' in serializer.validated_data else {}),
                actor_user=request.user
            )
            return api_response(AdminMenuItemSerializer(updated).data)
        except ValidationError as e:
            msg = validation_message(e)
            return api_error("MENU_ERROR", msg)
        except Exception as e:
            return server_error(e, request.path)

    def delete(self, request, item_id):
        item = get_menu_item_by_id(item_id)
        if not item:
            return api_error("RESOURCE_NOT_FOUND", "Menu la hetan.", http_status=status.HTTP_404_NOT_FOUND)

        try:
            result = delete_menu_item(item=item, actor_user=request.user)
            return api_response(result)
        except Exception as e:
            return server_error(e, request.path)



class CashierOrdersHistoryAPIView(APIView):
    authentication_classes = [SessionAuthentication, BasicAuthentication]
    permission_classes = [IsAuthenticated, IsCashierOrAdmin]

    def get(self, request):
        today = timezone.localdate()
        date_filter = request.GET.get('date_range', 'today')
        status_filter = request.GET.get('status', 'ALL')

        orders_qs = Order.objects.select_related(
            'table_session__table'
        ).prefetch_related('items').order_by('-created_at')

        if date_filter == 'today':
            orders_qs = orders_qs.filter(created_at__date=today)

        if status_filter and status_filter != 'ALL':
            orders_qs = orders_qs.filter(status=status_filter)

        orders = orders_qs[:150]

        data = []
        for o in orders:
            session = o.table_session
            data.append({
                'id': str(o.id),
                'order_code': o.order_code,
                'session_id': str(session.id),
                'session_code': f"SES-{str(session.id)[:6].upper()}",
                'session_status': session.status,
                'table_name': session.table.display_name,
                'table_code': session.table.table_code,
                'status': o.status,
                'grand_total': str(o.grand_total),
                'items_summary': ", ".join([f"{item.quantity}x {item.menu_name_snapshot}" for item in o.items.all()]),
                'customer_note': o.customer_note or '',
                'rejection_reason': o.rejection_reason or '',
                'created_at': o.created_at.strftime("%H:%M"),
                'created_date': o.created_at.strftime("%d/%m/%Y"),
                'can_print_receipt': bool(o.status == OrderStatus.COMPLETED or session.status in [SessionStatus.PAID, SessionStatus.CLOSED]),
            })
        return api_response(data)


class CashierBillsHistoryAPIView(APIView):
    authentication_classes = [SessionAuthentication, BasicAuthentication]
    permission_classes = [IsAuthenticated, IsCashierOrAdmin]

    def get(self, request):
        today = timezone.localdate()
        date_filter = request.GET.get('date_range', 'today')

        payments_qs = Payment.objects.filter(
            status=PaymentStatus.COMPLETED
        ).select_related('table_session__table', 'received_by').order_by('-paid_at')

        if date_filter == 'today':
            payments_qs = payments_qs.filter(paid_at__date=today)

        payments = payments_qs[:150]

        data = []
        for p in payments:
            session = p.table_session
            tbl = session.table
            cashier_name = (
                getattr(p.received_by, 'full_name', '') or getattr(p.received_by, 'username', 'Kaixa')
                if p.received_by else "Kaixa"
            )
            data.append({
                'id': str(p.id),
                'session_id': str(session.id),
                'payment_code': p.payment_code,
                'table_name': tbl.display_name,
                'table_code': tbl.table_code,
                'method': p.get_method_display(),
                'amount': str(p.amount),
                'tendered_amount': str(p.tendered_amount or p.amount),
                'change_amount': str(p.change_amount or '0.00'),
                'cashier_name': cashier_name,
                'paid_at_time': p.paid_at.strftime("%H:%M") if p.paid_at else "-",
                'paid_at_date': p.paid_at.strftime("%d/%m/%Y") if p.paid_at else "-",
                'paid_at_full': p.paid_at.strftime("%d/%m/%Y %H:%M") if p.paid_at else "-",
            })

        return api_response(data)


class CashierShiftAPIView(APIView):
    """GET /cashier/shift/ : my open shift with live figures (null when none).
    POST /cashier/shift/open/ {opening_cash} and POST /cashier/shift/close/ {counted_cash, note}."""
    authentication_classes = [SessionAuthentication, BasicAuthentication]
    permission_classes = [IsAuthenticated, IsCashierRole | IsAdminRole]
    action = 'get'

    def get(self, request):
        from apps.shifts.models import Shift
        from apps.shifts.services import current_shift, shift_dict
        last = Shift.objects.filter(cashier=request.user, status='CLOSED').order_by('-closed_at').first()
        return api_response({'current': shift_dict(current_shift(request.user)), 'last_closed': shift_dict(last)})

    def post(self, request):
        from apps.shifts import services as shifts
        data = request.data if hasattr(request.data, 'get') else {}
        try:
            if self.action == 'open':
                shift = shifts.open_shift(cashier=request.user, opening_cash=data.get('opening_cash'))
            else:
                shift = shifts.current_shift(request.user)
                if shift is None:
                    return api_error("SHIFT_NOT_OPEN", "Seidauk loke turnu.", http_status=status.HTTP_404_NOT_FOUND)
                shift = shifts.close_shift(shift=shift, counted_cash=data.get('counted_cash'),
                                           note=str(data.get('note', '')), actor=request.user)
        except ValidationError as e:
            return api_error("SHIFT_ERROR", validation_message(e))
        return api_response(shifts.shift_dict(shift))


class CashierMenuStockAPIView(APIView):
    """GET /cashier/menu-stock/ : every orderable-or-closed dish with today's quota, sold and remaining."""
    authentication_classes = [SessionAuthentication, BasicAuthentication]
    permission_classes = [IsAuthenticated, IsCashierRole | IsAdminRole]

    def get(self, request):
        from apps.catalog.availability import availability_map
        items = [i for i in get_all_menu_items() if i.availability != ItemAvailability.INACTIVE and i.category.is_active]
        states = availability_map(items)
        rows = []
        for item in items:
            a = states[str(item.id)]
            rows.append({
                'id': str(item.id), 'name': item.name, 'category_id': str(item.category_id),
                'category_name': item.category.name, 'price': str(item.price), 'image': item.display_image,
                'state': a.state, 'orderable': a.orderable, 'quota': a.quota, 'sold': a.sold,
                'remaining': a.remaining, 'closed_reason': a.closed_reason,
            })
        return api_response(rows)


class CashierMenuSoldOutAPIView(APIView):
    """POST /cashier/menu-items/<id>/sold-out/ {closed: true|false, reason}: close for today or reopen.
    Cashiers cannot change anything else about a dish."""
    authentication_classes = [SessionAuthentication, BasicAuthentication]
    permission_classes = [IsAuthenticated, IsCashierRole | IsAdminRole]

    def post(self, request, item_id):
        from apps.catalog.daily import close_menu_for_today, reopen_menu
        item = get_menu_item_by_id(item_id)
        if not item or item.availability == ItemAvailability.INACTIVE:
            return api_error("RESOURCE_NOT_FOUND", "Menu la hetan.", http_status=status.HTTP_404_NOT_FOUND)
        data = request.data if hasattr(request.data, 'get') else {}
        closed = str(data.get('closed', 'true')).lower() not in ('0', 'false', 'no')
        try:
            if closed:
                result = close_menu_for_today(menu_item=item, staff_user=request.user, reason=str(data.get('reason', '')))
            else:
                result = {'was_closed': reopen_menu(menu_item=item, staff_user=request.user)}
        except ValidationError as e:
            return api_error("MENU_ERROR", validation_message(e))
        return api_response({'id': str(item.id), 'closed': closed, **result})


class AdminRestaurantSettingsAPIView(APIView):
    """GET / PATCH /admin/restaurant/ : identity, contact, location, opening hours, tax and receipt note."""
    authentication_classes = [SessionAuthentication, BasicAuthentication]
    permission_classes = [IsAuthenticated, IsAdminRole]

    @staticmethod
    def _body(restaurant):
        from apps.restaurants.branding import brand_context
        from apps.restaurants.services import hours_list, opening_status, settings_dict
        data = settings_dict(restaurant)
        data['opening_hours'] = hours_list(restaurant)
        data['opening_status'] = opening_status(restaurant)
        data['brand'] = brand_context(restaurant)
        return data

    def get(self, request):
        from apps.restaurants.services import get_restaurant
        return api_response(self._body(get_restaurant()))

    def patch(self, request):
        from apps.restaurants.services import get_restaurant, update_restaurant_settings
        data = request.data if isinstance(request.data, dict) else {}
        try:
            restaurant = update_restaurant_settings(restaurant=get_restaurant(), data=data, actor_user=request.user)
        except ValidationError as e:
            return api_error("SETTINGS_INVALID", validation_message(e))
        return api_response(self._body(restaurant))

    def put(self, request):
        return self.patch(request)


class AdminRestaurantLogoAPIView(APIView):
    """POST (multipart `logo`) sets the logo, which also becomes the app icon; DELETE goes back to the default."""
    authentication_classes = [SessionAuthentication, BasicAuthentication]
    permission_classes = [IsAuthenticated, IsAdminRole]

    def post(self, request):
        from apps.restaurants.branding import set_logo
        from apps.restaurants.services import get_restaurant
        upload = request.FILES.get('logo')
        if upload is None:
            return api_error("SETTINGS_INVALID", "Choose an image file.")
        try:
            restaurant = set_logo(restaurant=get_restaurant(), upload=upload, actor_user=request.user)
        except ValidationError as e:
            return api_error("SETTINGS_INVALID", validation_message(e))
        return api_response(AdminRestaurantSettingsAPIView._body(restaurant))

    def delete(self, request):
        from apps.restaurants.branding import clear_logo
        from apps.restaurants.services import get_restaurant
        restaurant = clear_logo(restaurant=get_restaurant(), actor_user=request.user)
        return api_response(AdminRestaurantSettingsAPIView._body(restaurant))


class AdminOverviewAPIView(APIView):
    """Charts and KPI cards of the admin Overview. `range` is today, 7d or 30d."""
    authentication_classes = [SessionAuthentication, BasicAuthentication]
    permission_classes = [IsAuthenticated, IsAdminRole]

    def get(self, request):
        from apps.reporting.overview import build_overview
        from apps.reporting.reports import ReportError
        try:
            data = build_overview(request.GET.get('range'))
        except ReportError:
            return api_error("INVALID_INPUT", "Períodu la válidu (today, 7d ka 30d).")
        response = api_response(data)
        response['Cache-Control'] = 'no-store'
        return response


class AdminUserListCreateAPIView(APIView):
    authentication_classes = [SessionAuthentication, BasicAuthentication]
    permission_classes = [IsAuthenticated, IsAdminRole]

    def get(self, request):
        from apps.accounts.models import User
        users = User.objects.order_by('-is_active', 'role', 'username')
        return api_response(AdminUserSerializer(users, many=True, context={'request': request}).data)

    def post(self, request):
        from apps.accounts.admin_services import create_staff_user, PasswordError
        serializer = StaffUserWriteSerializer(data=request.data)
        if not serializer.is_valid():
            return api_error("INVALID_INPUT", "Dadus staff la válidu.", details=serializer.errors)
        data = serializer.validated_data
        if not data.get('username') or not data.get('password'):
            return api_error("INVALID_INPUT", "Naran-uzuáriu no liafuan xave obrigatóriu.")
        try:
            user = create_staff_user(
                username=data['username'],
                password=data['password'],
                role=data.get('role', 'CASHIER'),
                full_name=data.get('full_name', data.get('first_name', '')),
                actor_user=request.user,
            )
        except PasswordError as e:
            return api_error("WEAK_PASSWORD", " ".join(e.messages))
        except ValidationError as e:
            return api_error("USER_ERROR", validation_message(e))
        except Exception as e:
            return server_error(e, request.path)
        return api_response(AdminUserSerializer(user, context={'request': request}).data, http_status=status.HTTP_201_CREATED)


class AdminUserDetailAPIView(APIView):
    authentication_classes = [SessionAuthentication, BasicAuthentication]
    permission_classes = [IsAuthenticated, IsAdminRole]

    def _get(self, user_id):
        from apps.accounts.models import User
        return User.objects.filter(pk=user_id).first()

    def get(self, request, user_id):
        user = self._get(user_id)
        if not user:
            return api_error("RESOURCE_NOT_FOUND", "Staff la hetan.", http_status=status.HTTP_404_NOT_FOUND)
        return api_response(AdminUserSerializer(user, context={'request': request}).data)

    def put(self, request, user_id):
        return self.patch(request, user_id)

    def patch(self, request, user_id):
        from django.contrib.auth import update_session_auth_hash
        from apps.accounts.admin_services import update_staff_user, PasswordError
        user = self._get(user_id)
        if not user:
            return api_error("RESOURCE_NOT_FOUND", "Staff la hetan.", http_status=status.HTTP_404_NOT_FOUND)
        serializer = StaffUserWriteSerializer(data=request.data, partial=True)
        if not serializer.is_valid():
            return api_error("INVALID_INPUT", "Dadus staff la válidu.", details=serializer.errors)
        data = serializer.validated_data
        has_name = 'full_name' in data or 'first_name' in data
        password = data.get('password') or None
        try:
            updated = update_staff_user(
                user=user,
                actor_user=request.user,
                username=data.get('username'),
                full_name=data.get('full_name', data.get('first_name')) if has_name else None,
                role=data.get('role'),
                is_active=data['is_active'] if 'is_active' in data else None,
                password=password,
            )
        except PasswordError as e:
            return api_error("WEAK_PASSWORD", " ".join(e.messages))
        except ValidationError as e:
            return api_error("USER_ERROR", validation_message(e))
        except Exception as e:
            return server_error(e, request.path)
        if password and request.user.pk == updated.pk:
            # Keep the admin who changed their own password signed in on this device.
            update_session_auth_hash(request, updated)
        return api_response(AdminUserSerializer(updated, context={'request': request}).data)

    def delete(self, request, user_id):
        from apps.accounts.admin_services import delete_staff_user
        user = self._get(user_id)
        if not user:
            return api_error("RESOURCE_NOT_FOUND", "Staff la hetan.", http_status=status.HTTP_404_NOT_FOUND)
        try:
            return api_response(delete_staff_user(user=user, actor_user=request.user))
        except ValidationError as e:
            return api_error("USER_ERROR", validation_message(e))
        except Exception as e:
            return server_error(e, request.path)
