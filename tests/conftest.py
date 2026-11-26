import pytest
from decimal import Decimal
from apps.accounts.models import User, Role
from apps.restaurants.models import Restaurant
from apps.tables.models import RestaurantTable, TableStatus, TableSession, SessionStatus
from apps.catalog.models import Category, MenuItem, ItemAvailability

@pytest.fixture
def restaurant(db):
    return Restaurant.objects.create(
        name="Restaurante Sabor Dili",
        currency="USD",
        timezone="Asia/Dili",
        tax_percentage=Decimal("0.00"),
        is_active=True
    )

@pytest.fixture
def admin_user(db):
    return User.objects.create_superuser(
        username="admin_test",
        email="admin@test.tl",
        password="password123"
    )

@pytest.fixture
def cashier_user(db):
    user = User.objects.create_user(
        username="cashier_test",
        email="cashier@test.tl",
        password="password123",
        role=Role.CASHIER,
        is_staff=True
    )
    return user

@pytest.fixture
def kitchen_user(db):
    user = User.objects.create_user(
        username="kitchen_test",
        email="kitchen@test.tl",
        password="password123",
        role=Role.KITCHEN,
        is_staff=True
    )
    return user

@pytest.fixture
def cashier_two(db):
    return User.objects.create_user(
        username="cashier_two",
        email="c2@test.tl",
        password="password123",
        role=Role.CASHIER,
        is_staff=True,
        full_name="Maria Cashier"
    )

@pytest.fixture
def table_one(db, restaurant):
    return RestaurantTable.objects.create(
        restaurant=restaurant,
        table_code="T-01",
        display_name="Meja 01",
        capacity=4,
        status=TableStatus.AVAILABLE,
        is_active=True
    )

@pytest.fixture
def table_two(db, restaurant):
    return RestaurantTable.objects.create(
        restaurant=restaurant,
        table_code="T-02",
        display_name="Meja 02",
        capacity=4,
        sort_order=2,
        status=TableStatus.AVAILABLE,
        is_active=True
    )

@pytest.fixture
def session_two(db, restaurant, table_two, cashier_two):
    from apps.tables.services import open_table_session
    return open_table_session(table=table_two, opened_by=cashier_two, guest_count=3)

@pytest.fixture
def active_session(db, restaurant, table_one, cashier_user):
    from apps.tables.services import open_table_session
    return open_table_session(table=table_one, opened_by=cashier_user, guest_count=2)

@pytest.fixture
def category_main(db, restaurant):
    return Category.objects.create(
        restaurant=restaurant,
        name="Makanan Utama",
        slug="main",
        is_active=True
    )

@pytest.fixture
def menu_item_fish(db, restaurant, category_main):
    return MenuItem.objects.create(
        restaurant=restaurant,
        category=category_main,
        name="Ikan Saboko",
        slug="ikan-saboko",
        price=Decimal("6.50"),
        availability=ItemAvailability.AVAILABLE
    )

@pytest.fixture
def menu_item_corn(db, restaurant, category_main):
    return MenuItem.objects.create(
        restaurant=restaurant,
        category=category_main,
        name="Batar Da'an",
        slug="batar-daan",
        price=Decimal("3.50"),
        availability=ItemAvailability.AVAILABLE
    )


@pytest.fixture(autouse=True)
def _shift_for_payments(request, settings):
    """Tests that are not about shifts pay without opening one; test_shifts.py turns the rule back on."""
    settings.REQUIRE_SHIFT_FOR_PAYMENT = 'shift_required' in request.keywords
