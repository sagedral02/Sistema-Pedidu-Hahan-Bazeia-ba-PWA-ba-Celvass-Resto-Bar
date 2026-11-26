"""Per-item status: the order status and money are derived from its lines."""
from decimal import Decimal

import pytest

from apps.kitchen.services import mark_order_ready, mark_order_served, start_preparing_order
from apps.ordering.items import (
    can_transition, derive_order_status, order_totals, recompute_order, set_items_status,
)
from apps.ordering.models import ItemStatus, Order, OrderItem, OrderStatus, OrderStatusHistory
from apps.ordering.services import confirm_order_by_cashier, submit_customer_order
from apps.payments.services import calculate_session_bill, record_cash_payment

I = ItemStatus


# ----------------------------------------------------------------------------- pure rules

@pytest.mark.parametrize('statuses,expected', [
    ([I.QUEUED, I.QUEUED], OrderStatus.CONFIRMED),
    ([I.COOKING, I.QUEUED], OrderStatus.PREPARING),
    ([I.COOKING], OrderStatus.PREPARING),
    ([I.READY, I.QUEUED], OrderStatus.PREPARING),
    ([I.READY, I.COOKING], OrderStatus.PREPARING),
    ([I.READY, I.READY], OrderStatus.READY),
    ([I.READY, I.SERVED], OrderStatus.READY),
    ([I.SERVED, I.QUEUED], OrderStatus.PREPARING),
    ([I.SERVED, I.SERVED], OrderStatus.SERVED),
    ([I.UNAVAILABLE, I.QUEUED], OrderStatus.CONFIRMED),
    ([I.UNAVAILABLE, I.READY], OrderStatus.READY),
    ([I.UNAVAILABLE, I.SERVED], OrderStatus.SERVED),
    ([I.UNAVAILABLE, I.UNAVAILABLE], None),
    ([], None),
])
def test_order_status_follows_the_active_lines(statuses, expected):
    assert derive_order_status(statuses) == expected


def test_allowed_item_transitions():
    assert can_transition(I.QUEUED, I.COOKING) and can_transition(I.COOKING, I.READY)
    assert can_transition(I.READY, I.SERVED)
    assert can_transition(I.QUEUED, I.UNAVAILABLE) and can_transition(I.COOKING, I.UNAVAILABLE)
    assert not can_transition(I.READY, I.UNAVAILABLE), 'a finished dish cannot run out afterwards'
    assert not can_transition(I.SERVED, I.COOKING)
    assert not can_transition(I.UNAVAILABLE, I.QUEUED)


# ----------------------------------------------------------------------------- data helpers

@pytest.fixture
def two_item_order(active_session, menu_item_fish, menu_item_corn, cashier_user):
    """Fish 2 x 6.50 = 13.00 and corn 1 x 3.50, confirmed. Total 16.50."""
    order = submit_customer_order(
        table_session=active_session,
        items_data=[{'menu_item_id': menu_item_fish.id, 'quantity': 2}, {'menu_item_id': menu_item_corn.id, 'quantity': 1}],
        idempotency_key='two-items',
    )
    return confirm_order_by_cashier(order=order, confirmed_by=cashier_user)


def line(order, name):
    return OrderItem.objects.get(order=order, menu_name_snapshot=name)


# ----------------------------------------------------------------------------- recompute

@pytest.mark.django_db
def test_new_lines_start_queued_and_the_order_confirmed(two_item_order):
    assert two_item_order.status == OrderStatus.CONFIRMED
    assert set(two_item_order.items.values_list('status', flat=True)) == {I.QUEUED}
    assert two_item_order.grand_total == Decimal('16.50')


@pytest.mark.django_db
def test_one_line_cooking_makes_the_order_preparing(two_item_order, kitchen_user):
    set_items_status(two_item_order.items.filter(menu_name_snapshot='Ikan Saboko'), I.COOKING, actor_user=kitchen_user)
    order = recompute_order(two_item_order, actor_user=kitchen_user, reason='cooking')
    assert order.status == OrderStatus.PREPARING
    fish = line(order, 'Ikan Saboko')
    assert fish.status == I.COOKING and fish.cooking_started_at and fish.status_changed_by == kitchen_user
    assert line(order, "Batar Da'an").status == I.QUEUED


