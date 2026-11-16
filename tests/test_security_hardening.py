import pytest
from unittest import mock
from django.test import Client
from rest_framework.test import APIClient

from apps.tables.models import RestaurantTable


def _lock_session(session, token="device-owner-1"):
    session.primary_device_token = token
    session.authorized_device_tokens = [token]
    session.save(update_fields=["primary_device_token", "authorized_device_tokens"])
    return token


def _order_payload(menu_item):
    return {"items": [{"menu_item_id": str(menu_item.id), "quantity": 1}]}


@pytest.mark.django_db
def test_locked_session_rejects_request_without_device_token(active_session, menu_item_fish):
    _lock_session(active_session)
    client = APIClient()
    res = client.post(
        f"/api/v1/public/sessions/{active_session.public_token}/orders/",
        _order_payload(menu_item_fish),
        format="json",
        HTTP_IDEMPOTENCY_KEY="no-token-1",
    )
    assert res.status_code == 403
    assert res.json()["error"]["code"] == "DEVICE_LOCKED"


@pytest.mark.django_db
def test_locked_session_rejects_other_device_token(active_session, menu_item_fish):
    _lock_session(active_session)
    client = APIClient()
    res = client.post(
        f"/api/v1/public/sessions/{active_session.public_token}/orders/",
        _order_payload(menu_item_fish),
        format="json",
        HTTP_IDEMPOTENCY_KEY="other-token-1",
        HTTP_X_DEVICE_TOKEN="intruder",
    )
    assert res.status_code == 403


@pytest.mark.django_db
def test_locked_session_accepts_owner_device(active_session, menu_item_fish):
    token = _lock_session(active_session)
    client = APIClient()
    res = client.post(
        f"/api/v1/public/sessions/{active_session.public_token}/orders/",
        _order_payload(menu_item_fish),
        format="json",
        HTTP_IDEMPOTENCY_KEY="owner-1",
        HTTP_X_DEVICE_TOKEN=token,
    )
    assert res.status_code == 201


@pytest.mark.django_db
def test_locked_session_rejects_bill_request_without_token(active_session):
    _lock_session(active_session)
    client = APIClient()
    res = client.post(f"/api/v1/public/sessions/{active_session.public_token}/request-bill/")
    assert res.status_code == 403


@pytest.mark.django_db
def test_locked_session_rejects_order_list_without_token(active_session):
    _lock_session(active_session)
    client = APIClient()
    res = client.get(f"/api/v1/public/sessions/{active_session.public_token}/orders/list/")
    assert res.status_code == 403


@pytest.mark.django_db
def test_table_page_blocks_visitor_without_cookie_when_locked(active_session, table_one):
    _lock_session(active_session)
    res = Client().get(f"/t/{table_one.qr_token}/")
    assert res.status_code == 403


@pytest.mark.django_db
def test_table_page_allows_owner_cookie(active_session, table_one):
    token = _lock_session(active_session)
    client = Client()
    client.cookies["celvass_device_id"] = token
    res = client.get(f"/t/{table_one.qr_token}/")
    assert res.status_code == 200


def _make_order(active_session, menu_item_fish):
    from apps.ordering.services import submit_customer_order
    return submit_customer_order(
        table_session=active_session,
        items_data=[{"menu_item_id": menu_item_fish.id, "quantity": 1}],
        idempotency_key="tracker-order-1",
    )


@pytest.mark.django_db
def test_tracker_returns_order_for_own_table(active_session, table_one, menu_item_fish):
    order = _make_order(active_session, menu_item_fish)
    res = Client().get(f"/t/{table_one.qr_token}/order/{order.order_code}/")
    assert res.status_code == 200


@pytest.mark.django_db
def test_tracker_rejects_order_from_other_table(active_session, restaurant, menu_item_fish):
    order = _make_order(active_session, menu_item_fish)
    other = RestaurantTable.objects.create(restaurant=restaurant, table_code="T-09", display_name="Meza 09")
    res = Client().get(f"/t/{other.qr_token}/order/{order.order_code}/")
    assert res.status_code == 404


