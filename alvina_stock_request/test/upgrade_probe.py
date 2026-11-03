"""Disposable-database upgrade probe; execute via ``odoo-bin shell``.

Use ALVINA_UPGRADE_PROBE_PHASE=seed against 19.0.2.1.1, run ``-u`` with
19.0.2.2.0, then run phases check and idempotent. The baseline contains
credentials and is written only to a mode-0600 temporary file. No credential
is printed. This is intentionally separate from transactional unit tests.
"""

import base64
import hashlib
import io
import json
import os
import runpy
from pathlib import Path

from odoo import fields
from odoo.modules.module import get_module_path


baseline_path = Path(os.environ.get(
    'ALVINA_UPGRADE_PROBE_BASELINE', '/tmp/alvina-code128-upgrade-baseline.json'))
phase = os.environ.get('ALVINA_UPGRADE_PROBE_PHASE', 'check')


def save_baseline(data):
    descriptor = os.open(baseline_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.chmod(baseline_path, 0o600)
    with os.fdopen(descriptor, 'w') as target:
        json.dump(data, target)


def snapshot(request):
    secured = request.sudo()
    audit_fields = [
        'name', 'state', 'company_id', 'requester_id', 'department_id',
        'source_location_id', 'destination_location_id', 'picking_type_id',
        'submitted_at', 'delivered_at', 'delivered_by', 'cancelled_at',
        'cancelled_by', 'cancellation_reason', 'picking_id', 'create_uid',
        'create_date',
    ]
    values = {}
    for name in audit_fields:
        value = secured[name]
        field = secured._fields[name]
        if field.type == 'many2one':
            value = value.id or False
        elif field.type in ('date', 'datetime') and value:
            value = str(value)
        values[name] = value
    values.update({
        'token_uid': secured.token_uid,
        'token_secret': secured.token_secret,
        'token_digest': secured.token_digest,
        'messages': secured.message_ids.ids,
        'lines': [(line.product_id.id, line.quantity, line.product_uom_id.id)
                  for line in secured.line_ids],
    })
    # Round-trip normalizes tuples to lists, matching the saved JSON baseline.
    return json.loads(json.dumps(values))


def stock_snapshot(company_ids):
    context = {'allowed_company_ids': company_ids, 'active_test': False}
    quants = env['stock.quant'].with_context(context).search([
        ('company_id', 'in', company_ids)], order='id')
    return {
        'pickings': env['stock.picking'].with_context(context).search([
            ('company_id', 'in', company_ids)], order='id').ids,
        'moves': env['stock.move'].with_context(context).search([
            ('company_id', 'in', company_ids)], order='id').ids,
        'quants': [[quant.id, quant.product_id.id, quant.location_id.id,
                    quant.quantity, quant.reserved_quantity] for quant in quants],
    }


def decode_png(encoded_image, expected, format_name):
    import numpy as np
    import zxingcpp
    from PIL import Image
    decoded = zxingcpp.read_barcodes(np.array(Image.open(
        io.BytesIO(base64.b64decode(encoded_image))).convert('RGB')))
    assert len(decoded) == 1, 'Generated image must decode exactly one credential'
    assert decoded[0].text == expected, 'Generated credential does not round-trip'
    expected_format = {
        'QR Code': zxingcpp.BarcodeFormat.QRCode,
        'Code 128': zxingcpp.BarcodeFormat.Code128,
    }[format_name]
    assert decoded[0].format == expected_format, 'Unexpected barcode symbology'


if phase == 'seed':
    module = env['ir.module.module'].search([('name', '=', 'alvina_stock_request')])
    assert module.state == 'installed' and module.installed_version == '19.0.2.1.1'
    assert 'code128_secret' not in env['alvina.stock.request']._fields
    request_group = env.ref('alvina_stock_request.group_requester')
    keeper_group = env.ref('alvina_stock_request.group_warehouse')
    companies = env['res.company'].create([
        {'name': 'Code128 upgrade active company'},
        {'name': 'Code128 upgrade archived company'},
    ])
    contexts = {'allowed_company_ids': companies.ids, 'active_test': False}
    users = env['res.users'].with_context(no_reset_password=True)
    keeper = users.create({
        'name': 'Code128 upgrade keeper', 'login': 'code128-upgrade-keeper',
        'company_id': companies[0].id,
        'company_ids': [fields.Command.set(companies.ids)],
        'group_ids': [fields.Command.set(keeper_group.ids)],
    })
    records = []
    controls = []
    for index, company in enumerate(companies):
        scope = env['res.company'].with_context(contexts).with_company(company).env
        warehouse = scope['stock.warehouse'].create({
            'name': 'Insumos upgrade', 'code': 'INS', 'company_id': company.id,
        })
        warehouse.lot_stock_id.name = 'Existencias'
        # Fresh company fixtures may hide internal operations until location
        # features are enabled; this affects only our disposable test route.
        warehouse.int_type_id.active = True
        destination = scope['stock.location'].create({
            'name': 'TI upgrade', 'usage': 'internal', 'company_id': company.id,
            'location_id': warehouse.view_location_id.id,
        })
        assert warehouse.code == 'INS', 'Fixture warehouse code'
        assert warehouse.lot_stock_id.name == 'Existencias', 'Fixture source name'
        assert warehouse.lot_stock_id.location_id == warehouse.view_location_id, 'Fixture source parent'
        assert warehouse.lot_stock_id.company_id == company, 'Fixture source company'
        assert warehouse.int_type_id.company_id == company, 'Fixture operation company'
        assert warehouse.int_type_id.active, 'Fixture operation active'
        department = scope['alvina.stock.department'].create({
            'name': 'TI upgrade', 'company_id': company.id,
            'warehouse_id': warehouse.id, 'picking_type_id': warehouse.int_type_id.id,
            'location_id': destination.id,
        })
        product = scope['product.product'].create({
            'name': 'Upgrade paper', 'type': 'consu', 'is_storable': True,
        })
        scope['stock.quant']._update_available_quantity(product, warehouse.lot_stock_id, 100)
        owner = users.create({
            'name': 'Upgrade requester %s' % index,
            'login': 'code128-upgrade-owner-%s' % index,
            'company_id': company.id,
            'company_ids': [fields.Command.set(company.ids)],
            'group_ids': [fields.Command.set(request_group.ids)],
        })

        def new_request():
            return scope['alvina.stock.request'].with_user(owner).with_context(
                allowed_company_ids=company.ids).create({
                'department_id': department.id, 'company_id': company.id,
                'line_ids': [fields.Command.create({
                    'product_id': product.id, 'quantity': 5,
                })],
            })

        request = new_request()
        action = request.action_submit()
        wizard = scope[action['res_model']].with_user(owner).with_context(
            allowed_company_ids=company.ids).browse(action['res_id'])
        assert wizard.code == 'alvina-stock-request:%s:%s' % (
            request.sudo().token_uid, request.sudo().token_secret)
        records.append({
            'id': request.id, 'owner': owner.id, 'company': company.id,
            'wizard': wizard.id, 'old_code': wizard.code, 'snapshot': snapshot(request),
        })
        draft = new_request()
        controls.append({'id': draft.id, 'snapshot': snapshot(draft)})
        cancelled = new_request()
        cancelled.action_submit()
        cancelled._cancel('Upgrade control cancellation')
        controls.append({'id': cancelled.id, 'snapshot': snapshot(cancelled)})
        if not index:
            delivered = new_request()
            delivery_action = delivered.action_submit()
            delivery_qr = scope[delivery_action['res_model']].with_user(owner).with_context(
                allowed_company_ids=company.ids).browse(delivery_action['res_id']).code
            pickup = scope['alvina.stock.pickup.wizard'].with_user(keeper).create({
                'code': delivery_qr,
            })
            pickup.action_scan()
            pickup.action_redeem()
            controls.append({'id': delivered.id, 'snapshot': snapshot(delivered)})
    # Odoo forbids archiving a company while it is an active user's default.
    users.browse(records[1]['owner']).write({
        'company_id': companies[0].id,
        'company_ids': [fields.Command.set(companies.ids)],
    })
    companies[1].active = False
    data = {
        'companies': companies.ids, 'keeper': keeper.id,
        'pending': records, 'controls': controls,
        'stock': stock_snapshot(companies.ids),
        'request_ids': env['alvina.stock.request'].with_context(contexts).search([]).ids,
    }
    save_baseline(data)
    env.cr.commit()
    print('UPGRADE SEED PASS: 2 pending QR, 2 drafts, 2 cancelled, 1 delivered; one archived company.')
else:
    assert phase in ('check', 'idempotent'), 'Unknown upgrade probe phase'
    data = json.loads(baseline_path.read_text())
    context = {'allowed_company_ids': data['companies'], 'active_test': False}
    scope = env(context=context)
    if phase == 'idempotent':
        migration_path = Path(get_module_path('alvina_stock_request')) / \
            'migrations/19.0.2.2.0/post-migrate.py'
        # A same-version -u normally skips migrations. Explicitly rerun the
        # hook as well so idempotency covers both the upgrade and its helper.
        runpy.run_path(str(migration_path))['migrate'](env.cr, '19.0.2.1.1')
    module = scope['ir.module.module'].search([('name', '=', 'alvina_stock_request')])
    assert module.state == 'installed' and module.installed_version == '19.0.2.2.0'
    env.cr.execute("""
        SELECT pg_get_constraintdef(oid) FROM pg_constraint
        WHERE conrelid = 'alvina_stock_request'::regclass AND contype = 'u'
    """)
    assert 'UNIQUE (code128_digest)' in [row[0] for row in env.cr.fetchall()], \
        'Code128 digest must be unique in PostgreSQL after upgrade'
    assert scope['alvina.stock.request'].search([]).ids == data['request_ids']
    assert stock_snapshot(data['companies']) == data['stock']
    # Inspect persisted columns before reopening: this proves post-migrate ran.
    for record in data['pending']:
        request = scope['alvina.stock.request'].browse(record['id'])
        assert snapshot(request) == record['snapshot'], 'Upgrade changed pending QR/business audit'
        assert request.code128_secret and request.code128_digest, 'Migration did not backfill 1D credential'
        assert len(request.code128_secret) == 40 and request.code128_secret.isascii()
        assert request.code128_secret.isdigit()
        assert hashlib.sha256(request.code128_secret.encode('ascii')).hexdigest() == request.code128_digest
        if phase == 'idempotent':
            assert request.code128_secret == record['code128'], 'Second upgrade rotated Code128'
            assert request.code128_digest == record['code128_digest'], 'Second upgrade changed digest'
        else:
            record['code128'] = request.code128_secret
            record['code128_digest'] = request.code128_digest
        old_wizard = scope['alvina.stock.qr.wizard'].browse(record['wizard'])
        assert old_wizard.exists() and old_wizard.code == record['old_code'], 'Legacy transient QR changed'
    for control in data['controls']:
        request = scope['alvina.stock.request'].browse(control['id'])
        assert snapshot(request) == control['snapshot'], 'Upgrade changed non-pending request'
        assert not request.code128_secret and not request.code128_digest

    archived_company = scope['res.company'].browse(data['companies'][1])
    assert not archived_company.active, 'Archived-company fixture changed during upgrade'
    # Odoo itself disallows normal-user company contexts for archived companies.
    # Confirm the migration first, then temporarily reactivate for owner review;
    # the final rollback restores the archived company without persisting edits.
    archived_company.active = True

    # Reopening must show the same 1D credential and untouched QR; review must
    # not reserve or deliver. Roll back review chatter and new transient records.
    for record in data['pending']:
        owner = scope['res.users'].browse(record['owner'])
        request = scope['alvina.stock.request'].browse(record['id']).with_user(owner).with_context(
            allowed_company_ids=[record['company']])
        action = request.action_show_qr()
        popup = scope[action['res_model']].with_user(owner).with_context(
            allowed_company_ids=[record['company']]).browse(action['res_id'])
        assert popup.code == record['old_code']
        assert popup.code128_code == record['code128']
        assert popup.department_id == request.department_id
        assert popup.line_ids == request.line_ids
        decode_png(popup.image, popup.code, 'QR Code')
        decode_png(popup.code128_image, popup.code128_code, 'Code 128')
        keeper = scope['res.users'].browse(data['keeper'])
        for code in (record['old_code'], record['code128']):
            wizard = scope['alvina.stock.pickup.wizard'].with_user(keeper).create({'code': code})
            wizard.action_scan()
            assert wizard.request_id == request
            assert request.state == 'submitted' and not request.sudo().picking_id
            assert stock_snapshot(data['companies']) == data['stock']
    if phase == 'check':
        save_baseline(data)
    env.cr.rollback()
    print('UPGRADE %s PASS: legacy QR preserved, archived-company backfill, audit/stock unchanged, '
          'both PNGs decoded, owner reopen and both reviews create no stock '
          '(archived company temporarily reactivated, then rolled back).' % phase.upper())
