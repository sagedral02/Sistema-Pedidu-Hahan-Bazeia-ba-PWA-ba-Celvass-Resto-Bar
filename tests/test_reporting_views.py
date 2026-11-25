"""Reports pages and exports: access control, JSON, CSV and PDF output, language handling."""
import csv
import io
from datetime import timedelta
from decimal import Decimal

import pytest
from django.test import Client
from django.utils import timezone

from apps.accounts.models import Role, User
from apps.ordering.models import OrderStatus
from apps.payments.models import PaymentStatus
from reporting_helpers import add_item, make_order, make_payment

DATA = '/reports/data/'
EXPORT = '/reports/export/'


def login(user):
    client = Client()
    client.force_login(user)
    return client


@pytest.fixture
def today():
    return timezone.localdate()


def parse_csv(response):
    text = response.content.decode('utf-8-sig')
    return list(csv.reader(io.StringIO(text)))


# ----------------------------------------------------------------------------- access

@pytest.mark.django_db
@pytest.mark.parametrize('url', ['/reports/', DATA, EXPORT])
def test_anonymous_users_are_sent_to_login(url):
    res = Client().get(url)
    assert res.status_code == 302
    assert res['Location'].startswith('/login/')


@pytest.mark.django_db
@pytest.mark.parametrize('url', ['/reports/', DATA, EXPORT])
def test_kitchen_staff_cannot_open_reports(kitchen_user, url):
    assert login(kitchen_user).get(url).status_code == 403


@pytest.mark.django_db
def test_reports_only_accept_get(admin_user):
    assert login(admin_user).post(DATA).status_code == 405


@pytest.mark.django_db
def test_admin_page_uses_admin_shell_and_links(admin_user, restaurant):
    html = login(admin_user).get('/reports/').content.decode()
    assert 'data-role="admin"' in html
    assert 'data-panel-home="/admin-portal/"' in html
    assert 'id="reports-panel"' in html
    assert 'href="/reports/"' in html
    assert 'class="app-nav-link is-active"' in html
    assert 'data-panel="overview-panel"' in html
    assert 'cashierApp' not in html


@pytest.mark.django_db
def test_cashier_page_uses_cashier_shell_without_cashier_scripts(cashier_user, restaurant):
    html = login(cashier_user).get('/reports/').content.decode()
    assert 'data-role="cashier"' in html
    assert 'data-panel-home="/cashier/"' in html
    assert 'cashierApp' not in html
    assert 'data-panel="pos-panel"' in html
    assert 'data-panel="menu-panel"' not in html
    assert '/static/js/reports.js' in html


@pytest.mark.django_db
def test_portals_link_to_reports(admin_user, cashier_user, restaurant):
    assert 'href="/reports/"' in login(admin_user).get('/admin-portal/').content.decode()
    assert 'href="/reports/"' in login(cashier_user).get('/cashier/').content.decode()


# ----------------------------------------------------------------------------- JSON

@pytest.mark.django_db
def test_data_endpoint_returns_translated_report(
        admin_user, restaurant, active_session, cashier_user, today):
    make_payment(restaurant, active_session, cashier_user, '12.50', paid_day=today)

    en = login(admin_user).get(DATA, {'type': 'daily_sales', 'preset': 'today', 'lang': 'en'}).json()
    assert en['title'] == 'Daily sales'
    assert [c['label'] for c in en['columns']] == ['Date', 'Transactions', 'Total sales', 'Average']
    assert en['rows'] == [[today.strftime('%d/%m/%Y'), '1', '$12.50', '$12.50']]
    assert en['summary'][0] == {'label': 'Total sales', 'value': '$12.50'}
    assert en['scope_own'] is False

    idn = login(admin_user).get(DATA, {'type': 'daily_sales', 'lang': 'id'}).json()
    assert idn['title'] == 'Penjualan harian'
    assert idn['columns'][0]['label'] == 'Tanggal'


@pytest.mark.django_db
def test_unknown_language_falls_back_to_tetun(admin_user, restaurant):
    data = login(admin_user).get(DATA, {'type': 'daily_sales', 'lang': 'xx'}).json()
    assert data['title'] == 'Vendas loron-loron'


@pytest.mark.django_db
@pytest.mark.parametrize('params,code', [
    ({'type': 'bogus'}, 'INVALID_REPORT'),
    ({'type': 'daily_sales', 'preset': 'custom', 'start': 'x', 'end': 'y'}, 'INVALID_DATE'),
    ({'type': 'daily_sales', 'preset': 'custom', 'start': '2026-02-01', 'end': '2026-01-01'}, 'END_BEFORE_START'),
    ({'type': 'daily_sales', 'preset': 'custom', 'start': '2020-01-01', 'end': '2026-01-01'}, 'RANGE_TOO_LONG'),
])
def test_invalid_requests_return_localized_errors(admin_user, restaurant, params, code):
    res = login(admin_user).get(DATA, {**params, 'lang': 'pt'})
    assert res.status_code == 400
    error = res.json()['error']
    assert error['code'] == code
    assert error['message'] and not error['message'].startswith('rpt_err_')


