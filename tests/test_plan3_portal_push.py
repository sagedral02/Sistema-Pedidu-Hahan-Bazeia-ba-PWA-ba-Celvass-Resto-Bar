"""Plan 3: home portal, occupied-table page (no reload loop), QR scanner and Web Push."""
from pathlib import Path
from unittest import mock

import pytest
from django.test import Client
from django.urls import reverse
from rest_framework.test import APIClient

from apps.catalog.models import ItemAvailability
from apps.pwa import push
from apps.pwa.models import PushAudience, PushSubscription
from apps.tables.models import TableSession

ROOT = Path(__file__).resolve().parent.parent
SUB = {'endpoint': 'https://push.example.com/abc', 'keys': {'p256dh': 'BPk' + 'x' * 60, 'auth': 'a' * 16}}


def device(token='phone-1'):
    c = Client()
    c.cookies['celvass_device_id'] = token
    return c


def device_api(token='phone-1'):
    c = APIClient()
    c.cookies['celvass_device_id'] = token
    return c


@pytest.fixture
def vapid(settings):
    settings.VAPID_PUBLIC_KEY = 'BPUBLIC'
    settings.VAPID_PRIVATE_KEY = 'PRIVATE'
    settings.PUSH_ASYNC = False
    return settings


# ----------------------------------------------------------------------------- home portal

@pytest.mark.django_db
def test_home_shows_the_menu_read_only_and_restaurant_info(restaurant, menu_item_fish, menu_item_corn, category_main):
    restaurant.description = 'Hahan tasi furak'
    restaurant.whatsapp = '67077001234'
    restaurant.email = 'info@celvass.tl'
    restaurant.instagram = 'https://instagram.com/celvass'
    restaurant.save()
    menu_item_corn.availability = ItemAvailability.INACTIVE
    menu_item_corn.save()

    res = Client().get('/')
    assert res.status_code == 200
    html = res.content.decode()
    assert menu_item_fish.name in html and menu_item_corn.name not in html
    assert 'menu-card is-readonly' in html and 'menu-card-add' not in html  # cannot order from home
    assert 'Hahan tasi furak' in html and 'wa.me/67077001234' in html and 'info@celvass.tl' in html
    assert 'instagram.com/celvass' in html
    assert 'data-qr-scan' in html and 'js/home.js' in html and reverse('login') in html
    assert 'data-category="' + category_main.slug + '"' in html


@pytest.mark.django_db
def test_home_shows_closed_state_and_hours(restaurant):
    from datetime import time
    from apps.restaurants.models import OpeningHours
    for day in range(7):
        OpeningHours.objects.create(restaurant=restaurant, weekday=day, is_closed=True)
    html = Client().get('/').content.decode()
    assert 'home_closed_now' in html and 'home_hours_title' in html


@pytest.mark.django_db
def test_home_page_query_count_is_bounded(restaurant, menu_item_fish, menu_item_corn, django_assert_max_num_queries):
    with django_assert_max_num_queries(40):
        assert Client().get('/').status_code == 200


# ----------------------------------------------------------------------------- occupied table

@pytest.mark.django_db
def test_other_phone_gets_the_occupied_page_with_scanner(active_session, table_one, restaurant):
    restaurant.whatsapp = '67077001234'
    restaurant.save()
    assert device('owner').get(f'/t/{table_one.qr_token}/').status_code == 200
    res = device('intruder').get(f'/t/{table_one.qr_token}/')
    assert res.status_code == 403
    html = res.content.decode()
    assert 'occupied_title' in html and 'data-qr-scan' in html and 'wa.me/67077001234' in html
    assert 'customer.js' not in html, 'no polling script on the occupied page, so nothing can reload in a loop'


@pytest.mark.django_db
def test_staff_on_a_claimed_table_sees_the_occupied_page_instead_of_a_broken_menu(active_session, table_one,
                                                                                  cashier_user):
    assert device('owner').get(f'/t/{table_one.qr_token}/').status_code == 200
    c = device('staff-phone')
    c.force_login(cashier_user)
    res = c.get(f'/t/{table_one.qr_token}/')
    assert res.status_code == 403
    html = res.content.decode()
    assert 'occupied_cashier_note' in html and reverse('cashier-portal') in html
    assert '#pos-panel/takeaway' in html and 'occupied_open_takeaway' in html
    assert 'occupied_view_menu' not in html
    active_session.refresh_from_db()
    assert active_session.primary_device_token == 'owner'


@pytest.mark.django_db
@pytest.mark.parametrize('user_fixture', ['admin_user', 'kitchen_user'])
def test_admin_and_kitchen_on_an_occupied_table_get_no_scanner_or_pos(active_session, table_one, user_fixture,
                                                                       request):
    assert device('owner').get(f'/t/{table_one.qr_token}/').status_code == 200
    c = device('staff-phone')
    c.force_login(request.getfixturevalue(user_fixture))
    res = c.get(f'/t/{table_one.qr_token}/')
    assert res.status_code == 403
    html = res.content.decode()
    assert 'occupied_inside_note' in html and 'occupied_view_menu' in html
    assert 'data-qr-scan' not in html and reverse('cashier-portal') not in html


