"""Report builders: periods, definitions of "sale", scoping rules for cashiers."""
from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.catalog.models import Category, MenuItem
from apps.ordering.models import OrderStatus
from apps.payments.models import PaymentMethod, PaymentStatus
from apps.reporting.reports import (
    Label, Period, ReportError, build_report, cashiers_report, daily_sales_report, exceptions_report,
    menu_sales_report, parse_period, payment_methods_report, table_usage_report,
)
from reporting_helpers import add_item, make_history, make_order, make_payment


# ----------------------------------------------------------------------------- periods

TODAY = date(2026, 11, 25)


def test_preset_today():
    p = parse_period('today', today=TODAY)
    assert (p.start, p.end) == (TODAY, TODAY)


def test_preset_7days_includes_today():
    p = parse_period('7days', today=TODAY)
    assert (p.start, p.end) == (date(2026, 11, 19), TODAY)


def test_preset_month_starts_on_first():
    p = parse_period('month', today=TODAY)
    assert (p.start, p.end) == (date(2026, 11, 1), TODAY)


def test_default_preset_is_today():
    assert parse_period(None, today=TODAY).start == TODAY


def test_custom_range():
    p = parse_period('custom', '2026-10-01', '2026-10-31', today=TODAY)
    assert (p.start, p.end) == (date(2026, 10, 1), date(2026, 10, 31))


@pytest.mark.parametrize('start,end,code', [
    ('2026-13-01', '2026-10-31', 'invalid_date'),
    ('2026-10-01', 'abc', 'invalid_date'),
    ('', '', 'invalid_date'),
    (None, None, 'invalid_date'),
    ('2026-10-31', '2026-10-01', 'end_before_start'),
    ('2025-01-01', '2026-11-25', 'range_too_long'),
])
def test_custom_range_errors(start, end, code):
    with pytest.raises(ReportError) as exc:
        parse_period('custom', start, end, today=TODAY)
    assert exc.value.code == code


def test_custom_range_allows_exactly_366_days():
    p = parse_period('custom', '2026-01-01', '2027-01-01', today=TODAY)
    assert (p.end - p.start).days + 1 == 366


def test_unknown_preset_is_rejected():
    with pytest.raises(ReportError):
        parse_period('yesterday', today=TODAY)


# ----------------------------------------------------------------------------- fixtures

@pytest.fixture
def today():
    return timezone.localdate()


@pytest.fixture
def period_today(today):
    return Period(today, today)


# ----------------------------------------------------------------------------- daily sales

def test_daily_sales_groups_by_local_day_and_excludes_void(restaurant, active_session, cashier_user, today):
    d1, d2 = today - timedelta(days=1), today
    make_payment(restaurant, active_session, cashier_user, '10.00', paid_day=d1)
    make_payment(restaurant, active_session, cashier_user, '5.50', paid_day=d1, hour=18)
    make_payment(restaurant, active_session, cashier_user, '20.00', paid_day=d2)
    make_payment(restaurant, active_session, cashier_user, '999.00', paid_day=d2, status=PaymentStatus.VOIDED)

    report = daily_sales_report(Period(d1, d2))

    assert report.rows == [
        [d1, 2, Decimal('15.50'), Decimal('7.75')],
        [d2, 1, Decimal('20.00'), Decimal('20.00')],
    ]
    values = {s.label_key: s.value for s in report.summary}
    assert values['rpt_sum_total_sales'] == Decimal('35.50')
    assert values['rpt_sum_transactions'] == 3
    assert values['rpt_sum_average'] == Decimal('11.83')


def test_daily_sales_respects_midnight_in_dili(restaurant, active_session, cashier_user, today):
    yesterday = today - timedelta(days=1)
    make_payment(restaurant, active_session, cashier_user, '4.00', paid_day=yesterday, hour=23, minute=30)
    make_payment(restaurant, active_session, cashier_user, '6.00', paid_day=today, hour=0, minute=10)

    report = daily_sales_report(Period(today, today))
    assert report.rows == [[today, 1, Decimal('6.00'), Decimal('6.00')]]


def test_daily_sales_empty_period_has_zero_summary(db, period_today):
    report = daily_sales_report(period_today)
    assert report.rows == []
    values = {s.label_key: s.value for s in report.summary}
    assert values == {
        'rpt_sum_total_sales': Decimal('0.00'), 'rpt_sum_transactions': 0, 'rpt_sum_average': Decimal('0.00'),
    }