@pytest.mark.django_db
def test_order_is_ready_only_when_every_active_line_is(two_item_order):
    set_items_status(two_item_order.items.filter(menu_name_snapshot='Ikan Saboko'), I.READY)
    assert recompute_order(two_item_order).status == OrderStatus.PREPARING
    set_items_status(two_item_order.items.filter(menu_name_snapshot="Batar Da'an"), I.READY)
    assert recompute_order(two_item_order).status == OrderStatus.READY


@pytest.mark.django_db
def test_unavailable_line_drops_out_of_the_total_and_unblocks_the_order(two_item_order):
    set_items_status(two_item_order.items.filter(menu_name_snapshot="Batar Da'an"), I.UNAVAILABLE, reason='Out of corn')
    order = recompute_order(two_item_order, reason='out of stock')

    assert order.status == OrderStatus.CONFIRMED
    assert (order.subtotal, order.tax_total, order.grand_total) == (Decimal('13.00'), Decimal('0.00'), Decimal('13.00'))
    gone = line(order, "Batar Da'an")
    assert gone.status == I.UNAVAILABLE and gone.unavailable_reason == 'Out of corn'

    set_items_status(order.items.exclude(status=I.UNAVAILABLE), I.READY)
    assert recompute_order(order).status == OrderStatus.READY, 'the missing dish must not hold the order back'


@pytest.mark.django_db
def test_tax_is_recomputed_from_the_remaining_lines(restaurant, two_item_order):
    restaurant.tax_percentage = Decimal('10.00')
    restaurant.save()
    set_items_status(two_item_order.items.filter(menu_name_snapshot="Batar Da'an"), I.UNAVAILABLE)
    order = recompute_order(two_item_order)
    assert (order.subtotal, order.tax_total, order.grand_total) == (Decimal('13.00'), Decimal('1.30'), Decimal('14.30'))


@pytest.mark.django_db
def test_order_with_no_active_line_is_cancelled_with_history(two_item_order, kitchen_user):
    set_items_status(two_item_order.items.all(), I.UNAVAILABLE, reason='All out')
    order = recompute_order(two_item_order, actor_user=kitchen_user, reason='All items are out of stock')

    assert order.status == OrderStatus.CANCELLED
    assert order.cancellation_reason == 'All items are out of stock'
    assert order.grand_total == Decimal('0.00')
    entry = OrderStatusHistory.objects.get(order=order, to_status=OrderStatus.CANCELLED)
    assert (entry.from_status, entry.to_status, entry.actor_user) == (OrderStatus.CONFIRMED, OrderStatus.CANCELLED, kitchen_user)


@pytest.mark.django_db
def test_status_never_moves_backwards(two_item_order):
    set_items_status(two_item_order.items.filter(menu_name_snapshot='Ikan Saboko'), I.COOKING)
    assert recompute_order(two_item_order).status == OrderStatus.PREPARING
    # The only cooking line runs out: the remaining line is queued, but the kitchen already started the order.
    set_items_status(two_item_order.items.filter(menu_name_snapshot='Ikan Saboko'), I.UNAVAILABLE)
    assert recompute_order(two_item_order).status == OrderStatus.PREPARING


@pytest.mark.django_db
def test_recompute_is_idempotent_and_writes_history_once(two_item_order):
    set_items_status(two_item_order.items.all(), I.COOKING)
    recompute_order(two_item_order, reason='go')
    recompute_order(two_item_order, reason='go')
    recompute_order(two_item_order, reason='go')
    changes = OrderStatusHistory.objects.filter(order=two_item_order, to_status=OrderStatus.PREPARING)
    assert changes.count() == 1


