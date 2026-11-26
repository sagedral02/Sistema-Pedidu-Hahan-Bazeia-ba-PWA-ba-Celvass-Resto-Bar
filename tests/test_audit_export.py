"""The Audit tab downloads the audit log (not the previous tab's report)."""
from pathlib import Path

import pytest
from django.test import Client

from apps.audit.services import log_audit_event

ROOT = Path(__file__).resolve().parent.parent
EXPORT = '/reports/export/'


def login(user):
    c = Client()
    c.force_login(user)
    return c


@pytest.fixture
def events(admin_user):
    log_audit_event(actor_user=admin_user, actor_role='ADMIN', action='MENU_UPDATE', entity_type='MenuItem', entity_id='abc123')
    log_audit_event(actor_user=None, actor_role='', action='SESSION_SETTLED', entity_type='TableSession', entity_id='zzz999')


@pytest.mark.django_db
def test_audit_csv_has_audit_columns_and_rows(admin_user, events):
    res = login(admin_user).get(EXPORT, {'type': 'audit', 'format': 'csv', 'lang': 'en'})
    assert res.status_code == 200
    body = b''.join(res.streaming_content).decode('utf-8-sig')
    header = body.splitlines()[0]
    assert 'Action' in header and 'Entity ID' in header
    assert 'MENU_UPDATE' in body and 'SESSION_SETTLED' in body
    assert 'laporan-audit-' in res['Content-Disposition']


@pytest.mark.django_db
def test_audit_export_keeps_the_filters(admin_user, events):
    res = login(admin_user).get(EXPORT, {'type': 'audit', 'format': 'csv', 'entity': 'MenuItem'})
    body = b''.join(res.streaming_content).decode('utf-8-sig')
    assert 'MENU_UPDATE' in body and 'SESSION_SETTLED' not in body
    res = login(admin_user).get(EXPORT, {'type': 'audit', 'format': 'csv', 'q': 'settled'})
    body = b''.join(res.streaming_content).decode('utf-8-sig')
    assert 'SESSION_SETTLED' in body and 'MENU_UPDATE' not in body


@pytest.mark.django_db
def test_audit_pdf(admin_user, events):
    res = login(admin_user).get(EXPORT, {'type': 'audit', 'format': 'pdf', 'lang': 'id'})
    assert res.status_code == 200 and res['Content-Type'] == 'application/pdf'
    assert res.content[:4] == b'%PDF'


@pytest.mark.django_db
def test_cashier_cannot_export_the_audit_log(cashier_user, events):
    assert login(cashier_user).get(EXPORT, {'type': 'audit', 'format': 'csv'}).status_code == 403


def test_audit_tab_points_downloads_at_the_audit_export():
    js = (ROOT / 'static' / 'js' / 'reports-audit.js').read_text(encoding='utf-8')
    assert "p.set('type', 'audit')" in js and 'Page.setDownloads' in js
    shell = (ROOT / 'static' / 'js' / 'reports.js').read_text(encoding='utf-8')
    assert "el.downloads.hidden = name === 'audit'" not in shell
    css = (ROOT / 'static' / 'css' / 'style.css').read_text(encoding='utf-8')
    assert '.d-flex[hidden]' in css
