"""Riwayat selectors: filters, search, sorting, pagination, scope rules, details and query counts."""
import uuid
from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.ordering.models import OrderStatus
from apps.payments.models import PaymentMethod, PaymentStatus
from apps.reporting.history import (
    HistoryFilters, HistoryNotFound, MAX_PAGE_SIZE, orders_history, order_detail, page_display, parse_filters,
    parse_paging, parse_sort, transaction_detail, transactions_history,
)
from apps.reporting.reports import Period, ReportError
from reporting_helpers import add_item, make_history, make_order, make_payment


@pytest.fixture
def today():
    return timezone.localdate()


@pytest.fixture
def period(today):
    return Period(today, today)


@pytest.fixture
def data(restaurant, active_session, session_two, cashier_user, cashier_two, admin_user, today):
    """One order and one payment per cashier, plus an unowned waiting order."""
    mine = make_order(restaurant, active_session, OrderStatus.COMPLETED, '20.00', day=today, hour=10,
                      confirmed_by=cashier_user)
    add_item(mine, 'Ikan Saboko', 2, '6.50')
    add_item(mine, 'Teh', 3, '2.33')
    make_history(mine, OrderStatus.CONFIRMED, cashier_user, day=today)

    theirs = make_order(restaurant, session_two, OrderStatus.REJECTED, '9.00', day=today, hour=11,
                        confirmed_by=cashier_two)
    add_item(theirs, "Batar Da'an", 1, '9.00')
    make_history(theirs, OrderStatus.REJECTED, cashier_two, 'Stok habis', day=today)

    waiting = make_order(restaurant, session_two, OrderStatus.WAITING_CASHIER_CONFIRMATION, '4.00', day=today,
                         hour=13)
    add_item(waiting, 'Kopi', 1, '4.00')

    acted_only = make_order(restaurant, active_session, OrderStatus.CANCELLED, '3.00', day=today, hour=14)
    make_history(acted_only, OrderStatus.CANCELLED, cashier_user, 'Pelanggan pergi', day=today)

    pay_mine = make_payment(restaurant, active_session, cashier_user, '20.00', paid_day=today, hour=15)
    pay_theirs = make_payment(restaurant, session_two, cashier_two, '30.00', paid_day=today, hour=16,
                              method=PaymentMethod.MANUAL_OTHER)
    pay_void = make_payment(restaurant, session_two, cashier_two, '7.00', paid_day=today, hour=17,
                            status=PaymentStatus.VOIDED, void_reason='Salah input', voided_by=admin_user)
    return {
        'mine': mine, 'theirs': theirs, 'waiting': waiting, 'acted_only': acted_only,
        'pay_mine': pay_mine, 'pay_theirs': pay_theirs, 'pay_void': pay_void,
    }


# ----------------------------------------------------------------------------- parsing

def test_parse_sort_only_accepts_whitelisted_columns():
    assert parse_sort('orders', 'total') == 'total'
    assert parse_sort('orders', '-status') == '-status'
    assert parse_sort('orders', 'password; DROP TABLE') == '-time'
    assert parse_sort('orders', 'cashier') == '-time'   # a transactions-only column
    assert parse_sort('transactions', 'cashier') == 'cashier'
    assert parse_sort('orders', None) == '-time'


def test_parse_paging_clamps_values():
    assert parse_paging('2', '50') == (2, 50)
    assert parse_paging('0', '0') == (1, 1)
    assert parse_paging('-3', '100000') == (1, MAX_PAGE_SIZE)
    assert parse_paging('abc', 'xyz') == (1, 20)


@pytest.mark.parametrize('kind,params', [
    ('orders', {'status': 'NOPE'}),
    ('orders', {'source': 'ALIEN'}),
    ('transactions', {'status': 'PENDING'}),
    ('transactions', {'method': 'BITCOIN'}),
    ('orders', {'cashier': 'not-a-uuid'}),
])
def test_parse_filters_rejects_unknown_values(kind, params):
    with pytest.raises(ReportError) as exc:
        parse_filters(kind, params)
    assert exc.value.code == 'invalid_filter'


def test_parse_filters_ignores_irrelevant_parameters():
    f = parse_filters('orders', {'method': 'CASH', 'q': '  ikan  '})
    assert f.method == '' and f.q == 'ikan'


# ----------------------------------------------------------------------------- orders

