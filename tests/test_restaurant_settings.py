"""Restaurant settings: validation, Google Maps parsing, opening hours, admin API."""
from datetime import datetime, time
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from django.core.exceptions import ValidationError
from rest_framework.test import APIClient

from apps.audit.models import AuditEvent
from apps.restaurants.models import OpeningHours
from apps.restaurants.services import (
    clean_whatsapp, is_open_now, next_opening, parse_maps_url, update_restaurant_settings,
)

DILI = ZoneInfo('Asia/Dili')
URL = '/api/v1/admin/restaurant/'


def at(y, m, d, h, mi=0):
    return datetime(y, m, d, h, mi, tzinfo=DILI)


def api(user):
    c = APIClient()
    if user is not None:
        c.force_authenticate(user=user)
    return c


# ----------------------------------------------------------------------------- parsing

@pytest.mark.parametrize('url,expected', [
    ('https://www.google.com/maps/place/Celvass/@-8.5569,125.5603,17z', ('-8.556900', '125.560300')),
    ('https://www.google.com/maps/place/X/data=!3d-8.5512!4d125.5788', ('-8.551200', '125.578800')),
    ('https://maps.google.com/?q=-8.55,125.57', ('-8.550000', '125.570000')),
    ('https://www.google.com/maps/search/?api=1&query=-8.5,125.6', ('-8.500000', '125.600000')),
    ('https://maps.app.goo.gl/AbCdEf123', None),
    ('https://www.google.com/maps/@95.0,10.0,10z', None),
])
def test_parse_maps_url(url, expected):
    got = parse_maps_url(url)
    assert (tuple(str(v) for v in got) if got else None) == expected


def test_whatsapp_validation():
    assert clean_whatsapp('+670 7723 4567') == '+67077234567'
    assert clean_whatsapp('') == ''
    for bad in ('123', 'abc', '+670-77-23-45-67-89-01-23'):
        with pytest.raises(ValidationError):
            clean_whatsapp(bad)


# ----------------------------------------------------------------------------- hours

def set_hours(restaurant, rows):
    OpeningHours.objects.filter(restaurant=restaurant).delete()
    for day, opens, closes, closed in rows:
        OpeningHours.objects.create(restaurant=restaurant, weekday=day, opens_at=opens, closes_at=closes, is_closed=closed)


@pytest.mark.django_db
def test_no_hours_means_always_open(restaurant):
    assert is_open_now(restaurant, at(2026, 11, 23, 3)) is True
    assert next_opening(restaurant) is None


@pytest.mark.django_db
def test_regular_day(restaurant):
    # 2026-11-23 is a Monday
    set_hours(restaurant, [(0, time(10), time(22), False)])
    assert is_open_now(restaurant, at(2026, 11, 23, 9, 59)) is False
    assert is_open_now(restaurant, at(2026, 11, 23, 10)) is True
    assert is_open_now(restaurant, at(2026, 11, 23, 22)) is False
    assert next_opening(restaurant, at(2026, 11, 23, 23)) == at(2026, 11, 30, 10)


@pytest.mark.django_db
def test_after_midnight_and_closed_day(restaurant):
    set_hours(restaurant, [(4, time(18), time(2), False), (5, None, None, True)])  # Fri 18:00-02:00, Sat closed
    assert is_open_now(restaurant, at(2026, 11, 27, 23)) is True      # Friday night
    assert is_open_now(restaurant, at(2026, 11, 28, 1, 30)) is True   # Saturday early morning (Friday's shift)
    assert is_open_now(restaurant, at(2026, 11, 28, 2)) is False
    assert is_open_now(restaurant, at(2026, 11, 28, 19)) is False     # Saturday closed
    assert next_opening(restaurant, at(2026, 11, 28, 19)) == at(2026, 12, 4, 18)


@pytest.mark.django_db
def test_hours_use_the_restaurant_time_zone(restaurant):
    set_hours(restaurant, [(0, time(10), time(22), False)])
    utc_moment = datetime(2026, 11, 23, 1, 30, tzinfo=ZoneInfo('UTC'))  # 10:30 in Dili
    assert is_open_now(restaurant, utc_moment) is True


# ----------------------------------------------------------------------------- service

@pytest.mark.django_db
def test_update_settings_parses_maps_and_audits(restaurant, admin_user):
    r = update_restaurant_settings(restaurant=restaurant, actor_user=admin_user, data={
        'name': 'Celvass', 'whatsapp': '+670 7712 3456', 'email': 'info@celvass.tl',
        'maps_url': 'https://www.google.com/maps/place/X/@-8.5569,125.5603,17z', 'tax_percentage': '10',
        'opening_hours': [{'weekday': d, 'opens_at': '10:00', 'closes_at': '22:00'} for d in range(7)],
    })
    assert r.latitude == Decimal('-8.556900') and r.whatsapp_link == 'https://wa.me/67077123456'
    assert 'output=embed' in r.maps_embed_url and r.tax_percentage == Decimal('10.00')
    assert OpeningHours.objects.filter(restaurant=r).count() == 7
    assert AuditEvent.objects.filter(action='RESTAURANT_SETTINGS_UPDATE').exists()


@pytest.mark.django_db
@pytest.mark.parametrize('data', [
    {'name': ''}, {'email': 'nope'}, {'website': 'javascript:alert(1)'}, {'tax_percentage': '150'},
    {'coordinates': 'abc'}, {'opening_hours': [{'weekday': 9, 'opens_at': '10:00', 'closes_at': '11:00'}]},
    {'opening_hours': [{'weekday': 1, 'opens_at': '25:00', 'closes_at': '11:00'}]},
    {'opening_hours': [{'weekday': 1, 'opens_at': '10:00'}]},
])
def test_invalid_settings(restaurant, data):
    with pytest.raises(ValidationError):
        update_restaurant_settings(restaurant=restaurant, data=data)


# ----------------------------------------------------------------------------- API

@pytest.mark.django_db
def test_api_get_and_patch(restaurant, admin_user):
    c = api(admin_user)
    body = c.get(URL).json()['data']
    assert body['name'] == restaurant.name and len(body['opening_hours']) == 7
    assert body['opening_status']['is_open'] is True
    res = c.patch(URL, {'address': 'Rua de Colmera, Dili', 'coordinates': '-8.55, 125.57'}, format='json')
    assert res.status_code == 200, res.content
    assert res.json()['data']['coordinates'] == '-8.550000,125.570000'
    res = c.patch(URL, {'email': 'bad'}, format='json')
    assert res.status_code == 400


@pytest.mark.django_db
def test_api_is_admin_only(restaurant, cashier_user, kitchen_user):
    for user in (cashier_user, kitchen_user, None):
        assert api(user).get(URL).status_code in (401, 403)
        assert api(user).patch(URL, {'name': 'x'}, format='json').status_code in (401, 403)
