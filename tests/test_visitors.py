"""Visitor numbers: dine-in guests in the Overview and the Visitors report (take away is not a visit)."""
import csv
import io
import uuid
from datetime import timedelta

import pytest
from django.test import Client
from django.utils import timezone
from rest_framework.test import APIClient

from apps.reporting.overview import build_overview
from apps.reporting.reports import Period, build_report
from apps.tables.models import SessionStatus, TableSession


def login(user):
    c = Client()
    c.force_login(user)
    return c


def takeaway(cashier_user, item):
    c = APIClient()
    c.force_authenticate(user=cashier_user)
    res = c.post('/api/v1/cashier/orders/create/', {
        'destination': 'TAKEAWAY', 'customer_name': 'Ana',
        'items': [{'menu_item_id': str(item.id), 'quantity': 1}]},
        format='json', HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()))
    assert res.status_code == 201, res.content
    return res


@pytest.fixture
def visits(active_session, session_two, cashier_user, menu_item_fish):
    """Today: 2 + 3 guests at two tables and one take-away order."""
    takeaway(cashier_user, menu_item_fish)
    return active_session, session_two


@pytest.mark.django_db
def test_report_counts_dine_in_guests_and_takeaway_orders(visits):
    today = timezone.localdate()
    report = build_report('visitors', Period(today, today, 'today'))
    assert [c.label_key for c in report.columns] == [
        'rpt_col_date', 'rpt_col_sessions', 'rpt_col_guests', 'rpt_col_avg_guests', 'rpt_col_takeaway_orders']
    assert len(report.rows) == 1
    day, sessions, guests, avg, ta = report.rows[0]
    assert (day, sessions, guests, str(avg), ta) == (today, 2, 5, '2.5', 1)
    summary = {s.label_key: s.value for s in report.summary}
    assert summary['rpt_sum_guests'] == 5 and summary['rpt_sum_sessions'] == 2
    assert summary['rpt_sum_takeaway_orders'] == 1
    assert report.drills[0] == {'kind': 'orders', 'start': today.isoformat(), 'end': today.isoformat()}


@pytest.mark.django_db
def test_cancelled_sessions_and_takeaway_sessions_are_not_visitors(visits, table_two):
    a, b = visits
    TableSession.objects.filter(id=b.id).update(status=SessionStatus.CANCELLED)
    today = timezone.localdate()
    report = build_report('visitors', Period(today, today, 'today'))
    assert report.rows[0][1:3] == [1, 2]
    takeaway_guests = TableSession.objects.filter(table__is_takeaway=True).count()
    assert takeaway_guests >= 1  # exists but is not counted


@pytest.mark.django_db
def test_overview_matches_the_report(visits, admin_user):
    today = timezone.localdate()
    report = build_report('visitors', Period(today, today, 'today'))
    summary = {s.label_key: s.value for s in report.summary}
    v = build_overview('today')['kpi']['visitors']
    assert v['value'] == summary['rpt_sum_guests'] == 5
    assert v['sessions'] == 2 and v['takeaway_orders'] == 1 and v['average_per_session'] == 2.5
    assert v['series']['granularity'] == 'hour' and sum(v['series']['values']) == 5
    assert v['previous'] == 0 and v['delta_pct'] is None

    body = login(admin_user).get('/api/v1/admin/overview/', {'range': '7d'}).json()
    data = body.get('data', body)
    week = data['kpi']['visitors']
    assert week['value'] == 5 and week['series']['granularity'] == 'day' and len(week['series']['values']) == 7
    assert week['series']['values'][-1] == 5


@pytest.mark.django_db
def test_previous_period_comparison(visits, table_one, cashier_user):
    yesterday = timezone.now() - timedelta(days=1)
    TableSession.objects.filter(id=visits[0].id).update(status=SessionStatus.CLOSED, opened_at=yesterday)
    v = build_overview('today')['kpi']['visitors']
    assert v['value'] == 3 and v['previous'] == 2 and v['delta_pct'] == 50.0


@pytest.mark.django_db
def test_visitors_csv_and_pdf_export(visits, admin_user):
    client = login(admin_user)
    res = client.get('/reports/export/', {'format': 'csv', 'type': 'visitors', 'lang': 'en'})
    assert res.status_code == 200
    rows = list(csv.reader(io.StringIO(res.content.decode('utf-8-sig'))))
    assert rows[0] == ['Date', 'Sessions', 'Guests', 'Avg guests/session', 'Take-away orders']
    assert rows[1][1:] == ['2', '5', '2.5', '1']
    pdf = client.get('/reports/export/', {'format': 'pdf', 'type': 'visitors', 'lang': 'id'})
    assert pdf.status_code == 200 and pdf.content.startswith(b'%PDF')


@pytest.mark.django_db
def test_visitors_report_is_listed_on_the_reports_page(admin_user):
    html = login(admin_user).get('/reports/').content.decode()
    assert '<option value="visitors" data-i18n="rpt_type_visitors">' in html
