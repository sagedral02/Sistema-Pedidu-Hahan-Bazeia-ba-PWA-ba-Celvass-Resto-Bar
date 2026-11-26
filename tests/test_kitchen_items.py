"""Kitchen per-item actions: service layer and API."""
import uuid

import pytest
from django.core.exceptions import ValidationError
from rest_framework.test import APIClient

from apps.audit.models import AuditEvent
from apps.kitchen.services import (
    change_items_status, mark_item_unavailable, ready_item, serve_item, start_item,
)
from apps.ordering.models import ItemStatus, OrderItem, OrderStatus
from apps.ordering.services import confirm_order_by_cashier, submit_customer_order
from apps.outbox.models import OutboxEvent

I = ItemStatus


@pytest.fixture
def order(active_session, menu_item_fish, menu_item_corn, cashier_user):
    o = submit_customer_order(
        table_session=active_session,
        items_data=[{'menu_item_id': menu_item_fish.id, 'quantity': 2}, {'menu_item_id': menu_item_corn.id, 'quantity': 1}],
        idempotency_key='k-items',
    )
    return confirm_order_by_cashier(order=o, confirmed_by=cashier_user)


def line(order, name):
    return OrderItem.objects.get(order=order, menu_name_snapshot=name)


def as_user(user):
    client = APIClient()
    if user is not None:
        client.force_authenticate(user=user)
    return client


# ----------------------------------------------------------------------------- service

@pytest.mark.django_db
def test_single_item_walks_through_and_order_follows(order, kitchen_user):
    fish, corn = line(order, 'Ikan Saboko'), line(order, "Batar Da'an")
    o = start_item(item=fish, staff_user=kitchen_user)
    assert o.status == OrderStatus.PREPARING
    fish.refresh_from_db()
    assert fish.status == I.COOKING and fish.cooking_started_at
    assert line(order, "Batar Da'an").status == I.QUEUED

    ready_item(item=fish, staff_user=kitchen_user)
    assert OrderItem.objects.get(id=fish.id).ready_at
    o = start_item(item=corn, staff_user=kitchen_user)
    o = ready_item(item=corn, staff_user=kitchen_user)
    assert o.status == OrderStatus.READY

    serve_item(item=fish, staff_user=kitchen_user)
    o = serve_item(item=corn, staff_user=kitchen_user)
    assert o.status == OrderStatus.SERVED


@pytest.mark.django_db
def test_invalid_transitions_are_rejected(order, kitchen_user):
    fish = line(order, 'Ikan Saboko')
    with pytest.raises(ValidationError):
        serve_item(item=fish, staff_user=kitchen_user)  # QUEUED -> SERVED skips the kitchen
    ready_item(item=fish, staff_user=kitchen_user)
    with pytest.raises(ValidationError):
        start_item(item=fish, staff_user=kitchen_user)  # READY cannot go back
    assert OrderItem.objects.get(id=fish.id).status == I.READY


@pytest.mark.django_db
def test_repeating_an_action_is_harmless(order, kitchen_user):
    fish = line(order, 'Ikan Saboko')
    start_item(item=fish, staff_user=kitchen_user)
    before = OutboxEvent.objects.filter(event_type='ITEM_STATUS_CHANGED').count()
    start_item(item=fish, staff_user=kitchen_user)
    assert OutboxEvent.objects.filter(event_type='ITEM_STATUS_CHANGED').count() == before
    assert OrderItem.objects.get(id=fish.id).status == I.COOKING


@pytest.mark.django_db
def test_bulk_without_ids_moves_every_eligible_line(order, kitchen_user):
    o = change_items_status(order=order, to_status=I.COOKING, staff_user=kitchen_user)
    assert set(o.items.values_list('status', flat=True)) == {I.COOKING}
    events = OutboxEvent.objects.filter(event_type='ITEM_STATUS_CHANGED')
    assert events.count() == 1, 'one event per batch'
    assert len(events.first().payload['items']) == 2


@pytest.mark.django_db
def test_bulk_with_ids_only_moves_those_lines(order, kitchen_user):
    fish = line(order, 'Ikan Saboko')
    change_items_status(order=order, to_status=I.COOKING, item_ids=[fish.id], staff_user=kitchen_user)
    assert OrderItem.objects.get(id=fish.id).status == I.COOKING
    assert line(order, "Batar Da'an").status == I.QUEUED


@pytest.mark.django_db
def test_item_of_another_order_is_refused(order, kitchen_user):
    with pytest.raises(ValidationError):
        change_items_status(order=order, to_status=I.COOKING, item_ids=[uuid.uuid4()], staff_user=kitchen_user)


