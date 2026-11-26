import uuid
from urllib.parse import quote

from django.db import models


class Restaurant(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=150, default="Restaurante Sabor Dili")
    short_name = models.CharField(max_length=30, blank=True, default="")  # PWA short name (home screen label)
    legal_name = models.CharField(max_length=200, blank=True, null=True)
    description = models.CharField(max_length=300, blank=True, default="")
    phone = models.CharField(max_length=50, blank=True, null=True)
    whatsapp = models.CharField(max_length=20, blank=True, default="")  # digits, optional leading +
    email = models.EmailField(blank=True, default="")
    website = models.URLField(max_length=300, blank=True, default="")
    instagram = models.URLField(max_length=300, blank=True, default="")
    facebook = models.URLField(max_length=300, blank=True, default="")
    address = models.TextField(blank=True, null=True)
    maps_url = models.URLField(max_length=600, blank=True, default="")
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    receipt_note = models.CharField(max_length=300, blank=True, default="")
    # Cashier alert: an unpaid table with nothing in the kitchen and no activity for this long (0 = off).
    idle_alert_minutes = models.PositiveSmallIntegerField(default=45)
    # Hash of the uploaded logo; empty means the default fork-and-knife icon is used everywhere.
    logo_version = models.CharField(max_length=40, blank=True, default="")
    currency = models.CharField(max_length=3, default="USD")
    timezone = models.CharField(max_length=50, default="Asia/Dili")
    tax_percentage = models.DecimalField(max_digits=5, decimal_places=2, default=0.00)
    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'restaurants'
        verbose_name = 'Restaurant'
        verbose_name_plural = 'Restaurants'

    def __str__(self):
        return self.name

    # ------------------------------------------------------------------ contact links
    @property
    def has_logo(self):
        return bool(self.logo_version)

    @property
    def app_short_name(self):
        return (self.short_name or self.name or 'Celvass')[:30]

    @property
    def whatsapp_link(self):
        digits = ''.join(ch for ch in (self.whatsapp or '') if ch.isdigit())
        return f"https://wa.me/{digits}" if digits else ''

    @property
    def tel_link(self):
        number = ''.join(ch for ch in (self.phone or '') if ch.isdigit() or ch == '+')
        return f"tel:{number}" if number else ''

    @property
    def coordinates(self):
        if self.latitude is None or self.longitude is None:
            return ''
        return f"{self.latitude},{self.longitude}"

    @property
    def maps_link(self):
        if self.maps_url:
            return self.maps_url
        if self.coordinates:
            return f"https://www.google.com/maps/search/?api=1&query={self.coordinates}"
        if self.address:
            return f"https://www.google.com/maps/search/?api=1&query={quote(self.address)}"
        return ''

    @property
    def maps_embed_url(self):
        """Keyless Google Maps embed for the preview (coordinates first, else the address)."""
        query = self.coordinates or (self.address or '').strip()
        if not query:
            return ''
        return f"https://maps.google.com/maps?q={quote(query)}&z=16&output=embed"


class OpeningHours(models.Model):
    """Opening time of one weekday (0 = Monday). `closes_at <= opens_at` means it closes after midnight."""
    id = models.BigAutoField(primary_key=True)
    restaurant = models.ForeignKey(Restaurant, on_delete=models.CASCADE, related_name='opening_hours')
    weekday = models.PositiveSmallIntegerField()
    is_closed = models.BooleanField(default=False)
    opens_at = models.TimeField(null=True, blank=True)
    closes_at = models.TimeField(null=True, blank=True)

    class Meta:
        db_table = 'restaurant_opening_hours'
        ordering = ['weekday']
        constraints = [
            models.UniqueConstraint(fields=['restaurant', 'weekday'], name='unique_opening_hours_per_day'),
        ]

    def __str__(self):
        return f"{self.weekday}: {'closed' if self.is_closed else f'{self.opens_at}-{self.closes_at}'}"


class RestaurantIcon(models.Model):
    """PNG/ICO variants generated from the uploaded logo (kept in the database: Vercel's disk is read-only)."""
    id = models.BigAutoField(primary_key=True)
    restaurant = models.ForeignKey(Restaurant, on_delete=models.CASCADE, related_name='icons')
    name = models.CharField(max_length=40)
    content_type = models.CharField(max_length=40, default='image/png')
    data = models.BinaryField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'restaurant_icons'
        constraints = [
            models.UniqueConstraint(fields=['restaurant', 'name'], name='unique_restaurant_icon_name'),
        ]
