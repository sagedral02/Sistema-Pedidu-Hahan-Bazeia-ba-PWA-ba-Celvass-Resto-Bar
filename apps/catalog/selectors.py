from typing import List, Optional
from django.db.models import Count
from .models import Category, MenuItem, ItemAvailability

def get_categories(active_only: bool = True) -> List[Category]:
    qs = Category.objects.all()
    if active_only:
        qs = qs.filter(is_active=True)
    return qs.order_by('sort_order', 'name')

def get_categories_with_counts(active_only: bool = False):
    qs = Category.objects.annotate(item_count=Count('items'))
    if active_only:
        qs = qs.filter(is_active=True)
    return qs.order_by('sort_order', 'name')

def get_category_by_id(category_id) -> Optional[Category]:
    try:
        return Category.objects.filter(id=category_id).first()
    except Exception:
        return None

def get_public_menu(category_slug: Optional[str] = None):
    qs = MenuItem.objects.filter(
        availability__in=[ItemAvailability.AVAILABLE, ItemAvailability.SOLD_OUT],
        category__is_active=True
    ).select_related('category')
    
    if category_slug:
        qs = qs.filter(category__slug=category_slug)
        
    return qs.order_by('category__sort_order', 'sort_order', 'name')

def get_all_menu_items():
    return MenuItem.objects.select_related('category').order_by('category__sort_order', 'sort_order', 'name')

def get_menu_item_by_id(item_id) -> Optional[MenuItem]:
    try:
        return MenuItem.objects.select_related('category').filter(id=item_id).first()
    except Exception:
        return None
