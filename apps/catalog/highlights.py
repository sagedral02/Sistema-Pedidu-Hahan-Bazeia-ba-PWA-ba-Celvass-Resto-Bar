"""Menu highlights for the customer catalog: best rated, most ordered (last 30 days) and the house picks.

Every list can be limited to one category. Only active dishes of active categories are listed; dishes that
are sold out today stay in the list (the card shows them as unavailable).
"""
from datetime import timedelta
from typing import Dict, List, Optional

from django.db.models import Sum
from django.utils import timezone

from .models import ItemAvailability, MenuItem

MIN_REVIEWS = 3
POPULAR_DAYS = 30
DEFAULT_LIMIT = 8


def _visible(category_slug: Optional[str] = None):
    qs = MenuItem.objects.filter(category__is_active=True).exclude(availability=ItemAvailability.INACTIVE) \
        .select_related('category')
    if category_slug:
        qs = qs.filter(category__slug=category_slug)
    return qs


def top_rated(category_slug=None, limit=DEFAULT_LIMIT) -> List[MenuItem]:
    return list(_visible(category_slug).filter(rating_count__gte=MIN_REVIEWS)
                .order_by('-rating_avg', '-rating_count', 'name')[:limit])


def popular_counts(category_slug=None, now=None) -> Dict[str, int]:
    """Portions ordered per dish over the last POPULAR_DAYS (cancelled lines and refused orders excluded)."""
    from apps.ordering.models import ItemStatus, OrderItem, OrderStatus
    since = (now or timezone.now()) - timedelta(days=POPULAR_DAYS)
    qs = (OrderItem.objects.filter(order__created_at__gte=since, menu_item__isnull=False)
          .exclude(status=ItemStatus.UNAVAILABLE)
          .exclude(order__status__in=[OrderStatus.REJECTED, OrderStatus.CANCELLED, OrderStatus.DRAFT]))
    if category_slug:
        qs = qs.filter(menu_item__category__slug=category_slug)
    return {str(r['menu_item_id']): int(r['n'] or 0) for r in qs.values('menu_item_id').annotate(n=Sum('quantity'))}


def most_ordered(category_slug=None, limit=DEFAULT_LIMIT, now=None) -> List[MenuItem]:
    counts = popular_counts(category_slug, now)
    if not counts:
        return []
    items = {str(i.id): i for i in _visible(category_slug).filter(id__in=list(counts))}
    ranked = sorted(items.values(), key=lambda i: (-counts[str(i.id)], i.name.lower()))[:limit]
    for item in ranked:
        item.ordered_count = counts[str(item.id)]
    return ranked


def featured(category_slug=None, limit=DEFAULT_LIMIT) -> List[MenuItem]:
    return list(_visible(category_slug).filter(is_featured=True).order_by('category__sort_order', 'sort_order', 'name')[:limit])


def catalog_highlights(category_slug=None, limit=DEFAULT_LIMIT, now=None) -> Dict[str, List[MenuItem]]:
    return {
        'top_rated': top_rated(category_slug, limit),
        'most_ordered': most_ordered(category_slug, limit, now),
        'featured': featured(category_slug, limit),
    }


def highlight_sections(categories, limit=DEFAULT_LIMIT, now=None) -> List[Dict]:
    """[{slug: 'all' | category slug, top_rated, most_ordered, featured}] for the catalog chips."""
    out = [{'slug': 'all', **catalog_highlights(None, limit, now)}]
    for cat in categories:
        out.append({'slug': cat.slug, **catalog_highlights(cat.slug, limit, now)})
    return out
