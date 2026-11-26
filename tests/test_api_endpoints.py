import pytest
from rest_framework.test import APIClient
from decimal import Decimal

@pytest.mark.django_db
def test_public_table_resolve_endpoint(table_one, active_session):
    client = APIClient()
    response = client.get(f'/api/v1/public/tables/resolve/{table_one.qr_token}/')
    assert response.status_code == 200
    data = response.json()
    assert data['success'] is True
    assert data['data']['table']['table_code'] == 'T-01'
    assert data['data']['session']['has_active_session'] is True
    assert data['data']['session']['public_token'] == active_session.public_token

@pytest.mark.django_db
def test_public_menu_endpoint(menu_item_fish):
    client = APIClient()
    response = client.get('/api/v1/public/menu/')
    assert response.status_code == 200
    data = response.json()
    assert data['success'] is True
    assert len(data['data']) > 0

@pytest.mark.django_db
def test_public_order_submit_and_cashier_flow_api(active_session, menu_item_fish, cashier_user):
    client = APIClient()

    # 1. Customer submit order via API
    submit_res = client.post(
        f'/api/v1/public/sessions/{active_session.public_token}/orders/',
        {
            'items': [{'menu_item_id': str(menu_item_fish.id), 'quantity': 2, 'note': 'Tanpa pedas'}],
            'customer_note': 'Tolong sediakan tisu lebih.'
        },
        format='json',
        HTTP_IDEMPOTENCY_KEY='api-idem-test-99'
    )
    assert submit_res.status_code == 201
    order_data = submit_res.json()['data']
    order_id = order_data['id']
    assert order_data['status'] == 'WAITING_CASHIER_CONFIRMATION'

    # 2. Cashier checks pending orders
    client.force_authenticate(user=cashier_user)
    pending_res = client.get('/api/v1/cashier/orders/')
    assert pending_res.status_code == 200
    pending_list = pending_res.json()['data']
    assert any(o['id'] == order_id for o in pending_list)

    # 3. Cashier confirms order
    confirm_res = client.post(f'/api/v1/cashier/orders/{order_id}/confirm/')
    assert confirm_res.status_code == 200
    assert confirm_res.json()['data']['status'] == 'CONFIRMED'

    # 4. Kitchen checks queue
    kitchen_res = client.get('/api/v1/kitchen/orders/')
    assert kitchen_res.status_code == 200
    kitchen_list = kitchen_res.json()['data']
    assert any(o['id'] == order_id for o in kitchen_list)


@pytest.mark.django_db
def test_table_activation_request_and_approval_flow(restaurant, cashier_user):
    from apps.tables.models import RestaurantTable
    table_two = RestaurantTable.objects.create(
        restaurant=restaurant,
        table_code='T-02',
        display_name='Meza 02'
    )
    client = APIClient()

    # 1. Check unopened table resolve
    res0 = client.get(f'/api/v1/public/tables/resolve/{table_two.qr_token}/')
    assert res0.status_code == 200
    assert res0.json()['data']['session']['has_active_session'] is False
    assert res0.json()['data']['activation_request']['has_pending_activation'] is False


    # 2. Customer submits activation request
    act_res = client.post(
        f'/api/v1/public/tables/{table_two.qr_token}/request-activation/',
        {'guest_count': 3},
        format='json',
        HTTP_X_DEVICE_TOKEN='device-phone-999'
    )
    assert act_res.status_code == 201
    act_data = act_res.json()['data']
    assert act_data['status'] == 'PENDING'
    req_id = act_data['request_id']

    # 3. Table resolve now reports pending activation
    res1 = client.get(f'/api/v1/public/tables/resolve/{table_two.qr_token}/')
    assert res1.json()['data']['activation_request']['has_pending_activation'] is True

    # 4. Cashier views pending activation requests
    client.force_authenticate(user=cashier_user)
    list_res = client.get('/api/v1/cashier/activation-requests/')
    assert list_res.status_code == 200
    req_items = list_res.json()['data']
    assert any(r['id'] == req_id for r in req_items)

    # 5. Cashier approves activation request
    approve_res = client.post(f'/api/v1/cashier/activation-requests/{req_id}/approve/')
    assert approve_res.status_code == 200
    session_data = approve_res.json()['data']
    assert session_data['status'] == 'OPEN'
    assert session_data['table_code'] == table_two.table_code

    # 6. Customer now detects active session without refreshing manually
    client.logout()
    res2 = client.get(f'/api/v1/public/tables/resolve/{table_two.qr_token}/')
    assert res2.json()['data']['session']['has_active_session'] is True
    assert res2.json()['data']['session']['public_token'] == session_data['public_token']


