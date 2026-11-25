"""
Admin management of staff accounts.

Rules
-----
* Usernames are unique (case-insensitive) and use Django's username characters.
* Passwords go through Django's password validators. A changed password logs the user out of
  other devices because Django checks the session auth hash.
* An admin cannot change their own role, deactivate or delete themselves.
* Superuser accounts cannot be demoted, deactivated or deleted from the portal.
* The last active admin cannot be demoted, deactivated or deleted.
* Staff with history (opened table sessions or received payments, both PROTECT) are deactivated
  instead of deleted. Inactive users cannot log in and their current sessions stop working.
"""
from django.contrib.auth.password_validation import validate_password
from django.contrib.auth.validators import UnicodeUsernameValidator
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q

from apps.audit.services import log_audit_event
from .models import Role, User

STAFF_ROLES = {Role.ADMIN, Role.CASHIER, Role.KITCHEN}


class PasswordError(ValidationError):
    """Raised when a password fails Django's validators."""


def _snapshot(user: User) -> dict:
    return {
        'username': user.username,
        'full_name': user.full_name,
        'role': user.role,
        'is_active': user.is_active,
    }


def _audit(actor_user, action, user, before=None, after=None):
    log_audit_event(
        actor_user=actor_user,
        actor_role=getattr(actor_user, 'role', 'ADMIN') if actor_user else 'ADMIN',
        action=action,
        entity_type='User',
        entity_id=str(user.pk),
        before_data=before,
        after_data=after,
    )


def is_admin_account(user: User) -> bool:
    return bool(user.is_superuser or user.role == Role.ADMIN)


def has_history(user: User) -> bool:
    return user.opened_sessions.exists() or user.received_payments.exists()


def _other_active_admins(exclude_pk) -> int:
    return (
        User.objects.filter(is_active=True)
        .filter(Q(role=Role.ADMIN) | Q(is_superuser=True))
        .exclude(pk=exclude_pk)
        .count()
    )


def _clean_username(value, exclude_pk=None) -> str:
    username = (value or '').strip()
    if not username:
        raise ValidationError("Naran-uzuáriu obrigatóriu.")
    if len(username) > 150:
        raise ValidationError("Naran-uzuáriu maksimu karakter 150.")
    try:
        UnicodeUsernameValidator()(username)
    except ValidationError:
        raise ValidationError("Naran-uzuáriu bele uza letra, numeru no @ . + - _ de'it.")
    qs = User.objects.filter(username__iexact=username)
    if exclude_pk:
        qs = qs.exclude(pk=exclude_pk)
    if qs.exists():
        raise ValidationError(f"Naran-uzuáriu '{username}' uza ona.")
    return username


def _clean_full_name(value) -> str:
    name = (value or '').strip()
    if len(name) > 150:
        raise ValidationError("Naran kompletu maksimu karakter 150.")
    return name


def _clean_role(value) -> str:
    role = (value or '').strip().upper()
    if role not in STAFF_ROLES:
        raise ValidationError("Papél la válidu.")
    return role


def _check_password(password, user):
    try:
        validate_password(password, user=user)
    except ValidationError as e:
        raise PasswordError(e.messages)


def create_staff_user(*, username, password, role=Role.CASHIER, full_name='', actor_user=None) -> User:
    with transaction.atomic():
        username = _clean_username(username)
        role = _clean_role(role)
        full_name = _clean_full_name(full_name)
        if not password:
            raise PasswordError(["Liafuan xave obrigatóriu."])
        _check_password(password, User(username=username, full_name=full_name, role=role))
        user = User.objects.create_user(username=username, password=password, role=role, full_name=full_name)
        _audit(actor_user, 'USER_CREATE', user, after=_snapshot(user))
        return user


def update_staff_user(*, user, actor_user=None, username=None, full_name=None, role=None,
                      is_active=None, password=None) -> User:
    with transaction.atomic():
        locked = User.objects.select_for_update().get(pk=user.pk)
        before = _snapshot(locked)
        is_self = actor_user is not None and actor_user.pk == locked.pk

        if username is not None:
            locked.username = _clean_username(username, exclude_pk=locked.pk)
        if full_name is not None:
            locked.full_name = _clean_full_name(full_name)

        if role is not None:
            role = _clean_role(role)
            if role != locked.role:
                if is_self:
                    raise ValidationError("Ita-boot labele muda papél konta rasik.")
                if locked.is_superuser:
                    raise ValidationError("Konta superuser labele muda papél iha ne'e.")
                if locked.role == Role.ADMIN and locked.is_active and _other_active_admins(locked.pk) == 0:
                    raise ValidationError("Tenke iha admin ativu ida pelumenus.")
                locked.role = role

        if is_active is not None and bool(is_active) != locked.is_active:
            if not is_active:
                if is_self:
                    raise ValidationError("Ita-boot labele desativa konta rasik.")
                if locked.is_superuser:
                    raise ValidationError("Konta superuser labele desativa iha ne'e.")
                if is_admin_account(locked) and _other_active_admins(locked.pk) == 0:
                    raise ValidationError("Tenke iha admin ativu ida pelumenus.")
            locked.is_active = bool(is_active)

        password_changed = False
        if password:
            _check_password(password, locked)
            locked.set_password(password)
            password_changed = True

        locked.save()
        after = _snapshot(locked)
        after['password_changed'] = password_changed
        _audit(actor_user, 'USER_UPDATE', locked, before=before, after=after)
        return locked


def delete_staff_user(*, user, actor_user=None) -> dict:
    """Delete a staff account without history, otherwise deactivate it."""
    with transaction.atomic():
        locked = User.objects.select_for_update().get(pk=user.pk)
        if actor_user is not None and actor_user.pk == locked.pk:
            raise ValidationError("Ita-boot labele hamoos konta rasik.")
        if locked.is_superuser:
            raise ValidationError("Konta superuser labele hamoos iha ne'e.")
        if is_admin_account(locked) and locked.is_active and _other_active_admins(locked.pk) == 0:
            raise ValidationError("Tenke iha admin ativu ida pelumenus.")

        before = _snapshot(locked)
        if has_history(locked):
            if locked.is_active:
                locked.is_active = False
                locked.save(update_fields=['is_active', 'updated_at'])
            _audit(actor_user, 'USER_DEACTIVATE', locked, before=before, after=_snapshot(locked))
            return {'deleted': False, 'deactivated': True}

        _audit(actor_user, 'USER_DELETE', locked, before=before)
        locked.delete()
        return {'deleted': True, 'deactivated': False}
