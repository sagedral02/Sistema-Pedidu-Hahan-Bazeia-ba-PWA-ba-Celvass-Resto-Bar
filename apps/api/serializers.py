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

class MenuAvailabilityFieldsMixin(serializers.Serializer):
    """Effective availability for today: `orderable`, why not (`state`) and portions left (`remaining`)."""
    orderable = serializers.SerializerMethodField()
    state = serializers.SerializerMethodField()
    remaining = serializers.SerializerMethodField()

    def _availability(self, obj):
        cache = self.context.get('availability')
        if cache is not None and str(obj.id) in cache:
            return cache[str(obj.id)]
        from apps.catalog.availability import menu_availability
        return menu_availability(obj)

    def get_orderable(self, obj):
        return self._availability(obj).orderable

    def get_state(self, obj):
        return self._availability(obj).state

    def get_remaining(self, obj):
        return self._availability(obj).remaining


class MenuItemSerializer(MenuAvailabilityFieldsMixin, serializers.ModelSerializer):
    display_image = serializers.ReadOnlyField()
    category_name = serializers.CharField(source='category.name', read_only=True)
    category_slug = serializers.CharField(source='category.slug', read_only=True)

    class Meta:
        model = MenuItem
        fields = [
            'id', 'name', 'slug', 'sku', 'description', 'price',
            'display_image', 'availability', 'is_featured', 'sort_order',
            'preparation_note', 'category_name', 'category_slug',
            'orderable', 'state', 'remaining', 'rating_avg', 'rating_count',
        ]

class CategorySerializer(serializers.ModelSerializer):
    items = serializers.SerializerMethodField()

    class Meta:
        model = Category
        fields = ['id', 'name', 'slug', 'description', 'icon_name', 'sort_order', 'items']

    def get_items(self, obj):
        from apps.catalog.availability import availability_map
        items = list(obj.items.filter(availability__in=['AVAILABLE', 'SOLD_OUT']).order_by('sort_order', 'name'))
        return MenuItemSerializer(items, many=True, context={'availability': availability_map(items)}).data

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

class AdminMenuItemSerializer(MenuAvailabilityFieldsMixin, serializers.ModelSerializer):
    sold = serializers.SerializerMethodField()
    closed_reason = serializers.SerializerMethodField()
    display_image = serializers.ReadOnlyField()
    category_id = serializers.UUIDField(source='category.id', read_only=True)
    category_name = serializers.CharField(source='category.name', read_only=True)
    category_slug = serializers.CharField(source='category.slug', read_only=True)

    class Meta:
        model = MenuItem
        fields = [
            'id', 'name', 'slug', 'sku', 'description', 'price',
            'display_image', 'image_url', 'availability', 'is_featured', 'sort_order',
            'preparation_note', 'category_id', 'category_name', 'category_slug', 'created_at',
            'daily_quota', 'sold', 'remaining', 'state', 'orderable', 'closed_reason',
            'rating_avg', 'rating_count',
        ]

    def get_sold(self, obj):
        return self._availability(obj).sold

    def get_closed_reason(self, obj):
        return self._availability(obj).closed_reason

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
    daily_quota = serializers.IntegerField(required=False, allow_null=True, min_value=1)


class AdminUserSerializer(serializers.ModelSerializer):
    display_name = serializers.SerializerMethodField()
    role_display = serializers.CharField(source='get_role_display', read_only=True)
    has_history = serializers.SerializerMethodField()
    is_self = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            'id', 'username', 'full_name', 'display_name', 'role', 'role_display', 'is_active',
            'is_superuser', 'last_login', 'created_at', 'has_history', 'is_self',
        ]

    def get_display_name(self, obj):
        return obj.get_full_name()

    def get_has_history(self, obj):
        from apps.accounts.admin_services import has_history
        return has_history(obj)

    def get_is_self(self, obj):
        request = self.context.get('request')
        return bool(request and request.user and request.user.pk == obj.pk)


class StaffUserWriteSerializer(serializers.Serializer):
    username = serializers.CharField(max_length=150, required=False)
    full_name = serializers.CharField(max_length=150, required=False, allow_blank=True)
    first_name = serializers.CharField(max_length=150, required=False, allow_blank=True)
    password = serializers.CharField(required=False, allow_blank=True, trim_whitespace=False, write_only=True)
    role = serializers.ChoiceField(choices=['ADMIN', 'CASHIER', 'KITCHEN'], required=False)
    is_active = serializers.BooleanField(required=False)


class AdminTableSerializer(serializers.ModelSerializer):
    has_active_session = serializers.SerializerMethodField()
    sessions_count = serializers.SerializerMethodField()

    class Meta:
        model = RestaurantTable
        fields = [
            'id', 'table_code', 'display_name', 'capacity', 'qr_token', 'status',
            'sort_order', 'is_active', 'has_active_session', 'sessions_count', 'created_at',
        ]

    def get_has_active_session(self, obj):
        from apps.tables.admin_services import has_active_session
        return has_active_session(obj)

    def get_sessions_count(self, obj):
        return obj.sessions.count()


class TableCreateUpdateSerializer(serializers.Serializer):
    table_code = serializers.CharField(max_length=30)
    display_name = serializers.CharField(max_length=80)
    capacity = serializers.IntegerField(required=False, allow_null=True, min_value=1, max_value=50)
    sort_order = serializers.IntegerField(required=False, default=0)
    is_active = serializers.BooleanField(required=False, default=True)
    status = serializers.ChoiceField(choices=['AVAILABLE', 'MAINTENANCE'], required=False)