def test_daily_sales_outside_period_is_ignored(restaurant, active_session, cashier_user, today):
    make_payment(restaurant, active_session, cashier_user, '9.00', paid_day=today - timedelta(days=10))
    assert daily_sales_report(Period(today, today)).rows == []


# ----------------------------------------------------------------------------- menu sales

def test_menu_sales_counts_only_sold_statuses_and_groups_by_category(
        restaurant, active_session, today, period_today, menu_item_fish, menu_item_corn, category_main):
    drinks = Category.objects.create(restaurant=restaurant, name='Minuman', slug='drinks', is_active=True)
    tea = MenuItem.objects.create(restaurant=restaurant, category=drinks, name='Teh', slug='teh', price=Decimal('1.00'))

    o1 = make_order(restaurant, active_session, OrderStatus.COMPLETED, '20.00', day=today)
    add_item(o1, 'Ikan Saboko', 2, '6.50', menu_item_fish)
    add_item(o1, 'Teh', 3, '1.00', tea)
    o2 = make_order(restaurant, active_session, OrderStatus.SERVED, '6.50', day=today)
    add_item(o2, 'Ikan Saboko', 1, '6.50', menu_item_fish)
    add_item(o2, "Batar Da'an", 4, '3.50', menu_item_corn)
    rejected = make_order(restaurant, active_session, OrderStatus.REJECTED, '50.00', day=today)
    add_item(rejected, 'Ikan Saboko', 10, '6.50', menu_item_fish)
    waiting = make_order(restaurant, active_session, OrderStatus.WAITING_CASHIER_CONFIRMATION, '3.50', day=today)
    add_item(waiting, "Batar Da'an", 1, '3.50', menu_item_corn)

    report = menu_sales_report(period_today)

    assert report.rows == [
        ['Makanan Utama', "Batar Da'an", 4, Decimal('14.00')],
        ['Makanan Utama', 'Ikan Saboko', 3, Decimal('19.50')],
        ['Minuman', 'Teh', 3, Decimal('3.00')],
    ]
    values = {s.label_key: s.value for s in report.summary}
    assert values['rpt_sum_items_sold'] == 10
    assert values['rpt_sum_revenue'] == Decimal('36.50')


def test_menu_sales_item_without_menu_link_is_uncategorized(restaurant, active_session, today, period_today):
    order = make_order(restaurant, active_session, OrderStatus.COMPLETED, '2.00', day=today)
    add_item(order, 'Menu Lama', 2, '1.00', menu_item=None)

    report = menu_sales_report(period_today)
    assert report.rows == [[Label('rpt_uncategorized'), 'Menu Lama', 2, Decimal('2.00')]]


# ----------------------------------------------------------------------------- payment methods

def test_payment_methods_percentages_add_up_to_100(restaurant, active_session, cashier_user, today, period_today):
    make_payment(restaurant, active_session, cashier_user, '10.00', paid_day=today, method=PaymentMethod.CASH)
    make_payment(restaurant, active_session, cashier_user, '10.00', paid_day=today, method=PaymentMethod.CASH)
    make_payment(restaurant, active_session, cashier_user, '10.00', paid_day=today, method=PaymentMethod.MANUAL_OTHER)

    report = payment_methods_report(period_today)

    rows = {r[0].key: r for r in report.rows}
    assert rows['rpt_method_CASH'][1:3] == [2, Decimal('20.00')]
    assert rows['rpt_method_MANUAL_OTHER'][1:3] == [1, Decimal('10.00')]
    assert sum(r[3] for r in report.rows) == Decimal('100.0')
    assert {s.label_key: s.value for s in report.summary}['rpt_sum_total_sales'] == Decimal('30.00')


def test_payment_methods_with_no_sales_lists_both_methods_at_zero(db, period_today):
    report = payment_methods_report(period_today)
    assert [r[0].key for r in report.rows] == ['rpt_method_CASH', 'rpt_method_MANUAL_OTHER']
    assert all(r[1] == 0 and r[2] == Decimal('0.00') and r[3] == Decimal('0.0') for r in report.rows)


# ----------------------------------------------------------------------------- cashiers

@pytest.fixture
def two_cashier_sales(restaurant, active_session, session_two, cashier_user, cashier_two, today):
    make_payment(restaurant, active_session, cashier_user, '10.00', paid_day=today)
    make_payment(restaurant, active_session, cashier_user, '5.00', paid_day=today)
    make_payment(restaurant, session_two, cashier_two, '30.00', paid_day=today)
    make_payment(restaurant, session_two, cashier_two, '7.00', paid_day=today, status=PaymentStatus.VOIDED,
                 void_reason='Salah input')


