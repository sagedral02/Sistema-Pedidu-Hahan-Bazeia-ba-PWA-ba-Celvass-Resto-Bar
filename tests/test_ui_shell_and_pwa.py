"""
Guards for the light UI rewrite: template hygiene, i18n coverage, role navigation and PWA behaviour.
"""
import re
from pathlib import Path

import pytest
from django.test import Client
from rest_framework.test import APIClient

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = sorted((ROOT / 'templates').rglob('*.html'))
APP_JS = sorted(p for p in (ROOT / 'static' / 'js').glob('*.js'))
LANGS = ('tet', 'pt', 'en', 'id')

# Spacing utilities with decimals (e.g. gap-1.5, px-2.5) do not exist in Bootstrap 5.3.
INVALID_BS_CLASS = re.compile(r'(?<![\w-])(?:gap|[mp][trblxyse]?)-\d\.\d(?![\w.])')
EMOJI = re.compile('[\U0001F300-\U0001FAFF\u2600-\u27BF\U0001F1E6-\U0001F1FF]')


def _read(path):
    return path.read_text(encoding='utf-8')


def test_templates_do_not_disable_zoom():
    offenders = [p.name for p in TEMPLATES if 'user-scalable=no' in _read(p) or 'maximum-scale=1' in _read(p)]
    assert offenders == []


@pytest.mark.parametrize('path', TEMPLATES + APP_JS, ids=lambda p: p.name)
def test_no_nonexistent_bootstrap_spacing_classes(path):
    assert INVALID_BS_CLASS.findall(_read(path)) == []


@pytest.mark.parametrize('path', TEMPLATES, ids=lambda p: p.name)
def test_templates_use_self_hosted_assets(path):
    text = _read(path)
    for host in ('cdn.jsdelivr.net', 'cdnjs.cloudflare.com', 'fonts.googleapis.com', 'api.qrserver.com'):
        assert host not in text, f'{path.name} loads {host}'


@pytest.mark.parametrize('path', TEMPLATES, ids=lambda p: p.name)
def test_templates_have_no_emoji_labels(path):
    assert EMOJI.findall(_read(path)) == []


@pytest.mark.parametrize('path', APP_JS, ids=lambda p: p.name)
def test_scripts_do_not_use_native_confirm_or_alert(path):
    text = _read(path)
    assert not re.search(r'(?<![\w.])(confirm|alert)\(', text), path.name


def _i18n_keys():
    text = _read(ROOT / 'static' / 'js' / 'i18n.js')
    keys = {lang: set() for lang in LANGS}
    current = None
    for line in text.splitlines():
        m = re.match(r'^  (tet|pt|en|id): \{\s*$', line)
        if m:
            current = m.group(1)
            continue
        if current and re.match(r'^  \},?\s*$', line):
            current = None
            continue
        if current:
            k = re.match(r'^\s+([A-Za-z_][\w]*):', line)
            if k:
                keys[current].add(k.group(1))
    return keys


def _used_keys():
    used = set()
    attr = re.compile(r'data-i18n(?:-placeholder|-title|-label)?="([\w]+)"')
    for p in TEMPLATES:
        used.update(attr.findall(_read(p)))
    call = re.compile(r"(?<![\w.])t\('([A-Za-z_]\w*)'\)")
    for p in APP_JS:
        if p.name == 'i18n.js':
            continue
        used.update(call.findall(_read(p)))
        used.update(attr.findall(_read(p)))
    return used


def test_every_used_i18n_key_exists_in_all_languages():
    keys = _i18n_keys()
    used = _used_keys()
    missing = {lang: sorted(used - keys[lang]) for lang in LANGS}
    assert all(not v for v in missing.values()), missing


@pytest.mark.django_db
def test_cashier_portal_renders_cashier_navigation(cashier_user):
    client = Client()
    client.force_login(cashier_user)
    html = client.get('/cashier/').content.decode()
    assert 'data-role="cashier"' in html
    assert 'data-panel="pos-panel"' in html
    assert 'class="app-tabbar"' in html
    assert 'data-panel="menu-panel"' not in html


@pytest.mark.django_db
def test_kitchen_portal_renders_kitchen_navigation(kitchen_user):
    client = Client()
    client.force_login(kitchen_user)
    html = client.get('/kitchen/').content.decode()
    assert 'data-role="kitchen"' in html
    assert 'data-view="PREPARING"' in html
    assert 'data-panel="pos-panel"' not in html