def test_customer_script_never_reloads_without_the_guard():
    js = (ROOT / 'static' / 'js' / 'customer.js').read_text(encoding='utf-8')
    assert js.count('window.location.reload()') == 1, 'only safeReload() may reload'
    guard = js.split('safeReload() {', 1)[1].split('\n  }\n', 1)[0]
    assert 'sessionStorage' in guard and '15000' in guard and 'showLockedNotice' in guard
    assert "if (e.code === 'DEVICE_LOCKED') this.safeReload();" in js


# ----------------------------------------------------------------------------- QR scanner

def test_qr_scanner_only_accepts_table_links_of_this_site():
    js = (ROOT / 'static' / 'js' / 'qr-scanner.js').read_text(encoding='utf-8')
    assert 'url.origin !== window.location.origin' in js
    assert "TABLE_PATH = /^\\/t\\/([A-Za-z0-9_-]{8,128})\\/?$/" in js
    assert 'BarcodeDetector' in js and "/static/vendor/jsqr/jsQR.js" in js
    assert 'getTracks().forEach((track) => track.stop())' in js
    assert (ROOT / 'static' / 'vendor' / 'jsqr' / 'jsQR.js').stat().st_size > 100_000
    sw = (ROOT / 'static' / 'js' / 'sw.js').read_text(encoding='utf-8')
    assert "'/static/js/qr-scanner.js'" in sw and "'/static/js/push.js'" in sw


@pytest.mark.django_db
def test_scanner_is_loaded_on_every_page_and_offered_on_error_pages(restaurant):
    html = Client().get('/t/not-a-real-token-123/').content.decode()
    assert 'js/qr-scanner.js' in html and 'data-qr-scan' in html


# ----------------------------------------------------------------------------- push: config and subscribe

@pytest.mark.django_db
def test_push_is_off_without_keys(cashier_user):
    assert APIClient().get('/api/v1/push/config/').json()['data'] == {'enabled': False, 'public_key': ''}
    c = APIClient()
    c.force_authenticate(user=cashier_user)
    assert c.post('/api/v1/staff/push/subscribe/', {'subscription': SUB}, format='json').status_code == 409
    push.notify_for_event('ORDER_CREATED', {'table_name': 'Meja 01'})  # no error


@pytest.mark.django_db
def test_staff_subscribe_and_unsubscribe(vapid, cashier_user, kitchen_user):
    assert APIClient().get('/api/v1/push/config/').json()['data'] == {'enabled': True, 'public_key': 'BPUBLIC'}
    c = APIClient()
    c.force_authenticate(user=cashier_user)
    assert c.post('/api/v1/staff/push/subscribe/', {'subscription': SUB, 'lang': 'id'}, format='json').status_code == 201
    sub = PushSubscription.objects.get()
    assert sub.audience == PushAudience.STAFF and sub.user == cashier_user and sub.lang == 'id'
    assert c.post('/api/v1/staff/push/subscribe/', {'subscription': {'endpoint': 'http://x'}}, format='json').status_code == 400
    assert APIClient().post('/api/v1/staff/push/subscribe/', {'subscription': SUB}, format='json').status_code in (401, 403)

    other = APIClient()
    other.force_authenticate(user=kitchen_user)
    assert other.post('/api/v1/push/unsubscribe/', {'endpoint': SUB['endpoint']}, format='json').status_code == 403
    assert c.post('/api/v1/push/unsubscribe/', {'endpoint': SUB['endpoint']}, format='json').status_code == 200
    assert not PushSubscription.objects.exists()


@pytest.mark.django_db
def test_guest_subscription_requires_owning_the_session(vapid, active_session, table_one):
    assert device('owner').get(f'/t/{table_one.qr_token}/').status_code == 200  # claims the table
    body = {'subscription': SUB, 'qr_token': table_one.qr_token, 'session_token': active_session.public_token}
    assert device_api('intruder').post('/api/v1/public/push/subscribe/', body, format='json').status_code == 403
    res = device_api('owner').post('/api/v1/public/push/subscribe/', body, format='json')
    assert res.status_code == 201 and res.json()['data']['waiting_for_table'] is False
    assert PushSubscription.objects.get().table_session_id == active_session.id
    bad = dict(body, qr_token='unknown-token')
    assert device_api('owner').post('/api/v1/public/push/subscribe/', bad, format='json').status_code == 404


# ----------------------------------------------------------------------------- push: routing

def _staff_sub(user, endpoint):
    return PushSubscription.objects.create(endpoint=endpoint, p256dh='k', auth='a', audience=PushAudience.STAFF,
                                           user=user, lang='en')