@pytest.mark.django_db
def test_error_message_follows_language(admin_user, restaurant):
    params = {'type': 'daily_sales', 'preset': 'custom', 'start': 'x', 'end': 'y'}
    messages = {
        lang: login(admin_user).get(DATA, {**params, 'lang': lang}).json()['error']['message']
        for lang in ('tet', 'pt', 'en', 'id')
    }
    assert len(set(messages.values())) == 4


@pytest.mark.django_db
def test_every_report_type_renders_for_every_language(admin_user, restaurant, active_session, cashier_user, today):
    make_payment(restaurant, active_session, cashier_user, '3.00', paid_day=today)
    client = login(admin_user)
    for report_type in ('daily_sales', 'menu_sales', 'payment_methods', 'cashiers', 'exceptions', 'table_usage'):
        for lang in ('tet', 'pt', 'en', 'id'):
            res = client.get(DATA, {'type': report_type, 'lang': lang})
            assert res.status_code == 200, (report_type, lang)
            body = res.json()
            assert not body['title'].startswith('rpt_')
            assert all(not c['label'].startswith('rpt_') for c in body['columns'])
            assert all(not s['label'].startswith('rpt_') for s in body['summary'])
            assert all(not cell.startswith('rpt_') for row in body['rows'] for cell in row)


# ----------------------------------------------------------------------------- CSV

@pytest.mark.django_db
def test_csv_export_matches_report_and_opens_in_excel(
        admin_user, restaurant, active_session, cashier_user, today):
    make_payment(restaurant, active_session, cashier_user, '12.50', paid_day=today)

    res = login(admin_user).get(EXPORT, {'format': 'csv', 'type': 'daily_sales', 'lang': 'id'})

    assert res.status_code == 200
    assert res['Content-Type'].startswith('text/csv')
    assert res.content.startswith(b'\xef\xbb\xbf')
    assert res['Content-Disposition'] == (
        f'attachment; filename="laporan-daily_sales-{today.isoformat()}_{today.isoformat()}.csv"'
    )
    assert res['Cache-Control'] == 'no-store'
    assert parse_csv(res) == [
        ['Tanggal', 'Transaksi', 'Total penjualan', 'Rata-rata'],
        [today.isoformat(), '1', '12.50', '12.50'],
    ]


@pytest.mark.django_db
def test_csv_headers_follow_language(admin_user, restaurant):
    headers = {
        lang: parse_csv(login(admin_user).get(EXPORT, {'format': 'csv', 'type': 'cashiers', 'lang': lang}))[0][0]
        for lang in ('tet', 'pt', 'en', 'id')
    }
    assert headers == {'tet': 'Kaixa', 'pt': 'Caixa', 'en': 'Cashier', 'id': 'Kasir'}


@pytest.mark.django_db
def test_csv_neutralizes_spreadsheet_formulas(admin_user, restaurant, active_session, today):
    order = make_order(restaurant, active_session, OrderStatus.COMPLETED, '5.00', day=today)
    add_item(order, '=HYPERLINK("http://evil")', 1, '5.00')
    add_item(order, '@SUM(A1)', 1, '0.00')

    rows = parse_csv(login(admin_user).get(EXPORT, {'format': 'csv', 'type': 'menu_sales'}))

    names = {r[1] for r in rows[1:]}
    assert '\'=HYPERLINK("http://evil")' in names
    assert "'@SUM(A1)" in names
    assert not any(n.startswith(('=', '@')) for n in names)


@pytest.mark.django_db
def test_csv_empty_report_has_header_only(admin_user, restaurant):
    rows = parse_csv(login(admin_user).get(EXPORT, {'format': 'csv', 'type': 'menu_sales', 'lang': 'en'}))
    assert rows == [['Category', 'Menu item', 'Quantity', 'Revenue']]


@pytest.mark.django_db
def test_export_rejects_unknown_format(admin_user, restaurant):
    res = login(admin_user).get(EXPORT, {'format': 'xlsx'})
    assert res.status_code == 400


@pytest.mark.django_db
def test_export_rejects_invalid_period(admin_user, restaurant):
    res = login(admin_user).get(EXPORT, {'format': 'csv', 'preset': 'custom', 'start': '2026-02-01', 'end': '2026-01-01'})
    assert res.status_code == 400
    assert res.json()['error']['code'] == 'END_BEFORE_START'


# ----------------------------------------------------------------------------- scoping applies to every format

@pytest.fixture
def scoped_data(restaurant, active_session, session_two, cashier_user, cashier_two, today):
    make_payment(restaurant, active_session, cashier_user, '10.00', paid_day=today)
    make_payment(restaurant, session_two, cashier_two, '30.00', paid_day=today)
    make_payment(restaurant, session_two, cashier_two, '7.00', paid_day=today, status=PaymentStatus.VOIDED,
                 void_reason='Rahasia kasir dua')


