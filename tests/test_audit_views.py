"""Audit endpoints of the Reports page: admin only, localized, bounded, and secret-free."""
import uuid

import pytest
from django.test import Client
from django.utils import timezone

from apps.audit.models import AuditEvent

LIST = '/reports/audit/'


def login(user):
    client = Client()
    client.force_login(user)
    return client


@pytest.fixture
def events(admin_user, cashier_user):
    first = AuditEvent.objects.create(
        action='category.update', entity_type='Category', entity_id='cat-1', actor_user=admin_user, actor_role='ADMIN',
        before_data={'name': 'Old', 'password': 'p-old'}, after_data={'name': 'New', 'password': 'p-new'},
        ip_hash_or_ip='10.0.0.9', user_agent='UA/1.0', request_id='req-9',
    )
    second = AuditEvent.objects.create(action='table.create', entity_type='Table', entity_id='t-1', actor_user=cashier_user)
    system = AuditEvent.objects.create(action='outbox.retry', entity_type='Outbox', entity_id='o-1', actor_role='SYSTEM')
    return {'first': first, 'second': second, 'system': system}


# ----------------------------------------------------------------------------- access

@pytest.mark.django_db
@pytest.mark.parametrize('url', [LIST, LIST + 'x/'])
def test_anonymous_users_are_sent_to_login(url):
    res = Client().get(url)
    assert res.status_code == 302 and res['Location'].startswith('/login/')


@pytest.mark.django_db
@pytest.mark.parametrize('url', [LIST, LIST + 'x/'])
def test_cashier_and_kitchen_staff_get_403(cashier_user, kitchen_user, url):
    assert login(cashier_user).get(url).status_code == 403
    assert login(kitchen_user).get(url).status_code == 403


@pytest.mark.django_db
def test_audit_is_get_only(admin_user):
    assert login(admin_user).post(LIST).status_code == 405


@pytest.mark.django_db
def test_audit_can_be_exported_by_admins(admin_user, events):
    # The audit tab has its own CSV/PDF export (see test_audit_export.py).
    res = login(admin_user).get('/reports/export/', {'type': 'audit', 'format': 'csv'})
    assert res.status_code == 200


# ----------------------------------------------------------------------------- list

@pytest.mark.django_db
def test_list_shape_and_translation(admin_user, events):
    res = login(admin_user).get(LIST, {'lang': 'en'})
    assert res.status_code == 200 and res['Cache-Control'] == 'no-store'
    body = res.json()
    assert body['kind'] == 'audit' and body['total'] == 3 and body['page'] == 1 and body['pages'] == 1
    assert [c['key'] for c in body['columns']] == ['time', 'action', 'actor', 'entity']
    assert [c['label'] for c in body['columns']] == ['Time', 'Action', 'Actor', 'Entity']
    assert body['kpi'] == [
        {'label': 'Events', 'value': '3'}, {'label': 'Actors', 'value': '2'}, {'label': 'Entity types', 'value': '3'}]
    assert sorted(body['entity_types']) == ['Category', 'Outbox', 'Table']
    actors = {r['action']: r['actor'] for r in body['rows']}
    assert actors == {'category.update': 'admin_test', 'table.create': 'cashier_test', 'outbox.retry': 'SYSTEM'}


@pytest.mark.django_db
def test_system_actor_label_follows_language(admin_user):
    AuditEvent.objects.create(action='job.run', entity_type='Job', entity_id='1')
    labels = {
        lang: login(admin_user).get(LIST, {'lang': lang}).json()['rows'][0]['actor']
        for lang in ('tet', 'pt', 'en', 'id')
    }
    assert labels == {'tet': 'Sistema', 'pt': 'Sistema', 'en': 'System', 'id': 'Sistem'}


