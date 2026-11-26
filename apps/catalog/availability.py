"""Effective availability of a menu item for the current business day.

A menu item can be ordered when it is permanently AVAILABLE, was not closed for today by staff, and its daily
quota is not used up. "Sold" is counted from the order lines of the business day (never from a separate counter),
so a cancelled, rejected or out-of-stock line frees its portions again.
"""
from dataclasses import dataclass, asdict
from datetime import date as date_cls, datetime, time, timedelta
from typing import Dict, Iterable, Optional

from django.conf import settings
from django.db.models import Sum
from django.utils import timezone

from .models import DailyMenuState, ItemAvailability, MenuItem

# Why a menu item cannot be ordered right now.
AVAILABLE = 'AVAILABLE'
INACTIVE = 'INACTIVE'
SOLD_OUT = 'SOLD_OUT'            # switched off permanently in the item form
CLOSED_TODAY = 'CLOSED_TODAY'    # closed for the day by staff (kitchen ran out)
QUOTA_REACHED = 'QUOTA_REACHED'  # the daily quota is used up


@dataclass(frozen=True)
class MenuAvailability:
    state: str
    quota: Optional[int]
    sold: int
    remaining: Optional[int]   # None = no quota
    closed_reason: str = ''

    @property
    def orderable(self) -> bool:
        return self.state == AVAILABLE

    def as_dict(self) -> dict:
        data = asdict(self)
        data['orderable'] = self.orderable
        return data


def _start_hour() -> int:
    return int(getattr(settings, 'BUSINESS_DAY_START_HOUR', 0) or 0)


def business_date(now: Optional[datetime] = None) -> date_cls:
    """The business day a moment belongs to (restaurant time zone, shifted by BUSINESS_DAY_START_HOUR)."""
    moment = timezone.localtime(now or timezone.now())
    return (moment - timedelta(hours=_start_hour())).date()


def business_day_bounds(day: Optional[date_cls] = None):
    """[start, end) of a business day as aware datetimes."""
    day = day or business_date()
    tz = timezone.get_current_timezone()
    start = timezone.make_aware(datetime.combine(day, time(hour=_start_hour())), tz)
    return start, start + timedelta(days=1)


def _sold_by_item(item_ids: Iterable, day: date_cls) -> Dict[str, int]:
    from apps.ordering.models import ItemStatus, OrderItem, OrderStatus
    start, end = business_day_bounds(day)
    rows = (
        OrderItem.objects.filter(menu_item_id__in=list(item_ids), order__submitted_at__gte=start, order__submitted_at__lt=end)
        .exclude(status=ItemStatus.UNAVAILABLE)
        .exclude(order__status__in=[OrderStatus.REJECTED, OrderStatus.CANCELLED, OrderStatus.DRAFT])
        .values('menu_item_id').annotate(total=Sum('quantity'))
    )
    return {str(r['menu_item_id']): int(r['total'] or 0) for r in rows}


def _closed_ids(item_ids: Iterable, day: date_cls) -> Dict[str, str]:
    rows = DailyMenuState.objects.filter(menu_item_id__in=list(item_ids), business_date=day, is_closed=True)
    return {str(r.menu_item_id): r.reason for r in rows}


def _build(item: MenuItem, sold: int, closed: Dict[str, str]) -> MenuAvailability:
    quota = item.daily_quota
    remaining = None if quota is None else max(quota - sold, 0)
    if item.availability == ItemAvailability.INACTIVE:
        state = INACTIVE
    elif item.availability == ItemAvailability.SOLD_OUT:
        state = SOLD_OUT
    elif str(item.id) in closed:
        state = CLOSED_TODAY
    elif remaining is not None and remaining <= 0:
        state = QUOTA_REACHED
    else:
        state = AVAILABLE
    return MenuAvailability(state=state, quota=quota, sold=sold, remaining=remaining,
                            closed_reason=closed.get(str(item.id), ''))


def menu_availability(item: MenuItem, day: Optional[date_cls] = None) -> MenuAvailability:
    day = day or business_date()
    return _build(item, _sold_by_item([item.id], day).get(str(item.id), 0), _closed_ids([item.id], day))


def availability_map(items: Iterable[MenuItem], day: Optional[date_cls] = None) -> Dict[str, MenuAvailability]:
    """Availability for many items in two queries (used by menu lists)."""
    items = list(items)
    day = day or business_date()
    ids = [i.id for i in items]
    sold, closed = _sold_by_item(ids, day), _closed_ids(ids, day)
    return {str(i.id): _build(i, sold.get(str(i.id), 0), closed) for i in items}
