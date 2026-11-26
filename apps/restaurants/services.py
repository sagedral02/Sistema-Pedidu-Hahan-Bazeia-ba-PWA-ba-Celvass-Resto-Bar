"""Restaurant settings (identity, contact, location, opening hours, tax) and opening-hours checks."""
import re
from datetime import datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from typing import Optional
from urllib.parse import parse_qs, unquote, urlparse
from zoneinfo import ZoneInfo

from django.core.exceptions import ValidationError
from django.core.validators import EmailValidator, URLValidator
from django.db import transaction
from django.utils import timezone

from .models import OpeningHours, Restaurant

WEEKDAYS = range(7)  # 0 = Monday
TEXT_LIMITS = {
    'name': 150, 'short_name': 30, 'legal_name': 200, 'description': 300, 'phone': 50, 'address': 500,
    'receipt_note': 300,
}
URL_FIELDS = ('website', 'instagram', 'facebook', 'maps_url')
IDLE_ALERT_MAX = 600
_COORD = r'(-?\d{1,2}(?:\.\d+)?)\s*,\s*(-?\d{1,3}(?:\.\d+)?)'


def get_restaurant() -> Restaurant:
    restaurant = Restaurant.objects.filter(is_active=True).first()
    if restaurant is None:
        restaurant = Restaurant.objects.create()
    return restaurant


# --------------------------------------------------------------------------- parsing helpers

def _valid_coords(lat, lng):
    try:
        lat, lng = Decimal(str(lat)), Decimal(str(lng))
    except (InvalidOperation, ValueError):
        return None
    if not (-90 <= lat <= 90 and -180 <= lng <= 180):
        return None
    return lat.quantize(Decimal('0.000001')), lng.quantize(Decimal('0.000001'))


def parse_coordinates(text: str):
    """'lat,lng' -> (Decimal, Decimal) or None."""
    match = re.fullmatch(r'\s*' + _COORD + r'\s*', text or '')
    return _valid_coords(*match.groups()) if match else None


def parse_maps_url(url: str):
    """Coordinates from a Google Maps link: '@lat,lng', '!3d<lat>!4d<lng>', or q= / ll= / query= / center=.
    Short links (maps.app.goo.gl) carry no coordinates and return None."""
    if not url:
        return None
    text = unquote(url)
    match = re.search(r'!3d(-?\d+(?:\.\d+)?)!4d(-?\d+(?:\.\d+)?)', text)
    if match:
        return _valid_coords(*match.groups())
    match = re.search(r'@' + _COORD, text)
    if match:
        return _valid_coords(*match.groups())
    query = parse_qs(urlparse(url).query)
    for key in ('q', 'll', 'query', 'center', 'destination'):
        for value in query.get(key, []):
            found = parse_coordinates(value)
            if found:
                return found
    return None


def clean_whatsapp(raw: str) -> str:
    raw = (raw or '').strip()
    if not raw:
        return ''
    compact = re.sub(r'[\s\-().]', '', raw)
    if not re.fullmatch(r'\+?\d{8,15}', compact):
        raise ValidationError("WhatsApp number must have 8 to 15 digits (country code included).")
    return compact


def _parse_time(value, field):
    if value in (None, ''):
        return None
    if isinstance(value, time):
        return value
    match = re.fullmatch(r'\s*(\d{1,2}):(\d{2})(?::\d{2})?\s*', str(value))
    if not match or int(match.group(1)) > 23 or int(match.group(2)) > 59:
        raise ValidationError(f"{field}: use HH:MM.")
    return time(int(match.group(1)), int(match.group(2)))


