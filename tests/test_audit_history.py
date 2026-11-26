"""Audit selectors: filters, search, whitelisted sort, pagination, Dili day boundaries, masking and diffs."""
import uuid
from datetime import timedelta

import pytest
from django.utils import timezone

from apps.audit.models import AuditEvent
from apps.audit.selectors import (
    AuditNotFound, MASK, MAX_PAGE_SIZE, audit_event_detail, audit_events_page, clamp_page_size, diff_snapshots,
    parse_audit_sort, redact,
)
from apps.reporting.reports import Period
from reporting_helpers import local_dt


@pytest.fixture
def today():
    return timezone.localdate()


@pytest.fixture
def period(today):
    return Period(today, today)


def make_event(action='category.update', entity_type='Category', entity_id='c1', actor=None, role=None,
               before=None, after=None, day=None, hour=12, minute=0, **extra):
    event = AuditEvent.objects.create(
        action=action, entity_type=entity_type, entity_id=entity_id, actor_user=actor, actor_role=role,
        before_data=before, after_data=after, **extra,
    )
    if day is not None:
        AuditEvent.objects.filter(pk=event.pk).update(created_at=local_dt(day, hour, minute))
    return event


# ----------------------------------------------------------------------------- parsing

def test_sort_is_whitelisted():
    assert parse_audit_sort('action') == 'action'
    assert parse_audit_sort('-actor') == '-actor'
    assert parse_audit_sort('created_at; DROP TABLE x') == '-time'
    assert parse_audit_sort(None) == '-time'


def test_page_size_is_clamped():
    assert clamp_page_size('50') == 50
    assert clamp_page_size('0') == 1
    assert clamp_page_size('100000') == MAX_PAGE_SIZE
    assert clamp_page_size('abc') == 20


# ----------------------------------------------------------------------------- masking

def test_redact_masks_sensitive_keys_at_any_depth():
    data = {
        'name': 'Maria', 'password': 'hunter2', 'Api_Token': 'abc',
        'nested': {'client_secret': 'x', 'ok': 1, 'deeper': [{'qr_token': 'zzz', 'label': 'a'}]},
        'list': [{'password_hash': 'h'}],
    }
    out = redact(data)
    assert out['name'] == 'Maria'
    assert out['password'] == MASK and out['Api_Token'] == MASK
    assert out['nested']['client_secret'] == MASK and out['nested']['ok'] == 1
    assert out['nested']['deeper'][0] == {'qr_token': MASK, 'label': 'a'}
    assert out['list'][0]['password_hash'] == MASK
    assert data['password'] == 'hunter2', 'the original must not be modified'


def test_diff_shows_only_changed_keys_and_masks_secrets():
    changes = diff_snapshots(
        {'name': 'A', 'price': '1.00', 'password': 'old'},
        {'name': 'B', 'price': '1.00', 'password': 'new', 'tags': ['x']},
    )
    by_key = {c['key']: c for c in changes}
    assert set(by_key) == {'name', 'tags[0]'}, 'masked values are equal, so the password key is not reported'
    assert by_key['name'] == {'key': 'name', 'before': 'A', 'after': 'B'}
    assert by_key['tags[0]'] == {'key': 'tags[0]', 'before': None, 'after': 'x'}
    assert 'hunter' not in str(changes)


def test_diff_masks_even_when_only_one_side_has_the_secret():
    changes = diff_snapshots(None, {'user': {'password': 'secret-value', 'name': 'N'}})
    assert {c['key']: c['after'] for c in changes} == {'user.password': MASK, 'user.name': 'N'}


def test_diff_handles_empty_and_null_snapshots():
    assert diff_snapshots(None, None) == []
    assert diff_snapshots({}, []) == []
    assert diff_snapshots({'a': None}, {'a': None}) == []
    assert diff_snapshots({'a': None}, {'a': 1}) == [{'key': 'a', 'before': 'null', 'after': '1'}]


def test_long_values_are_truncated():
    changes = diff_snapshots({'note': 'x' * 1000}, {'note': 'y'})
    assert len(changes[0]['before']) == 300 and changes[0]['before'].endswith('\u2026')


# ----------------------------------------------------------------------------- list

@pytest.mark.django_db
def test_page_lists_newest_first_with_actor_names(period, today, admin_user, cashier_two):
    make_event(action='a.first', actor=admin_user, day=today, hour=9)
    make_event(action='a.second', actor=cashier_two, day=today, hour=10)
    make_event(action='a.system', day=today, hour=11, role='SYSTEM')

    page = audit_events_page(period)
    assert [r['action'] for r in page['rows']] == ['a.system', 'a.second', 'a.first']
    assert [r['actor'] for r in page['rows']] == ['', 'Maria Cashier', 'admin_test']
    assert page['rows'][0]['actor_role'] == 'SYSTEM'
    assert page['kpi'] == {'events': 3, 'actors': 2, 'entities': 1}
    assert (page['total'], page['pages'], page['page']) == (3, 1, 1)


