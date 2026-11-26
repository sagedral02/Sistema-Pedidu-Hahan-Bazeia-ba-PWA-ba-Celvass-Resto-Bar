"""Web Push subscription endpoints (staff and guests). Sending lives in apps/pwa/push.py."""
from rest_framework import status
from rest_framework.authentication import SessionAuthentication
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.views import APIView

from apps.pwa import push
from apps.pwa.models import PushAudience, PushSubscription
from apps.tables.device import extract_device_token, is_device_authorized
from apps.tables.selectors import get_session_by_public_token, get_table_by_qr_token

from .views import api_error, api_response

MAX_FIELD = 1000


def _clean_subscription(data):
    """{'endpoint': 'https://…', 'keys': {'p256dh': …, 'auth': …}} from PushSubscription.toJSON()."""
    sub = data.get('subscription') if hasattr(data, 'get') else None
    if not isinstance(sub, dict):
        return None
    endpoint = str(sub.get('endpoint') or '').strip()
    keys = sub.get('keys') if isinstance(sub.get('keys'), dict) else {}
    p256dh = str(keys.get('p256dh') or '').strip()
    auth = str(keys.get('auth') or '').strip()
    if not endpoint.startswith('https://') or len(endpoint) > MAX_FIELD:
        return None
    if not p256dh or not auth or len(p256dh) > 255 or len(auth) > 255:
        return None
    return {'endpoint': endpoint, 'p256dh': p256dh, 'auth': auth}


def _save(fields, **owner):
    obj, _ = PushSubscription.objects.update_or_create(
        endpoint=fields['endpoint'],
        defaults={'p256dh': fields['p256dh'], 'auth': fields['auth'], 'failure_count': 0, 'last_error': '', **owner},
    )
    return obj


class PushConfigAPIView(APIView):
    """GET /push/config/ : whether push is on, and the public VAPID key for PushManager.subscribe()."""
    authentication_classes = []
    permission_classes = [AllowAny]

    def get(self, request):
        return api_response({'enabled': push.is_enabled(), 'public_key': push.vapid_public_key() if push.is_enabled() else ''})


class StaffPushSubscribeAPIView(APIView):
    """POST /staff/push/subscribe/ {subscription, lang} : this browser gets the notifications of the user's role."""
    authentication_classes = [SessionAuthentication]
    permission_classes = [IsAuthenticated]

    def post(self, request):
        if not push.is_enabled():
            return api_error("PUSH_DISABLED", "Push notification la ativu.", http_status=status.HTTP_409_CONFLICT)
        fields = _clean_subscription(request.data)
        if not fields:
            return api_error("INVALID_INPUT", "Subscription la válidu.")
        _save(fields, audience=PushAudience.STAFF, user=request.user, table_session=None, qr_token='',
              device_token='', lang=push.normalize_lang(request.data.get('lang')))
        return api_response({'subscribed': True}, http_status=status.HTTP_201_CREATED)


class PublicPushSubscribeAPIView(APIView):
    """
    POST /public/push/subscribe/ {subscription, qr_token, session_token?, lang}

    With a session token the phone must own the session (device lock). Without one it waits for the table
    to be opened: it is tied to the QR token and this device, and joins the session when the cashier opens it.
    """
    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request):
        if not push.is_enabled():
            return api_error("PUSH_DISABLED", "Push notification la ativu.", http_status=status.HTTP_409_CONFLICT)
        fields = _clean_subscription(request.data)
        if not fields:
            return api_error("INVALID_INPUT", "Subscription la válidu.")
        device = extract_device_token(request)
        if not device:
            return api_error("DEVICE_LOCKED", "Telemóvel la rekoñese.", http_status=status.HTTP_403_FORBIDDEN)
        table = get_table_by_qr_token(str(request.data.get('qr_token') or ''))
        if table is None:
            return api_error("RESOURCE_NOT_FOUND", "Meza la hetan.", http_status=status.HTTP_404_NOT_FOUND)
        session = None
        token = str(request.data.get('session_token') or '').strip()
        if token:
            session = get_session_by_public_token(token)
            if session is None or session.table_id != table.id:
                return api_error("RESOURCE_NOT_FOUND", "Sesaun la hetan.", http_status=status.HTTP_404_NOT_FOUND)
            if not session.primary_device_token or not is_device_authorized(session, device):
                return api_error("DEVICE_LOCKED", "Telemóvel ne'e la bele uza meza ne'e.",
                                 http_status=status.HTTP_403_FORBIDDEN)
        _save(fields, audience=PushAudience.CUSTOMER, user=None, table_session=session, qr_token=table.qr_token,
              device_token=device, lang=push.normalize_lang(request.data.get('lang')))
        return api_response({'subscribed': True, 'waiting_for_table': session is None},
                            http_status=status.HTTP_201_CREATED)


class PushUnsubscribeAPIView(APIView):
    """POST /push/unsubscribe/ {endpoint} : removes this browser's subscription (only its owner can)."""
    authentication_classes = [SessionAuthentication]
    permission_classes = [AllowAny]

    def post(self, request):
        endpoint = str(request.data.get('endpoint') or '').strip() if hasattr(request.data, 'get') else ''
        sub = PushSubscription.objects.filter(endpoint=endpoint).first()
        if sub:
            owner = (sub.user_id and request.user.is_authenticated and sub.user_id == request.user.id) or \
                    (sub.device_token and sub.device_token == extract_device_token(request))
            if not owner:
                return api_error("FORBIDDEN", "La bele.", http_status=status.HTTP_403_FORBIDDEN)
            sub.delete()
        return api_response({'subscribed': False})
