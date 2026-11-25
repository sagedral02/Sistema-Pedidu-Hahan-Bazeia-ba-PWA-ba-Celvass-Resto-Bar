"""Riwayat exports: CSV streams, PDF prints, and both honour the filters, the language and the cashier scope."""
import csv
import io

import pytest
from django.test import Client
from django.utils import timezone

from apps.accounts.models import Role, User
from apps.ordering.models import OrderStatus
from apps.payments.models import PaymentMethod, PaymentStatus
from reporting_helpers import add_item, make_history, make_order, make_payment

EXPORT = '/reports/export/'


def login(user):
    client = Client()
    client.force_login(user)
    return client


def csv_rows(response):
    assert response.streaming, 'history CSV must stream'
    text = b''.join(response.streaming_content).decode('utf-8-sig')
    return list(csv.reader(io.StringIO(text)))


def pdf_text(content):
    from pypdf import PdfReader
    return ' '.join(page.extract_text() for page in PdfReader(io.BytesIO(content)).pages).replace('\n', ' ')


@pytest.fixture
def today():
    return timezone.localdate()


@pytest.fixture
def data(restaurant, active_session, session_two, cashier_user, cashier_two, admin_user, today):
    mine = make_order(restaurant, active_session, OrderStatus.COMPLETED, '20.00', day=today, hour=10,
                      confirmed_by=cashier_user)
    add_item(mine, 'Ikan Saboko', 2, '10.00')
    make_history(mine, OrderStatus.CONFIRMED, cashier_user, day=today)
    theirs = make_order(restaurant, session_two, OrderStatus.REJECTED, '9.00', day=today, hour=11,
                        confirmed_by=cashier_two)
    make_history(theirs, OrderStatus.REJECTED, cashier_two, 'Stok habis', day=today)
    pay_mine = make_payment(restaurant, active_session, cashier_user, '20.00', paid_day=today, hour=12)
    pay_theirs = make_payment(restaurant, session_two, cashier_two, '30.00', paid_day=today, hour=13,
                              method=PaymentMethod.MANUAL_OTHER)
    pay_void = make_payment(restaurant, session_two, cashier_two, '7.00', paid_day=today, hour=14,
                            status=PaymentStatus.VOIDED, void_reason='Salah input', voided_by=admin_user)
    return {'mine': mine, 'theirs': theirs, 'pay_mine': pay_mine, 'pay_theirs': pay_theirs, 'pay_void': pay_void}


# ----------------------------------------------------------------------------- CSV

@pytest.mark.django_db
def test_orders_csv_streams_all_rows_in_the_chosen_language(admin_user, data, today):
    res = login(admin_user).get(EXPORT, {'type': 'orders_history', 'format': 'csv', 'lang': 'id', 'sort': 'time'})
    assert res.status_code == 200
    assert res['Content-Type'].startswith('text/csv')
    assert res['Content-Disposition'] == (
        f'attachment; filename="laporan-orders_history-{today.isoformat()}_{today.isoformat()}.csv"')
    assert res['Cache-Control'] == 'no-store'
    rows = csv_rows(res)
    assert rows[0] == ['Waktu', 'Kode', 'Meja', 'Status', 'Item', 'Total', 'Staf']
    assert [r[1] for r in rows[1:]] == [data['mine'].order_code, data['theirs'].order_code]
    assert rows[1][3:] == ['Selesai', '2', '20.00', 'cashier_test']
    assert rows[1][0].startswith(today.isoformat())


@pytest.mark.django_db
def test_csv_starts_with_a_bom_for_excel(admin_user, data):
    res = login(admin_user).get(EXPORT, {'type': 'transactions_history', 'format': 'csv'})
    assert next(iter(res.streaming_content)).startswith(b'\xef\xbb\xbf')


@pytest.mark.django_db
def test_transactions_csv_has_amounts_and_statuses(admin_user, data):
    rows = csv_rows(login(admin_user).get(
        EXPORT, {'type': 'transactions_history', 'format': 'csv', 'lang': 'en', 'sort': 'amount'}))
    assert rows[0] == ['Time', 'Code', 'Table', 'Method', 'Amount', 'Tendered', 'Change', 'Cashier', 'Status']
    assert [(r[4], r[8]) for r in rows[1:]] == [('7.00', 'Voided'), ('20.00', 'Completed'), ('30.00', 'Completed')]
    assert rows[1][3] == 'Cash'


@pytest.mark.django_db
def test_csv_follows_the_active_filters(admin_user, data):
    client = login(admin_user)
    rows = csv_rows(client.get(EXPORT, {'type': 'transactions_history', 'format': 'csv', 'status': 'VOIDED'}))
    assert [r[1] for r in rows[1:]] == [data['pay_void'].payment_code]

    rows = csv_rows(client.get(EXPORT, {'type': 'orders_history', 'format': 'csv', 'q': 'saboko'}))
    assert [r[1] for r in rows[1:]] == [data['mine'].order_code]


@pytest.mark.django_db
def test_csv_matches_what_the_screen_lists(admin_user, data):
    client = login(admin_user)
    screen = client.get('/reports/history/transactions/', {'sort': 'code', 'lang': 'en'}).json()
    rows = csv_rows(client.get(EXPORT, {'type': 'transactions_history', 'format': 'csv', 'sort': 'code', 'lang': 'en'}))
    assert [r[1] for r in rows[1:]] == [r['code'] for r in screen['rows']]