@pytest.mark.django_db
def test_events_reach_the_right_people_in_their_language(vapid, cashier_user, kitchen_user, active_session):
    _staff_sub(cashier_user, 'https://push.example.com/cashier')
    _staff_sub(kitchen_user, 'https://push.example.com/kitchen')
    PushSubscription.objects.create(endpoint='https://push.example.com/guest', p256dh='k', auth='a',
                                    audience=PushAudience.CUSTOMER, table_session=active_session, lang='id')
    with mock.patch('pywebpush.webpush') as wp:
        push.notify_for_event('ORDER_CREATED', {'table_name': 'Meja 01', 'order_code': 'ORD-1',
                                                'session_token': active_session.public_token})
        assert [c.kwargs['subscription_info']['endpoint'] for c in wp.call_args_list] == ['https://push.example.com/cashier']
        assert 'New order' in wp.call_args.kwargs['data']
        wp.reset_mock()
        push.notify_for_event('ORDER_CONFIRMED', {'table_name': 'Meja 01', 'order_code': 'ORD-1',
                                                  'session_token': active_session.public_token})
        endpoints = sorted(c.kwargs['subscription_info']['endpoint'] for c in wp.call_args_list)
        assert endpoints == ['https://push.example.com/guest', 'https://push.example.com/kitchen']
        guest = next(c for c in wp.call_args_list if c.kwargs['subscription_info']['endpoint'].endswith('guest'))
        assert 'Pesanan dikonfirmasi' in guest.kwargs['data']


@pytest.mark.django_db
def test_waiting_guest_joins_the_session_when_the_table_opens(vapid, restaurant, table_one, cashier_user):
    APIClient().post(f'/api/v1/public/tables/{table_one.qr_token}/request-activation/', {'guest_count': 2},
                     format='json', HTTP_COOKIE='celvass_device_id=phone-9')
    res = device_api('phone-9').post('/api/v1/public/push/subscribe/',
                                     {'subscription': SUB, 'qr_token': table_one.qr_token}, format='json')
    assert res.status_code == 201 and res.json()['data']['waiting_for_table'] is True
    from apps.tables.models import TableActivationRequest
    req = TableActivationRequest.objects.get()
    req.device_token = 'phone-9'
    req.save()
    from apps.tables.services import approve_table_activation
    session = approve_table_activation(request_id=req.id, approved_by=cashier_user)
    with mock.patch('pywebpush.webpush') as wp:
        push.notify_for_event('SESSION_OPENED', {'public_token': session.public_token, 'qr_token': table_one.qr_token})
    assert wp.call_count == 1 and 'Meza loke ona' in wp.call_args.kwargs['data']
    assert PushSubscription.objects.get().table_session_id == session.id
    push.notify_for_event('SESSION_CLOSED', {'public_token': session.public_token})
    assert not PushSubscription.objects.exists(), 'guest subscriptions end with the session'


@pytest.mark.django_db
def test_expired_subscription_is_removed(vapid, cashier_user):
    from pywebpush import WebPushException
    _staff_sub(cashier_user, 'https://push.example.com/gone')
    gone = WebPushException('gone', response=mock.Mock(status_code=410))
    with mock.patch('pywebpush.webpush', side_effect=gone):
        assert push.send(PushSubscription.objects.all(), 'order_waiting', table='T', code='C') == 0
    assert not PushSubscription.objects.exists()


@pytest.mark.django_db
def test_push_runs_after_commit_for_outbox_events(vapid, cashier_user, table_one, django_capture_on_commit_callbacks):
    _staff_sub(cashier_user, 'https://push.example.com/cashier')
    with mock.patch('pywebpush.webpush') as wp:
        with django_capture_on_commit_callbacks(execute=True):
            APIClient().post(f'/api/v1/public/tables/{table_one.qr_token}/request-activation/', {'guest_count': 2},
                             format='json')
    assert wp.call_count == 1 and 'Table opening request' in wp.call_args.kwargs['data']


def test_service_worker_shows_notifications_and_opens_same_origin_links_only():
    sw = (ROOT / 'static' / 'js' / 'sw.js').read_text(encoding='utf-8')
    assert "addEventListener('push'" in sw and 'showNotification' in sw
    assert "addEventListener('notificationclick'" in sw and 'target.origin !== self.location.origin' in sw
    js = (ROOT / 'static' / 'js' / 'push.js').read_text(encoding='utf-8')
    assert 'Notification.requestPermission' in js and "addEventListener('click'" in js


def test_vapid_key_command_prints_a_usable_pair():
    from io import StringIO
    from django.core.management import call_command
    out = StringIO()
    call_command('generate_vapid_keys', stdout=out)
    text = out.getvalue()
    pub = text.split('VAPID_PUBLIC_KEY=', 1)[1].split()[0]
    priv = text.split('VAPID_PRIVATE_KEY=', 1)[1].split()[0]
    from py_vapid import Vapid
    assert Vapid.from_string(priv).public_key is not None
    assert len(pub) == 87  # 65 bytes, base64url without padding
