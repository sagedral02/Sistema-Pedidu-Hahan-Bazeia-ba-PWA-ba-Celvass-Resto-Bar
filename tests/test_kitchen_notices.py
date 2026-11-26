"""Kitchen reports a dish as out of stock; the cashier receives a notice and closes the dish."""
import uuid

import pytest
from rest_framework.test import APIClient

from apps.audit.models import AuditEvent
from apps.catalog.availability import CLOSED_TODAY, menu_availability
from apps.kitchen.models import KitchenNotice, NoticeStatus
from apps.kitchen.notices import acknowledge_notice, report_out_of_stock, resolve_notice
from apps.ordering.models import ItemStatus, OrderItem, OrderSource, OrderStatus
from apps.ordering.services import submit_customer_order
from apps.outbox.models import OutboxEvent


def make(session, lines, key, user=None):
    return submit_customer_order(
        table_session=session, idempotency_key=key, source=OrderSource.CASHIER, created_by=user,
        items_data=[{'menu_item_id': i.id, 'quantity': q} for i, q in lines])


def line(order, item):
    return OrderItem.objects.get(order=order, menu_item=item)


def client_for(user):
    c = APIClient()
    if user is not None:
        c.force_authenticate(user=user)
    return c


@pytest.mark.django_db
def test_report_marks_the_line_unavailable_and_creates_one_notice(active_session, menu_item_fish, menu_item_corn, kitchen_user, cashier_user):
    o = make(active_session, [(menu_item_fish, 2), (menu_item_corn, 1)], 'a', cashier_user)
    order, notice = report_out_of_stock(item=line(o, menu_item_fish), staff_user=kitchen_user, reason='habis')
    assert line(o, menu_item_fish).status == ItemStatus.UNAVAILABLE
    assert str(order.grand_total) == '3.50'
    assert notice.status == NoticeStatus.OPEN and notice.menu_name_snapshot == 'Ikan Saboko'
    assert notice.order_code == o.order_code and notice.message == 'habis'
    assert OutboxEvent.objects.filter(event_type='KITCHEN_NOTICE').count() == 1
    assert AuditEvent.objects.filter(action='KITCHEN_NOTICE_CREATE').count() == 1


@pytest.mark.django_db
def test_second_report_for_the_same_dish_does_not_duplicate(active_session, session_two, menu_item_fish, kitchen_user, cashier_user):
    o1 = make(active_session, [(menu_item_fish, 1)], 'a', cashier_user)
    o2 = make(session_two, [(menu_item_fish, 1)], 'b', cashier_user)
    _, first = report_out_of_stock(item=line(o1, menu_item_fish), staff_user=kitchen_user)
    _, second = report_out_of_stock(item=line(o2, menu_item_fish), staff_user=kitchen_user)
    assert first.id == second.id
    assert KitchenNotice.objects.count() == 1


@pytest.mark.django_db
def test_sold_out_action_closes_the_menu_and_settles_the_notice(active_session, session_two, menu_item_fish, kitchen_user, cashier_user):
    o1 = make(active_session, [(menu_item_fish, 1)], 'a', cashier_user)
    o2 = make(session_two, [(menu_item_fish, 3)], 'b', cashier_user)
    _, notice = report_out_of_stock(item=line(o1, menu_item_fish), staff_user=kitchen_user, reason='habis')
    res = client_for(cashier_user).post(f'/api/v1/cashier/kitchen-notices/{notice.id}/sold-out/')
    assert res.status_code == 200, res.content
    assert res.json()['data']['cancelled_items'] == 3, 'the queued line in the other order is cancelled'
    notice.refresh_from_db()
    assert notice.status == NoticeStatus.RESOLVED and notice.resolution == 'MENU_CLOSED'
    assert menu_availability(menu_item_fish).state == CLOSED_TODAY
    o2.refresh_from_db()
    assert o2.status == OrderStatus.CANCELLED


