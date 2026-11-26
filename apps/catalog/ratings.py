"""Customer ratings for dishes.

A guest rates (1-5 stars, optional comment) the dishes their table actually received, from the device that
owned the table session, within RATING_WINDOW after the session was paid or closed. One rating per dish per
session: sending it again updates it. MenuItem.rating_avg / rating_count are recomputed in the same transaction.
"""
from datetime import timedelta
from decimal import Decimal
from typing import Dict, List, Optional

from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Avg, Count
from django.utils import timezone

from .models import MenuItem, MenuRating

RATING_WINDOW = timedelta(hours=6)
MAX_COMMENT = 300
MAX_POSTS_PER_SESSION = 30
RATED_ORDER_STATUSES = ('CONFIRMED', 'PREPARING', 'READY', 'SERVED', 'COMPLETED')


def session_finished_at(session):
    return session.closed_at or session.paid_at


def finished_session(public_token: str, now=None):
    """A PAID or CLOSED table session still inside the thank-you / rating window, else None."""
    from apps.tables.models import SessionStatus, TableSession
    session = (TableSession.objects.select_related('table', 'restaurant')
               .filter(public_token=public_token, status__in=[SessionStatus.PAID, SessionStatus.CLOSED]).first())
    if session is None:
        return None
    finished = session_finished_at(session)
    if finished is None or (now or timezone.now()) - finished > RATING_WINDOW:
        return None
    return session


def device_owns(session, device_token: str) -> bool:
    """Only the device that claimed the table may see the thank-you page and rate."""
    if not session.primary_device_token or not device_token:
        return False
    return device_token == session.primary_device_token or device_token in (session.authorized_device_tokens or [])


def ratable_items(session) -> List[MenuItem]:
    from apps.ordering.models import ItemStatus, OrderItem
    ids = (OrderItem.objects.filter(order__table_session=session, order__status__in=RATED_ORDER_STATUSES,
                                    menu_item__isnull=False)
           .exclude(status=ItemStatus.UNAVAILABLE).values_list('menu_item_id', flat=True).distinct())
    return list(MenuItem.objects.filter(id__in=list(ids)).order_by('name'))


def ratings_payload(session) -> List[Dict]:
    current = {r.menu_item_id: r for r in MenuRating.objects.filter(table_session=session)}
    out = []
    for item in ratable_items(session):
        r = current.get(item.id)
        out.append({
            'menu_item_id': str(item.id), 'name': item.name, 'image': item.display_image,
            'stars': r.stars if r else None, 'comment': r.comment if r else '',
            'rating_avg': str(item.rating_avg), 'rating_count': item.rating_count,
        })
    return out


def recompute_item_rating(menu_item_id) -> MenuItem:
    item = MenuItem.objects.select_for_update().get(id=menu_item_id)
    agg = MenuRating.objects.filter(menu_item_id=menu_item_id).aggregate(avg=Avg('stars'), n=Count('id'))
    item.rating_count = agg['n'] or 0
    item.rating_avg = Decimal(str(agg['avg'] or 0)).quantize(Decimal('0.01'))
    item.save(update_fields=['rating_avg', 'rating_count', 'updated_at'])
    return item


def _clean_stars(value) -> int:
    try:
        stars = int(value)
    except (TypeError, ValueError):
        raise ValidationError("Stars must be a number from 1 to 5.")
    if isinstance(value, bool) or not 1 <= stars <= 5:
        raise ValidationError("Stars must be a number from 1 to 5.")
    return stars


def _clean_comment(value) -> str:
    text = ' '.join(str(value or '').split())
    if len(text) > MAX_COMMENT:
        raise ValidationError(f"The comment can have at most {MAX_COMMENT} characters.")
    return text


def submit_ratings(*, session, device_token: str, entries) -> List[Dict]:
    """entries: [{menu_item_id, stars, comment}] for dishes of this session. Returns ratings_payload()."""
    if not device_owns(session, device_token):
        raise PermissionError("device")
    if not isinstance(entries, (list, tuple)) or not entries:
        raise ValidationError("Choose at least one star rating.")
    if len(entries) > 50:
        raise ValidationError("Too many ratings at once.")
    key = f'ratings-posts:{session.id}'
    posts = cache.get(key, 0)
    if posts >= MAX_POSTS_PER_SESSION:
        raise ValidationError("Too many rating attempts. Please try again later.")
    cache.set(key, posts + 1, int(RATING_WINDOW.total_seconds()))

    allowed = {str(i.id) for i in ratable_items(session)}
    cleaned = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValidationError("Invalid rating.")
        menu_id = str(entry.get('menu_item_id') or '')
        if menu_id not in allowed:
            raise ValidationError("You can only rate dishes your table received.")
        cleaned.append((menu_id, _clean_stars(entry.get('stars')), _clean_comment(entry.get('comment'))))

    with transaction.atomic():
        for menu_id, stars, comment in cleaned:
            MenuRating.objects.update_or_create(
                table_session=session, menu_item_id=menu_id,
                defaults={'stars': stars, 'comment': comment, 'device_token': device_token[:128]},
            )
        for menu_id in sorted({c[0] for c in cleaned}):
            recompute_item_rating(menu_id)
    return ratings_payload(session)


def recent_reviews(menu_item, limit: int = 5) -> List[Dict]:
    return [{
        'stars': r.stars, 'comment': r.comment,
        'date': timezone.localtime(r.updated_at).strftime('%d/%m/%Y %H:%M'),
    } for r in MenuRating.objects.filter(menu_item=menu_item).order_by('-updated_at')[:limit]]


def latest_finished_session_for_device(table, device_token: str, now=None) -> Optional[object]:
    """The session this device just finished at this table (shown instead of a new opening request)."""
    from apps.tables.models import SessionStatus, TableSession
    if not device_token:
        return None
    since = (now or timezone.now()) - RATING_WINDOW
    for session in (TableSession.objects.filter(table=table, status=SessionStatus.CLOSED, closed_at__gte=since)
                    .order_by('-closed_at')[:5]):
        if device_owns(session, device_token):
            return session
    return None
