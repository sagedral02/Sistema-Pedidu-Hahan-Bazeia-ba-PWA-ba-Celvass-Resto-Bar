"""Plan 3: cashier table flow (progress, request bill, padlock / walkout, idle alert) and order rules."""
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from django.test import Client
from django.utils import timezone
from rest_framework.test import APIClient

from apps.audit.models import AuditEvent
from apps.kitchen.services import change_items_status
from apps.ordering.models import ItemStatus, Order, OrderSource, OrderStatus
from apps.ordering.services import submit_customer_order
from apps.payments.billing import request_bill
from apps.payments.models import Bill, BillStatus
from apps.payments.services import record_cash_payment
from apps.reporting.overview import build_overview
from apps.reporting.reports import Period, build_report
from apps.tables.models import CloseKind, SessionStatus, TableSession, TableStatus
from apps.tables.progress import session_progress

ROOT = Path(__file__).resolve().parent.parent


def api(user):
    c = APIClient()
    c.force_authenticate(user=user)
    return c


def order(session, user, *lines, source=OrderSource.CASHIER, key='o1'):
    return submit_customer_order(table_session=session, idempotency_key=key, source=source, created_by=user,
                                 items_data=[{'menu_item_id': m.id, 'quantity': q} for m, q in lines])


def serve(o, kitchen_user, to=ItemStatus.SERVED):
    for step in (ItemStatus.COOKING, ItemStatus.READY, ItemStatus.SERVED):
        change_items_status(order=o, to_status=step, staff_user=kitchen_user)
        if step == to:
            break


def table_row(user, table):
    rows = api(user).get('/api/v1/cashier/tables/').json()['data']
    return next(r for r in rows if r['id'] == str(table.id))


def close(user, session, **body):
    return api(user).post(f'/api/v1/cashier/table-sessions/{session.id}/close/', body, format='json')


# ----------------------------------------------------------------------------- progress on the card

@pytest.mark.django_db
def test_card_shows_orders_and_item_states_until_everything_is_served(active_session, cashier_user, kitchen_user,
                                                                       menu_item_fish):
    s = table_row(cashier_user, active_session.table)['active_session']
    assert s['progress'] == [] and s['all_delivered'] is False

    o = order(active_session, cashier_user, (menu_item_fish, 2))
    serve(o, kitchen_user, to=ItemStatus.COOKING)
    s = table_row(cashier_user, active_session.table)['active_session']
    assert s['pending_items'] == 2 and s['all_delivered'] is False
    line = s['progress'][0]['items'][0]
    assert line == {'name': menu_item_fish.name, 'quantity': 2, 'status': 'COOKING', 'note': ''}
    assert s['progress'][0]['code'] == o.order_code

    serve(o, kitchen_user)
    s = table_row(cashier_user, active_session.table)['active_session']
    assert s['served_items'] == 2 and s['pending_items'] == 0 and s['all_delivered'] is True


@pytest.mark.django_db
def test_customer_order_waiting_for_the_cashier_is_listed_and_blocks_the_bill(active_session, cashier_user,
                                                                               kitchen_user, menu_item_fish):
    served = order(active_session, cashier_user, (menu_item_fish, 1))
    serve(served, kitchen_user)
    order(active_session, None, (menu_item_fish, 1), source=OrderSource.CUSTOMER, key='c1')
    s = table_row(cashier_user, active_session.table)['active_session']
    assert s['waiting_items'] == 1 and s['all_delivered'] is False
    assert any(i['status'] == 'WAITING' for o in s['progress'] for i in o['items'])


@pytest.mark.django_db
def test_idle_flag_uses_the_admin_limit(active_session, cashier_user, kitchen_user, menu_item_fish, restaurant):
    o = order(active_session, cashier_user, (menu_item_fish, 1))
    serve(o, kitchen_user)
    later = timezone.now() + timedelta(minutes=50)
    assert session_progress(active_session, idle_minutes=45, now=later)['is_idle'] is True
    assert session_progress(active_session, idle_minutes=0, now=later)['is_idle'] is False  # 0 = off
    assert session_progress(active_session, idle_minutes=60, now=later)['is_idle'] is False
    # food still in the kitchen is activity: never idle
    o2 = order(active_session, cashier_user, (menu_item_fish, 1), key='o2')
    change_items_status(order=o2, to_status=ItemStatus.COOKING, staff_user=kitchen_user)
    assert session_progress(active_session, idle_minutes=45, now=later + timedelta(hours=2))['is_idle'] is False


