import pytest
from rest_framework.test import APIClient
from decimal import Decimal
from apps.catalog.models import Category, MenuItem, ItemAvailability
from apps.ordering.models import Order, OrderItem, OrderStatus

@pytest.mark.django_db
def test_admin_category_crud_flow(restaurant, admin_user, cashier_user):
    client = APIClient()

    # 1. Unauthenticated gets 401 or 403
    res = client.get('/api/v1/admin/categories/')
    assert res.status_code in [401, 403]

    # 2. Cashier (non-admin) gets 403
    client.force_authenticate(user=cashier_user)
    res = client.get('/api/v1/admin/categories/')
    assert res.status_code == 403

    # 3. Admin authenticate
    client.force_authenticate(user=admin_user)
    res = client.get('/api/v1/admin/categories/')
    assert res.status_code == 200
    assert res.json()['success'] is True

    # 4. Create Category
    create_res = client.post(
        '/api/v1/admin/categories/',
        {
            'name': 'Hahan Manas (Hot Dishes)',
            'icon_name': 'utensils',
            'is_active': True,
            'sort_order': 1
        },
        format='json'
    )
    assert create_res.status_code == 201
    created_cat = create_res.json()['data']
    cat_id = created_cat['id']
    assert created_cat['name'] == 'Hahan Manas (Hot Dishes)'
    assert created_cat['icon_name'] == 'utensils'
    assert created_cat['slug'] == 'hahan-manas-hot-dishes'

    # 5. Retrieve Detail
    detail_res = client.get(f'/api/v1/admin/categories/{cat_id}/')
    assert detail_res.status_code == 200
    assert detail_res.json()['data']['id'] == cat_id

    # 6. Update Category
    update_res = client.put(
        f'/api/v1/admin/categories/{cat_id}/',
        {
            'name': 'Hahan Manas Especial',
            'icon_name': 'fire',
            'is_active': True,
            'sort_order': 2
        },
        format='json'
    )
    assert update_res.status_code == 200
    assert update_res.json()['data']['name'] == 'Hahan Manas Especial'
    assert update_res.json()['data']['icon_name'] == 'fire'

    # 7. Toggle Status
    toggle_res = client.post(f'/api/v1/admin/categories/{cat_id}/toggle-status/')
    assert toggle_res.status_code == 200
    assert toggle_res.json()['data']['is_active'] is False

    # 8. Delete Category (empty)
    del_res = client.delete(f'/api/v1/admin/categories/{cat_id}/')
    assert del_res.status_code == 200
    assert not Category.objects.filter(id=cat_id).exists()


@pytest.mark.django_db
def test_admin_category_delete_blocked_when_has_items(restaurant, admin_user, category_main, menu_item_fish):
    client = APIClient()
    client.force_authenticate(user=admin_user)

    # Try to delete category_main which contains menu_item_fish
    del_res = client.delete(f'/api/v1/admin/categories/{category_main.id}/')
    assert del_res.status_code == 400
    assert del_res.json()['success'] is False
    assert Category.objects.filter(id=category_main.id).exists()


@pytest.mark.django_db
def test_admin_menu_item_crud_flow(restaurant, admin_user, category_main):
    client = APIClient()
    client.force_authenticate(user=admin_user)

    # 1. Create Menu Item
    create_res = client.post(
        '/api/v1/admin/menu-items/',
        {
            'category_id': str(category_main.id),
            'name': 'Koto Da\'an Spesial',
            'description': 'Koto fresku ho modo kinur',
            'price': '4.50',
            'availability': 'AVAILABLE',
            'sort_order': 1,
            'image_url': 'https://images.unsplash.com/photo-1546069901-ba9599a7e63c'
        },
        format='json'
    )
    assert create_res.status_code == 201
    item_data = create_res.json()['data']
    item_id = item_data['id']
    assert item_data['name'] == 'Koto Da\'an Spesial'
    assert item_data['price'] == '4.50'

    # 2. Get Detail
    detail_res = client.get(f'/api/v1/admin/menu-items/{item_id}/')
    assert detail_res.status_code == 200
    assert detail_res.json()['data']['name'] == 'Koto Da\'an Spesial'

    # 3. Update Menu Item
    update_res = client.put(
        f'/api/v1/admin/menu-items/{item_id}/',
        {
            'category_id': str(category_main.id),
            'name': 'Koto Da\'an Super Spesial',
            'price': '5.00',
            'availability': 'SOLD_OUT',
            'is_featured': True
        },
        format='json'
    )
    assert update_res.status_code == 200
    assert update_res.json()['data']['name'] == 'Koto Da\'an Super Spesial'
    assert update_res.json()['data']['price'] == '5.00'
    assert update_res.json()['data']['availability'] == 'SOLD_OUT'
    assert update_res.json()['data']['is_featured'] is True

    # 4. Hard Delete when no historical order
    del_res = client.delete(f'/api/v1/admin/menu-items/{item_id}/')
    assert del_res.status_code == 200
    assert del_res.json()['data']['soft_deleted'] is False
    assert not MenuItem.objects.filter(id=item_id).exists()


@pytest.mark.django_db
def test_admin_menu_item_soft_delete_when_has_order_history(restaurant, admin_user, active_session, menu_item_fish):
    # Create an order referencing menu_item_fish using client order submit endpoint
    client = APIClient()
    submit_res = client.post(
        f'/api/v1/public/sessions/{active_session.public_token}/orders/',
        {
            'items': [{'menu_item_id': str(menu_item_fish.id), 'quantity': 1, 'note': ''}],
            'customer_note': ''
        },
        format='json',
        HTTP_IDEMPOTENCY_KEY='admin-test-idem-01'
    )
    assert submit_res.status_code == 201

    client.force_authenticate(user=admin_user)

    # Deleting should perform soft-deactivation instead of hard delete
    del_res = client.delete(f'/api/v1/admin/menu-items/{menu_item_fish.id}/')
    assert del_res.status_code == 200
    data = del_res.json()['data']
    assert data['soft_deleted'] is True

    # Check menu item still exists in DB but is INACTIVE
    menu_item_fish.refresh_from_db()
    assert menu_item_fish.availability == ItemAvailability.INACTIVE
