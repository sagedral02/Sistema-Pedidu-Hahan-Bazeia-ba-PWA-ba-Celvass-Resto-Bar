"""
Web Push notifications (VAPID, no third-party service).

* Keys come from the environment (VAPID_PUBLIC_KEY / VAPID_PRIVATE_KEY / VAPID_SUBJECT); without them push is
  simply off and nothing fails. Create a pair with `python manage.py generate_vapid_keys`.
* Every outbox event is offered to `notify_for_event` after the transaction commits. It decides who gets a
  notification (cashiers, kitchen, or the guests of one table) and sends it in their language.
* Sending never breaks the request: errors are logged, and subscriptions the push service no longer knows
  (HTTP 404/410) are deleted.
"""
import json
import logging
import threading
from typing import Dict, Iterable, List, Optional

from django.conf import settings

logger = logging.getLogger(__name__)

LANGS = ('tet', 'pt', 'en', 'id')
TTL_SECONDS = 60 * 30

# title / body per message key, {table} {code} are filled from the event payload.
MESSAGES: Dict[str, Dict[str, tuple]] = {
    'activation_requested': {
        'tet': ('Husu loke meza', '{table}: bainaka husu loke meza.'),
        'pt': ('Pedido para abrir mesa', '{table}: clientes pedem para abrir a mesa.'),
        'en': ('Table opening request', '{table}: guests ask to open the table.'),
        'id': ('Permintaan buka meja', '{table}: tamu minta meja dibuka.'),
    },
    'order_waiting': {
        'tet': ('Pedidu foun', '{table}: pedidu {code} hein konfirmasaun.'),
        'pt': ('Novo pedido', '{table}: pedido {code} aguarda confirmação.'),
        'en': ('New order', '{table}: order {code} is waiting for confirmation.'),
        'id': ('Pesanan baru', '{table}: pesanan {code} menunggu konfirmasi.'),
    },
    'bill_requested': {
        'tet': ('Husu konta', '{table} husu konta.'),
        'pt': ('Conta pedida', '{table} pediu a conta.'),
        'en': ('Bill requested', '{table} asked for the bill.'),
        'id': ('Minta tagihan', '{table} minta tagihan.'),
    },
    'kitchen_notice': {
        'tet': ('Informasaun dapur', '{table}: {menu} la iha.'),
        'pt': ('Aviso da cozinha', '{table}: {menu} indisponível.'),
        'en': ('Kitchen notice', '{table}: {menu} is not available.'),
        'id': ('Info dapur', '{table}: {menu} tidak tersedia.'),
    },
    'table_idle': {
        'tet': ('Meza la iha atividade', '{table}: seidauk selu no la iha atividade {minutes} minutu.'),
        'pt': ('Mesa sem atividade', '{table}: por pagar e sem atividade há {minutes} minutos.'),
        'en': ('Table inactive', '{table}: unpaid and no activity for {minutes} minutes.'),
        'id': ('Meja tidak aktif', '{table}: belum bayar dan tidak ada aktivitas {minutes} menit.'),
    },
    'kitchen_new': {
        'tet': ('Pedidu ba dapur', '{table}: {code} konfirma ona.'),
        'pt': ('Pedido para a cozinha', '{table}: {code} confirmado.'),
        'en': ('Order for the kitchen', '{table}: {code} confirmed.'),
        'id': ('Pesanan untuk dapur', '{table}: {code} dikonfirmasi.'),
    },
    'guest_opened': {
        'tet': ('Meza loke ona', 'Ita bele halo pedidu agora.'),
        'pt': ('Mesa aberta', 'Já pode fazer o pedido.'),
        'en': ('Table opened', 'You can order now.'),
        'id': ('Meja dibuka', 'Silakan pesan sekarang.'),
    },
    'guest_confirmed': {
        'tet': ('Pedidu konfirma ona', 'Pedidu {code} ba ona dapur.'),
        'pt': ('Pedido confirmado', 'O pedido {code} foi para a cozinha.'),
        'en': ('Order confirmed', 'Order {code} went to the kitchen.'),
        'id': ('Pesanan dikonfirmasi', 'Pesanan {code} sudah ke dapur.'),
    },
    'guest_rejected': {
        'tet': ('Pedidu rekuza', 'Pedidu {code} la simu. Haree razaun iha aplikasaun.'),
        'pt': ('Pedido recusado', 'O pedido {code} não foi aceite. Veja o motivo na app.'),
        'en': ('Order rejected', 'Order {code} was not accepted. See the reason in the app.'),
        'id': ('Pesanan ditolak', 'Pesanan {code} tidak diterima. Lihat alasannya di aplikasi.'),
    },
    'guest_ready': {
        'tet': ('Hahan prontu', '{items} prontu ona.'),
        'pt': ('Comida pronta', '{items} pronto.'),
        'en': ('Food is ready', '{items} is ready.'),
        'id': ('Makanan siap', '{items} sudah siap.'),
    },
    'guest_bill': {
        'tet': ('Konta prontu', 'Kaixa sei mai ho ita-nia konta.'),
        'pt': ('Conta pronta', 'O caixa vai trazer a sua conta.'),
        'en': ('Bill is ready', 'The cashier is bringing your bill.'),
        'id': ('Tagihan siap', 'Kasir akan membawa tagihan Anda.'),
    },
    'guest_paid': {
        'tet': ('Obrigadu barak!', 'Pagamentu simu ona.'),
        'pt': ('Muito obrigado!', 'Pagamento recebido.'),
        'en': ('Thank you!', 'Payment received.'),
        'id': ('Terima kasih!', 'Pembayaran diterima.'),
    },
}


