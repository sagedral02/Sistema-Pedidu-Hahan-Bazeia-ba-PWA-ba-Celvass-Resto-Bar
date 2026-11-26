import uuid
from decimal import Decimal
from django.db import models
from django.conf import settings
from django.core.validators import MinValueValidator, MaxValueValidator

class OrderSource(models.TextChoices):
    CUSTOMER = 'CUSTOMER', 'Customer Dine-In PWA'
    CASHIER = 'CASHIER', 'Cashier POS'

class OrderStatus(models.TextChoices):
    DRAFT = 'DRAFT', 'Draft'
    WAITING_CASHIER_CONFIRMATION = 'WAITING_CASHIER_CONFIRMATION', 'Waiting Cashier Confirmation'
    CONFIRMED = 'CONFIRMED', 'Confirmed (Sent to Kitchen)'
    PREPARING = 'PREPARING', 'Preparing in Kitchen'
    READY = 'READY', 'Ready to Serve'
    SERVED = 'SERVED', 'Served to Table'
    COMPLETED = 'COMPLETED', 'Completed'
    REJECTED = 'REJECTED', 'Rejected by Cashier'
    CANCELLED = 'CANCELLED', 'Cancelled'

class ItemStatus(models.TextChoices):
    """Kitchen lifecycle of a single order line. The order status is derived from these (see items.py)."""
    QUEUED = 'QUEUED', 'Queued'
    COOKING = 'COOKING', 'Cooking'
    READY = 'READY', 'Ready'
    SERVED = 'SERVED', 'Served'
    UNAVAILABLE = 'UNAVAILABLE', 'Unavailable (out of stock)'

class ActorType(models.TextChoices):
    CUSTOMER = 'CUSTOMER', 'Customer'
    STAFF = 'STAFF', 'Staff'
    SYSTEM = 'SYSTEM', 'System'

class Order(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    restaurant = models.ForeignKey('restaurants.Restaurant', on_delete=models.CASCADE, related_name='orders')
    table_session = models.ForeignKey('tables.TableSession', on_delete=models.CASCADE, related_name='orders')
    order_code = models.CharField(max_length=40, unique=True, db_index=True)
    source = models.CharField(max_length=20, choices=OrderSource.choices, default=OrderSource.CUSTOMER)
    status = models.CharField(max_length=40, choices=OrderStatus.choices, default=OrderStatus.WAITING_CASHIER_CONFIRMATION, db_index=True)
    
    idempotency_key = models.CharField(max_length=128, db_index=True)
    
    subtotal = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'), validators=[MinValueValidator(Decimal('0.00'))])
    discount_total = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'), validators=[MinValueValidator(Decimal('0.00'))])
    tax_total = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'), validators=[MinValueValidator(Decimal('0.00'))])
    grand_total = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'), validators=[MinValueValidator(Decimal('0.00'))])
    
    customer_note = models.TextField(blank=True, null=True)
    rejection_reason = models.TextField(blank=True, null=True)
    cancellation_reason = models.TextField(blank=True, null=True)
    
    submitted_at = models.DateTimeField(auto_now_add=True, db_index=True)
    confirmed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='confirmed_orders')
    confirmed_at = models.DateTimeField(null=True, blank=True, db_index=True)
    
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='created_orders')
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'orders'
        verbose_name = 'Order'
        verbose_name_plural = 'Orders'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['status', 'created_at']),
            models.Index(fields=['table_session', 'created_at']),
            models.Index(fields=['confirmed_at']),
        ]
        constraints = [
            models.UniqueConstraint(fields=['table_session', 'idempotency_key'], name='unique_session_idempotency_order'),
        ]

    def __str__(self):
        return f"{self.order_code} - {self.table_session.table.display_name} [{self.get_status_display()}] (${self.grand_total})"

class OrderItem(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='items')
    menu_item = models.ForeignKey('catalog.MenuItem', on_delete=models.SET_NULL, null=True, blank=True, related_name='order_items')
    
    menu_name_snapshot = models.CharField(max_length=150)
    sku_snapshot = models.CharField(max_length=50, blank=True, null=True)
    unit_price = models.DecimalField(max_digits=12, decimal_places=2, validators=[MinValueValidator(Decimal('0.00'))])
    quantity = models.PositiveIntegerField(default=1, validators=[MinValueValidator(1), MaxValueValidator(99)])
    subtotal = models.DecimalField(max_digits=12, decimal_places=2, validators=[MinValueValidator(Decimal('0.00'))])
    note = models.CharField(max_length=500, blank=True, null=True)

    status = models.CharField(max_length=20, choices=ItemStatus.choices, default=ItemStatus.QUEUED, db_index=True)
    cooking_started_at = models.DateTimeField(null=True, blank=True)
    ready_at = models.DateTimeField(null=True, blank=True)
    served_at = models.DateTimeField(null=True, blank=True)
    unavailable_reason = models.CharField(max_length=255, blank=True, default="")
    status_changed_at = models.DateTimeField(null=True, blank=True)
    status_changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+'
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'order_items'
        verbose_name = 'Order Item'
        verbose_name_plural = 'Order Items'

    def __str__(self):
        return f"{self.quantity}x {self.menu_name_snapshot} (${self.subtotal})"

class OrderStatusHistory(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='status_history')
    from_status = models.CharField(max_length=40, null=True, blank=True)
    to_status = models.CharField(max_length=40)
    actor_user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    actor_type = models.CharField(max_length=20, choices=ActorType.choices, default=ActorType.CUSTOMER)
    reason = models.TextField(blank=True, null=True)
    request_id = models.CharField(max_length=100, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = 'order_status_history'
        verbose_name = 'Order Status History'
        verbose_name_plural = 'Order Status Histories'
        ordering = ['created_at']

    def __str__(self):
        return f"{self.order.order_code}: {self.from_status} -> {self.to_status}"

class IdempotencyRecord(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    scope = models.CharField(max_length=100, db_index=True)
    key = models.CharField(max_length=128, db_index=True)
    request_hash = models.CharField(max_length=64, blank=True)
    response_status = models.IntegerField(default=200)
    response_body = models.JSONField(default=dict)
    resource_type = models.CharField(max_length=80, blank=True)
    resource_id = models.CharField(max_length=100, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'idempotency_records'
        constraints = [
            models.UniqueConstraint(fields=['scope', 'key'], name='unique_scope_key'),
        ]

    def __str__(self):
        return f"{self.scope}:{self.key} -> {self.resource_type}:{self.resource_id}"