@pytest.mark.django_db
def test_search_matches_action_entity_actor_and_role(period, today, admin_user, cashier_two):
    make_event(action='category.update', entity_type='Category', entity_id='abc', actor=admin_user, day=today)
    make_event(action='table.delete', entity_type='Table', entity_id='t-99', actor=cashier_two, day=today)

    def found(q):
        return {r['action'] for r in audit_events_page(period, q=q)['rows']}

    assert found('category') == {'category.update'}
    assert found('T-99') == {'table.delete'}
    assert found('maria') == {'table.delete'}
    assert found('admin_test') == {'category.update'}
    assert found('zzz') == set()


@pytest.mark.django_db
def test_entity_filter_and_dropdown_values(period, today):
    make_event(entity_type='Category', day=today)
    make_event(entity_type='Table', day=today)
    make_event(entity_type='Order', day=today - timedelta(days=40))

    page = audit_events_page(period, entity='Table')
    assert [r['entity_type'] for r in page['rows']] == ['Table']
    assert page['entity_types'] == ['Category', 'Order', 'Table']


@pytest.mark.django_db
def test_sorting_by_action_ascending(period, today):
    for action in ('b.x', 'a.x', 'c.x'):
        make_event(action=action, day=today)
    assert [r['action'] for r in audit_events_page(period, sort='action')['rows']] == ['a.x', 'b.x', 'c.x']
    assert [r['action'] for r in audit_events_page(period, sort='-action')['rows']] == ['c.x', 'b.x', 'a.x']


@pytest.mark.django_db
def test_pagination_clamps_and_never_repeats_rows(period, today):
    for i in range(7):
        make_event(action=f'e.{i}', day=today, minute=i)
    first = audit_events_page(period, page=1, page_size=3)
    last = audit_events_page(period, page=99, page_size=3)
    assert (first['total'], first['pages'], len(first['rows'])) == (7, 3, 3)
    assert (last['page'], len(last['rows'])) == (3, 1)
    seen = [r['id'] for p in (1, 2, 3) for r in audit_events_page(period, page=p, page_size=3)['rows']]
    assert len(seen) == len(set(seen)) == 7


@pytest.mark.django_db
def test_events_use_the_dili_calendar_day(today):
    late = make_event(action='late', day=today - timedelta(days=1), hour=23, minute=30)
    early = make_event(action='early', day=today, hour=0, minute=10)
    assert [r['id'] for r in audit_events_page(Period(today, today))['rows']] == [str(early.id)]
    yesterday = today - timedelta(days=1)
    assert [r['id'] for r in audit_events_page(Period(yesterday, yesterday))['rows']] == [str(late.id)]


@pytest.mark.django_db
def test_empty_period(period):
    page = audit_events_page(period)
    assert page['rows'] == [] and page['pages'] == 1
    assert page['kpi'] == {'events': 0, 'actors': 0, 'entities': 0}


@pytest.mark.django_db
def test_list_query_count_does_not_grow_with_rows(period, today, admin_user, django_assert_max_num_queries):
    for i in range(30):
        make_event(action=f'bulk.{i}', actor=admin_user, day=today, minute=i, before={'a': 1}, after={'a': 2})
    with django_assert_max_num_queries(4):
        page = audit_events_page(period, page_size=30)
        assert len(page['rows']) == 30


# ----------------------------------------------------------------------------- detail

@pytest.mark.django_db
def test_detail_has_changes_and_request_context(admin_user):
    event = make_event(
        actor=admin_user, before={'name': 'Old', 'password': 'p1'}, after={'name': 'New', 'password': 'p2'},
        ip_hash_or_ip='10.0.0.1', user_agent='Mozilla', request_id='req-1',
    )
    detail = audit_event_detail(event.id)
    assert detail['actor'] == 'admin_test'
    assert detail['changes'] == [{'key': 'name', 'before': 'Old', 'after': 'New'}]
    assert (detail['ip'], detail['user_agent'], detail['request_id']) == ('10.0.0.1', 'Mozilla', 'req-1')
    assert 'p1' not in str(detail) and 'p2' not in str(detail)


@pytest.mark.django_db
def test_detail_without_snapshots_and_system_actor():
    detail = audit_event_detail(make_event(role='SYSTEM').id)
    assert detail['changes'] == [] and detail['actor'] == '' and detail['actor_role'] == 'SYSTEM'


@pytest.mark.django_db
@pytest.mark.parametrize('bad', ['nope', '', uuid.uuid4()])
def test_detail_rejects_malformed_and_unknown_ids(bad):
    with pytest.raises(AuditNotFound):
        audit_event_detail(bad)
