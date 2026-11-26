"""Labels added for the per-item / quota / take-away / shift work exist in all four languages."""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
LANG_NAMES = ('tet', 'pt', 'en', 'id')

# Every key that starts with one of these prefixes must exist, non-empty, in every language.
FEATURE_PREFIXES = (
    'kds_item', 'csh_', 'cust_item', 'cust_stock', 'cust_max', 'stock_th', 'stock_btn', 'stock_daily',
    'stock_closed', 'stock_quota', 'stock_reopened', 'stock_cancelled', 'rpt_ot_', 'rpt_type_order_types',
    # Plan 2: restaurant settings, highlights, guests, opening hours, thanks/ratings, visitors
    'set_', 'cust_hl', 'cust_guests', 'cust_closed', 'cust_recent', 'thx_', 'menu_reviews', 'ov_visitors',
    # Plan 3: table flow, walkout, QR scanner, push, home portal, occupied page
    'qr_scan', 'push_', 'home_', 'occupied_', 'rpt_kind_walkout', 'rpt_sum_walkout',
)
# Keys that must exist (add the ones the JS builds dynamically or that have no common prefix).
REQUIRED_KEYS = (
    'RESTAURANT_CLOSED', 'cust_paid_toast', 'cust_contact_title', 'menu_th_rating', 'menu_no_rating',
    'ov_kpi_visitors', 'ov_chart_visitors', 'rpt_type_visitors', 'rpt_col_avg_guests', 'rpt_col_takeaway_orders',
    'rpt_sum_avg_guests', 'rpt_sum_takeaway_orders', 'rpt_type_audit_log',
    'SESSION_UNPAID', 'WALKOUT_REASON_REQUIRED', 'ov_kpi_unpaid', 'set_idle_alert', 'set_idle_alert_hint',
    'csh_tab_idle', 'csh_close_walkout', 'csh_btn_order', 'csh_btn_order_more',
)

# Scripts and templates written for Plan 2: every literal t('key') / data-i18n="key" they use must exist.
PLAN2_FILES = (
    'static/js/admin-settings.js', 'static/js/thanks.js', 'static/js/customer.js', 'static/js/admin.js',
    'static/js/admin-overview.js', 'static/js/cashier.js', 'static/js/pos-order.js', 'static/js/reports-audit.js',
    'templates/admin_custom/_settings_panel.html', 'templates/admin_custom/dashboard.html',
    'templates/customer/index.html', 'templates/customer/thanks.html', 'templates/customer/_menu_card.html',
    'templates/customer/_contact.html', 'templates/cashier/dashboard.html', 'templates/reports/index.html',
    'static/js/qr-scanner.js', 'static/js/push.js', 'static/js/home.js', 'templates/customer/landing.html',
    'templates/customer/occupied_locked.html', 'templates/customer/invalid_qr.html', 'templates/base/_account_menu.html',
)
KEY_PATTERNS = (
    re.compile(r"""\b(?:t|tr)\(\s*'([A-Za-z_]\w*)'\s*\)"""),
    re.compile(r'data-i18n(?:-placeholder|-title|-label)?="([A-Za-z_]\w*)"'),
)


def test_every_literal_key_used_by_plan2_files_exists():
    blocks = _blocks()
    known = set(blocks['tet'])
    missing = {}
    for rel in PLAN2_FILES:
        text = (ROOT / rel).read_text(encoding='utf-8')
        for pattern in KEY_PATTERNS:
            for key in pattern.findall(text):
                if key not in known:
                    missing.setdefault(rel, set()).add(key)
    assert not missing, {k: sorted(v) for k, v in missing.items()}


def _blocks():
    text = (ROOT / 'static' / 'js' / 'i18n.js').read_text(encoding='utf-8')
    result = {lang: {} for lang in LANG_NAMES}
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
            k = re.match(r'^\s+([A-Za-z_]\w*):\s*"((?:[^"\\]|\\.)*)"', line)
            if k:
                result[current][k.group(1)] = k.group(2)
    return result


def test_feature_keys_exist_in_every_language():
    blocks = _blocks()
    keys = set(REQUIRED_KEYS)
    for lang in LANG_NAMES:
        keys |= {k for k in blocks[lang] if k.startswith(FEATURE_PREFIXES)}
    assert keys
    for lang in LANG_NAMES:
        missing = sorted(k for k in keys if not blocks[lang].get(k, '').strip())
        assert not missing, (lang, missing)


def test_feature_labels_have_no_emoji():
    blocks = _blocks()
    emoji = re.compile('[\U0001F300-\U0001FAFF\u2600-\u27BF]')
    for lang in LANG_NAMES:
        bad = [k for k, v in blocks[lang].items() if k.startswith(FEATURE_PREFIXES) and emoji.search(v)]
        assert not bad, (lang, bad)


def test_kitchen_status_labels_cover_every_item_status():
    from apps.ordering.models import ItemStatus
    blocks = _blocks()
    for lang in LANG_NAMES:
        for status in ItemStatus.values:
            assert blocks[lang].get(f'kds_item_{status}'), (lang, status)


@pytest.mark.django_db
def test_kitchen_page_loads_the_item_script(client, kitchen_user):
    client.force_login(kitchen_user)
    res = client.get('/kitchen/')
    assert res.status_code == 200
    assert b'js/kitchen.js' in res.content


def test_kitchen_script_renders_item_actions_and_listens_for_item_events():
    js = (ROOT / 'static' / 'js' / 'kitchen.js').read_text(encoding='utf-8')
    for action in ('start', 'ready', 'served'):
        assert f"'{action}'" in js
    assert '/api/v1/kitchen/items/' in js
    assert 'ITEM_STATUS_CHANGED' in js
