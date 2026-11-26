import uuid
from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models


class ShiftStatus(models.TextChoices):
    OPEN = 'OPEN', 'Open'
    CLOSED = 'CLOSED', 'Closed'


class Shift(models.Model):
    """A cashier's working period: opening cash, cash payments taken, counted cash at the end."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    restaurant = models.ForeignKey('restaurants.Restaurant', on_delete=models.CASCADE, related_name='shifts')
    cashier = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='shifts')
    status = models.CharField(max_length=10, choices=ShiftStatus.choices, default=ShiftStatus.OPEN, db_index=True)

    opening_cash = models.DecimalField(max_digits=12, decimal_places=2, validators=[MinValueValidator(Decimal('0.00'))])
    opened_at = models.DateTimeField(auto_now_add=True, db_index=True)

    expected_cash = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    counted_cash = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True,
                                       validators=[MinValueValidator(Decimal('0.00'))])
    difference = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    cash_sales = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    payments_count = models.PositiveIntegerField(default=0)
    closing_note = models.TextField(blank=True, default="")
    closed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'shifts'
        ordering = ['-opened_at']
        constraints = [
            models.UniqueConstraint(fields=['cashier'], condition=models.Q(status='OPEN'), name='one_open_shift_per_cashier'),
        ]

    def __str__(self):
        return f"Shift {self.cashier_id} {self.status} {self.opened_at:%Y-%m-%d %H:%M}"