def clean_hours(rows):
    """[{weekday, is_closed, opens_at, closes_at}] -> list of dicts for all 7 days. Raises ValidationError."""
    if not isinstance(rows, (list, tuple)):
        raise ValidationError("Opening hours must be a list.")
    by_day = {}
    for row in rows:
        try:
            day = int(row.get('weekday'))
        except (TypeError, ValueError, AttributeError):
            raise ValidationError("Opening hours: invalid weekday.")
        if day not in WEEKDAYS or day in by_day:
            raise ValidationError("Opening hours: invalid or repeated weekday.")
        closed = bool(row.get('is_closed'))
        opens = _parse_time(row.get('opens_at'), 'opens_at')
        closes = _parse_time(row.get('closes_at'), 'closes_at')
        if not closed and (opens is None or closes is None):
            raise ValidationError("Opening hours: an open day needs an opening and a closing time.")
        if not closed and opens == closes:
            raise ValidationError("Opening hours: opening and closing time cannot be the same.")
        by_day[day] = {'weekday': day, 'is_closed': closed, 'opens_at': None if closed else opens,
                       'closes_at': None if closed else closes}
    return [by_day[d] for d in sorted(by_day)]


# --------------------------------------------------------------------------- update

def update_restaurant_settings(*, restaurant: Restaurant, data: dict, actor_user=None, request_id: str = "") -> Restaurant:
    from apps.audit.services import log_audit_event

    with transaction.atomic():
        locked = Restaurant.objects.select_for_update().get(id=restaurant.id)
        before = settings_dict(locked)
        changed = []

        for field, limit in TEXT_LIMITS.items():
            if field in data:
                value = str(data.get(field) or '').strip()
                if field == 'name' and not value:
                    raise ValidationError("Restaurant name is required.")
                if len(value) > limit:
                    raise ValidationError(f"{field}: at most {limit} characters.")
                setattr(locked, field, value)
                changed.append(field)

        if 'whatsapp' in data:
            locked.whatsapp = clean_whatsapp(data.get('whatsapp'))
            changed.append('whatsapp')
        if 'email' in data:
            email = str(data.get('email') or '').strip()
            if email:
                EmailValidator(message="Invalid e-mail address.")(email)
            locked.email = email
            changed.append('email')
        for field in URL_FIELDS:
            if field in data:
                url = str(data.get(field) or '').strip()
                if url:
                    URLValidator(schemes=['http', 'https'], message=f"{field}: invalid link.")(url)
                setattr(locked, field, url)
                changed.append(field)

        if 'maps_url' in data or 'coordinates' in data:
            coords = None
            raw = str(data.get('coordinates') or '').strip()
            if raw:
                coords = parse_coordinates(raw)
                if coords is None:
                    raise ValidationError("Coordinates must look like -8.556,125.578.")
            elif locked.maps_url:
                coords = parse_maps_url(locked.maps_url)
            locked.latitude, locked.longitude = coords if coords else (None, None)
            changed += ['latitude', 'longitude']

        if 'tax_percentage' in data:
            try:
                tax = Decimal(str(data.get('tax_percentage') or '0')).quantize(Decimal('0.01'))
            except (InvalidOperation, ValueError):
                raise ValidationError("Tax must be a number.")
            if tax < 0 or tax > 100:
                raise ValidationError("Tax must be between 0 and 100.")
            locked.tax_percentage = tax
            changed.append('tax_percentage')

        if 'idle_alert_minutes' in data:
            raw = data.get('idle_alert_minutes')
            try:
                minutes = int(str(raw if raw not in (None, '') else 0).strip())
            except (TypeError, ValueError):
                raise ValidationError("Idle alert must be a whole number of minutes.")
            if isinstance(raw, bool) or minutes < 0 or minutes > IDLE_ALERT_MAX:
                raise ValidationError(f"Idle alert must be between 0 and {IDLE_ALERT_MAX} minutes.")
            locked.idle_alert_minutes = minutes
            changed.append('idle_alert_minutes')

        hours = clean_hours(data['opening_hours']) if 'opening_hours' in data else None

        if changed:
            locked.save()
        if hours is not None:
            OpeningHours.objects.filter(restaurant=locked).delete()
            OpeningHours.objects.bulk_create([OpeningHours(restaurant=locked, **h) for h in hours])

        log_audit_event(
            actor_user=actor_user, actor_role=getattr(actor_user, 'role', 'ADMIN') if actor_user else 'ADMIN',
            action='RESTAURANT_SETTINGS_UPDATE', entity_type='Restaurant', entity_id=str(locked.id),
            before_data=before, after_data=settings_dict(locked), request_id=request_id)
        return locked