def test_admin_sees_every_order_newest_first(data, admin_user, period):
    page = orders_history(period, admin_user)
    assert page.total == 4
    assert [r['id'] for r in page.rows] == [str(data[k].id) for k in ('acted_only', 'waiting', 'theirs', 'mine')]
    assert page.kpi['orders'] == 4
    assert page.kpi['sold_total'] == Decimal('20.00')
    assert page.kpi['average'] == Decimal('20.00')
    assert page.kpi['exceptions'] == 2


def test_order_rows_have_item_counts_and_staff(data, admin_user, period):
    rows = {r['id']: r for r in orders_history(period, admin_user).rows}
    mine = rows[str(data['mine'].id)]
    assert mine['item_count'] == 5
    assert mine['staff'] == 'cashier_test'
    assert mine['table'] == 'Meja 01'
    assert rows[str(data['theirs'].id)]['staff'] == 'Maria Cashier'
    assert rows[str(data['waiting'].id)]['staff'] == '-'


def test_cashier_sees_only_orders_they_own(data, cashier_user, period):
    page = orders_history(period, cashier_user)
    ids = {r['id'] for r in page.rows}
    assert ids == {str(data['mine'].id), str(data['acted_only'].id)}
    assert page.kpi['orders'] == 2


def test_cashier_never_sees_orders_waiting_for_confirmation(data, cashier_two, period):
    # The waiting order sits on cashier_two's own table but has no owner yet.
    ids = {r['id'] for r in orders_history(period, cashier_two).rows}
    assert str(data['waiting'].id) not in ids
    assert ids == {str(data['theirs'].id)}


def test_order_filters_status_source_and_cashier(data, admin_user, cashier_two, period):
    by_status = orders_history(period, admin_user, HistoryFilters(status=OrderStatus.REJECTED))
    assert [r['id'] for r in by_status.rows] == [str(data['theirs'].id)]

    by_cashier = orders_history(period, admin_user, HistoryFilters(cashier=cashier_two.id))
    assert [r['id'] for r in by_cashier.rows] == [str(data['theirs'].id)]

    assert orders_history(period, admin_user, HistoryFilters(source='CASHIER')).total == 0


def test_cashier_filter_cannot_widen_scope(data, cashier_user, cashier_two, period):
    page = orders_history(period, cashier_user, HistoryFilters(cashier=cashier_two.id))
    assert str(data['theirs'].id) not in {r['id'] for r in page.rows}


@pytest.mark.parametrize('term,key', [
    ('ORD-T', None),
    ('meja 02', 'theirs'),
    ('T-02', 'theirs'),
    ('saboko', 'mine'),
    ("batar", 'theirs'),
])
def test_order_search_matches_code_table_and_menu(data, admin_user, period, term, key):
    page = orders_history(period, admin_user, HistoryFilters(q=term))
    if key is None:
        assert page.total == 4
    else:
        assert str(data[key].id) in {r['id'] for r in page.rows}
        assert page.total >= 1


def test_order_search_without_match_is_empty(data, admin_user, period):
    page = orders_history(period, admin_user, HistoryFilters(q='zzzz'))
    assert page.total == 0 and page.rows == [] and page.pages == 1 and page.kpi['average'] == Decimal('0.00')


def test_order_sorting_by_total_and_items(data, admin_user, period):
    asc = [r['grand_total'] for r in orders_history(period, admin_user, sort='total').rows]
    assert asc == sorted(asc)
    desc = [r['item_count'] for r in orders_history(period, admin_user, sort='-items').rows]
    assert desc == sorted(desc, reverse=True)


def test_order_pagination_and_clamping(restaurant, active_session, admin_user, today, period):
    for i in range(7):
        make_order(restaurant, active_session, OrderStatus.COMPLETED, '1.00', day=today, hour=8, minute=i)

    first = orders_history(period, admin_user, page=1, page_size=3)
    last = orders_history(period, admin_user, page=99, page_size=3)
    assert (first.total, first.pages, first.page, len(first.rows)) == (7, 3, 1, 3)
    assert (last.page, len(last.rows)) == (3, 1)
    seen = [r['id'] for p in (1, 2, 3) for r in orders_history(period, admin_user, page=p, page_size=3).rows]
    assert len(seen) == len(set(seen)) == 7