def test_cashiers_report_admin_sees_everyone(two_cashier_sales, admin_user, period_today):
    report = cashiers_report(period_today, admin_user)

    assert report.scoped_to_user is False
    assert report.rows == [
        ['Maria Cashier', 1, Decimal('30.00'), 1],
        ['cashier_test', 2, Decimal('15.00'), 0],
    ]
    assert {s.label_key: s.value for s in report.summary}['rpt_sum_total_sales'] == Decimal('45.00')


def test_cashiers_report_cashier_sees_only_own_rows(two_cashier_sales, cashier_user, period_today):
    report = cashiers_report(period_today, cashier_user)

    assert report.scoped_to_user is True
    assert report.rows == [['cashier_test', 2, Decimal('15.00'), 0]]
    assert {s.label_key: s.value for s in report.summary}['rpt_sum_total_sales'] == Decimal('15.00')


# ----------------------------------------------------------------------------- exceptions

@pytest.fixture
def exception_data(restaurant, active_session, session_two, cashier_user, cashier_two, admin_user, today):
    rejected = make_order(restaurant, active_session, OrderStatus.REJECTED, '12.00', day=today)
    make_history(rejected, OrderStatus.REJECTED, cashier_user, 'Stok habis', day=today)
    cancelled = make_order(restaurant, session_two, OrderStatus.CANCELLED, '8.00', day=today)
    make_history(cancelled, OrderStatus.CANCELLED, cashier_two, 'Pelanggan pergi', day=today)
    make_payment(restaurant, session_two, cashier_two, '30.00', paid_day=today, status=PaymentStatus.VOIDED,
                 void_reason='Salah input', voided_by=admin_user)
    return rejected, cancelled


def test_exceptions_admin_sees_all_kinds(exception_data, admin_user, period_today):
    report = exceptions_report(period_today, admin_user)

    kinds = sorted(r[1].key for r in report.rows)
    assert kinds == ['rpt_kind_cancelled', 'rpt_kind_rejected', 'rpt_kind_voided']
    values = {s.label_key: s.value for s in report.summary}
    assert values == {'rpt_sum_rejected': 1, 'rpt_sum_cancelled': 1, 'rpt_sum_voided': 1}

    rejected_row = next(r for r in report.rows if r[1].key == 'rpt_kind_rejected')
    assert rejected_row[3] == 'Meja 01'
    assert rejected_row[4] == Decimal('12.00')
    assert rejected_row[5] == 'Stok habis'
    assert rejected_row[6] == 'cashier_test'


def test_exceptions_cashier_sees_own_actions_and_payments_they_received(
        exception_data, cashier_user, cashier_two, period_today):
    first = exceptions_report(period_today, cashier_user)
    assert [r[1].key for r in first.rows] == ['rpt_kind_rejected']
    assert first.scoped_to_user is True

    second = exceptions_report(period_today, cashier_two)
    assert sorted(r[1].key for r in second.rows) == ['rpt_kind_cancelled', 'rpt_kind_voided']


def test_exceptions_empty_period(db, admin_user, period_today):
    report = exceptions_report(period_today, admin_user)
    assert report.rows == []
    assert all(s.value == 0 for s in report.summary)


# ----------------------------------------------------------------------------- table usage

def test_table_usage_counts_sessions_guests_and_sales(
        restaurant, active_session, session_two, cashier_user, cashier_two, today, period_today):
    make_payment(restaurant, active_session, cashier_user, '10.00', paid_day=today)
    make_payment(restaurant, session_two, cashier_two, '25.00', paid_day=today)
    make_payment(restaurant, session_two, cashier_two, '5.00', paid_day=today)

    report = table_usage_report(period_today)

    assert report.rows == [
        ['Meja 01', 1, 2, Decimal('10.00')],
        ['Meja 02', 1, 3, Decimal('30.00')],
    ]
    values = {s.label_key: s.value for s in report.summary}
    assert values == {
        'rpt_sum_sessions': 2, 'rpt_sum_guests': 5, 'rpt_sum_total_sales': Decimal('40.00'),
    }


def test_build_report_rejects_unknown_type(period_today):
    with pytest.raises(ReportError) as exc:
        build_report('nope', period_today)
    assert exc.value.code == 'invalid_report'