@pytest.mark.django_db
def test_unavailable_items_leave_the_bill_and_the_order_cancels_when_none_left(order, kitchen_user):
    fish, corn = line(order, 'Ikan Saboko'), line(order, "Batar Da'an")
    o = mark_item_unavailable(item=fish, staff_user=kitchen_user, reason='habis')
    assert str(o.grand_total) == '3.50'
    assert o.status == OrderStatus.CONFIRMED
    o = mark_item_unavailable(item=corn, staff_user=kitchen_user, reason='habis')
    assert o.status == OrderStatus.CANCELLED
    assert OutboxEvent.objects.filter(event_type='ORDER_CANCELLED').exists()


@pytest.mark.django_db
def test_audit_entry_per_line_and_events_written(order, kitchen_user):
    change_items_status(order=order, to_status=I.COOKING, staff_user=kitchen_user)
    assert AuditEvent.objects.filter(action='KITCHEN_ITEM_START', entity_type='OrderItem').count() == 2
    assert OutboxEvent.objects.filter(event_type='ORDER_PREPARING').exists()


@pytest.mark.django_db
def test_order_outside_the_kitchen_cannot_change(active_session, menu_item_fish):
    o = submit_customer_order(
        table_session=active_session,
        items_data=[{'menu_item_id': menu_item_fish.id, 'quantity': 1}], idempotency_key='k-wait')
    assert o.status == OrderStatus.WAITING_CASHIER_CONFIRMATION
    with pytest.raises(ValidationError):
        change_items_status(order=o, to_status=I.COOKING)


# ----------------------------------------------------------------------------- API

@pytest.mark.django_db
def test_api_single_item_actions(order, kitchen_user):
    fish = line(order, 'Ikan Saboko')
    client = as_user(kitchen_user)
    for action in ('start', 'ready', 'served'):
        res = client.post(f'/api/v1/kitchen/items/{fish.id}/{action}/')
        assert res.status_code == 200, res.content
    assert OrderItem.objects.get(id=fish.id).status == I.SERVED


@pytest.mark.django_db
def test_api_invalid_transition_returns_conflict_payload(order, kitchen_user):
    fish = line(order, 'Ikan Saboko')
    res = as_user(kitchen_user).post(f'/api/v1/kitchen/items/{fish.id}/served/')
    assert res.status_code == 400
    assert res.json()['error']['code'] == 'ORDER_INVALID_STATE'


@pytest.mark.django_db
def test_api_unknown_item_and_order_are_404(kitchen_user):
    client = as_user(kitchen_user)
    assert client.post(f'/api/v1/kitchen/items/{uuid.uuid4()}/start/').status_code == 404
    assert client.post(f'/api/v1/kitchen/orders/{uuid.uuid4()}/items/start/').status_code == 404


@pytest.mark.django_db
def test_api_bulk_with_and_without_ids(order, kitchen_user):
    fish = line(order, 'Ikan Saboko')
    client = as_user(kitchen_user)
    res = client.post(f'/api/v1/kitchen/orders/{order.id}/items/start/', {'item_ids': [str(fish.id)]}, format='json')
    assert res.status_code == 200
    assert line(order, "Batar Da'an").status == I.QUEUED
    res = client.post(f'/api/v1/kitchen/orders/{order.id}/items/start/', {}, format='json')
    assert res.status_code == 200
    res = client.post(f'/api/v1/kitchen/orders/{order.id}/items/ready/', {}, format='json')
    assert res.json()['data']['status'] == OrderStatus.READY


@pytest.mark.django_db
def test_api_rejects_malformed_item_ids(order, kitchen_user):
    client = as_user(kitchen_user)
    for bad in ('x', ['not-a-uuid']):
        res = client.post(f'/api/v1/kitchen/orders/{order.id}/items/start/', {'item_ids': bad}, format='json')
        assert res.status_code == 400


@pytest.mark.django_db
def test_api_permissions(order, kitchen_user, admin_user, cashier_user):
    fish = line(order, 'Ikan Saboko')
    url = f'/api/v1/kitchen/items/{fish.id}/start/'
    assert as_user(None).post(url).status_code in (401, 403)
    assert as_user(cashier_user).post(url).status_code == 403
    assert as_user(admin_user).post(url).status_code == 200
    bulk = f'/api/v1/kitchen/orders/{order.id}/items/ready/'
    assert as_user(cashier_user).post(bulk, {}, format='json').status_code == 403
