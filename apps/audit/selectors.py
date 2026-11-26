"""
Audit log queries for the Reports > Audit tab (admin only).

Time filters use [start, end) aware datetimes from `Period.bounds()` (Asia/Dili). Sorting is a whitelist.
Snapshots (`before_data` / `after_data`) are flattened into a per-key diff and any value stored under a
sensitive key (password, token, secret, qr_token) is masked before it leaves this module.
"""
import json
import math
import uuid
from typing import Any, Dict, List, Optional

from django.db.models import Count, Q

from .models import AuditEvent

DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100
MAX_ENTITY_TYPES = 50
MAX_DIFF_KEYS = 200
MAX_VALUE_CHARS = 300
MASK = '********'
SENSITIVE_MARKERS = ('password', 'token', 'secret')

AUDIT_SORTS = {
    'time': 'created_at',
    'action': 'action',
    'entity': 'entity_type',
    'actor': 'actor_user__username',
}


class AuditNotFound(Exception):
    """The audit event does not exist or the id is malformed."""


# --------------------------------------------------------------------------- parsing

def parse_audit_sort(raw: Optional[str]) -> str:
    """Whitelisted sort ('time' or '-time'); anything else falls back to newest first."""
    raw = (raw or '').strip()
    return raw if raw.lstrip('-') in AUDIT_SORTS else '-time'


def clamp_page_size(value, default: int = DEFAULT_PAGE_SIZE) -> int:
    try:
        size = int(value)
    except (TypeError, ValueError):
        size = default
    return min(max(size, 1), MAX_PAGE_SIZE)


# --------------------------------------------------------------------------- masking and diff

def _is_sensitive(key: Any) -> bool:
    lowered = str(key).lower()
    return any(marker in lowered for marker in SENSITIVE_MARKERS)


def redact(value: Any, key: Any = None) -> Any:
    """Return a copy of `value` with every sensitive entry masked, however deeply it is nested."""
    if key is not None and _is_sensitive(key):
        return MASK
    if isinstance(value, dict):
        return {k: redact(v, k) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(v) for v in value]
    return value


def _flatten(value: Any, prefix: str = '', out: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    out = {} if out is None else out
    if len(out) >= MAX_DIFF_KEYS:
        return out
    if isinstance(value, dict) and value:
        for k, v in value.items():
            _flatten(v, f'{prefix}.{k}' if prefix else str(k), out)
    elif isinstance(value, (list, tuple)) and value:
        for i, v in enumerate(value):
            _flatten(v, f'{prefix}[{i}]', out)
    else:
        out[prefix or '(value)'] = value
    return out


def _display(value: Any) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    return text if len(text) <= MAX_VALUE_CHARS else text[:MAX_VALUE_CHARS - 1] + '\u2026'


def diff_snapshots(before: Any, after: Any) -> List[Dict[str, Any]]:
    """
    Per-key changes between two snapshots. `before` / `after` is None when the key is absent on that side
    (a JSON null is shown as the text "null"), so the page can tell "missing" from "empty".
    """
    old = _flatten(redact(before)) if before not in (None, {}, []) else {}
    new = _flatten(redact(after)) if after not in (None, {}, []) else {}
    changes = []
    for key in sorted(set(old) | set(new)):
        if key in old and key in new and old[key] == new[key]:
            continue
        changes.append({
            'key': key,
            'before': _display(old[key]) if key in old else None,
            'after': _display(new[key]) if key in new else None,
        })
    return changes


# --------------------------------------------------------------------------- queries

def _actor_name(event: AuditEvent) -> str:
    user = event.actor_user
    if user is not None:
        return (user.full_name or user.username or '').strip() or '-'
    return ''


def audit_queryset(period, q: str = '', entity: str = ''):
    start_dt, end_dt = period.bounds()
    qs = AuditEvent.objects.filter(created_at__gte=start_dt, created_at__lt=end_dt)
    if entity:
        qs = qs.filter(entity_type=entity)
    q = (q or '').strip()[:80]
    if q:
        qs = qs.filter(
            Q(action__icontains=q) | Q(entity_type__icontains=q) | Q(entity_id__icontains=q)
            | Q(actor_role__icontains=q) | Q(actor_user__username__icontains=q) | Q(actor_user__full_name__icontains=q)
        )
    return qs


def audit_entity_types() -> List[str]:
    """Entity types for the filter dropdown (all time, bounded)."""
    return list(
        AuditEvent.objects.order_by().values_list('entity_type', flat=True).distinct().order_by('entity_type')[:MAX_ENTITY_TYPES]
    )


def audit_events_page(period, q: str = '', entity: str = '', sort: str = '-time', page: int = 1,
                      page_size: int = DEFAULT_PAGE_SIZE) -> Dict[str, Any]:
    qs = audit_queryset(period, q, entity)
    agg = qs.aggregate(
        n=Count('pk'),
        actors=Count('actor_user', distinct=True),
        entities=Count('entity_type', distinct=True),
    )
    page_size = clamp_page_size(page_size)
    total = agg['n']
    pages = max(1, math.ceil(total / page_size))
    page = min(max(int(page or 1), 1), pages)

    desc = sort.startswith('-')
    order_field = AUDIT_SORTS[sort.lstrip('-')]
    ordered = qs.select_related('actor_user').order_by(('-' if desc else '') + order_field, '-pk' if desc else 'pk')
    window = ordered[(page - 1) * page_size:page * page_size]

    rows = [{
        'id': str(e.id),
        'created_at': e.created_at,
        'action': e.action,
        'actor': _actor_name(e),
        'actor_role': e.actor_role or '',
        'entity_type': e.entity_type,
        'entity_id': e.entity_id,
    } for e in window]
    return {
        'rows': rows, 'total': total, 'page': page, 'pages': pages, 'page_size': page_size,
        'kpi': {'events': total, 'actors': agg['actors'], 'entities': agg['entities']},
        'entity_types': audit_entity_types(),
    }


def audit_event_detail(event_id) -> Dict[str, Any]:
    try:
        key = uuid.UUID(str(event_id))
    except ValueError:
        raise AuditNotFound()
    event = AuditEvent.objects.select_related('actor_user').filter(pk=key).first()
    if event is None:
        raise AuditNotFound()
    return {
        'id': str(event.id),
        'created_at': event.created_at,
        'action': event.action,
        'actor': _actor_name(event),
        'actor_role': event.actor_role or '',
        'entity_type': event.entity_type,
        'entity_id': event.entity_id,
        'changes': diff_snapshots(event.before_data, event.after_data),
        'ip': event.ip_hash_or_ip or '',
        'user_agent': event.user_agent or '',
        'request_id': event.request_id or '',
    }