def test_orders_use_dili_midnight(restaurant, active_session, admin_user, today):
    late = make_order(restaurant, active_session, OrderStatus.COMPLETED, '1.00', day=today - timedelta(days=1),
                      hour=23, minute=30)
    early = make_order(restaurant, active_session, OrderStatus.COMPLETED, '1.00', day=today, hour=0, minute=10)

    now = {r['id'] for r in orders_history(Period(today, today), admin_user).rows}
    before = {r['id'] for r in orders_history(Period(today - timedelta(days=1), today - timedelta(days=1)), admin_user).rows}
    assert now == {str(early.id)}
    assert before == {str(late.id)}


# ----------------------------------------------------------------------------- transactions

def test_admin_sees_completed_and_voided_transactions(data, admin_user, period):
    page = transactions_history(period, admin_user)
    assert page.total == 3
    assert page.kpi['transactions'] == 3
    assert page.kpi['collected'] == Decimal('50.00')
    assert page.kpi['average'] == Decimal('25.00')
    assert page.kpi['voided'] == 1


def test_cashier_sees_only_transactions_they_received(data, cashier_user, period):
    page = transactions_history(period, cashier_user)
    assert [r['id'] for r in page.rows] == [str(data['pay_mine'].id)]
    assert page.kpi['collected'] == Decimal('20.00')


def test_transaction_filters(data, admin_user, cashier_two, period):
    cash = {r['id'] for r in transactions_history(period, admin_user, HistoryFilters(method='CASH')).rows}
    assert cash == {str(data['pay_mine'].id), str(data['pay_void'].id)}
    card = transactions_history(period, admin_user, HistoryFilters(method='MANUAL_OTHER'))
    assert [r['id'] for r in card.rows] == [str(data['pay_theirs'].id)]
    assert [r['id'] for r in transactions_history(period, admin_user, HistoryFilters(status='VOIDED')).rows] \
        == [str(data['pay_void'].id)]
    assert transactions_history(period, admin_user, HistoryFilters(cashier=cashier_two.id)).total == 2


def test_transaction_search_by_code_table_and_cashier(data, admin_user, cashier_user, period):
    code = data['pay_theirs'].payment_code
    assert [r['id'] for r in transactions_history(period, admin_user, HistoryFilters(q=code)).rows] \
        == [str(data['pay_theirs'].id)]
    assert transactions_history(period, admin_user, HistoryFilters(q='maria')).total == 2
    # A cashier cannot probe other people's names through the search box.
    assert transactions_history(period, cashier_user, HistoryFilters(q='maria')).total == 0


def test_transaction_sorting_and_row_values(data, admin_user, period):
    rows = transactions_history(period, admin_user, sort='-amount').rows
    assert [r['amount'] for r in rows] == [Decimal('30.00'), Decimal('20.00'), Decimal('7.00')]
    row = next(r for r in rows if r['id'] == str(data['pay_mine'].id))
    assert row['cashier'] == 'cashier_test'
    assert row['table'] == 'Meja 01'


def test_transactions_use_dili_midnight(restaurant, active_session, cashier_user, admin_user, today):
    late = make_payment(restaurant, active_session, cashier_user, '4.00', paid_day=today - timedelta(days=1),
                        hour=23, minute=30)
    early = make_payment(restaurant, active_session, cashier_user, '6.00', paid_day=today, hour=0, minute=10)
    assert [r['id'] for r in transactions_history(Period(today, today), admin_user).rows] == [str(early.id)]
    yesterday = today - timedelta(days=1)
    assert [r['id'] for r in transactions_history(Period(yesterday, yesterday), admin_user).rows] == [str(late.id)]


# ----------------------------------------------------------------------------- details

def test_order_detail_has_items_money_timeline_and_payments(data, admin_user):
    d = order_detail(data['mine'].id, admin_user, 'en')
    assert d['code'] == data['mine'].order_code
    assert d['status'] == {'code': 'COMPLETED', 'label': 'Completed', 'tone': 'success'}
    assert [i['name'] for i in d['items']] == ['Ikan Saboko', 'Teh']
    assert d['items'][0]['subtotal'] == '$13.00'
    assert d['money']['total'] == '$20.00'
    assert [t['to']['code'] for t in d['timeline']] == ['CONFIRMED']
    assert d['timeline'][0]['actor'] == 'cashier_test'
    assert d['table'] == 'Meja 01'
    assert [p['code'] for p in d['payments']] == [data['pay_mine'].payment_code]


