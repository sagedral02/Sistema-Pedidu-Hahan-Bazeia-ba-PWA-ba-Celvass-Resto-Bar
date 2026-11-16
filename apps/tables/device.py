"""
Device-lock helpers for customer table sessions.

Rules
-----
* A session with no ``primary_device_token`` is unclaimed. The first request that
  carries a device token claims it.
* A session that is claimed accepts only requests whose token is the primary
  token or is listed in ``authorized_device_tokens``. A request without any
  token is rejected, because it cannot prove it belongs to the table.
"""
from typing import Optional

from django.core.exceptions import ValidationError

MIN_GUEST_COUNT = 1
MAX_GUEST_COUNT = 25

DEVICE_COOKIE = 'celvass_device_id'
DEVICE_HEADER = 'X-Device-Token'
MAX_TOKEN_LENGTH = 128


def extract_device_token(request, body: Optional[dict] = None) -> str:
    """
    Return the device token. The httpOnly cookie issued by the server wins, so a
    browser always presents the same id; the header/body exist for non-browser clients.
    """
    token = (
        request.COOKIES.get(DEVICE_COOKIE)
        or request.headers.get(DEVICE_HEADER)
    )
    if not token and body is not None:
        try:
            token = body.get('device_token')
        except AttributeError:
            token = None
    token = (token or '').strip()
    return token[:MAX_TOKEN_LENGTH]


def is_device_authorized(session, device_token: str) -> bool:
    """True when the device may use the session (unclaimed sessions allow everyone)."""
    if not session.primary_device_token:
        return True
    if not device_token:
        return False
    if device_token == session.primary_device_token:
        return True
    return device_token in (session.authorized_device_tokens or [])


def claim_session_if_unclaimed(session, device_token: str) -> bool:
    """Bind an unclaimed session to the first device that presents a token."""
    if session.primary_device_token or not device_token:
        return False
    session.primary_device_token = device_token
    session.authorized_device_tokens = [device_token]
    session.save(update_fields=['primary_device_token', 'authorized_device_tokens', 'updated_at'])
    return True


def parse_guest_count(raw, default: int) -> int:
    """
    Convert user input to a guest count in [MIN_GUEST_COUNT, MAX_GUEST_COUNT].
    Missing values fall back to ``default``. Anything else raises ValidationError.
    """
    if raw is None:
        return default
    if isinstance(raw, bool):
        raise ValidationError("Totál bainaka la válidu.")
    if isinstance(raw, int):
        value = raw
    else:
        text = str(raw).strip()
        if not text.lstrip('-').isdigit():
            raise ValidationError("Totál bainaka tenke numeru.")
        value = int(text)
    if value < MIN_GUEST_COUNT or value > MAX_GUEST_COUNT:
        raise ValidationError(f"Totál bainaka tenke entre {MIN_GUEST_COUNT} no {MAX_GUEST_COUNT}.")
    return value