@pytest.mark.django_db
def test_tracker_rejects_unknown_qr_token(active_session, menu_item_fish):
    order = _make_order(active_session, menu_item_fish)
    res = Client().get(f"/t/does-not-exist/order/{order.order_code}/")
    assert res.status_code == 404


@pytest.mark.django_db
def test_tracker_blocks_foreign_device_on_locked_session(active_session, table_one, menu_item_fish):
    order = _make_order(active_session, menu_item_fish)
    _lock_session(active_session)
    res = Client().get(f"/t/{table_one.qr_token}/order/{order.order_code}/")
    assert res.status_code == 403


@pytest.mark.django_db
@pytest.mark.parametrize("bad_value", ["abc", "0", "-3", "26", "1e3", ""])
def test_activation_request_rejects_invalid_guest_count(restaurant, bad_value):
    table = RestaurantTable.objects.create(restaurant=restaurant, table_code="T-05", display_name="Meza 05")
    res = APIClient().post(
        f"/api/v1/public/tables/{table.qr_token}/request-activation/",
        {"guest_count": bad_value},
        format="json",
    )
    assert res.status_code == 400
    assert res.json()["error"]["code"] == "INVALID_INPUT"


@pytest.mark.django_db
def test_cashier_open_session_rejects_invalid_guest_count(table_one, cashier_user):
    client = APIClient()
    client.force_authenticate(user=cashier_user)
    res = client.post(f"/api/v1/cashier/tables/{table_one.id}/open-session/", {"guest_count": "many"}, format="json")
    assert res.status_code == 400
    assert res.json()["error"]["code"] == "INVALID_INPUT"


@pytest.mark.django_db
def test_server_error_does_not_leak_internal_details(active_session, menu_item_fish):
    client = APIClient()
    with mock.patch("apps.api.views.submit_customer_order", side_effect=RuntimeError("secret-db-password")):
        res = client.post(
            f"/api/v1/public/sessions/{active_session.public_token}/orders/",
            _order_payload(menu_item_fish),
            format="json",
            HTTP_IDEMPOTENCY_KEY="boom-1",
        )
    assert res.status_code == 500
    assert "secret-db-password" not in res.content.decode()
    assert res.json()["error"]["code"] == "SERVER_ERROR"


@pytest.mark.django_db
def test_admin_dashboard_active_category_count(admin_user, restaurant):
    from apps.catalog.models import Category
    Category.objects.create(restaurant=restaurant, name="A", slug="a", is_active=True)
    Category.objects.create(restaurant=restaurant, name="B", slug="b", is_active=True)
    Category.objects.create(restaurant=restaurant, name="C", slug="c", is_active=False)
    client = Client()
    client.force_login(admin_user)
    res = client.get("/admin-portal/")
    assert res.status_code == 200
    assert res.context["active_category_count"] == 2


@pytest.mark.django_db
def test_login_redirects_by_role(cashier_user, kitchen_user, admin_user):
    expectations = [
        (admin_user, "/admin-portal/"),
        (cashier_user, "/cashier/"),
        (kitchen_user, "/kitchen/"),
    ]
    for user, target in expectations:
        client = Client()
        client.force_login(user)
        res = client.get("/login/")
        assert res.status_code == 302
        assert res.url == target


@pytest.mark.django_db
def test_staff_preview_does_not_claim_table_session(active_session, table_one, cashier_user):
    client = Client()
    client.force_login(cashier_user)
    res = client.get(f"/t/{table_one.qr_token}/")
    assert res.status_code == 200
    active_session.refresh_from_db()
    assert active_session.primary_device_token is None

    # The real guest can still claim the table afterwards.
    guest = Client()
    assert guest.get(f"/t/{table_one.qr_token}/").status_code == 200
    active_session.refresh_from_db()
    assert active_session.primary_device_token
