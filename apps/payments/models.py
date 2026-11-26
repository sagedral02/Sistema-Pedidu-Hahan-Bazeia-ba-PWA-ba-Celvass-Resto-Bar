import uuid
from decimal import Decimal
from django.db import models
from django.conf import settings
from django.core.validators import MinValueValidator

class PaymentMethod(models.TextChoices):
    CASH = 'CASH', 'Cash'
    MANUAL_OTHER = 'MANUAL_OTHER', 'Manual Card / Transfer'

class PaymentStatus(models.TextChoices):
    PENDING = 'PENDING', 'Pending'
    COMPLETED = 'COMPLETED', 'Completed'
    VOIDED = 'VOIDED', 'Voided'

class Payment(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    restaurant = models.ForeignKey('restaurants.Restaurant', on_delete=models.CASCADE, related_name='payments')
    table_session = models.ForeignKey('tables.TableSession', on_delete=models.CASCADE, related_name='payments')
    payment_code = models.CharField(max_length=40, unique=True, db_index=True)
    method = models.CharField(max_length=20, choices=PaymentMethod.choices, default=PaymentMethod.CASH)
    
    amount = models.DecimalField(max_digits=12, decimal_places=2, validators=[MinValueValidator(Decimal('0.00'))])
    tendered_amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True, validators=[MinValueValidator(Decimal('0.00'))])
    change_amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True, default=Decimal('0.00'))
    
    status = models.CharField(max_length=20, choices=PaymentStatus.choices, default=PaymentStatus.PENDING, db_index=True)
    
    received_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='received_payments')
    paid_at = models.DateTimeField(null=True, blank=True, db_index=True)
    
    voided_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='voided_payments')
    void_reason = models.TextField(null=True, blank=True)
    
    idempotency_key = models.CharField(max_length=128, db_index=True, blank=True, default="")
    bill = models.ForeignKey('payments.Bill', on_delete=models.SET_NULL, null=True, blank=True, related_name='payments')
    shift = models.ForeignKey('shifts.Shift', on_delete=models.SET_NULL, null=True, blank=True, related_name='payments')

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'payments'
        verbose_name = 'Payment'
        verbose_name_plural = 'Payments'
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.payment_code} - ${self.amount} ({self.get_status_display()})"


class BillStatus(models.TextChoices):
    REQUESTED = 'REQUESTED', 'Requested'
    REOPENED = 'REOPENED', 'Reopened (customer ordered again)'
    PAID = 'PAID', 'Paid'
    WALKOUT = 'WALKOUT', 'Unpaid (guests left)'


class Bill(models.Model):
    """The bill of one table session. A payment can only be taken against a REQUESTED bill."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    restaurant = models.ForeignKey('restaurants.Restaurant', on_delete=models.CASCADE, related_name='bills')
    table_session = models.ForeignKey('tables.TableSession', on_delete=models.CASCADE, related_name='bills')
    status = models.CharField(max_length=20, choices=BillStatus.choices, default=BillStatus.REQUESTED, db_index=True)
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    forced = models.BooleanField(default=False)  # cashier asked for it while items were still being prepared

    subtotal = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    tax_total = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    discount_total = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    grand_total = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))

    requested_at = models.DateTimeField(auto_now_add=True)
    reopened_at = models.DateTimeField(null=True, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'bills'
        ordering = ['-requested_at']
        indexes = [models.Index(fields=['table_session', 'status'])]

    def __str__(self):
        return f"Bill {self.id} {self.status} ${self.grand_total}"


class BillLine(models.Model):
    """Snapshot of one billed order line (out-of-stock lines are never copied)."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    bill = models.ForeignKey(Bill, on_delete=models.CASCADE, related_name='lines')
    order_item = models.ForeignKey('ordering.OrderItem', on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    order_code = models.CharField(max_length=40, blank=True, default="")
    name = models.CharField(max_length=150)
    quantity = models.PositiveIntegerField(default=1)
    unit_price = models.DecimalField(max_digits=12, decimal_places=2)
    subtotal = models.DecimalField(max_digits=12, decimal_places=2)
    note = models.CharField(max_length=500, blank=True, default="")

    class Meta:
        db_table = 'bill_lines'
        ordering = ['order_code', 'id']