@pytest.mark.django_db
def test_waiting_orders_only_have_their_money_recomputed(active_session, menu_item_fish, menu_item_corn):
    order = submit_customer_order(
        table_session=active_session,
        items_data=[{'menu_item_id': menu_item_fish.id, 'quantity': 1}, {'menu_item_id': menu_item_corn.id, 'quantity': 1}],
        idempotency_key='waiting-order',
    )
    assert order.status == OrderStatus.WAITING_CASHIER_CONFIRMATION and order.grand_total == Decimal('10.00')
    set_items_status(order.items.filter(menu_name_snapshot="Batar Da'an"), I.UNAVAILABLE)
    order = recompute_order(order)
    assert order.status == OrderStatus.WAITING_CASHIER_CONFIRMATION
    assert order.grand_total == Decimal('6.50')


@pytest.mark.django_db
def test_rejected_and_completed_orders_are_left_alone(two_item_order, cashier_user):
    rejected = Order.objects.get(pk=two_item_order.pk)
    rejected.status = OrderStatus.REJECTED
    rejected.save()
    set_items_status(rejected.items.all(), I.UNAVAILABLE)
    out = recompute_order(rejected)
    assert out.status == OrderStatus.REJECTED and out.grand_total == Decimal('16.50')


def test_order_totals_ignore_unavailable_lines_and_clamp_at_zero():
    class L:
        def __init__(self, subtotal, status):
            self.subtotal, self.status = Decimal(subtotal), status

    lines = [L('10.00', I.SERVED), L('5.00', I.UNAVAILABLE)]
    assert order_totals(lines, Decimal('0'), Decimal('0')) == (Decimal('10.00'), Decimal('0.00'), Decimal('10.00'))
    assert order_totals(lines, Decimal('10'), Decimal('50.00'))[2] == Decimal('0.00')


# ----------------------------------------------------------------------------- order-level kitchen actions

@pytest.mark.django_db
def test_whole_order_actions_move_every_line(two_item_order, kitchen_user):
    prep = start_preparing_order(order=two_item_order, staff_user=kitchen_user)
    assert prep.status == OrderStatus.PREPARING
    assert set(prep.items.values_list('status', flat=True)) == {I.COOKING}

    ready = mark_order_ready(order=prep, staff_user=kitchen_user)
    assert ready.status == OrderStatus.READY
    assert set(ready.items.values_list('status', flat=True)) == {I.READY}

    served = mark_order_served(order=ready, staff_user=kitchen_user)
    assert served.status == OrderStatus.SERVED
    assert all(i.served_at for i in served.items.all())


@pytest.mark.django_db
def test_whole_order_actions_skip_lines_that_ran_out(two_item_order, kitchen_user):
    set_items_status(two_item_order.items.filter(menu_name_snapshot="Batar Da'an"), I.UNAVAILABLE)
    recompute_order(two_item_order)
    ready = mark_order_ready(order=two_item_order, staff_user=kitchen_user)
    assert ready.status == OrderStatus.READY
    assert line(ready, "Batar Da'an").status == I.UNAVAILABLE
    assert line(ready, 'Ikan Saboko').status == I.READY


@pytest.mark.django_db
def test_whole_order_actions_are_idempotent_and_keep_their_errors(two_item_order, kitchen_user):
    from django.core.exceptions import ValidationError
    prep = start_preparing_order(order=two_item_order, staff_user=kitchen_user)
    assert start_preparing_order(order=prep, staff_user=kitchen_user).status == OrderStatus.PREPARING

    ready = mark_order_ready(order=prep, staff_user=kitchen_user)
    assert mark_order_ready(order=ready, staff_user=kitchen_user).status == OrderStatus.READY

    served = mark_order_served(order=ready, staff_user=kitchen_user)
    assert mark_order_served(order=served, staff_user=kitchen_user).status == OrderStatus.SERVED
    with pytest.raises(ValidationError):
        start_preparing_order(order=served, staff_user=kitchen_user)
    with pytest.raises(ValidationError):
        mark_order_ready(order=served, staff_user=kitchen_user)


