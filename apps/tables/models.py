import uuid
import secrets
from django.db import models
from django.conf import settings

class TableStatus(models.TextChoices):
    AVAILABLE = 'AVAILABLE', 'Available'
    OCCUPIED = 'OCCUPIED', 'Occupied'
    CLEANING = 'CLEANING', 'Cleaning'
    MAINTENANCE = 'MAINTENANCE', 'Maintenance'

class SessionStatus(models.TextChoices):
    OPEN = 'OPEN', 'Open'
    BILL_REQUESTED = 'BILL_REQUESTED', 'Bill Requested'
    PAYMENT_PENDING = 'PAYMENT_PENDING', 'Payment Pending'
    PAID = 'PAID', 'Paid'
    CLOSED = 'CLOSED', 'Closed'
    CANCELLED = 'CANCELLED', 'Cancelled'

def generate_secure_token():
    return secrets.token_urlsafe(32)

class RestaurantTable(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    restaurant = models.ForeignKey('restaurants.Restaurant', on_delete=models.CASCADE, related_name='tables')
    table_code = models.CharField(max_length=30)
    display_name = models.CharField(max_length=80)
    capacity = models.PositiveIntegerField(null=True, blank=True)
    qr_token = models.CharField(max_length=128, unique=True, default=generate_secure_token, db_index=True)
    status = models.CharField(max_length=20, choices=TableStatus.choices, default=TableStatus.AVAILABLE, db_index=True)
    sort_order = models.IntegerField(default=0)
    is_active = models.BooleanField(default=True, db_index=True)
    # Hidden virtual table that carries every take-away order (never listed, never has a QR page).
    is_takeaway = models.BooleanField(default=False, db_index=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'restaurant_tables'
        verbose_name = 'Restaurant Table'
        verbose_name_plural = 'Restaurant Tables'
        ordering = ['sort_order', 'table_code']
        constraints = [
            models.UniqueConstraint(fields=['restaurant', 'table_code'], name='unique_restaurant_table_code'),
        ]

    def __str__(self):
        return f"{self.display_name} ({self.table_code})"

    @property
    def compact_name(self):
        """Returns clean short table name (e.g. 'Meza 01') without long descriptions."""
        if self.display_name:
            base = self.display_name.split('(')[0].strip()
            if base:
                return base
        return self.table_code or "Meza"

    def rotate_qr(self):
        self.qr_token = generate_secure_token()
        self.save(update_fields=['qr_token', 'updated_at'])
        return self.qr_token

class TableSession(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    restaurant = models.ForeignKey('restaurants.Restaurant', on_delete=models.CASCADE, related_name='sessions')
    table = models.ForeignKey(RestaurantTable, on_delete=models.PROTECT, related_name='sessions')
    public_token = models.CharField(max_length=128, unique=True, default=generate_secure_token, db_index=True)
    status = models.CharField(max_length=30, choices=SessionStatus.choices, default=SessionStatus.OPEN, db_index=True)
    guest_count = models.PositiveIntegerField(default=1)
    primary_device_token = models.CharField(max_length=128, null=True, blank=True, db_index=True)
    authorized_device_tokens = models.JSONField(default=list, blank=True)
    
    opened_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='opened_sessions')
    opened_at = models.DateTimeField(auto_now_add=True)
    bill_requested_at = models.DateTimeField(null=True, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    closed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='closed_sessions')
    closed_at = models.DateTimeField(null=True, blank=True)
    close_reason = models.TextField(null=True, blank=True)
    # NORMAL = settled and closed; WALKOUT = the guests left without paying (unpaid_amount is the loss).
    close_kind = models.CharField(max_length=10, blank=True, default="")
    unpaid_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    # Idle alert already raised for this session (so the cashier is notified once).
    idle_alerted_at = models.DateTimeField(null=True, blank=True)
    # Take away only: daily queue number (TA-001) and the optional name the customer gave.
    takeaway_number = models.PositiveIntegerField(null=True, blank=True)
    takeaway_date = models.DateField(null=True, blank=True)
    customer_name = models.CharField(max_length=80, blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'table_sessions'
        verbose_name = 'Table Session'
        verbose_name_plural = 'Table Sessions'
        indexes = [
            models.Index(fields=['table', 'status']),
            models.Index(fields=['opened_at']),
        ]

    def __str__(self):
        return f"Session {self.public_token[:8]} - {self.table.display_name} ({self.status})"

    @property
    def is_takeaway(self):
        return bool(self.takeaway_number) or self.table.is_takeaway

    @property
    def label(self):
        """Short name for lists and receipts: the table name, or "Take away #012 - Budi"."""
        if not self.is_takeaway:
            return self.table.display_name
        base = f"Take away #{(self.takeaway_number or 0):03d}"
        return f"{base} - {self.customer_name}" if self.customer_name else base

    @property
    def is_active(self):
        return self.status in [
            SessionStatus.OPEN,
            SessionStatus.BILL_REQUESTED,
            SessionStatus.PAYMENT_PENDING,
            SessionStatus.PAID
        ]


class CloseKind(models.TextChoices):
    NORMAL = 'NORMAL', 'Normal'
    WALKOUT = 'WALKOUT', 'Left without paying'


class ActivationRequestStatus(models.TextChoices):
    PENDING = 'PENDING', 'Pending'
    APPROVED = 'APPROVED', 'Approved'
    REJECTED = 'REJECTED', 'Rejected'
    CANCELLED = 'CANCELLED', 'Cancelled'


class TableActivationRequest(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    restaurant = models.ForeignKey('restaurants.Restaurant', on_delete=models.CASCADE, related_name='activation_requests')
    table = models.ForeignKey(RestaurantTable, on_delete=models.CASCADE, related_name='activation_requests')
    device_token = models.CharField(max_length=128, null=True, blank=True, db_index=True)
    status = models.CharField(max_length=20, choices=ActivationRequestStatus.choices, default=ActivationRequestStatus.PENDING, db_index=True)
    guest_count = models.PositiveIntegerField(default=2)
    requested_at = models.DateTimeField(auto_now_add=True, db_index=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='resolved_activations')

    class Meta:
        db_table = 'table_activation_requests'
        verbose_name = 'Table Activation Request'
        verbose_name_plural = 'Table Activation Requests'
        ordering = ['-requested_at']
        indexes = [
            models.Index(fields=['table', 'status']),
            models.Index(fields=['status', 'requested_at']),
        ]

    def __str__(self):
        return f"Activation Request: {self.table.display_name} [{self.status}]"

