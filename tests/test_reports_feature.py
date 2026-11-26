"""Reports follow per-item status, take away and shifts."""
from decimal import Decimal

import pytest
from django.test import Client
from django.utils import timezone

from apps.kitchen.notices import report_out_of_stock
from apps.ordering.models import OrderItem, OrderSource, OrderStatus
from apps.ordering.services import submit_customer_order
from apps.reporting.overview import build_overview
from apps.reporting.reports import Label, Period, exceptions_report, menu_sales_report, shifts_report, table_usage_report
from apps.shifts.services import close_shift, open_shift
from apps.tables.takeaway import create_takeaway_session

DATA = '/reports/data/'


def order(session, lines, key, user):
    return submit_customer_order(table_session=session, idempotency_key=key, source=OrderSource.CASHIER, created_by=user,
                                 items_data=[{'menu_item_id': m.id, 'quantity': q} for m, q in lines])


@pytest.fixture
def period():
    today = timezone.localdate()
    return Period(today, today)


@pytest.mark.django_db
def test_out_of_stock_lines_are_not_sold_but_are_listed_as_exceptions(active_session, menu_item_fish, menu_item_corn, cashier_user, kitchen_user, period):
    o = order(active_session, [(menu_item_fish, 2), (menu_item_corn, 1)], 'a', cashier_user)
    report_out_of_stock(item=OrderItem.objects.get(order=o, menu_item=menu_item_corn), staff_user=kitchen_user, reason='habis')
    sold = {r[1]: r[2] for r in menu_sales_report(period).rows}
    assert sold == {'Ikan Saboko': 2}
    top = {i['name'] for i in build_overview('today')['top_items']['by_qty']}
    assert top == {'Ikan Saboko'}
    ex = exceptions_report(period)
    rows = [r for r in ex.rows if r[1] == Label('rpt_kind_unavailable')]
    assert len(rows) == 1 and rows[0][4] == Decimal('3.50') and 'habis' in rows[0][5]
    assert {s.label_key: s.value for s in ex.summary}['rpt_sum_unavailable'] == 1


@pytest.mark.django_db
def test_table_usage_shows_one_take_away_row(restaurant, active_session, menu_item_fish, cashier_user, period):
    create_takeaway_session(restaurant=restaurant, opened_by=cashier_user)
    create_takeaway_session(restaurant=restaurant, opened_by=cashier_user)
    rows = table_usage_report(period).rows
    take = [r for r in rows if r[0] == Label('rpt_ot_TAKEAWAY')]
    assert len(take) == 1 and take[0][1] == 2


@pytest.mark.django_db
def test_shift_report_is_scoped_and_shows_the_difference(cashier_user, cashier_two, admin_user, restaurant, period):
    s1 = open_shift(cashier=cashier_user, opening_cash='20')
    close_shift(shift=s1, counted_cash='18', note='kurang')
    open_shift(cashier=cashier_two, opening_cash='10')
    report = shifts_report(period, admin_user)
    assert len(report.rows) == 2
    mine = shifts_report(period, cashier_user)
    assert len(mine.rows) == 1
    row = mine.rows[0]
    assert row[3] == Decimal('20.00') and row[6] == Decimal('18.00') and row[7] == Decimal('-2.00') and row[8] == 'kurang'
    assert {s.label_key: s.value for s in mine.summary}['rpt_sum_difference'] == Decimal('-2.00')


@pytest.mark.django_db
def test_shift_report_renders_and_exports(admin_user, cashier_user, restaurant):
    open_shift(cashier=cashier_user, opening_cash='5')
    c = Client()
    c.force_login(admin_user)
    for lang in ('tet', 'pt', 'en', 'id'):
        res = c.get(DATA, {'type': 'shifts', 'lang': lang})
        assert res.status_code == 200, res.content
    csv = c.get('/reports/export/', {'format': 'csv', 'type': 'shifts', 'lang': 'id'})
    assert csv.status_code == 200 and 'Kas awal' in csv.content.decode('utf-8-sig')
    pdf = c.get('/reports/export/', {'format': 'pdf', 'type': 'shifts', 'lang': 'en'})
    assert pdf.status_code == 200 and pdf['Content-Type'] == 'application/pdf'


@pytest.mark.django_db
def test_history_filters_by_order_type_and_uses_take_away_labels(restaurant, active_session, menu_item_fish, cashier_user, admin_user):
    session = create_takeaway_session(restaurant=restaurant, opened_by=cashier_user, customer_name='Budi')
    order(session, [(menu_item_fish, 1)], 'ta', cashier_user)
    order(active_session, [(menu_item_fish, 1)], 'dine', cashier_user)
    c = Client()
    c.force_login(admin_user)
    rows = c.get('/reports/history/orders/', {'order_type': 'TAKEAWAY'}).json()['rows']
    assert [r['table'] for r in rows] == ['Take away #001 - Budi']
    rows = c.get('/reports/history/orders/', {'order_type': 'DINE_IN'}).json()['rows']
    assert len(rows) == 1 and rows[0]['table'] == active_session.table.display_name
    assert c.get('/reports/history/orders/', {'order_type': 'NOPE'}).status_code == 400


@pytest.mark.django_db
def test_order_detail_lists_each_item_status(active_session, menu_item_fish, menu_item_corn, cashier_user, kitchen_user, admin_user):
    o = order(active_session, [(menu_item_fish, 1), (menu_item_corn, 1)], 'a', cashier_user)
    report_out_of_stock(item=OrderItem.objects.get(order=o, menu_item=menu_item_corn), staff_user=kitchen_user, reason='habis')
    c = Client()
    c.force_login(admin_user)
    detail = c.get(f'/reports/history/orders/{o.id}/', {'lang': 'id'}).json()
    items = {i['name']: i for i in detail['items']}
    assert items["Batar Da'an"]['status'] == 'Tidak tersedia' and items["Batar Da'an"]['unavailable_reason'] == 'habis'
    assert items['Ikan Saboko']['status_code'] == 'QUEUED'


def test_report_selector_offers_the_shift_report():
    from pathlib import Path
    html = (Path(__file__).resolve().parent.parent / 'templates' / 'reports' / 'index.html').read_text(encoding='utf-8')
    assert 'value="shifts" data-i18n="rpt_type_shifts"' in html
    assert 'value="order_types" data-i18n="rpt_type_order_types"' in html