# --------------------------------------------------------------------------- opening hours

def _zone(restaurant):
    try:
        return ZoneInfo(restaurant.timezone or 'Asia/Dili')
    except Exception:
        return ZoneInfo('Asia/Dili')


def _hours_by_day(restaurant):
    return {h.weekday: h for h in restaurant.opening_hours.all()}


def _windows(hours, day_date):
    """Open intervals (start, end) as naive local datetimes that start on `day_date`."""
    row = hours.get(day_date.weekday())
    if row is None or row.is_closed or row.opens_at is None or row.closes_at is None:
        return []
    start = datetime.combine(day_date, row.opens_at)
    end = datetime.combine(day_date, row.closes_at)
    if row.closes_at <= row.opens_at:
        end += timedelta(days=1)  # closes after midnight
    return [(start, end)]


def is_open_now(restaurant: Restaurant, now: Optional[datetime] = None) -> bool:
    """True when ordering is allowed. A restaurant without any opening hours set is always open."""
    hours = _hours_by_day(restaurant)
    if not hours:
        return True
    local = (now or timezone.now()).astimezone(_zone(restaurant)).replace(tzinfo=None)
    for day in (local.date() - timedelta(days=1), local.date()):
        for start, end in _windows(hours, day):
            if start <= local < end:
                return True
    return False


def next_opening(restaurant: Restaurant, now: Optional[datetime] = None) -> Optional[datetime]:
    """Next opening time (aware, restaurant time zone) or None when nothing is scheduled."""
    hours = _hours_by_day(restaurant)
    if not hours:
        return None
    zone = _zone(restaurant)
    local = (now or timezone.now()).astimezone(zone).replace(tzinfo=None)
    for offset in range(0, 8):
        for start, _end in _windows(hours, local.date() + timedelta(days=offset)):
            if start > local:
                return start.replace(tzinfo=zone)
    return None


def opening_status(restaurant: Restaurant, now: Optional[datetime] = None) -> dict:
    nxt = next_opening(restaurant, now)
    return {
        'is_open': is_open_now(restaurant, now),
        'next_opening': nxt.isoformat() if nxt else None,
        'next_opening_label': nxt.strftime('%d/%m %H:%M') if nxt else '',
        'next_opening_weekday': nxt.weekday() if nxt else None,
    }


def hours_list(restaurant: Restaurant):
    hours = _hours_by_day(restaurant)
    out = []
    for day in WEEKDAYS:
        h = hours.get(day)
        out.append({
            'weekday': day,
            'configured': h is not None,
            'is_closed': bool(h and h.is_closed),
            'opens_at': h.opens_at.strftime('%H:%M') if h and h.opens_at else '',
            'closes_at': h.closes_at.strftime('%H:%M') if h and h.closes_at else '',
        })
    return out


def settings_dict(restaurant: Restaurant) -> dict:
    return {
        'id': str(restaurant.id), 'name': restaurant.name, 'short_name': restaurant.short_name,
        'legal_name': restaurant.legal_name or '', 'description': restaurant.description,
        'phone': restaurant.phone or '', 'whatsapp': restaurant.whatsapp, 'email': restaurant.email,
        'website': restaurant.website, 'instagram': restaurant.instagram, 'facebook': restaurant.facebook,
        'address': restaurant.address or '', 'maps_url': restaurant.maps_url,
        'coordinates': restaurant.coordinates, 'maps_link': restaurant.maps_link,
        'maps_embed_url': restaurant.maps_embed_url, 'whatsapp_link': restaurant.whatsapp_link,
        'tel_link': restaurant.tel_link, 'receipt_note': restaurant.receipt_note,
        'tax_percentage': str(restaurant.tax_percentage), 'timezone': restaurant.timezone,
        'currency': restaurant.currency, 'has_logo': restaurant.has_logo, 'logo_version': restaurant.logo_version,
        'idle_alert_minutes': restaurant.idle_alert_minutes,
    }