@pytest.mark.django_db
def test_acknowledge_then_resolve(active_session, menu_item_fish, kitchen_user, cashier_user):
    o = make(active_session, [(menu_item_fish, 1)], 'a', cashier_user)
    _, notice = report_out_of_stock(item=line(o, menu_item_fish), staff_user=kitchen_user)
    n = acknowledge_notice(notice=notice, staff_user=cashier_user)
    assert n.status == NoticeStatus.ACKNOWLEDGED and n.acknowledged_by == cashier_user
    n = resolve_notice(notice=n, staff_user=cashier_user)
    assert n.status == NoticeStatus.RESOLVED and n.resolution == 'DISMISSED'
    # repeating is harmless
    assert resolve_notice(notice=n, staff_user=cashier_user).status == NoticeStatus.RESOLVED


@pytest.mark.django_db
def test_notices_return_after_a_resolved_one_for_the_same_dish(active_session, session_two, menu_item_fish, kitchen_user, cashier_user):
    o1 = make(active_session, [(menu_item_fish, 1)], 'a', cashier_user)
    _, n1 = report_out_of_stock(item=line(o1, menu_item_fish), staff_user=kitchen_user)
    resolve_notice(notice=n1, staff_user=cashier_user)
    o2 = make(session_two, [(menu_item_fish, 1)], 'b', cashier_user)
    _, n2 = report_out_of_stock(item=line(o2, menu_item_fish), staff_user=kitchen_user)
    assert n2.id != n1.id


# ----------------------------------------------------------------------------- API

@pytest.mark.django_db
def test_api_kitchen_reports_and_cashier_lists(active_session, menu_item_fish, kitchen_user, cashier_user):
    o = make(active_session, [(menu_item_fish, 1)], 'a', cashier_user)
    item = line(o, menu_item_fish)
    res = client_for(kitchen_user).post(f'/api/v1/kitchen/items/{item.id}/unavailable/', {'reason': 'habis'}, format='json')
    assert res.status_code == 200, res.content
    assert res.json()['data']['notice']['menu_name'] == 'Ikan Saboko'
    listed = client_for(cashier_user).get('/api/v1/cashier/kitchen-notices/').json()['data']
    assert [n['menu_name'] for n in listed] == ['Ikan Saboko']
    client_for(cashier_user).post(f"/api/v1/cashier/kitchen-notices/{listed[0]['id']}/resolve/")
    assert client_for(cashier_user).get('/api/v1/cashier/kitchen-notices/').json()['data'] == []


@pytest.mark.django_db
def test_api_item_that_cannot_run_out_is_refused(active_session, menu_item_fish, kitchen_user, cashier_user):
    from apps.kitchen.services import change_items_status
    o = make(active_session, [(menu_item_fish, 1)], 'a', cashier_user)
    change_items_status(order=o, to_status=ItemStatus.READY, staff_user=kitchen_user)
    item = line(o, menu_item_fish)
    res = client_for(kitchen_user).post(f'/api/v1/kitchen/items/{item.id}/unavailable/')
    assert res.status_code == 400
    assert KitchenNotice.objects.count() == 0


@pytest.mark.django_db
def test_api_permissions(active_session, menu_item_fish, kitchen_user, cashier_user, admin_user):
    o = make(active_session, [(menu_item_fish, 1)], 'a', cashier_user)
    item = line(o, menu_item_fish)
    url = f'/api/v1/kitchen/items/{item.id}/unavailable/'
    assert client_for(None).post(url).status_code in (401, 403)
    assert client_for(cashier_user).post(url).status_code == 403
    notices = '/api/v1/cashier/kitchen-notices/'
    assert client_for(kitchen_user).get(notices).status_code == 403
    assert client_for(None).get(notices).status_code in (401, 403)
    assert client_for(admin_user).get(notices).status_code == 200
    ghost = uuid.uuid4()
    assert client_for(cashier_user).post(f'/api/v1/cashier/kitchen-notices/{ghost}/sold-out/').status_code == 404
    assert client_for(kitchen_user).post(f'/api/v1/cashier/kitchen-notices/{ghost}/resolve/').status_code == 403


@pytest.mark.django_db
def test_cashier_events_include_notice_events():
    from apps.outbox.services import CASHIER_EVENTS
    assert {'KITCHEN_NOTICE', 'KITCHEN_NOTICE_UPDATED', 'MENU_AVAILABILITY_CHANGED'} <= CASHIER_EVENTS