# --------------------------------------------------------------------------- configuration

def vapid_public_key() -> str:
    return (getattr(settings, 'VAPID_PUBLIC_KEY', '') or '').strip()


def is_enabled() -> bool:
    return bool(vapid_public_key() and (getattr(settings, 'VAPID_PRIVATE_KEY', '') or '').strip())


def normalize_lang(lang: Optional[str]) -> str:
    lang = (lang or '').strip().lower()[:5]
    return lang if lang in LANGS else 'tet'


def render(key: str, lang: str, **values) -> Dict[str, str]:
    title, body = MESSAGES[key][normalize_lang(lang)]
    safe = {k: str(v) for k, v in values.items()}
    try:
        body = body.format(**safe)
    except (KeyError, IndexError):
        pass
    return {'title': title, 'body': body}


# --------------------------------------------------------------------------- sending

def _send_one(sub, payload: dict) -> bool:
    from pywebpush import WebPushException, webpush
    try:
        webpush(
            subscription_info=sub.as_webpush(),
            data=json.dumps(payload),
            vapid_private_key=settings.VAPID_PRIVATE_KEY,
            vapid_claims={'sub': getattr(settings, 'VAPID_SUBJECT', '') or 'mailto:admin@example.com'},
            ttl=TTL_SECONDS,
            timeout=5,
        )
        return True
    except WebPushException as exc:
        status = getattr(getattr(exc, 'response', None), 'status_code', None)
        if status in (404, 410):
            sub.delete()
            return False
        _record_failure(sub, f"{status or ''} {exc}")
        return False
    except Exception as exc:  # network errors, bad keys: never break the caller
        _record_failure(sub, str(exc))
        return False


def _record_failure(sub, message: str):
    logger.warning("Web push failed for %s: %s", sub.id, message)
    sub.failure_count += 1
    sub.last_error = message[:255]
    if sub.failure_count >= 5:
        sub.delete()
    else:
        sub.save(update_fields=['failure_count', 'last_error', 'updated_at'])


def send(subscriptions: Iterable, key: str, url: str = '/', tag: str = '', **values) -> int:
    """Send one message (in each subscriber's language). Returns how many were accepted."""
    if not is_enabled():
        return 0
    sent = 0
    for sub in list(subscriptions):
        text = render(key, sub.lang, **values)
        sent += _send_one(sub, {**text, 'url': url, 'tag': tag or key, 'icon': '/pwa/icons/icon-192.png',
                                'badge': '/pwa/icons/favicon-32.png'})
    return sent


def _run(fn, *args, **kwargs):
    """Push calls go to external services: run them off the request thread unless configured otherwise."""
    if getattr(settings, 'PUSH_ASYNC', True):
        threading.Thread(target=_safe, args=(fn,) + args, kwargs=kwargs, daemon=True).start()
    else:
        _safe(fn, *args, **kwargs)


def _safe(fn, *args, **kwargs):
    try:
        fn(*args, **kwargs)
    except Exception:
        logger.exception("Web push dispatch failed")


# --------------------------------------------------------------------------- recipients