@pytest.mark.django_db
def test_public_request_bill_endpoint(active_session, menu_item_fish, cashier_user, kitchen_user):
    from rest_framework.test import APIClient
    from apps.ordering.services import submit_customer_order, confirm_order_by_cashier
    from apps.kitchen.services import start_preparing_order, mark_order_ready, mark_order_served
    from apps.ordering.models import OrderStatus

    client = APIClient()

    # 1. Request bill when 0 orders -> Should return 400 with BILL_REQUEST_INVALID
    res = client.post(f'/api/v1/public/sessions/{active_session.public_token}/request-bill/')
    print('DEBUG 1 (0 orders):', res.status_code, res.json())

    # 2. Submit order, confirm, prepare, mark ready, mark served
    order = submit_customer_order(
        table_session=active_session,
        items_data=[{'menu_item_id': menu_item_fish.id, 'quantity': 1}],
        idempotency_key="bill-api-order-1"
    )
    confirmed = confirm_order_by_cashier(order=order, confirmed_by=cashier_user)
    prep = start_preparing_order(order=confirmed, staff_user=kitchen_user)
    ready = mark_order_ready(order=prep, staff_user=kitchen_user)
    served = mark_order_served(order=ready, staff_user=cashier_user)
    assert served.status == OrderStatus.SERVED

    # 3. Request bill when served -> Should succeed (200)
    res2 = client.post(f'/api/v1/public/sessions/{active_session.public_token}/request-bill/')
    assert res2.status_code == 200
    assert res2.json()['success'] is True


@pytest.mark.django_db
def test_cashier_orders_and_bills_history_endpoints(active_session, menu_item_fish, cashier_user):
    from rest_framework.test import APIClient
    from apps.ordering.services import submit_customer_order, confirm_order_by_cashier
    from apps.payments.services import record_cash_payment
    from decimal import Decimal

    client = APIClient()
    client.force_authenticate(user=cashier_user)

    order = submit_customer_order(
        table_session=active_session,
        items_data=[{'menu_item_id': menu_item_fish.id, 'quantity': 2}],
        idempotency_key="history-test-order"
    )
    confirm_order_by_cashier(order=order, confirmed_by=cashier_user)

    # 1. Orders History Endpoint
    res_orders = client.get('/api/v1/cashier/orders/history/?date_range=today')
    assert res_orders.status_code == 200
    orders_data = res_orders.json().get('data', [])
    assert len(orders_data) >= 1
    assert any(o['order_code'] == order.order_code for o in orders_data)

    # 2. Pay Bill (only through a requested bill)
    from apps.payments.billing import request_bill
    request_bill(session=active_session, requested_by=cashier_user, force=True)
    payment = record_cash_payment(
        session=active_session,
        cashier_user=cashier_user,
        tendered_amount=Decimal("20.00")
    )

    # 3. Bills History Endpoint
    res_bills = client.get('/api/v1/cashier/bills/history/?date_range=today')
    assert res_bills.status_code == 200
    bills_data = res_bills.json().get('data', [])
    assert len(bills_data) >= 1
    assert any(b['payment_code'] == payment.payment_code for b in bills_data)


