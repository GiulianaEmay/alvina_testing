"""Run via odoo-bin shell on a DISPOSABLE test database (this fixture commits).

Two real PostgreSQL sessions, using separate warehouse users, review the same
request with its QR and its Code128, then race authorization. The second session
must serialize/retry and reject, with one stock transfer only and both codes spent.
Not imported by the ordinary rollback-based TransactionCase suite.
"""
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

import psycopg2
from odoo import api, fields
from odoo.exceptions import UserError


def run_probe(env):
    suffix = uuid.uuid4().hex[:12]
    company = env['res.company'].create({'name': 'Concurrency ' + suffix})
    local = env(context=dict(env.context, allowed_company_ids=company.ids))
    warehouse = local['stock.warehouse'].create({
        'name': 'Insumos concurrency', 'code': 'INS', 'company_id': company.id,
    })
    warehouse.lot_stock_id.name = 'Existencias'
    warehouse.int_type_id.active = True  # Test fixture only; production type is never changed.
    destination = local['stock.location'].create({'name': 'Department', 'usage': 'internal', 'company_id': company.id})
    department = local['alvina.stock.department'].create({
        'name': 'Concurrency department', 'company_id': company.id,
        'warehouse_id': warehouse.id, 'picking_type_id': warehouse.int_type_id.id,
        'location_id': destination.id,
    })
    users = local['res.users'].with_context(no_reset_password=True)
    requester = users.create({
        'name': 'Concurrency requester', 'login': 'concurrency-requester-' + suffix,
        'company_id': company.id, 'company_ids': [fields.Command.set(company.ids)],
        'group_ids': [fields.Command.set(local.ref('alvina_stock_request.group_requester').ids)],
    })
    keepers = users.create([{
        'name': 'Concurrency keeper ' + str(index),
        'login': 'concurrency-keeper-' + str(index) + '-' + suffix,
        'company_id': company.id, 'company_ids': [fields.Command.set(company.ids)],
        'group_ids': [fields.Command.set(local.ref('alvina_stock_request.group_warehouse').ids)],
    } for index in range(2)])
    product = local['product.product'].create({'name': 'Concurrency stock', 'type': 'consu', 'is_storable': True})
    local['stock.quant']._update_available_quantity(product, warehouse.lot_stock_id, 20)
    request = local['alvina.stock.request'].with_user(requester).create({
        'department_id': department.id, 'company_id': company.id,
        'line_ids': [fields.Command.create({'product_id': product.id, 'quantity': 5})],
    })
    action = request.action_submit()
    display = local[action['res_model']].with_user(requester).browse(action['res_id'])
    codes = {'qr': display.code, 'code128': display.code128_code}
    assert codes['qr'].startswith('alvina-stock-request:')
    assert len(codes['code128']) == 40 and all('0' <= char <= '9' for char in codes['code128'])
    request_id, company_id = request.id, company.id
    requester_id, keeper_ids = requester.id, keepers.ids
    product_id, source_id, destination_id = product.id, warehouse.lot_stock_id.id, destination.id
    env.cr.commit()
    barrier = threading.Barrier(2)
    registry = env.registry
    backend_pids = set()

    def worker_environment(cr, user_id):
        return api.Environment(cr, user_id, {'allowed_company_ids': [company_id], 'lang': 'en_US'})

    # Persist each review as a separate browser RPC before the delivery race.
    # A retry reuses that reviewed wizard; it does not rescan inside authorization.
    competitors = []
    for kind, keeper_id in zip(('qr', 'code128'), keeper_ids):
        with registry.cursor() as cr:
            review_env = worker_environment(cr, keeper_id)
            wizard = review_env['alvina.stock.pickup.wizard'].create({'code': codes[kind]})
            wizard.action_scan()
            assert wizard.request_id.id == request_id
            assert wizard.request_id.state == 'submitted'
            assert not wizard.request_id.picking_id
            competitors.append((kind, keeper_id, wizard.id))
            cr.commit()

    def redeem(competitor):
        kind, keeper_id, wizard_id = competitor
        serialization_failures = 0
        for attempt in range(3):
            with registry.cursor() as cr:
                worker_env = worker_environment(cr, keeper_id)
                try:
                    cr.execute("SET LOCAL lock_timeout = '25s'")
                    cr.execute("SET LOCAL statement_timeout = '30s'")
                    wizard = worker_env['alvina.stock.pickup.wizard'].browse(wizard_id)
                    if attempt == 0:
                        cr.execute('SELECT pg_backend_pid()')
                        backend_pids.add(cr.fetchone()[0])
                        reviewed = worker_env['alvina.stock.request'].browse(request_id)
                        assert reviewed.state == 'submitted'
                        assert not reviewed.picking_id
                        assert wizard.request_id.id == request_id
                        barrier.wait(timeout=20)
                    wizard.action_redeem()
                    cr.commit()
                    return kind, keeper_id, 'delivered', serialization_failures
                except psycopg2.errors.SerializationFailure:
                    cr.rollback()
                    serialization_failures += 1
                    continue  # Odoo's normal RPC dispatcher also retries serialization conflicts.
                except UserError as error:
                    assert type(error) is UserError, 'Rejection was an unrelated access/validation error'
                    assert worker_env['alvina.stock.request'].browse(request_id).state == 'done'
                    cr.rollback()
                    return kind, keeper_id, 'rejected', serialization_failures
        raise AssertionError('Serialization retry limit exceeded')

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(redeem, competitors))
    assert sorted(outcome[2] for outcome in outcomes) == ['delivered', 'rejected'], outcomes
    assert {outcome[0] for outcome in outcomes} == {'qr', 'code128'}
    assert len(backend_pids) == 2, 'The competitors did not use two PostgreSQL sessions'
    assert sum(outcome[3] for outcome in outcomes) >= 1, 'No real serialization failure was retried'
    winner = next(outcome for outcome in outcomes if outcome[2] == 'delivered')
    with registry.cursor() as cr:
        check = worker_environment(cr, keeper_ids[0])
        result = check['alvina.stock.request'].browse(request_id)
        assert result.state == 'done'
        assert result.requester_id.id == requester_id
        assert result.picking_id.state == 'done'
        assert result.delivered_by.id == winner[1]
        assert result.delivered_at
        assert check['stock.picking'].search_count([('alvina_request_id', '=', request_id)]) == 1
        assert not result.sudo().token_digest
        assert not result.sudo().token_secret
        assert not result.sudo().token_uid
        assert not result.sudo().code128_secret
        assert not result.sudo().code128_digest
        quantity_destination = check['stock.quant']._get_available_quantity(
            check['product.product'].browse(product_id), check['stock.location'].browse(destination_id))
        quantity_source = check['stock.quant']._get_available_quantity(
            check['product.product'].browse(product_id), check['stock.location'].browse(source_id))
        assert quantity_destination == 5, quantity_destination
        assert quantity_source == 15, quantity_source
        for code in codes.values():
            invalid = check['alvina.stock.pickup.wizard'].create({'code': code})
            try:
                invalid.action_scan()
            except UserError as error:
                assert type(error) is UserError, 'Consumed code failed for an unrelated access/validation reason'
            else:
                raise AssertionError('A consumed QR or Code128 remained valid')
        cr.rollback()
    print('MIXED CONCURRENCY PASS: 2 sessions/users, QR versus Code128, winner=%s, '
          '1 delivered, 1 rejected, 1 picking, source 15, destination 5, both codes invalid' % winner[0])


if 'env' in globals():
    run_probe(env)
