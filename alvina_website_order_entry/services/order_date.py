"""Date policy only; the production controller must explicitly call these helpers.

No model, field, request parameter or creation endpoint is assumed here.
"""

import re
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo


LIMA = ZoneInfo("America/Lima")


class InvalidOrderDate(ValueError):
    """An absent, malformed or out-of-policy date in a new order."""


def minimum_order_date(*, now=None):
    """Return tomorrow in Lima; injectable aware instant for deterministic tests."""
    if now is None:
        now = datetime.now(timezone.utc)
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be a timezone-aware datetime")
    return now.astimezone(LIMA).date() + timedelta(days=1)


def new_order_date_values(*, now=None):
    """Return generic values to map onto the real new-form QWeb context."""
    minimum = minimum_order_date(now=now).isoformat()
    return {"default_date": minimum, "min_date": minimum}


def validate_new_order_date(value, *, now=None):
    """Validate BEFORE any order creation or other write; return a date.

    Never use this policy for historical records or existing-order views.
    Missing dates are rejected rather than silently replaced on submission.
    """
    if isinstance(value, datetime):
        raise InvalidOrderDate("La fecha del pedido debe contener solo una fecha.")
    if isinstance(value, date):
        selected = value
    elif isinstance(value, str) and re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        try:
            selected = date.fromisoformat(value)
        except ValueError as exc:
            raise InvalidOrderDate("Ingresa una fecha válida para el pedido.") from exc
    else:
        raise InvalidOrderDate("Ingresa una fecha válida para el pedido.")
    minimum = minimum_order_date(now=now)
    if selected < minimum:
        raise InvalidOrderDate(
            "La fecha del pedido debe ser desde el %s (mañana en America/Lima)."
            % minimum.strftime("%d/%m/%Y")
        )
    return selected