@pytest.mark.django_db
def test_admin_portal_renders_admin_navigation_and_staff_list(admin_user, cashier_user, restaurant):
    client = Client()
    client.force_login(admin_user)
    html = client.get('/admin-portal/').content.decode()
    assert 'data-role="admin"' in html
    for panel in ('overview-panel', 'menu-panel', 'categories-panel', 'tables-panel', 'users-panel', 'audit-panel'):
        assert f'id="{panel}"' in html
    assert '@cashier_test' in html


@pytest.mark.django_db
def test_kitchen_user_cannot_open_cashier_portal(kitchen_user):
    client = Client()
    client.force_login(kitchen_user)
    assert client.get('/cashier/').status_code == 403


@pytest.mark.django_db
def test_customer_page_has_no_staff_tabbar(active_session, table_one):
    html = Client().get(f'/t/{table_one.qr_token}/').content.decode()
    assert 'data-role="customer"' in html
    assert 'class="app-tabbar"' not in html
    assert 'id="cartOffcanvas"' in html


@pytest.mark.django_db
def test_table_page_issues_httponly_device_cookie(active_session, table_one):
    res = Client().get(f'/t/{table_one.qr_token}/')
    cookie = res.cookies['celvass_device_id']
    assert cookie.value
    assert cookie['httponly']
    active_session.refresh_from_db()
    assert active_session.primary_device_token == cookie.value


@pytest.mark.django_db
def test_qr_sheet_generates_codes_locally(admin_user, table_one):
    client = Client()
    client.force_login(admin_user)
    html = client.get('/admin-portal/qr-sheet/').content.decode()
    assert 'api.qrserver.com' not in html
    assert f'/t/{table_one.qr_token}/' in html


@pytest.mark.django_db
def test_manifest_is_installable_in_any_orientation():
    res = Client().get('/manifest.json')
    assert res.status_code == 200
    assert res['Content-Type'].startswith('application/manifest+json')
    data = res.json()
    assert data['orientation'] == 'any'
    assert data['display'] == 'standalone'
    assert all(icon['purpose'] != 'any maskable' for icon in data['icons'])
    assert {'192x192', '512x512'} <= {icon['sizes'] for icon in data['icons']}


@pytest.mark.django_db
def test_service_worker_is_served_and_skips_staff_pages():
    res = Client().get('/sw.js')
    assert res.status_code == 200
    assert res['Service-Worker-Allowed'] == '/'
    body = res.content.decode()
    for prefix in ("'/cashier/'", "'/kitchen/'", "'/admin-portal/'", "'/api/'", "'/login/'"):
        assert prefix in body
    assert 'NEVER_CACHE_PREFIXES' in body


@pytest.mark.django_db
def test_logout_clears_browser_cache(cashier_user):
    client = Client()
    client.force_login(cashier_user)
    res = client.get('/logout/')
    assert res.status_code == 302
    assert res['Clear-Site-Data'] == '"cache"'


@pytest.mark.django_db
def test_admin_cannot_create_staff_with_weak_password(admin_user):
    client = APIClient()
    client.force_authenticate(user=admin_user)
    res = client.post('/api/v1/admin/users/create/', {'username': 'kaixa9', 'password': '123', 'role': 'CASHIER'}, format='json')
    assert res.status_code == 400
    assert res.json()['error']['code'] == 'WEAK_PASSWORD'

    ok = client.post('/api/v1/admin/users/create/', {'username': 'kaixa9', 'password': 'Tasi-Dili-2026', 'role': 'CASHIER'}, format='json')
    assert ok.status_code == 201


@pytest.mark.parametrize('path', TEMPLATES, ids=lambda p: p.name)
def test_django_tags_stay_inside_quoted_attribute_values(path):
    """Django tags directly inside an HTML tag, or unquoted inside an on* handler, show up as editor errors."""
    problems = []
    for i, line in enumerate(_read(path).splitlines(), 1):
        for tag in re.findall(r'<[a-zA-Z][^<>]*>', line):
            bare = re.sub(r'"[^"]*"', '""', tag)
            if '{%' in bare or '{{' in bare:
                problems.append(f'{i}: {tag[:100]}')
        for val in re.findall(r'\son\w+="([^"]*)"', line):
            if '{%' in val or '{{' in re.sub(r"'[^']*'", "''", val):
                problems.append(f'{i}: {val[:100]}')
    assert problems == []