@pytest.mark.django_db
def test_cashier_only_sees_own_rows_on_screen_and_in_csv(scoped_data, cashier_user):
    client = login(cashier_user)

    screen = client.get(DATA, {'type': 'cashiers', 'lang': 'en'}).json()
    assert screen['scope_own'] is True
    assert [r[0] for r in screen['rows']] == ['cashier_test']

    rows = parse_csv(client.get(EXPORT, {'format': 'csv', 'type': 'cashiers', 'lang': 'en'}))
    assert [r[0] for r in rows[1:]] == ['cashier_test']

    exceptions = parse_csv(client.get(EXPORT, {'format': 'csv', 'type': 'exceptions', 'lang': 'en'}))
    assert len(exceptions) == 1  # header only, the void belongs to the other cashier
    assert b'Rahasia kasir dua' not in client.get(EXPORT, {'format': 'pdf', 'type': 'exceptions'}).content


@pytest.mark.django_db
def test_admin_sees_every_cashier(scoped_data, admin_user):
    screen = login(admin_user).get(DATA, {'type': 'cashiers'}).json()
    assert {r[0] for r in screen['rows']} == {'cashier_test', 'Maria Cashier'}


@pytest.mark.django_db
def test_cashier_can_still_see_unscoped_reports(scoped_data, cashier_user):
    data = login(cashier_user).get(DATA, {'type': 'daily_sales', 'lang': 'en'}).json()
    assert data['scope_own'] is False
    assert data['summary'][0]['value'] == '$40.00'


# ----------------------------------------------------------------------------- PDF

def pdf_pages(content):
    from pypdf import PdfReader
    reader = PdfReader(io.BytesIO(content))
    return [page.extract_text() for page in reader.pages]


@pytest.mark.django_db
def test_pdf_export_contains_header_summary_and_rows(
        admin_user, restaurant, active_session, cashier_user, today):
    make_payment(restaurant, active_session, cashier_user, '12.50', paid_day=today)

    res = login(admin_user).get(EXPORT, {'format': 'pdf', 'type': 'daily_sales', 'lang': 'pt'})

    assert res.status_code == 200
    assert res['Content-Type'] == 'application/pdf'
    assert res.content.startswith(b'%PDF')
    assert res['Content-Disposition'].endswith('.pdf"')
    text = pdf_pages(res.content)[0]
    for expected in (restaurant.name, 'Relatório', 'Vendas diárias', 'Total de vendas', '$12.50',
                     today.strftime('%d/%m/%Y'), 'Impresso por', 'Página'):
        assert expected in text, expected


@pytest.mark.django_db
def test_pdf_with_no_data_shows_message(admin_user, restaurant):
    res = login(admin_user).get(EXPORT, {'format': 'pdf', 'type': 'menu_sales', 'lang': 'en'})
    assert res.status_code == 200
    assert 'No data for this period.' in pdf_pages(res.content)[0]


@pytest.mark.django_db
def test_pdf_accented_text_is_rendered(admin_user, restaurant, active_session, today):
    order = make_order(restaurant, active_session, OrderStatus.COMPLETED, '5.00', day=today)
    add_item(order, 'Peixe à Dili', 1, '5.00')
    res = login(admin_user).get(EXPORT, {'format': 'pdf', 'type': 'menu_sales', 'lang': 'pt'})
    assert 'Peixe à Dili' in pdf_pages(res.content)[0]


@pytest.mark.django_db
def test_long_pdf_spans_pages_and_repeats_header(admin_user, restaurant, active_session, today):
    order = make_order(restaurant, active_session, OrderStatus.COMPLETED, '500.00', day=today)
    for i in range(120):
        add_item(order, f'Menu nomor {i:03d}', 1, '1.00')

    res = login(admin_user).get(EXPORT, {'format': 'pdf', 'type': 'menu_sales', 'lang': 'en'})
    pages = pdf_pages(res.content)

    assert len(pages) >= 2
    assert 'Menu nomor 119' in ''.join(pages)
    assert 'Page 2 of' in pages[1]
    assert 'Quantity' in pages[1]  # table header repeated on the next page


@pytest.mark.django_db
def test_wide_report_prints_in_landscape(admin_user, restaurant):
    from pypdf import PdfReader
    res = login(admin_user).get(EXPORT, {'format': 'pdf', 'type': 'exceptions'})
    page = PdfReader(io.BytesIO(res.content)).pages[0]
    assert page.mediabox.width > page.mediabox.height


@pytest.mark.django_db
def test_pdf_notes_when_rows_are_truncated(admin_user, restaurant, active_session, today, monkeypatch):
    from apps.reporting import exporters
    monkeypatch.setattr(exporters, 'PDF_MAX_ROWS', 3)
    order = make_order(restaurant, active_session, OrderStatus.COMPLETED, '50.00', day=today)
    for i in range(6):
        add_item(order, f'Menu {i}', 1, '1.00')

    res = login(admin_user).get(EXPORT, {'format': 'pdf', 'type': 'menu_sales', 'lang': 'en'})
    text = ' '.join(pdf_pages(res.content)).replace('\n', ' ')
    assert 'Showing only 3 rows' in text
    assert 'Menu 5' not in text
