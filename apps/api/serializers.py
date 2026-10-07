from decimal import Decimal
from rest_framework import serializers
from apps.accounts.models import User
from apps.restaurants.models import Restaurant
from apps.tables.models import RestaurantTable, TableSession
from apps.catalog.models import Category, MenuItem
from apps.ordering.models import Order, OrderItem
from apps.payments.models import Payment

class RestaurantSerializer(serializers.ModelSerializer):
    class Meta:
        model = Restaurant
        fields = ['id', 'name', 'legal_name', 'phone', 'address', 'currency', 'timezone', 'tax_percentage']

class MenuItemSerializer(serializers.ModelSerializer):
    display_image = serializers.ReadOnlyField()
    category_name = serializers.CharField(source='category.name', read_only=True)
    category_slug = serializers.CharField(source='category.slug', read_only=True)

    class Meta:
        model = MenuItem
        fields = [
            'id', 'name', 'slug', 'sku', 'description', 'price',
            'display_image', 'availability', 'is_featured', 'sort_order',
            'preparation_note', 'category_name', 'category_slug'
        ]

class CategorySerializer(serializers.ModelSerializer):
    items = serializers.SerializerMethodField()

    class Meta:
        model = Category
        fields = ['id', 'name', 'slug', 'description', 'icon_name', 'sort_order', 'items']

    def get_items(self, obj):
        items = obj.items.filter(availability__in=['AVAILABLE', 'SOLD_OUT']).order_by('sort_order', 'name')
        return MenuItemSerializer(items, many=True).data

class AdminCategorySerializer(serializers.ModelSerializer):
    item_count = serializers.SerializerMethodField()

    class Meta:
        model = Category
        fields = ['id', 'name', 'slug', 'description', 'icon_name', 'sort_order', 'is_active', 'item_count', 'created_at']

    def get_item_count(self, obj):
        if hasattr(obj, 'item_count'):
            return obj.item_count
        return obj.items.count()

class CategoryCreateUpdateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=100)
    slug = serializers.CharField(max_length=120, required=False, allow_blank=True)
    description = serializers.CharField(required=False, allow_blank=True, default="")
    icon_name = serializers.CharField(max_length=50, required=False, default="utensils")
    sort_order = serializers.IntegerField(required=False, default=0)
    is_active = serializers.BooleanField(required=False, default=True)

class AdminMenuItemSerializer(serializers.ModelSerializer):
    display_image = serializers.ReadOnlyField()
    category_id = serializers.UUIDField(source='category.id', read_only=True)
    category_name = serializers.CharField(source='category.name', read_only=True)
    category_slug = serializers.CharField(source='category.slug', read_only=True)

    class Meta:
        model = MenuItem
        fields = [
            'id', 'name', 'slug', 'sku', 'description', 'price',
            'display_image', 'image_url', 'availability', 'is_featured', 'sort_order',
            'preparation_note', 'category_id', 'category_name', 'category_slug', 'created_at'
        ]

class MenuItemCreateUpdateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=150)
    category_id = serializers.UUIDField()
    price = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=Decimal('0.00'))
    slug = serializers.CharField(max_length=180, required=False, allow_blank=True)
    sku = serializers.CharField(max_length=50, required=False, allow_blank=True)
    description = serializers.CharField(required=False, allow_blank=True, default="")
    image_url = serializers.CharField(max_length=500, required=False, allow_blank=True)
    image = serializers.ImageField(required=False, allow_null=True)
    availability = serializers.ChoiceField(choices=['AVAILABLE', 'SOLD_OUT', 'INACTIVE'], required=False, default='AVAILABLE')
    is_featured = serializers.BooleanField(required=False, default=False)
    sort_order = serializers.IntegerField(required=False, default=0)
    preparation_note = serializers.CharField(required=False, allow_blank=True, default="")


class RestaurantTableSerializer(serializers.ModelSerializer):
    active_session = serializers.SerializerMethodField()
    pending_activation = serializers.SerializerMethodField()

    class Meta:
        model = RestaurantTable
        fields = ['id', 'table_code', 'display_name', 'capacity', 'qr_token', 'status', 'sort_order', 'active_session', 'pending_activation']

    def get_active_session(self, obj):
        from apps.tables.selectors import get_active_session_for_table
        from apps.payments.services import calculate_session_bill
        session = get_active_session_for_table(obj)
        if not session:
            return None
        bill = calculate_session_bill(session)
        return {
            'id': str(session.id),
            'public_token': session.public_token,
            'status': session.status,
            'guest_count': session.guest_count,
            'opened_at': session.opened_at,
            'bill_total': str(bill['grand_total']),
            'total_paid': str(bill['total_paid']),
            'remaining_balance': str(bill['remaining_balance']),
            'is_fully_paid': bill['is_fully_paid'],
            'orders_count': bill['orders_count'],
            'has_unconfirmed': bill['has_active_unconfirmed_orders'],
            'items': bill['items'],
        }

    def get_pending_activation(self, obj):
        try:
            from apps.tables.models import TableActivationRequest, ActivationRequestStatus
            req = obj.activation_requests.filter(status=ActivationRequestStatus.PENDING).first()
            if not req:
                return None
            return {
                'id': str(req.id),
                'guest_count': req.guest_count,
                'device_token': req.device_token,
                'requested_at': req.requested_at.isoformat(),
            }
        except Exception:
            return None


class TableActivationRequestSerializer(serializers.ModelSerializer):
    table_id = serializers.UUIDField(source='table.id', read_only=True)
    table_code = serializers.CharField(source='table.table_code', read_only=True)
    table_name = serializers.CharField(source='table.display_name', read_only=True)

    class Meta:
        from apps.tables.models import TableActivationRequest
        model = TableActivationRequest
        fields = ['id', 'table_id', 'table_code', 'table_name', 'device_token', 'guest_count', 'status', 'requested_at']


class OrderItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = OrderItem
        fields = ['id', 'menu_name_snapshot', 'sku_snapshot', 'unit_price', 'quantity', 'subtotal', 'note']

class OrderSerializer(serializers.ModelSerializer):
    items = OrderItemSerializer(many=True, read_only=True)
    table_code = serializers.CharField(source='table_session.table.table_code', read_only=True)
    table_name = serializers.CharField(source='table_session.table.display_name', read_only=True)
    session_token = serializers.CharField(source='table_session.public_token', read_only=True)

    class Meta:
        model = Order
        fields = [
            'id', 'order_code', 'source', 'status', 'subtotal',
            'tax_total', 'discount_total', 'grand_total', 'customer_note',
            'rejection_reason', 'submitted_at', 'confirmed_at', 'created_at',
            'table_code', 'table_name', 'session_token', 'items'
        ]

class OrderSubmitItemSerializer(serializers.Serializer):
    menu_item_id = serializers.UUIDField()
    quantity = serializers.IntegerField(min_value=1, max_value=99)
    note = serializers.CharField(required=False, allow_blank=True, max_length=500)

class OrderSubmitRequestSerializer(serializers.Serializer):
    items = OrderSubmitItemSerializer(many=True)
    customer_note = serializers.CharField(required=False, allow_blank=True, max_length=1000)

class CashPaymentRequestSerializer(serializers.Serializer):
    tendered_amount = serializers.DecimalField(max_digits=12, decimal_places=2)
    method = serializers.ChoiceField(choices=['CASH', 'MANUAL_OTHER'], default='CASH')
