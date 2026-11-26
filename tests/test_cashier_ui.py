"""Cashier portal shell: five menus, new panels, scripts and old bookmarks (rendered with the Django client)."""
import re
from pathlib import Path

import pytest
from django.test import Client

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def html(cashier_user, restaurant):
    c = Client()
    c.force_login(cashier_user)
    return c.get('/cashier/').content.decode()


def _block(html, start, end):
    return html.split(start, 1)[1].split(end, 1)[0]


@pytest.mark.django_db
def test_top_nav_and_tab_bar_have_exactly_five_menus(html):
    nav = _block(html, '<nav class="app-nav"', '</nav>')
    tabbar = _block(html, '<nav class="app-tabbar"', '</nav>')
    for part in (nav, tabbar):
        targets = re.findall(r'data-panel="([\w-]+)"', part) + re.findall(r'data-nav="(\w+)"', part)
        assert targets == ['pos-panel', 'service-panel', 'stock-panel', 'shift-panel', 'reports'], targets
        assert 'More' not in part and 'more-sheet' not in part
    assert 'data-nav="history"' not in html, 'History is a tab inside Reports now'


@pytest.mark.django_db
def test_new_panels_and_scripts_are_present(html):
    for panel in ('pos-panel', 'service-panel', 'stock-panel', 'shift-panel'):
        assert f'id="{panel}"' in html
    for old in ('id="bill-alerts-panel"', 'id="shift-close-panel"'):
        assert old not in html
    assert 'js/pos-order.js' in html and 'id="posOrderModal"' in html
    assert 'data-view="takeaway"' in html and 'data-view="kitchen"' in html


@pytest.mark.django_db
def test_old_bookmarks_are_redirected(html):
    redirects = re.search(r'data-panel-redirects="([^"]*)"', html).group(1).split()
    assert 'bill-alerts-panel=#service-panel/bills' in redirects
    assert 'shift-close-panel=#shift-panel' in redirects
    assert 'orders-history-panel=/reports/#history' in redirects


@pytest.mark.django_db
def test_cashier_reports_page_has_no_audit_tab(cashier_user, restaurant):
    c = Client()
    c.force_login(cashier_user)
    page = c.get('/reports/').content.decode()
    assert 'id="rpt-tab-summary"' in page and 'id="rpt-tab-history"' in page
    assert 'rpt-tab-audit' not in page


def test_scripts_use_the_new_endpoints_and_cash_only():
    cashier = (ROOT / 'static' / 'js' / 'cashier.js').read_text(encoding='utf-8')
    pos = (ROOT / 'static' / 'js' / 'pos-order.js').read_text(encoding='utf-8')
    for url in ('/request-bill/', '/hand-over/', '/api/v1/cashier/takeaway/', '/api/v1/cashier/kitchen-notices/',
                '/api/v1/cashier/menu-stock/', '/sold-out/', '/api/v1/cashier/shift/'):
        assert url in cashier, url
    for url in ('/api/v1/cashier/menu/', '/api/v1/cashier/orders/create/', 'Idempotency-Key'):
        assert url in pos, url
    assert 'MANUAL_OTHER' not in cashier
    assert "method: 'CASH'" in cashier


def test_scripts_do_not_use_browser_dialogs():
    for name in ('cashier.js', 'pos-order.js', 'kitchen.js', 'customer.js'):
        text = (ROOT / 'static' / 'js' / name).read_text(encoding='utf-8')
        assert not re.search(r'(?<![\w.])(confirm|alert|prompt)\(', text), name