@pytest.mark.django_db
def test_idle_alert_is_raised_once_per_session(active_session, cashier_user, kitchen_user, menu_item_fish, restaurant):
    restaurant.idle_alert_minutes = 1
    restaurant.save()
    o = order(active_session, cashier_user, (menu_item_fish, 1))
    serve(o, kitchen_user)
    TableSession.objects.filter(id=active_session.id).update(opened_at=timezone.now() - timedelta(minutes=10))
    from apps.ordering.models import OrderItem
    OrderItem.objects.filter(order=o).update(status_changed_at=timezone.now() - timedelta(minutes=10))
    Order.objects.filter(id=o.id).update(updated_at=timezone.now() - timedelta(minutes=10))

    s = table_row(cashier_user, active_session.table)['active_session']
    assert s['is_idle'] is True and s['idle_minutes'] >= 9
    active_session.refresh_from_db()
    first = active_session.idle_alerted_at
    assert first is not None
    table_row(cashier_user, active_session.table)
    active_session.refresh_from_db()
    assert active_session.idle_alerted_at == first


@pytest.mark.django_db
def test_idle_limit_is_an_admin_setting(admin_user, restaurant):
    c = api(admin_user)
    assert c.get('/api/v1/admin/restaurant/').json()['data']['idle_alert_minutes'] == 45
    assert c.patch('/api/v1/admin/restaurant/', {'idle_alert_minutes': 20}, format='json').status_code == 200
    restaurant.refresh_from_db()
    assert restaurant.idle_alert_minutes == 20
    for bad in (-1, 601, 'abc'):
        assert c.patch('/api/v1/admin/restaurant/', {'idle_alert_minutes': bad}, format='json').status_code == 400
    assert AuditEvent.objects.filter(action='RESTAURANT_SETTINGS_UPDATE').exists()


# ----------------------------------------------------------------------------- padlock

@pytest.mark.django_db
def test_normal_close_needs_a_settled_bill(active_session, cashier_user, kitchen_user, menu_item_fish):
    o = order(active_session, cashier_user, (menu_item_fish, 1))
    serve(o, kitchen_user)
    res = close(cashier_user, active_session, mode='normal')
    assert res.status_code == 409 and res.json()['error']['code'] == 'SESSION_UNPAID'
    active_session.refresh_from_db()
    assert active_session.status == SessionStatus.OPEN

    request_bill(session=active_session, requested_by=cashier_user)
    record_cash_payment(session=active_session, cashier_user=cashier_user, tendered_amount=Decimal('50'))
    active_session.refresh_from_db()
    if active_session.status == SessionStatus.CLOSED:
        return  # paying a fully served table closes it automatically
    res = close(cashier_user, active_session, mode='normal')
    assert res.status_code == 200 and res.json()['data']['close_kind'] == CloseKind.NORMAL


@pytest.mark.django_db
def test_empty_table_closes_normally(active_session, cashier_user):
    res = close(cashier_user, active_session)
    assert res.status_code == 200
    active_session.refresh_from_db()
    assert active_session.status == SessionStatus.CLOSED and active_session.close_kind == CloseKind.NORMAL
    assert active_session.table.status == TableStatus.AVAILABLE


@pytest.mark.django_db
def test_walkout_needs_a_reason_and_records_the_loss(active_session, cashier_user, kitchen_user, menu_item_fish):
    o = order(active_session, cashier_user, (menu_item_fish, 2))
    serve(o, kitchen_user)
    request_bill(session=active_session, requested_by=cashier_user)
    pending = order(active_session, None, (menu_item_fish, 1), source=OrderSource.CUSTOMER, key='c9')

    res = close(cashier_user, active_session, mode='walkout', reason=' ')
    assert res.status_code == 400 and res.json()['error']['code'] == 'WALKOUT_REASON_REQUIRED'

    res = close(cashier_user, active_session, mode='walkout', reason='Sai la selu')
    assert res.status_code == 200, res.content
    data = res.json()['data']
    assert data['close_kind'] == CloseKind.WALKOUT and Decimal(data['unpaid_amount']) == o.grand_total
    active_session.refresh_from_db()
    assert active_session.status == SessionStatus.CLOSED and active_session.unpaid_amount == o.grand_total
    assert active_session.table.status == TableStatus.AVAILABLE
    assert Order.objects.get(id=pending.id).status == OrderStatus.CANCELLED
    assert Bill.objects.filter(table_session=active_session, status=BillStatus.WALKOUT).exists()
    assert AuditEvent.objects.filter(action='TABLE_WALKOUT', entity_id=str(active_session.id)).exists()


@pytest.mark.django_db
def test_walkout_bill_does_not_block_closing_the_shift(active_session, cashier_user, kitchen_user, menu_item_fish, settings):
    from apps.shifts.services import close_shift, open_shift
    shift = open_shift(cashier=cashier_user, opening_cash=Decimal('0'))
    o = order(active_session, cashier_user, (menu_item_fish, 1))
    serve(o, kitchen_user)
    request_bill(session=active_session, requested_by=cashier_user)
    assert close(cashier_user, active_session, mode='walkout', reason='Lari').status_code == 200
    closed = close_shift(shift=shift, counted_cash=Decimal('0'))
    assert closed.status == 'CLOSED'


