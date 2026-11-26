import uuid

from django.conf import settings
from django.db import models


class NoticeKind(models.TextChoices):
    OUT_OF_STOCK = 'OUT_OF_STOCK', 'Out of stock'


class NoticeStatus(models.TextChoices):
    OPEN = 'OPEN', 'Open'
    ACKNOWLEDGED = 'ACKNOWLEDGED', 'Acknowledged'
    RESOLVED = 'RESOLVED', 'Resolved'


class KitchenNotice(models.Model):
    """A message from the kitchen to the cashier (for now: a dish ran out)."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    restaurant = models.ForeignKey('restaurants.Restaurant', on_delete=models.CASCADE, related_name='kitchen_notices')
    kind = models.CharField(max_length=20, choices=NoticeKind.choices, default=NoticeKind.OUT_OF_STOCK)
    menu_item = models.ForeignKey('catalog.MenuItem', on_delete=models.SET_NULL, null=True, blank=True, related_name='kitchen_notices')
    menu_name_snapshot = models.CharField(max_length=150, blank=True, default="")
    order_item = models.ForeignKey('ordering.OrderItem', on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    order_code = models.CharField(max_length=40, blank=True, default="")
    table_label = models.CharField(max_length=120, blank=True, default="")
    message = models.CharField(max_length=255, blank=True, default="")
    status = models.CharField(max_length=20, choices=NoticeStatus.choices, default=NoticeStatus.OPEN, db_index=True)

    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    acknowledged_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    acknowledged_at = models.DateTimeField(null=True, blank=True)
    resolved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolution = models.CharField(max_length=30, blank=True, default="")  # MENU_CLOSED / DISMISSED

    class Meta:
        db_table = 'kitchen_notices'
        ordering = ['-created_at']
        indexes = [models.Index(fields=['restaurant', 'status', 'created_at'])]

    def __str__(self):
        return f"{self.kind} {self.menu_name_snapshot} [{self.status}]"
