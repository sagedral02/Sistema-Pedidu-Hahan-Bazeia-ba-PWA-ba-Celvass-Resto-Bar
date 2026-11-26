"""App icons: default fork-and-knife files, uploaded logo as PWA icon, manifest."""
import io
from pathlib import Path

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from PIL import Image
from rest_framework.test import APIClient

from apps.restaurants.branding import ICON_SPECS, clear_logo, set_logo
from apps.restaurants.models import RestaurantIcon

ROOT = Path(__file__).resolve().parent.parent


def png_bytes(size=(300, 200), color=(10, 120, 200, 255)):
    out = io.BytesIO()
    Image.new('RGBA', size, color).save(out, format='PNG')
    return out.getvalue()


def test_default_icon_files_exist_with_the_right_size():
    icons = ROOT / 'static' / 'icons'
    for name, (size, _style) in ICON_SPECS.items():
        with Image.open(icons / name) as img:
            assert img.size == (size, size), name
    with Image.open(icons / 'favicon.ico') as ico:
        assert ico.format == 'ICO'
    assert (icons / 'icon.svg').exists()


@pytest.mark.django_db
def test_manifest_has_any_and_maskable_icons(restaurant):
    m = Client().get('/manifest.json').json()
    purposes = {(i['sizes'], i['purpose']) for i in m['icons']}
    assert purposes == {('192x192', 'any'), ('512x512', 'any'), ('512x512', 'maskable')}
    assert m['name'] == restaurant.name
    assert all('/static/icons/' in i['src'] for i in m['icons'])


@pytest.mark.django_db
def test_uploaded_logo_becomes_the_app_icon(restaurant, admin_user):
    r = set_logo(restaurant=restaurant, upload=SimpleUploadedFile('logo.png', png_bytes()), actor_user=admin_user)
    assert r.logo_version
    assert RestaurantIcon.objects.filter(restaurant=r).count() == len(ICON_SPECS) + 1
    m = Client().get('/manifest.json').json()
    assert all(i['src'].startswith('/pwa/icons/') and f'v={r.logo_version}' in i['src'] for i in m['icons'])
    res = Client().get(f'/pwa/icons/icon-512.png?v={r.logo_version}')
    assert res.status_code == 200 and res['Content-Type'] == 'image/png'
    assert 'immutable' in res['Cache-Control']
    with Image.open(io.BytesIO(res.content)) as img:
        assert img.size == (512, 512)
    assert Client().get('/pwa/icons/favicon.ico').status_code == 200
    html = Client().get('/').content.decode()
    assert f'/pwa/icons/favicon-32.png?v={r.logo_version}' in html


@pytest.mark.django_db
def test_new_logo_changes_the_version_and_clearing_restores_default(restaurant):
    first = set_logo(restaurant=restaurant, upload=SimpleUploadedFile('a.png', png_bytes())).logo_version
    second = set_logo(restaurant=restaurant, upload=SimpleUploadedFile('b.png', png_bytes(color=(200, 0, 0, 255)))).logo_version
    assert first != second
    clear_logo(restaurant=restaurant)
    restaurant.refresh_from_db()
    assert restaurant.logo_version == '' and not RestaurantIcon.objects.exists()
    res = Client().get('/pwa/icons/icon-192.png')
    assert res.status_code == 302 and '/static/icons/icon-192.png' in res['Location']


@pytest.mark.django_db
def test_logo_validation_and_api(restaurant, admin_user, cashier_user):
    c = APIClient()
    c.force_authenticate(user=admin_user)
    bad = c.post('/api/v1/admin/restaurant/logo/', {'logo': SimpleUploadedFile('x.png', b'not an image')}, format='multipart')
    assert bad.status_code == 400
    tiny = c.post('/api/v1/admin/restaurant/logo/', {'logo': SimpleUploadedFile('t.png', png_bytes((20, 20)))}, format='multipart')
    assert tiny.status_code == 400
    ok = c.post('/api/v1/admin/restaurant/logo/', {'logo': SimpleUploadedFile('l.png', png_bytes())}, format='multipart')
    assert ok.status_code == 200 and ok.json()['data']['has_logo'] is True
    assert c.delete('/api/v1/admin/restaurant/logo/').json()['data']['has_logo'] is False
    c.force_authenticate(user=cashier_user)
    assert c.post('/api/v1/admin/restaurant/logo/', {'logo': SimpleUploadedFile('l.png', png_bytes())}, format='multipart').status_code == 403


@pytest.mark.django_db
def test_unknown_icon_name_is_404():
    assert Client().get('/pwa/icons/../settings.py').status_code == 404
    assert Client().get('/pwa/icons/evil.png').status_code == 404


def test_templates_use_the_brand_icons():
    base = (ROOT / 'templates' / 'base' / 'base.html').read_text(encoding='utf-8')
    for key in ('brand.favicon_ico', 'brand.favicon_32_png', 'brand.apple_touch_icon_png', 'brand.icon_192_png'):
        assert key in base, key
    sw = (ROOT / 'static' / 'js' / 'sw.js').read_text(encoding='utf-8')
    assert "const VERSION = 'v11';" in sw
