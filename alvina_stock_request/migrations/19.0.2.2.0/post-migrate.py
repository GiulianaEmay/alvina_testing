"""Add a stable 1D credential without changing existing pickup QR codes."""

import hashlib
import logging
import re
import secrets
import uuid

from odoo import SUPERUSER_ID, api


_logger = logging.getLogger(__name__)
_QR_SECRET = re.compile(r'[A-Za-z0-9_-]{43}', flags=re.ASCII)
_QR_DIGEST = re.compile(r'[0-9a-f]{64}', flags=re.ASCII)


def _has_valid_qr(request):
    """Require the original credential to be coherent before adding an alias."""
    identity = request.token_uid
    token = request.token_secret
    digest = request.token_digest
    if not all(isinstance(value, str) for value in (identity, token, digest)):
        return False
    try:
        if str(uuid.UUID(identity)) != identity:
            return False
    except ValueError:
        return False
    if not _QR_SECRET.fullmatch(token) or not _QR_DIGEST.fullmatch(digest):
        return False
    expected = hashlib.sha256(token.encode('ascii')).hexdigest()
    return secrets.compare_digest(expected, digest)


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    company_ids = env['res.company'].with_context(active_test=False).search([]).ids
    env = api.Environment(cr, SUPERUSER_ID, {'allowed_company_ids': company_ids})
    requests = env['alvina.stock.request'].search([
        ('state', '=', 'submitted'),
        ('token_uid', '!=', False),
        ('token_secret', '!=', False),
        ('token_digest', '!=', False),
        '|',
        ('code128_secret', '=', False),
        ('code128_digest', '=', False),
    ], order='id')
    for request in requests:
        # Cancellation, delivery and lazy creation share this lock. Re-read all
        # credential fields after acquiring it; the search may be stale by now.
        request._lock()
        if request.state != 'submitted':
            continue
        if request.code128_secret and request.code128_digest:
            continue
        if request.code128_secret or request.code128_digest:
            _logger.warning(
                'Code128 migration skipped stock request %s: incomplete 1D credential.',
                request.id,
            )
            continue
        if not _has_valid_qr(request):
            _logger.warning(
                'Code128 migration skipped stock request %s: inconsistent QR credential.',
                request.id,
            )
            continue
        # The helper writes only the new secret/digest. Original QR, state,
        # transfer links and business audit fields must remain unchanged.
        request._ensure_code128()