@pytest.mark.django_db
def test_csv_neutralizes_formulas_in_free_text(restaurant, active_session, admin_user, today):
    evil = User.objects.create_user(username='evil', password='x', role=Role.CASHIER, is_staff=True,
                                    full_name='=HYPERLINK("http://evil")')
    make_payment(restaurant, active_session, evil, '5.00', paid_day=today)
    rows = csv_rows(login(admin_user).get(EXPORT, {'type': 'transactions_history', 'format': 'csv'}))
    assert rows[1][7] == '\'=HYPERLINK("http://evil")'


@pytest.mark.django_db
def test_csv_of_an_empty_period_has_only_the_header(admin_user, restaurant):
    rows = csv_rows(login(admin_user).get(EXPORT, {'type': 'orders_history', 'format': 'csv', 'lang': 'pt'}))
    assert rows == [['Hora', 'Código', 'Mesa', 'Estado', 'Itens', 'Total', 'Funcionário']]


# ----------------------------------------------------------------------------- scope

@pytest.mark.django_db
def test_cashier_csv_only_contains_their_own_rows(cashier_user, data):
    client = login(cashier_user)
    tx = csv_rows(client.get(EXPORT, {'type': 'transactions_history', 'format': 'csv'}))
    assert [r[1] for r in tx[1:]] == [data['pay_mine'].payment_code]
    orders = csv_rows(client.get(EXPORT, {'type': 'orders_history', 'format': 'csv'}))
    assert [r[1] for r in orders[1:]] == [data['mine'].order_code]


@pytest.mark.django_db
def test_cashier_cannot_widen_their_export_with_a_cashier_filter(cashier_user, cashier_two, data):
    res = login(cashier_user).get(EXPORT, {
        'type': 'transactions_history', 'format': 'csv', 'cashier': str(cashier_two.id)})
    assert [r[1] for r in csv_rows(res)[1:]] == [data['pay_mine'].payment_code]


@pytest.mark.django_db
def test_cashier_pdf_leaks_nothing_and_says_so(cashier_user, data):
    res = login(cashier_user).get(EXPORT, {'type': 'transactions_history', 'format': 'pdf', 'lang': 'en'})
    text = pdf_text(res.content)
    assert data['pay_mine'].payment_code in text
    assert data['pay_theirs'].payment_code not in text
    assert data['pay_void'].payment_code not in text
    assert 'Maria' not in text
    assert 'This data covers your own activity only.' in text


# ----------------------------------------------------------------------------- PDF

@pytest.mark.django_db
def test_orders_pdf_has_title_summary_filters_and_rows(admin_user, data, restaurant):
    res = login(admin_user).get(EXPORT, {
        'type': 'orders_history', 'format': 'pdf', 'lang': 'en', 'status': 'COMPLETED'})
    assert res.status_code == 200
    assert res['Content-Type'] == 'application/pdf'
    assert res.content.startswith(b'%PDF')
    text = pdf_text(res.content)
    for expected in (restaurant.name, 'Order history', 'Orders', 'Sold value', '$20.00', data['mine'].order_code,
                     'Filters: Status: Completed', 'cashier_test'):
        assert expected in text, expected
    assert data['theirs'].order_code not in text


@pytest.mark.django_db
def test_transactions_pdf_is_landscape_and_translated(admin_user, data):
    from pypdf import PdfReader
    res = login(admin_user).get(EXPORT, {'type': 'transactions_history', 'format': 'pdf', 'lang': 'pt'})
    page = PdfReader(io.BytesIO(res.content)).pages[0]
    assert page.mediabox.width > page.mediabox.height
    text = pdf_text(res.content)
    for expected in ('Histórico de transações', 'Total recebido', 'Anulado', 'Concluído', 'Dinheiro'):
        assert expected in text, expected


@pytest.mark.django_db
def test_pdf_is_capped_and_says_so(admin_user, restaurant, active_session, cashier_user, today, monkeypatch):
    from apps.reporting import exporters
    monkeypatch.setattr(exporters, 'PDF_MAX_ROWS', 3)
    for i in range(6):
        make_payment(restaurant, active_session, cashier_user, '1.00', paid_day=today, minute=i)
    text = pdf_text(login(admin_user).get(
        EXPORT, {'type': 'transactions_history', 'format': 'pdf', 'lang': 'en'}).content)
    assert 'Showing only 3 rows' in text


@pytest.mark.django_db
@pytest.mark.parametrize('params', [
    {'type': 'orders_history', 'status': 'NOPE'},
    {'type': 'transactions_history', 'method': 'BITCOIN'},
    {'type': 'orders_history', 'preset': 'custom', 'start': '2026-02-01', 'end': '2026-01-01'},
])
def test_invalid_history_export_requests_are_rejected(admin_user, restaurant, params):
    for fmt in ('csv', 'pdf'):
        res = login(admin_user).get(EXPORT, {**params, 'format': fmt})
        assert res.status_code == 400
        assert res.json()['error']['code'] in ('INVALID_FILTER', 'END_BEFORE_START')


@pytest.mark.django_db
@pytest.mark.parametrize('kind', ['orders_history', 'transactions_history'])
def test_history_export_requires_login_and_a_report_role(kitchen_user, kind):
    assert Client().get(EXPORT, {'type': kind, 'format': 'csv'}).status_code == 302
    assert login(kitchen_user).get(EXPORT, {'type': kind, 'format': 'csv'}).status_code == 403
