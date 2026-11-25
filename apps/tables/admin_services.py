"""
Admin CRUD for restaurant tables.

Rules
-----
* ``table_code`` is unique per restaurant (stored upper-case) and ``display_name`` is required.
* Capacity is optional, 1-50.
* Manual status is AVAILABLE or MAINTENANCE (shown as "unavailable"). OCCUPIED is set only by the session
  flow, and status cannot be changed while a session is active.
* A table with an active session cannot be deactivated or deleted.
* A table that has session history is never hard-deleted (sessions use PROTECT and reports depend
  on them); it is deactivated instead, which also invalidates its QR code.
"""
from typing import Optional

from django.core.exceptions import ValidationError
from django.db import transaction

from apps.audit.services import log_audit_event
from .models import RestaurantTable, TableSession, TableStatus
from .selectors import ACTIVE_SESSION_STATUSES

MAX_CAPACITY = 50
MANUAL_STATUSES = {TableStatus.AVAILABLE, TableStatus.MAINTENANCE}


def _snapshot(table: RestaurantTable) -> dict:
    return {
        'table_code': table.table_code,
        'display_name': table.display_name,
        'capacity': table.capacity,
        'sort_order': table.sort_order,
        'status': table.status,
        'is_active': table.is_active,
    }


def _audit(actor_user, action, table, before=None, after=None, request_id=""):
    log_audit_event(
        actor_user=actor_user,
        actor_role=getattr(actor_user, 'role', 'ADMIN') if actor_user else 'ADMIN',
        action=action,
        entity_type='RestaurantTable',
        entity_id=str(table.id),
        before_data=before,
        after_data=after,
        request_id=request_id,
    )


def _clean_code(value) -> str:
    code = (value or '').strip().upper()
    if not code:
        raise ValidationError("Kódigu meza obrigatóriu.")
    if len(code) > 30:
        raise ValidationError("Kódigu meza maksimu karakter 30.")
    return code


def _clean_name(value) -> str:
    name = (value or '').strip()
    if not name:
        raise ValidationError("Naran meza obrigatóriu.")
    if len(name) > 80:
        raise ValidationError("Naran meza maksimu karakter 80.")
    return name


def _clean_capacity(value) -> Optional[int]:
    if value in (None, ''):
        return None
    try:
        capacity = int(value)
    except (TypeError, ValueError):
        raise ValidationError("Kapasidade tenke numeru.")
    if capacity < 1 or capacity > MAX_CAPACITY:
        raise ValidationError(f"Kapasidade tenke entre 1 no {MAX_CAPACITY}.")
    return capacity


def _ensure_unique_code(restaurant, code, exclude_id=None):
    qs = RestaurantTable.objects.filter(restaurant=restaurant, table_code__iexact=code)
    if exclude_id:
        qs = qs.exclude(id=exclude_id)
    if qs.exists():
        raise ValidationError(f"Kódigu meza '{code}' uza ona.")


def has_active_session(table: RestaurantTable) -> bool:
    return TableSession.objects.filter(table=table, status__in=ACTIVE_SESSION_STATUSES).exists()


def create_table(*, restaurant, table_code, display_name, capacity=None, sort_order=0,
                 is_active=True, actor_user=None, request_id="") -> RestaurantTable:
    code = _clean_code(table_code)
    name = _clean_name(display_name)
    cap = _clean_capacity(capacity)
    with transaction.atomic():
        _ensure_unique_code(restaurant, code)
        table = RestaurantTable.objects.create(
            restaurant=restaurant,
            table_code=code,
            display_name=name,
            capacity=cap,
            sort_order=int(sort_order or 0),
            is_active=bool(is_active),
            status=TableStatus.AVAILABLE,
        )
        _audit(actor_user, 'TABLE_CREATE', table, after=_snapshot(table), request_id=request_id)
        return table


def update_table(*, table: RestaurantTable, table_code=None, display_name=None, capacity=None,
                 capacity_provided=False, sort_order=None, is_active=None, status=None,
                 actor_user=None, request_id="") -> RestaurantTable:
    with transaction.atomic():
        locked = RestaurantTable.objects.select_for_update().get(id=table.id)
        before = _snapshot(locked)
        busy = has_active_session(locked)

        if table_code is not None:
            code = _clean_code(table_code)
            _ensure_unique_code(locked.restaurant, code, exclude_id=locked.id)
            locked.table_code = code
        if display_name is not None:
            locked.display_name = _clean_name(display_name)
        if capacity_provided:
            locked.capacity = _clean_capacity(capacity)
        if sort_order is not None:
            locked.sort_order = int(sort_order)

        if is_active is not None and bool(is_active) != locked.is_active:
            if not is_active and busy:
                raise ValidationError("Meza iha sesaun ativu. Taka sesaun uluk molok desativa.")
            locked.is_active = bool(is_active)

        if status is not None and status != locked.status:
            if busy:
                raise ValidationError("Meza iha sesaun ativu. Status labele muda agora.")
            if status not in MANUAL_STATUSES:
                raise ValidationError("Status meza la válidu.")
            locked.status = status

        locked.save()
        _audit(actor_user, 'TABLE_UPDATE', locked, before=before, after=_snapshot(locked), request_id=request_id)
        return locked


def delete_table(*, table: RestaurantTable, actor_user=None, request_id="") -> dict:
    """Hard-delete a table without history, otherwise deactivate it. Returns what happened."""
    with transaction.atomic():
        locked = RestaurantTable.objects.select_for_update().get(id=table.id)
        if has_active_session(locked):
            raise ValidationError("Meza iha sesaun ativu. Taka sesaun uluk molok hamoos.")

        before = _snapshot(locked)
        if TableSession.objects.filter(table=locked).exists():
            locked.is_active = False
            locked.save(update_fields=['is_active', 'updated_at'])
            _audit(actor_user, 'TABLE_DEACTIVATE', locked, before=before, after=_snapshot(locked), request_id=request_id)
            return {'deleted': False, 'deactivated': True}

        _audit(actor_user, 'TABLE_DELETE', locked, before=before, request_id=request_id)
        locked.delete()
        return {'deleted': True, 'deactivated': False}