def test_order_detail_is_translated(data, admin_user):
    assert order_detail(data['theirs'].id, admin_user, 'id')['status']['label'] == 'Ditolak'
    assert order_detail(data['theirs'].id, admin_user, 'pt')['status']['label'] == 'Rejeitado'
    assert order_detail(data['theirs'].id, admin_user, 'tet')['status']['label'] == 'Rekuza'


def test_scoped_cashier_gets_not_found_outside_scope(data, cashier_user):
    with pytest.raises(HistoryNotFound):
        order_detail(data['theirs'].id, cashier_user)
    with pytest.raises(HistoryNotFound):
        order_detail(data['waiting'].id, cashier_user)
    with pytest.raises(HistoryNotFound):
        transaction_detail(data['pay_theirs'].id, cashier_user)


def test_detail_rejects_malformed_and_unknown_ids(data, admin_user):
    for bad in ('nope', '', uuid.uuid4()):
        with pytest.raises(HistoryNotFound):
            order_detail(bad, admin_user)
        with pytest.raises(HistoryNotFound):
            transaction_detail(bad, admin_user)


def test_order_detail_only_lists_the_cashiers_own_payments(restaurant, session_two, cashier_user, cashier_two, today):
    order = make_order(restaurant, session_two, OrderStatus.COMPLETED, '9.00', day=today, confirmed_by=cashier_user)
    make_payment(restaurant, session_two, cashier_two, '9.00', paid_day=today)
    assert order_detail(order.id, cashier_user)['payments'] == []


def test_transaction_detail_carries_receipt_for_completed_payments(data, admin_user):
    d = transaction_detail(data['pay_mine'].id, admin_user, 'en')
    assert d['status']['code'] == 'COMPLETED'
    assert d['method'] == 'Cash'
    assert d['cashier'] == 'cashier_test'
    assert d['receipt']['payment_code'] == data['pay_mine'].payment_code
    assert d['receipt']['table_name'] == 'Meja 01'
    assert {i['name'] for i in d['receipt']['items']} == {'Ikan Saboko', 'Teh'}


def test_voided_transaction_detail_shows_reason_and_has_no_receipt(data, admin_user):
    d = transaction_detail(data['pay_void'].id, admin_user, 'en')
    assert d['status']['code'] == 'VOIDED'
    assert d['void_reason'] == 'Salah input'
    assert d['voided_by'] == 'admin_test'
    assert d['receipt'] is None


# ----------------------------------------------------------------------------- display

def test_page_display_is_translated_and_complete(data, admin_user, period):
    shown = page_display(orders_history(period, admin_user), 'id', scoped=False)
    assert [c['key'] for c in shown['columns']] == ['time', 'code', 'table', 'status', 'items', 'total', 'staff']
    assert shown['columns'][0]['label'] == 'Waktu'
    assert shown['scope_own'] is False
    row = next(r for r in shown['rows'] if r['id'] == str(data['mine'].id))
    assert row['status']['label'] == 'Selesai'
    assert row['total'] == '$20.00'
    assert shown['kpi'][1] == {'label': 'Nilai terjual', 'value': '$20.00'}

    tx = page_display(transactions_history(period, admin_user), 'en', scoped=False)
    assert [c['key'] for c in tx['columns']][-1] == 'status'
    row = next(r for r in tx['rows'] if r['id'] == str(data['pay_mine'].id))
    assert row['method'] == 'Cash'
    assert row['tendered'].count('$') == 2


# ----------------------------------------------------------------------------- performance

def test_history_queries_do_not_grow_with_row_count(restaurant, active_session, admin_user, cashier_user, today,
                                                   period, django_assert_max_num_queries):
    for i in range(30):
        o = make_order(restaurant, active_session, OrderStatus.COMPLETED, '5.00', day=today, minute=i,
                       confirmed_by=cashier_user)
        add_item(o, f'Menu {i}', 2, '2.50')
        make_history(o, OrderStatus.CONFIRMED, cashier_user, day=today)
        make_payment(restaurant, active_session, cashier_user, '5.00', paid_day=today, minute=i)

    with django_assert_max_num_queries(4):
        page = orders_history(period, admin_user, page_size=30)
        assert len(page.rows) == 30
    with django_assert_max_num_queries(4):
        page = transactions_history(period, admin_user, page_size=30)
        assert len(page.rows) == 30
    with django_assert_max_num_queries(4):
        orders_history(period, cashier_user, HistoryFilters(q='menu'), page_size=30)
