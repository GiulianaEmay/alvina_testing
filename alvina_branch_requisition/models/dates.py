"""The request lead time follows Peru's calendar, independently of client TZ."""
from datetime import timedelta, timezone
from zoneinfo import ZoneInfo


_LIMA = ZoneInfo('America/Lima')


def minimum_required_date(utc_now):
    """Return Lima's current calendar day plus two calendar days.

    Odoo stores/returns naive UTC datetimes.  Explicitly attach UTC before the
    conversion; never interpret a naive value as the execution host's timezone.
    A timezone-aware instant is also accepted for deterministic local tests.
    """
    if utc_now.tzinfo is None:
        utc_now = utc_now.replace(tzinfo=timezone.utc)
    return utc_now.astimezone(_LIMA).date() + timedelta(days=2)
