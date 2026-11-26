"""Admin navigation: five menus on desktop and phone, Katalog tabs, Audit as the third Reports tab."""
import re

import pytest
from django.test import Client

PORTAL = '/admin-portal/'
REPORTS = '/reports/'


def html_of(user, url):
    client = Client()
    client.force_login(user)
    res = client.get(url)
    assert res.status_code == 200
    return res.content.decode()


def block(html, open_pattern, close_tag):
    start = re.search(open_pattern, html)
    assert start, open_pattern
    return html[start.end():html.index(close_tag, start.end())]


def nav_items(html):
    """(label key, href or data-panel) of each entry in the top navigation."""
    nav = block(html, r'<nav class="app-nav"[^>]*>', '</nav>')
    return re.findall(r'class="app-nav-link[^"]*"[^>]*?(?:data-panel="([\w-]+)"|href="([^"]+)")[^>]*>.*?data-i18n="(\w+)"', nav)


def tabbar_items(html):
    bar = block(html, r'<nav class="app-tabbar"[^>]*>', '</nav>')
    return re.findall(r'class="app-tab[^"]*"[^>]*?(?:data-panel="([\w-]+)"|href="([^"]+)")[^>]*>.*?data-i18n="(\w+)"', bar)


@pytest.mark.django_db
@pytest.mark.parametrize('url', [PORTAL, REPORTS])
def test_admin_top_navigation_lists_the_menus_in_order(admin_user, restaurant, url):
    items = nav_items(html_of(admin_user, url))
    assert [label for _, _, label in items] == ['nav_overview', 'nav_catalog', 'tables_qr', 'staff_users', 'set_nav', 'reports']
    assert [panel or href for panel, href, _ in items] == [
        'overview-panel', 'menu-panel', 'tables-panel', 'users-panel', 'settings-panel', '/reports/']


@pytest.mark.django_db
@pytest.mark.parametrize('url', [PORTAL, REPORTS])
def test_admin_phone_tabbar_has_five_tabs_and_no_more_sheet(admin_user, restaurant, url):
    html = html_of(admin_user, url)
    items = tabbar_items(html)
    assert [label for _, _, label in items] == ['nav_overview', 'nav_catalog', 'nav_tables', 'staff_users', 'reports']
    assert 'moreSheet' not in html
    assert 'nav_more' not in html
    assert 'sheet-list' not in html


@pytest.mark.django_db
def test_audit_and_categories_are_no_longer_separate_menus(admin_user, restaurant):
    html = html_of(admin_user, PORTAL)
    assert 'data-panel="categories-panel"' in html      # only as a tab inside Katalog
    nav = block(html, r'<nav class="app-nav"[^>]*>', '</nav>')
    assert 'categories-panel' not in nav.replace('data-panel-group="menu-panel categories-panel"', '')
    assert 'audit-panel' not in nav
    assert 'id="audit-panel"' not in html


@pytest.mark.django_db
def test_katalog_menu_covers_both_panels_and_each_panel_carries_the_tabs(admin_user, restaurant):
    html = html_of(admin_user, PORTAL)
    assert html.count('data-panel-group="menu-panel categories-panel"') == 2   # top nav and phone tabbar
    for panel_id in ('menu-panel', 'categories-panel'):
        section = block(html, rf'<section class="app-panel" id="{panel_id}"[^>]*>', '</section>')
        assert section.count('role="tablist"') == 1
        assert 'data-panel="menu-panel"' in section and 'data-panel="categories-panel"' in section
        assert 'data-i18n="menu_stock"' in section and 'data-i18n="categories_nav"' in section


@pytest.mark.django_db
def test_old_audit_bookmark_is_redirected_to_the_reports_audit_tab(admin_user, restaurant):
    html = html_of(admin_user, PORTAL)
    assert 'data-panel-redirects="audit-panel=/reports/#audit"' in html


@pytest.mark.django_db
def test_audit_data_is_not_rendered_into_the_portal_page(admin_user, restaurant):
    from apps.audit.models import AuditEvent
    AuditEvent.objects.create(action='secret.action.name', entity_type='Thing', entity_id='1')
    assert 'secret.action.name' not in html_of(admin_user, PORTAL)


@pytest.mark.django_db
def test_reports_page_gives_admins_a_third_audit_tab(admin_user, restaurant):
    html = html_of(admin_user, REPORTS)
    tabs = re.findall(r'data-tab="(\w+)"', block(html, r'<div class="segmented mb-3" role="tablist"[^>]*>', '</div>'))
    assert tabs == ['summary', 'history', 'audit']
    assert 'id="rpt-pane-audit"' in html
    assert 'id="rpt-audit-controls"' in html
    assert '/static/js/reports-audit.js' in html
    assert 'id="rpt-downloads"' in html          # hidden by the page script while the Audit tab is open
    assert 'auditUrl: "/reports/audit/"' in html


@pytest.mark.django_db
def test_reports_page_hides_the_audit_tab_from_cashiers(cashier_user, restaurant):
    html = html_of(cashier_user, REPORTS)
    assert 'data-tab="audit"' not in html
    assert 'rpt-pane-audit' not in html
    assert 'reports-audit.js' not in html
    # History is a tab of Reports for cashiers too (no separate menu entry).
    assert 'data-nav="history"' not in html and 'data-panel="menu-panel"' not in html
    assert 'data-panel="service-panel"' in html


@pytest.mark.django_db
def test_cashier_and_kitchen_navigation_is_untouched(cashier_user, kitchen_user, restaurant):
    cashier = html_of(cashier_user, '/cashier/')
    assert 'data-panel="pos-panel"' in cashier and 'nav_catalog' not in cashier
    kitchen = html_of(kitchen_user, '/kitchen/')
    assert 'nav_catalog' not in kitchen and 'data-panel-group' not in kitchen


def test_service_worker_cache_was_bumped_for_the_new_assets():
    from pathlib import Path
    sw = (Path(__file__).resolve().parent.parent / 'static' / 'js' / 'sw.js').read_text(encoding='utf-8')
    assert "const VERSION = 'v11';" in sw
