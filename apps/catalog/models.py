import uuid
from decimal import Decimal
from django.db import models
from django.core.validators import MinValueValidator

class ItemAvailability(models.TextChoices):
    AVAILABLE = 'AVAILABLE', 'Available'
    SOLD_OUT = 'SOLD_OUT', 'Sold Out'
    INACTIVE = 'INACTIVE', 'Inactive'

class Category(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    restaurant = models.ForeignKey('restaurants.Restaurant', on_delete=models.CASCADE, related_name='categories')
    name = models.CharField(max_length=100)
    slug = models.SlugField(max_length=120)
    description = models.TextField(blank=True, null=True)
    icon_name = models.CharField(max_length=50, blank=True, default="utensils")
    sort_order = models.IntegerField(default=0)
    is_active = models.BooleanField(default=True, db_index=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'categories'
        verbose_name = 'Category'
        verbose_name_plural = 'Categories'
        ordering = ['sort_order', 'name']
        constraints = [
            models.UniqueConstraint(fields=['restaurant', 'slug'], name='unique_restaurant_category_slug'),
        ]

    def __str__(self):
        return self.name

class MenuItem(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    restaurant = models.ForeignKey('restaurants.Restaurant', on_delete=models.CASCADE, related_name='menu_items')
    category = models.ForeignKey(Category, on_delete=models.CASCADE, related_name='items')
    sku = models.CharField(max_length=50, blank=True, null=True)
    name = models.CharField(max_length=150)
    slug = models.SlugField(max_length=180)
    description = models.TextField(blank=True)
    price = models.DecimalField(max_digits=12, decimal_places=2, validators=[MinValueValidator(Decimal('0.00'))])
    image = models.ImageField(upload_to='menu_items/', blank=True, null=True)
    image_url = models.URLField(max_length=500, blank=True, null=True)
    availability = models.CharField(max_length=20, choices=ItemAvailability.choices, default=ItemAvailability.AVAILABLE, db_index=True)
    is_featured = models.BooleanField(default=False)
    sort_order = models.IntegerField(default=0)
    preparation_note = models.TextField(blank=True, null=True)
    # Portions the kitchen can make per business day. Empty means no limit.
    daily_quota = models.PositiveIntegerField(null=True, blank=True, validators=[MinValueValidator(1)])
    # Customer ratings (recomputed from MenuRating whenever a rating is saved).
    rating_avg = models.DecimalField(max_digits=3, decimal_places=2, default=Decimal('0.00'))
    rating_count = models.PositiveIntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'menu_items'
        verbose_name = 'Menu Item'
        verbose_name_plural = 'Menu Items'
        ordering = ['sort_order', 'name']
        constraints = [
            models.UniqueConstraint(fields=['restaurant', 'slug'], name='unique_restaurant_menu_slug'),
        ]

    def __str__(self):
        return f"{self.name} (${self.price})"

    @property
    def display_image(self):
        if self.image:
            try:
                url = self.image.url
                if url:
                    return url
            except Exception:
                pass
        if self.image_url:
            return self.image_url
        return "/static/icons/default-food.png"

    @property
    def is_available(self):
        return self.availability == ItemAvailability.AVAILABLE


class DailyMenuState(models.Model):
    """A menu item closed by staff for one business day (the quota itself lives on MenuItem)."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    menu_item = models.ForeignKey(MenuItem, on_delete=models.CASCADE, related_name='daily_states')
    business_date = models.DateField(db_index=True)
    is_closed = models.BooleanField(default=True)
    reason = models.CharField(max_length=255, blank=True, default="")
    closed_by = models.ForeignKey(
        'accounts.User', on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    closed_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'daily_menu_states'
        constraints = [
            models.UniqueConstraint(fields=['menu_item', 'business_date'], name='unique_menu_state_per_day'),
        ]

    def __str__(self):
        return f"{self.menu_item_id} {self.business_date} closed={self.is_closed}"


class MenuRating(models.Model):
    """One customer's stars (1-5) for one dish of a finished table session."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    menu_item = models.ForeignKey(MenuItem, on_delete=models.CASCADE, related_name='ratings')
    table_session = models.ForeignKey('tables.TableSession', on_delete=models.CASCADE, related_name='menu_ratings')
    stars = models.PositiveSmallIntegerField()
    comment = models.CharField(max_length=300, blank=True, default="")
    device_token = models.CharField(max_length=128, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'menu_ratings'
        ordering = ['-updated_at']
        constraints = [
            models.UniqueConstraint(fields=['table_session', 'menu_item'], name='unique_rating_per_session_item'),
        ]

    def __str__(self):
        return f"{self.menu_item_id} {self.stars}*"
