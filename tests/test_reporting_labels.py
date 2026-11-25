"""Every report label exists in all four languages, and the page chrome stays in sync with the server."""
import re
from pathlib import Path

import pytest

from apps.reporting.labels import DEFAULT_LANG, LABELS, LANGS, label, normalize_lang
from apps.reporting.reports import Label, REPORT_BUILDERS, build_report, parse_period

ROOT = Path(__file__).resolve().parent.parent
LANG_NAMES = ('tet', 'pt', 'en', 'id')


def _i18n_blocks():
    """Parse static/js/i18n.js into {lang: {key: value}} (same layout rules as test_ui_shell_and_pwa)."""
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
                result[current][k.group(1)] = k.group(2).replace('\\"', '"')
    return result


def test_python_labels_cover_the_same_keys_in_every_language():
    reference = set(LABELS[DEFAULT_LANG])
    assert set(LABELS) == set(LANG_NAMES) == set(LANGS)
    for lang in LANG_NAMES:
        assert set(LABELS[lang]) == reference, (lang, reference ^ set(LABELS[lang]))


def test_no_label_is_empty_or_a_copy_of_its_key():
    for lang, labels in LABELS.items():
        for key, value in labels.items():
            assert value.strip(), (lang, key)
            assert value != key, (lang, key)


def test_languages_do_not_share_untranslated_text_by_accident():
    """Spot check: the report names must differ between Tetun, Portuguese, English and Indonesian."""
    for key in ('rpt_type_cashiers', 'rpt_col_cashier', 'rpt_err_invalid_date', 'rpt_pdf_no_data'):
        assert len({LABELS[lang][key] for lang in LANG_NAMES}) == 4, key


def test_truncation_message_keeps_its_placeholder_in_every_language():
    for lang in LANG_NAMES:
        assert '{limit}' in LABELS[lang]['rpt_pdf_truncated']
        assert '3' in label(lang, 'rpt_pdf_truncated', limit=3)


def test_label_falls_back_instead_of_failing():
    assert label('xx', 'rpt_type_cashiers') == LABELS[DEFAULT_LANG]['rpt_type_cashiers']
    assert label('en', 'does_not_exist') == 'does_not_exist'
    assert normalize_lang(None) == DEFAULT_LANG


@pytest.mark.django_db
def test_every_key_used_by_a_report_has_a_label(restaurant, active_session, cashier_user, admin_user):
    """Builds every report and checks all keys it emits (title, columns, summary, translated cells)."""
    from reporting_helpers import make_payment
    from django.utils import timezone

    today = timezone.localdate()
    make_payment(restaurant, active_session, cashier_user, '5.00', paid_day=today)
    period = parse_period('today')
    known = set(LABELS[DEFAULT_LANG])

    for report_type in REPORT_BUILDERS:
        report = build_report(report_type, period, admin_user)
        used = {report.title_key}
        used.update(c.label_key for c in report.columns)
        used.update(s.label_key for s in report.summary)
        used.update(v.key for row in report.rows for v in row if isinstance(v, Label))
        assert used <= known, (report_type, used - known)


def test_every_possible_enum_cell_has_a_label():
    from apps.payments.models import PaymentMethod
    for method in PaymentMethod.values:
        assert f'rpt_method_{method}' in LABELS[DEFAULT_LANG]
    for kind in ('rejected', 'cancelled', 'voided'):
        assert f'rpt_kind_{kind}' in LABELS[DEFAULT_LANG]


def test_every_error_code_has_a_label():
    for code in ('invalid_date', 'end_before_start', 'range_too_long', 'invalid_report', 'invalid_filter', 'not_found'):
        assert f'rpt_err_{code}' in LABELS[DEFAULT_LANG]


def test_page_chrome_keys_exist_in_all_four_browser_languages():
    blocks = _i18n_blocks()
    rpt_keys = {k for lang in LANG_NAMES for k in blocks[lang] if k.startswith('rpt_')}
    assert rpt_keys, 'no rpt_ keys found in i18n.js'
    for lang in LANG_NAMES:
        missing = sorted(rpt_keys - set(blocks[lang]))
        assert not missing, (lang, missing)


def test_page_chrome_keys_used_by_templates_and_script_exist():
    blocks = _i18n_blocks()
    used = set()
    for path in [ROOT / 'templates' / 'reports' / 'index.html', ROOT / 'static' / 'js' / 'reports.js']:
        used.update(re.findall(r'''(?:data-i18n(?:-placeholder|-title|-label)?="|\btr\(')(rpt_\w+|reports)['"]''',
                               path.read_text(encoding='utf-8')))
    used.update(re.findall(r"""'(rpt_\w+)'""", (ROOT / 'static' / 'js' / 'reports.js').read_text(encoding='utf-8')))
    assert used
    for lang in LANG_NAMES:
        assert not (used - set(blocks[lang])), (lang, sorted(used - set(blocks[lang])))


def test_report_names_match_between_browser_and_server():
    """The report selector (i18n.js) and the report titles (labels.py) must read the same."""
    blocks = _i18n_blocks()
    shared = [k for k in LABELS[DEFAULT_LANG] if k in blocks['tet']]
    assert 'rpt_type_daily_sales' in shared
    for lang in LANG_NAMES:
        for key in shared:
            assert blocks[lang][key] == LABELS[lang][key], (lang, key)


def test_report_selector_offers_every_report_type():
    html = (ROOT / 'templates' / 'reports' / 'index.html').read_text(encoding='utf-8')
    for report_type in REPORT_BUILDERS:
        assert f'value="{report_type}" data-i18n="rpt_type_{report_type}"' in html