class RestaurantTableSerializer(serializers.ModelSerializer):
    active_session = serializers.SerializerMethodField()
    pending_activation = serializers.SerializerMethodField()

    class Meta:
        model = RestaurantTable
        fields = ['id', 'table_code', 'display_name', 'capacity', 'guest_limit', 'qr_token', 'status', 'sort_order', 'active_session', 'pending_activation']

    guest_limit = serializers.SerializerMethodField()

    def get_guest_limit(self, obj):
        from apps.tables.device import guest_limit
        return guest_limit(obj)

    def get_active_session(self, obj):
        from apps.tables.selectors import get_active_session_for_table
        from apps.payments.services import calculate_session_bill
        from apps.tables.progress import session_progress
        session = get_active_session_for_table(obj)
        if not session:
            return None
        bill = calculate_session_bill(session)
        idle_minutes = self.context.get('idle_minutes') if hasattr(self, 'context') else None
        progress = session_progress(session, idle_minutes=idle_minutes)
        return {
            'progress': progress['orders'],
            'pending_items': progress['pending_items'],
            'ready_items': progress['ready_items'],
            'served_items': progress['served_items'],
            'waiting_items': progress['waiting_items'],
            'all_delivered': progress['all_delivered'],
            'last_activity_at': progress['last_activity_at'],
            'idle_minutes': progress['idle_minutes'],
            'is_idle': progress['is_idle'],
            'id': str(session.id),
            'public_token': session.public_token,
            'status': session.status,
            'guest_count': session.guest_count,
            'opened_at': session.opened_at,
            'bill_requested_at': session.bill_requested_at,
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
                'requested_at': req.requested_at.isoformat(),
            }
        except Exception:
            return None


class TableActivationRequestSerializer(serializers.ModelSerializer):
    table_capacity = serializers.IntegerField(source='table.capacity', read_only=True)
    table_id = serializers.UUIDField(source='table.id', read_only=True)
    table_code = serializers.CharField(source='table.table_code', read_only=True)
    table_name = serializers.CharField(source='table.display_name', read_only=True)
    guest_limit = serializers.SerializerMethodField()

    class Meta:
        from apps.tables.models import TableActivationRequest
        model = TableActivationRequest
        fields = ['id', 'table_id', 'table_code', 'table_name', 'table_capacity', 'guest_limit', 'guest_count',
                  'status', 'requested_at']

    def get_guest_limit(self, obj):
        from apps.tables.device import guest_limit
        return guest_limit(obj.table)


class OrderItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = OrderItem
        fields = [
            'id', 'menu_name_snapshot', 'sku_snapshot', 'unit_price', 'quantity', 'subtotal', 'note',
            'status', 'unavailable_reason', 'cooking_started_at', 'ready_at', 'served_at',
        ]

class OrderSerializer(serializers.ModelSerializer):
    items = OrderItemSerializer(many=True, read_only=True)
    table_code = serializers.CharField(source='table_session.table.table_code', read_only=True)
    table_name = serializers.CharField(source='table_session.label', read_only=True)
    session_token = serializers.CharField(source='table_session.public_token', read_only=True)
    session_id = serializers.UUIDField(source='table_session.id', read_only=True)
    session_status = serializers.CharField(source='table_session.status', read_only=True)
    is_takeaway = serializers.BooleanField(source='table_session.is_takeaway', read_only=True)
    takeaway_number = serializers.IntegerField(source='table_session.takeaway_number', read_only=True)
    customer_name = serializers.CharField(source='table_session.customer_name', read_only=True)

    class Meta:
        model = Order
        fields = [
            'id', 'order_code', 'source', 'status', 'subtotal',
            'tax_total', 'discount_total', 'grand_total', 'customer_note',
            'rejection_reason', 'submitted_at', 'confirmed_at', 'created_at',
            'table_code', 'table_name', 'session_token', 'items',
            'session_id', 'session_status', 'is_takeaway', 'takeaway_number', 'customer_name',
        ]

class OrderSubmitItemSerializer(serializers.Serializer):
    menu_item_id = serializers.UUIDField()
    quantity = serializers.IntegerField(min_value=1, max_value=99)
    note = serializers.CharField(required=False, allow_blank=True, max_length=500)

class OrderSubmitRequestSerializer(serializers.Serializer):
    items = OrderSubmitItemSerializer(many=True)
    customer_note = serializers.CharField(required=False, allow_blank=True, max_length=1000)

class CashierOrderCreateSerializer(serializers.Serializer):
    """A cashier order: `destination` TAKEAWAY (optional customer name) or TABLE (with `table_id`)."""
    destination = serializers.ChoiceField(choices=['TAKEAWAY', 'TABLE'])
    table_id = serializers.UUIDField(required=False)
    customer_name = serializers.CharField(required=False, allow_blank=True, max_length=80, default='')
    customer_note = serializers.CharField(required=False, allow_blank=True, max_length=1000, default='')
    items = OrderSubmitItemSerializer(many=True)

    def validate(self, data):
        if data['destination'] == 'TABLE' and not data.get('table_id'):
            raise serializers.ValidationError({'table_id': 'Required for a table order.'})
        if not data['items']:
            raise serializers.ValidationError({'items': 'At least one item.'})
        return data


class CashPaymentRequestSerializer(serializers.Serializer):
    tendered_amount = serializers.DecimalField(max_digits=12, decimal_places=2)
    method = serializers.ChoiceField(choices=['CASH'], default='CASH')