@pytest.mark.django_db
def test_filters_sort_and_paging_reach_the_selector(admin_user, events):
    client = login(admin_user)
    assert [r['action'] for r in client.get(LIST, {'q': 'table'}).json()['rows']] == ['table.create']
    assert [r['action'] for r in client.get(LIST, {'entity': 'Outbox'}).json()['rows']] == ['outbox.retry']
    assert [r['action'] for r in client.get(LIST, {'sort': 'action'}).json()['rows']] == [
        'category.update', 'outbox.retry', 'table.create']
    paged = client.get(LIST, {'page_size': 2, 'page': 2}).json()
    assert (paged['total'], paged['pages'], paged['page'], len(paged['rows'])) == (3, 2, 2, 1)


@pytest.mark.django_db
def test_unknown_sort_is_ignored(admin_user, events):
    res = login(admin_user).get(LIST, {'sort': 'actor_user__password; DROP'})
    assert res.status_code == 200 and res.json()['sort'] == '-time'


@pytest.mark.django_db
@pytest.mark.parametrize('params,code', [
    ({'preset': 'custom', 'start': 'x', 'end': 'y'}, 'INVALID_DATE'),
    ({'preset': 'custom', 'start': '2026-02-01', 'end': '2026-01-01'}, 'END_BEFORE_START'),
    ({'preset': 'custom', 'start': '2020-01-01', 'end': '2026-01-01'}, 'RANGE_TOO_LONG'),
])
def test_invalid_period_gives_localized_errors(admin_user, params, code):
    messages = {}
    for lang in ('tet', 'pt', 'en', 'id'):
        res = login(admin_user).get(LIST, {**params, 'lang': lang})
        assert res.status_code == 400
        assert res.json()['error']['code'] == code
        messages[lang] = res.json()['error']['message']
    assert len(set(messages.values())) == 4


@pytest.mark.django_db
def test_list_works_in_every_language_without_raw_keys(admin_user, events):
    client = login(admin_user)
    for lang in ('tet', 'pt', 'en', 'id'):
        body = client.get(LIST, {'lang': lang}).json()
        assert all(not c['label'].startswith('rpt_') for c in body['columns'])
        assert all(not k['label'].startswith('rpt_') for k in body['kpi'])


@pytest.mark.django_db
def test_events_outside_the_period_are_left_out(admin_user, events):
    yesterday = (timezone.localdate() - timezone.timedelta(days=3)).isoformat()
    body = login(admin_user).get(LIST, {'preset': 'custom', 'start': yesterday, 'end': yesterday}).json()
    assert body['total'] == 0 and body['rows'] == []


# ----------------------------------------------------------------------------- detail

@pytest.mark.django_db
def test_detail_shows_changes_without_secrets(admin_user, events):
    res = login(admin_user).get(f"{LIST}{events['first'].id}/", {'lang': 'en'})
    assert res.status_code == 200 and res['Cache-Control'] == 'no-store'
    body = res.json()
    assert body['actor'] == 'admin_test' and body['action'] == 'category.update'
    assert body['changes'] == [{'key': 'name', 'before': 'Old', 'after': 'New'}]
    assert (body['ip'], body['user_agent'], body['request_id']) == ('10.0.0.9', 'UA/1.0', 'req-9')
    assert 'p-old' not in res.content.decode() and 'p-new' not in res.content.decode()


@pytest.mark.django_db
def test_detail_of_a_system_event_uses_the_role(admin_user, events):
    body = login(admin_user).get(f"{LIST}{events['system'].id}/", {'lang': 'id'}).json()
    assert body['actor'] == 'SYSTEM' and body['changes'] == []


@pytest.mark.django_db
@pytest.mark.parametrize('bad', ['not-a-uuid', str(uuid.uuid4())])
def test_unknown_or_malformed_ids_are_404_and_localized(admin_user, bad):
    res = login(admin_user).get(f'{LIST}{bad}/', {'lang': 'pt'})
    assert res.status_code == 404
    assert res.json()['error'] == {'code': 'NOT_FOUND', 'message': 'Registo não encontrado.'}