@pytest.mark.django_db
def test_kitchen_actions_write_one_history_entry_each(two_item_order, kitchen_user):
    start_preparing_order(order=two_item_order, staff_user=kitchen_user)
    mark_order_ready(order=two_item_order, staff_user=kitchen_user)
    mark_order_served(order=two_item_order, staff_user=kitchen_user)
    path = list(OrderStatusHistory.objects.filter(order=two_item_order).order_by('created_at')
                .values_list('to_status', flat=True))
    assert path[-3:] == [OrderStatus.PREPARING, OrderStatus.READY, OrderStatus.SERVED]


# ----------------------------------------------------------------------------- bill and payment

@pytest.mark.django_db
def test_bill_skips_unavailable_lines(active_session, two_item_order):
    set_items_status(two_item_order.items.filter(menu_name_snapshot="Batar Da'an"), I.UNAVAILABLE)
    recompute_order(two_item_order)
    bill = calculate_session_bill(active_session)
    assert bill['grand_total'] == Decimal('13.00')
    assert [i['name'] for i in bill['items']] == ['Ikan Saboko']


@pytest.mark.django_db
def test_paying_early_waits_for_the_last_line_to_be_served(active_session, two_item_order, cashier_user, kitchen_user):
    from apps.kitchen.services import change_items_status
    from apps.payments.billing import request_bill
    from apps.tables.models import SessionStatus
    set_items_status(two_item_order.items.filter(menu_name_snapshot="Batar Da'an"), I.UNAVAILABLE)
    recompute_order(two_item_order)
    request_bill(session=active_session, requested_by=cashier_user, force=True)
    record_cash_payment(session=active_session, cashier_user=cashier_user, tendered_amount=Decimal('20.00'))

    active_session.refresh_from_db()
    assert active_session.status == SessionStatus.PAID, 'the fish is still in the kitchen'
    change_items_status(order=two_item_order, to_status=I.COOKING, staff_user=kitchen_user)
    change_items_status(order=two_item_order, to_status=I.READY, staff_user=kitchen_user)
    change_items_status(order=two_item_order, to_status=I.SERVED, staff_user=kitchen_user)

    order = Order.objects.get(pk=two_item_order.pk)
    active_session.refresh_from_db()
    assert order.status == OrderStatus.COMPLETED
    assert active_session.status == SessionStatus.CLOSED
    assert line(order, 'Ikan Saboko').status == I.SERVED
    assert line(order, "Batar Da'an").status == I.UNAVAILABLE


# ----------------------------------------------------------------------------- migration backfill

@pytest.mark.django_db
def test_backfill_gives_existing_lines_the_progress_of_their_order(active_session, menu_item_fish):
    from importlib import import_module
    from django.apps import apps

    backfill = import_module('apps.ordering.migrations.0002_orderitem_status').backfill_item_status
    expected = {
        OrderStatus.WAITING_CASHIER_CONFIRMATION: I.QUEUED, OrderStatus.CONFIRMED: I.QUEUED,
        OrderStatus.PREPARING: I.COOKING, OrderStatus.READY: I.READY,
        OrderStatus.SERVED: I.SERVED, OrderStatus.COMPLETED: I.SERVED,
        OrderStatus.REJECTED: I.QUEUED, OrderStatus.CANCELLED: I.QUEUED,
    }
    orders = {}
    for n, status in enumerate(expected):
        order = submit_customer_order(
            table_session=active_session, items_data=[{'menu_item_id': menu_item_fish.id, 'quantity': 1}],
            idempotency_key=f'backfill-{n}', source='CASHIER',
        )
        Order.objects.filter(pk=order.pk).update(status=status)
        OrderItem.objects.filter(order=order).update(status=I.QUEUED)   # the state before the migration
        orders[status] = order

    backfill(apps, None)

    for status, order in orders.items():
        assert OrderItem.objects.get(order=order).status == expected[status], status


# ----------------------------------------------------------------------------- API shape

@pytest.mark.django_db
def test_serializer_exposes_the_line_status(two_item_order):
    from apps.api.serializers import OrderSerializer
    data = OrderSerializer(two_item_order).data
    assert {i['status'] for i in data['items']} == {I.QUEUED}
    assert {'unavailable_reason', 'cooking_started_at', 'ready_at', 'served_at'} <= set(data['items'][0])