@pytest.mark.django_db
def test_walkout_appears_in_exceptions_report_and_overview(active_session, cashier_user, cashier_two, kitchen_user,
                                                           menu_item_fish, admin_user):
    o = order(active_session, cashier_user, (menu_item_fish, 1))
    serve(o, kitchen_user)
    close(cashier_user, active_session, mode='walkout', reason='Lari')
    today = timezone.localdate()
    report = build_report('exceptions', Period(today, today, 'today'), admin_user)
    row = next(r for r in report.rows if r[1].key == 'rpt_kind_walkout')
    assert row[4] == o.grand_total and row[5] == 'Lari' and row[2] == o.order_code
    summary = {s.label_key: s.value for s in report.summary}
    assert summary['rpt_sum_walkout'] == 1 and summary['rpt_sum_walkout_amount'] == o.grand_total
    unpaid = build_overview('today')['kpi']['unpaid']
    assert unpaid['value'] == float(o.grand_total) and unpaid['count'] == 1
    # a cashier only sees the walkouts they recorded
    other = build_report('exceptions', Period(today, today, 'today'), cashier_two)
    assert not any(r[1].key == 'rpt_kind_walkout' for r in other.rows)


# ----------------------------------------------------------------------------- order rules

@pytest.mark.django_db
def test_cashier_can_add_to_a_table_whose_bill_was_requested(active_session, cashier_user, kitchen_user, menu_item_fish):
    o = order(active_session, cashier_user, (menu_item_fish, 1))
    serve(o, kitchen_user)
    request_bill(session=active_session, requested_by=cashier_user)
    res = api(cashier_user).post('/api/v1/cashier/orders/create/', {
        'destination': 'TABLE', 'table_id': str(active_session.table_id),
        'items': [{'menu_item_id': str(menu_item_fish.id), 'quantity': 1}]}, format='json', HTTP_IDEMPOTENCY_KEY='more')
    assert res.status_code == 201, res.content


@pytest.mark.django_db
def test_pending_activation_no_longer_exposes_the_device_token(restaurant, table_one, cashier_user):
    APIClient().post(f'/api/v1/public/tables/{table_one.qr_token}/request-activation/', {'guest_count': 2}, format='json')
    row = table_row(cashier_user, table_one)
    assert row['pending_activation'] and 'device_token' not in row['pending_activation']


# ----------------------------------------------------------------------------- cashier UI (static checks)

def _js(name):
    return (ROOT / 'static' / 'js' / name).read_text(encoding='utf-8')


def test_table_card_buttons_follow_the_flow():
    js = _js('cashier.js')
    tile = js.split('renderTableTile(table) {', 1)[1].split('\n  }\n', 1)[0]
    assert 'openPaymentModal' not in tile, 'payment is taken in Atendimentu only'
    assert 'js-bill-requested' in tile and 'disabled aria-disabled="true"' in tile
    assert "csh_btn_order'" in tile and 'js-order-more' in tile and "state === 'ready_bill'" in tile
    free = tile.split('} else {', 2)[-1]
    assert 'posOrder.open' not in free, 'an empty table only offers "open session"'
    assert 'toggleTableDetail' in tile and 'aria-expanded' in tile
    request_bill_fn = js.split('async requestBill(sessionId, name) {', 1)[1].split('\n  }\n', 1)[0]
    assert 'openPaymentModal' not in request_bill_fn


def test_padlock_dialog_and_idle_tab_exist(cashier_user):
    c = Client()
    c.force_login(cashier_user)
    html = c.get('/cashier/').content.decode()
    for marker in ('id="closeSessionModal"', 'id="close-mode-normal"', 'id="close-mode-walkout"',
                   'id="close-session-reason"', 'data-view="idle"', 'id="idle-tables-container"',
                   'data-count-for="idle"', 'id="pos-order-no-tables"'):
        assert marker in html, marker
    js = _js('cashier.js')
    assert "mode: walkout ? 'walkout' : 'normal'" in js and 'renderIdleTables' in js


def test_order_screen_is_a_single_column_list_with_sideways_chips_only():
    css = (ROOT / 'static' / 'css' / 'style.css').read_text(encoding='utf-8')
    assert '#posOrderModal .modal-body { overflow-x: hidden; }' in css
    assert '.pos-order > * { min-width: 0; }' in css
    assert '#posOrderModal .pos-order-grid { display: flex; flex-direction: column;' in css
    chips = css.split('#posOrderModal .chip-row {', 1)[1].split('}', 1)[0]
    assert 'overflow-x: auto' in chips and 'flex-wrap: nowrap' in chips
    js = _js('pos-order.js')
    assert 'class="pos-row' in js and 'orderableTables()' in js and 'guest_count' not in js