def staff_subscriptions(roles: List[str]):
    from .models import PushAudience, PushSubscription
    return PushSubscription.objects.filter(audience=PushAudience.STAFF, user__is_active=True, user__role__in=roles)


def session_subscriptions(public_token: str):
    from .models import PushAudience, PushSubscription
    return PushSubscription.objects.filter(audience=PushAudience.CUSTOMER, table_session__public_token=public_token)


def _attach_waiting_guests(payload: dict):
    """When a table opens, the phone that asked for it (subscribed with the QR token) joins the session."""
    from apps.tables.models import TableSession
    from .models import PushAudience, PushSubscription
    session = TableSession.objects.filter(public_token=payload.get('public_token')).first()
    if not session or not session.primary_device_token or not payload.get('qr_token'):
        return []
    subs = PushSubscription.objects.filter(audience=PushAudience.CUSTOMER, qr_token=payload['qr_token'],
                                           device_token=session.primary_device_token, table_session__isnull=True)
    subs.update(table_session=session)
    return list(PushSubscription.objects.filter(table_session=session))


# --------------------------------------------------------------------------- event routing

def notify_for_event(event_type: str, payload: dict) -> None:
    if not is_enabled():
        return
    _run(_route, event_type, payload or {})


def _route(event_type: str, p: dict) -> None:
    table = p.get('table_name') or p.get('table_label') or p.get('table_code') or ''
    code = p.get('order_code') or ''
    token = p.get('session_token') or p.get('public_token') or ''
    qr = p.get('qr_token') or ''
    guest_url = f"/t/{qr}/" if qr else '/'
    cashier_url = '/cashier/#pos-panel/queue'

    if event_type == 'TABLE_ACTIVATION_REQUESTED':
        send(staff_subscriptions(['CASHIER']), 'activation_requested', url=cashier_url, tag=f"act-{p.get('table_id', '')}",
             table=table)
    elif event_type == 'ORDER_CREATED':
        send(staff_subscriptions(['CASHIER']), 'order_waiting', url=cashier_url, tag=f"ord-{code}", table=table, code=code)
    elif event_type == 'ORDER_CONFIRMED':
        send(staff_subscriptions(['KITCHEN']), 'kitchen_new', url='/kitchen/', tag=f"kds-{code}", table=table, code=code)
        if token:
            send(session_subscriptions(token), 'guest_confirmed', url=guest_url, tag=f"ord-{code}", code=code)
    elif event_type == 'ORDER_REJECTED' and token:
        send(session_subscriptions(token), 'guest_rejected', url=guest_url, tag=f"ord-{code}", code=code)
    elif event_type == 'ITEM_STATUS_CHANGED' and token and p.get('status') == 'READY':
        items = ', '.join(f"{i.get('quantity')}x {i.get('name')}" for i in (p.get('items') or [])[:3])
        send(session_subscriptions(token), 'guest_ready', url=guest_url, tag=f"ready-{code}", items=items or code)
    elif event_type == 'BILL_REQUESTED':
        send(staff_subscriptions(['CASHIER']), 'bill_requested', url='/cashier/#service-panel/bills',
             tag=f"bill-{p.get('session_id', '')}", table=table or p.get('label', ''))
        if token:
            send(session_subscriptions(token), 'guest_bill', url=guest_url, tag='bill')
    elif event_type == 'PAYMENT_COMPLETED' and token:
        send(session_subscriptions(token), 'guest_paid', url=guest_url, tag='paid')
    elif event_type == 'KITCHEN_NOTICE':
        send(staff_subscriptions(['CASHIER']), 'kitchen_notice', url='/cashier/#service-panel/kitchen',
             tag=f"notice-{p.get('notice_id', '')}", table=table, menu=p.get('menu_name') or '')
    elif event_type == 'SESSION_OPENED':
        subs = _attach_waiting_guests(p)
        if subs:
            send(subs, 'guest_opened', url=guest_url, tag='opened')
    elif event_type == 'SESSION_CLOSED' and token:
        session_subscriptions(token).delete()


def notify_idle_table(session, minutes: int) -> None:
    if not is_enabled():
        return
    _run(lambda: send(staff_subscriptions(['CASHIER']), 'table_idle', url='/cashier/#service-panel/idle',
                      tag=f"idle-{session.id}", table=session.table.display_name, minutes=minutes))
