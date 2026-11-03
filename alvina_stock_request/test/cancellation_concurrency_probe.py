"""Run via odoo-bin shell on a DISPOSABLE test database: fixtures are committed.

Two PostgreSQL sessions race requester cancellation against Code128 delivery.
Both lock orders are exercised deterministically. The shell connection observes
pg_blocking_pids to confirm the loser is actually blocked before the winner
commits; this is not a timing-only simulation. Not imported by TransactionCase.
"""
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

import psycopg2
from odoo import api, fields
from odoo.exceptions import UserError


_TIMEOUT = 20
_RETRIES = 3


def run_probe(env):
    suffix = uuid.uuid4().hex[:12]
    company = env['res.company'].create({'name': 'Cancellation concurrency ' + suffix})
    local = env(context=dict(env.context, allowed_company_ids=company.ids))
    warehouse = local['stock.warehouse'].create({
        'name': 'Insumos cancellation concurrency', 'code': 'INS',
        'company_id': company.id,
    })
    warehouse.lot_stock_id.name = 'Existencias'
    # Enable only the disposable fixture's existing internal operation type.
    warehouse.int_type_id.active = True
    destination = local['stock.location'].create({
        'name': 'Cancellation concurrency department', 'usage': 'internal',
        'company_id': company.id,
    })
    department = local['alvina.stock.department'].create({
        'name': 'Cancellation concurrency department', 'company_id': company.id,
        'warehouse_id': warehouse.id, 'picking_type_id': warehouse.int_type_id.id,
        'location_id': destination.id,
    })
    users = local['res.users'].with_context(no_reset_password=True)
    requester = users.create({
        'name': 'Cancellation concurrency requester',
        'login': 'cancellation-requester-' + suffix,
        'company_id': company.id, 'company_ids': [fields.Command.set(company.ids)],
        'group_ids': [fields.Command.set(local.ref('alvina_stock_request.group_requester').ids)],
    })
    keeper = users.create({
        'name': 'Cancellation concurrency keeper',
        'login': 'cancellation-keeper-' + suffix,
        'company_id': company.id, 'company_ids': [fields.Command.set(company.ids)],
        'group_ids': [fields.Command.set(local.ref('alvina_stock_request.group_warehouse').ids)],
    })
    assert requester.id != keeper.id
    company_id, requester_id, keeper_id = company.id, requester.id, keeper.id
    source_id, destination_id = warehouse.lot_stock_id.id, destination.id
    registry = env.registry

    def worker_environment(cr, user_id):
        return api.Environment(cr, user_id, {
            'allowed_company_ids': [company_id], 'lang': 'en_US',
        })

    def run_race(first_operation):
        product = local['product.product'].create({
            'name': 'Cancellation concurrency stock ' + first_operation,
            'type': 'consu', 'is_storable': True,
        })
        local['stock.quant']._update_available_quantity(product, warehouse.lot_stock_id, 20)
        request = local['alvina.stock.request'].with_user(requester).create({
            'department_id': department.id, 'company_id': company_id,
            'line_ids': [fields.Command.create({'product_id': product.id, 'quantity': 5})],
        })
        action = request.action_submit()
        display = local[action['res_model']].with_user(requester).browse(action['res_id'])
        codes = {'qr': display.code, 'code128': display.code128_code}
        assert codes['qr'].startswith('alvina-stock-request:')
        assert len(codes['code128']) == 40 and all('0' <= char <= '9' for char in codes['code128'])
        request_id, product_id = request.id, product.id
        reason = 'Requester cancellation ' + first_operation + ' ' + suffix
        env.cr.commit()

        # These are completed browser RPCs, not uncommitted preparation inside
        # the competing transactions. Neither review nor opening cancellation
        # reserves stock or consumes either credential.
        with registry.cursor() as cr:
            review_env = worker_environment(cr, keeper_id)
            pickup = review_env['alvina.stock.pickup.wizard'].create({'code': codes['code128']})
            pickup.action_scan()
            assert pickup.request_id.id == request_id
            assert pickup.request_id.state == 'submitted'
            assert not pickup.request_id.picking_id
            pickup_id = pickup.id
            cr.commit()
        with registry.cursor() as cr:
            cancel_env = worker_environment(cr, requester_id)
            pending = cancel_env['alvina.stock.request'].browse(request_id)
            redisplay_action = pending.action_show_qr()
            redisplay = cancel_env[redisplay_action['res_model']].browse(redisplay_action['res_id'])
            assert redisplay.code == codes['qr'], 'Code128 review changed the existing QR'
            assert redisplay.code128_code == codes['code128'], 'The short code was regenerated'
            cancel = cancel_env['alvina.stock.cancel.wizard'].create({
                'request_id': request_id, 'reason': reason,
            })
            assert cancel.request_id.state == 'submitted'
            cancel_id = cancel.id
            cr.commit()

        winner_locked = threading.Event()
        loser_snapshot = threading.Event()
        loser_attempting = threading.Event()
        release_winner = threading.Event()
        backend_pids = {}
        other_operation = 'deliver' if first_operation == 'cancel' else 'cancel'

        def compete(operation, winner):
            serialization_failures = 0
            user_id = requester_id if operation == 'cancel' else keeper_id
            for attempt in range(_RETRIES):
                with registry.cursor() as cr:
                    current_env = worker_environment(cr, user_id)
                    try:
                        cr.execute("SET LOCAL lock_timeout = '25s'")
                        cr.execute("SET LOCAL statement_timeout = '30s'")
                        cr.execute('SELECT pg_backend_pid()')
                        backend_pids['winner' if winner else 'loser'] = cr.fetchone()[0]
                        current_request = current_env['alvina.stock.request'].browse(request_id)
                        if attempt == 0:
                            if not winner:
                                assert winner_locked.wait(_TIMEOUT), 'Winner did not acquire its lock'
                            # Establish both REPEATABLE READ snapshots while the
                            # request is still pending, before the winner commits.
                            assert current_request.state == 'submitted'
                            assert not current_request.sudo().picking_id
                            if winner:
                                current_request._lock()
                                winner_locked.set()
                                assert loser_snapshot.wait(_TIMEOUT), 'Loser did not establish a snapshot'
                                assert release_winner.wait(_TIMEOUT), 'Winner was not released'
                            else:
                                loser_snapshot.set()
                                loser_attempting.set()

                        if operation == 'cancel':
                            current_env['alvina.stock.cancel.wizard'].browse(cancel_id).action_cancel()
                            assert current_request.state == 'cancelled'
                        else:
                            current_env['alvina.stock.pickup.wizard'].browse(pickup_id).action_redeem()
                            assert current_request.state == 'done'
                        cr.commit()
                        return operation, 'accepted', serialization_failures
                    except psycopg2.errors.SerializationFailure:
                        cr.rollback()
                        serialization_failures += 1
                        # Retry the whole authorization/cancellation RPC in a
                        # fresh cursor/environment, as Odoo's dispatcher does.
                        continue
                    except UserError as error:
                        assert not winner, 'The designated winner unexpectedly rejected its action'
                        assert type(error) is UserError, 'Rejection was an unexpected access/validation error'
                        expected_state = 'cancelled' if first_operation == 'cancel' else 'done'
                        assert current_request.state == expected_state, 'Rejection occurred before the winning state'
                        cr.rollback()
                        return operation, 'rejected', serialization_failures
            raise AssertionError('Serialization retry limit exceeded for ' + operation)

        with ThreadPoolExecutor(max_workers=2) as pool:
            winner_future = pool.submit(compete, first_operation, True)
            try:
                assert winner_locked.wait(_TIMEOUT), 'Winner did not acquire its lock'
                loser_future = pool.submit(compete, other_operation, False)
                assert loser_snapshot.wait(_TIMEOUT), 'Loser did not establish a snapshot'
                assert loser_attempting.wait(_TIMEOUT), 'Loser did not start its action'
                deadline = time.monotonic() + _TIMEOUT
                blocked = False
                while time.monotonic() < deadline:
                    env.cr.execute('SELECT pg_blocking_pids(%s)', [backend_pids['loser']])
                    if backend_pids['winner'] in env.cr.fetchone()[0]:
                        blocked = True
                        break
                    if loser_future.done() or winner_future.done():
                        break
                    # A short event wait yields to the competitors without
                    # relying on a guessed delay to establish lock ordering.
                    release_winner.wait(0.02)
                assert blocked, 'Loser never blocked behind the winner: ' + repr(backend_pids)
                release_winner.set()
                winner_result = winner_future.result(timeout=40)
                loser_result = loser_future.result(timeout=40)
            finally:
                release_winner.set()
                env.cr.rollback()

        assert winner_result[:2] == (first_operation, 'accepted'), winner_result
        assert loser_result[:2] == (other_operation, 'rejected'), loser_result
        assert loser_result[2] >= 1, 'No real serialization failure was retried'
        with registry.cursor() as cr:
            check = worker_environment(cr, keeper_id)
            result = check['alvina.stock.request'].browse(request_id)
            assert result.requester_id.id == requester_id
            assert result.company_id.id == company_id
            assert result.submitted_at
            secured = result.sudo()
            assert not secured.token_digest
            assert not secured.token_secret
            assert not secured.token_uid
            assert not secured.code128_secret
            assert not secured.code128_digest
            pickings = check['stock.picking'].search([('alvina_request_id', '=', request_id)])
            quantity_source = check['stock.quant']._get_available_quantity(
                check['product.product'].browse(product_id), check['stock.location'].browse(source_id))
            quantity_destination = check['stock.quant']._get_available_quantity(
                check['product.product'].browse(product_id), check['stock.location'].browse(destination_id))
            if first_operation == 'cancel':
                assert result.state == 'cancelled'
                assert not result.picking_id and not pickings
                assert quantity_source == 20 and quantity_destination == 0
                assert result.cancelled_by.id == requester_id and result.cancelled_at
                assert result.cancellation_reason == reason
                assert not result.delivered_by and not result.delivered_at
                assert check['mail.message'].search_count([
                    ('model', '=', 'alvina.stock.request'), ('res_id', '=', request_id),
                    ('author_id', '=', requester.partner_id.id), ('body', 'ilike', reason),
                ]), 'Successful cancellation was not logged in the request chatter'
            else:
                assert result.state == 'done' and result.picking_id.state == 'done'
                assert len(pickings) == 1 and pickings == result.picking_id
                assert quantity_source == 15 and quantity_destination == 5
                assert result.delivered_by.id == keeper_id and result.delivered_at
                assert not result.cancelled_by and not result.cancelled_at
                assert not result.cancellation_reason
                assert check['mail.message'].search_count([
                    ('model', '=', 'alvina.stock.request'), ('res_id', '=', request_id),
                    ('author_id', '=', keeper.partner_id.id), ('body', 'ilike', result.picking_id.name),
                ]), 'Successful delivery was not logged in the request chatter'
                assert not check['mail.message'].search_count([
                    ('model', '=', 'alvina.stock.request'), ('res_id', '=', request_id),
                    ('body', 'ilike', reason),
                ]), 'Rejected cancellation left a successful cancellation audit message'
            for code in codes.values():
                with cr.savepoint():
                    invalid = check['alvina.stock.pickup.wizard'].create({'code': code})
                    try:
                        invalid.action_scan()
                    except UserError as error:
                        assert type(error) is UserError, 'Code check failed for an unrelated access/validation reason'
                    else:
                        raise AssertionError('The QR or Code128 remained valid after the winning action')
            # The verification transaction intentionally discards its temporary wizard.
            cr.rollback()
        print('CODE128 CANCELLATION CONCURRENCY PASS: first=%s, loser rejected after %s serialization retry; '
              'pickings=%s, source=%s, destination=%s, QR and Code128 invalid, audit correct' % (
                  first_operation, loser_result[2], len(pickings), quantity_source, quantity_destination))

    run_race('cancel')
    run_race('deliver')


if 'env' in globals():
    run_probe(env)
