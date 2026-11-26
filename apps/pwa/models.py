import uuid

from django.conf import settings
from django.db import models


class PushAudience(models.TextChoices):
    STAFF = 'STAFF', 'Staff'
    CUSTOMER = 'CUSTOMER', 'Customer'


class PushSubscription(models.Model):
    """
    One browser's Web Push subscription.

    Staff subscriptions belong to a user (the role decides which events they get). Customer subscriptions
    belong to one table: first to the QR token + device while the opening request waits, then to the
    session once the table is opened. They are removed when the session closes.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    endpoint = models.URLField(max_length=1000, unique=True)
    p256dh = models.CharField(max_length=255)
    auth = models.CharField(max_length=255)
    audience = models.CharField(max_length=10, choices=PushAudience.choices)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, null=True, blank=True,
                             related_name='push_subscriptions')
    table_session = models.ForeignKey('tables.TableSession', on_delete=models.CASCADE, null=True, blank=True,
                                      related_name='push_subscriptions')
    qr_token = models.CharField(max_length=128, blank=True, default='', db_index=True)
    device_token = models.CharField(max_length=128, blank=True, default='')
    lang = models.CharField(max_length=5, default='tet')
    failure_count = models.PositiveSmallIntegerField(default=0)
    last_error = models.CharField(max_length=255, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'push_subscriptions'
        indexes = [models.Index(fields=['audience', 'user'])]

    def __str__(self):
        return f"{self.audience} {self.endpoint[:40]}"

    def as_webpush(self):
        return {'endpoint': self.endpoint, 'keys': {'p256dh': self.p256dh, 'auth': self.auth}}
