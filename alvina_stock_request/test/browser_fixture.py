"""Seed ONLY a disposable local database before tests/browser_odoo_smoke.mjs.

Run through odoo-bin shell on alvina_browser_* or alvina_code128_clean. The
fixture grants admin module roles, creates a request and stock, and commits.
It never authorizes a delivery. Do not use on a real company's database.
"""
import json
import os
from pathlib import Path
from odoo import fields
assert env.cr.dbname == 'alvina_code128_clean' or env.cr.dbname.startswith('alvina_browser_'), 'Disposable browser-test database required'
assert env.cr._cnx.info.host in (None, '', 'localhost', '127.0.0.1', '::1'), 'Only a local PostgreSQL test service is permitted'
admin = env.ref('base.user_admin')
if 'tour_enabled' in admin._fields:
    # Native first-login tours otherwise navigate away while the smoke test runs.
    admin.tour_enabled = False
admin.write({'group_ids': [fields.Command.link(env.ref('alvina_stock_request.group_warehouse').id), fields.Command.link(env.ref('alvina_stock_request.group_config').id)]})
company = admin.company_id
warehouse = env['stock.warehouse'].search([('code', '=', 'INS'), ('company_id', '=', company.id)], limit=1)
if not warehouse:
    warehouse = env['stock.warehouse'].create({'name': 'Browser test Insumos', 'code': 'INS', 'company_id': company.id})
warehouse.lot_stock_id.name = 'Existencias'
# Fresh test databases hide internal operations until multiple locations are
# enabled. This is fixture setup in the guarded disposable DB, never addon flow.
warehouse.int_type_id.active = True
destination = env['stock.location'].create({'name': 'Browser test TI', 'usage': 'internal', 'company_id': company.id, 'location_id': warehouse.view_location_id.id})
department = env['alvina.stock.department'].create({'name': 'Browser test TI', 'company_id': company.id, 'warehouse_id': warehouse.id, 'picking_type_id': warehouse.int_type_id.id, 'location_id': destination.id})
product = env['product.product'].create({'name': 'Browser test Papel', 'type': 'consu', 'is_storable': True})
env['stock.quant']._update_available_quantity(product, warehouse.lot_stock_id, 10)
request = env['alvina.stock.request'].with_user(admin).create({'company_id': company.id, 'department_id': department.id, 'line_ids': [fields.Command.create({'product_id': product.id, 'quantity': 2})]})
action = request.action_submit()
receipt = env[action['res_model']].with_user(admin).browse(action['res_id'])
path = Path(os.environ.get('ALVINA_BROWSER_FIXTURE', '/tmp/alvina-browser-fixture.json'))
with open(path, 'w', opener=lambda file_path, flags: os.open(file_path, flags, 0o600)) as output:
    os.chmod(path, 0o600)
    json.dump({'db': env.cr.dbname, 'request_id': request.id, 'receipt_id': receipt.id,
               'qr_code': receipt.code, 'code128': receipt.code128_code,
               'company_id': company.id}, output)
env.cr.commit()
print('Browser test fixture seeded in disposable local DB; secrets not printed')
