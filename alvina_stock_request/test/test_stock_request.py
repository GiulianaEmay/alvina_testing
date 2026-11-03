from datetime import timedelta

from odoo import fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestStockRequest(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.warehouse = cls.env['stock.warehouse'].create({
            'name': 'Insumos pruebas', 'code': 'INS', 'company_id': cls.company.id,
        })
        cls.warehouse.lot_stock_id.name = 'Existencias'
        cls.destination = cls.env['stock.location'].create({
            'name': 'Administración', 'usage': 'internal', 'company_id': cls.company.id,
            'location_id': cls.warehouse.view_location_id.id,
        })
        cls.department = cls.env['alvina.stock.department'].create({
            'name': 'Administración', 'warehouse_id': cls.warehouse.id,
            'picking_type_id': cls.warehouse.int_type_id.id,
            'location_id': cls.destination.id, 'company_id': cls.company.id,
        })
        cls.product = cls.env['product.product'].create({
            'name': 'Papel ALVIÑA', 'type': 'consu', 'is_storable': True,
        })
        cls.env['stock.quant']._update_available_quantity(cls.product, cls.warehouse.lot_stock_id, 100)
        users = cls.env['res.users'].with_context(no_reset_password=True)
        cls.requester = users.create({
            'name': 'Solicitante ALVIÑA', 'login': 'alvina-requester',
            'company_id': cls.company.id, 'company_ids': [fields.Command.set(cls.company.ids)],
            'group_ids': [fields.Command.set(cls.env.ref('alvina_stock_request.group_requester').ids)],
        })
        cls.other_requester = users.create({
            'name': 'Otro solicitante', 'login': 'alvina-other',
            'company_id': cls.company.id, 'company_ids': [fields.Command.set(cls.company.ids)],
            'group_ids': [fields.Command.set(cls.env.ref('alvina_stock_request.group_requester').ids)],
        })
        cls.keeper = users.create({
            'name': 'Almacenero ALVIÑA', 'login': 'alvina-keeper',
            'company_id': cls.company.id, 'company_ids': [fields.Command.set(cls.company.ids)],
            'group_ids': [fields.Command.set(cls.env.ref('alvina_stock_request.group_warehouse').ids)],
        })
        cls.stock_user = users.create({
            'name': 'Usuario inventario sin autorización', 'login': 'alvina-stock-user',
            'company_id': cls.company.id, 'company_ids': [fields.Command.set(cls.company.ids)],
            'group_ids': [fields.Command.set(cls.env.ref('stock.group_stock_user').ids)],
        })

    def _request(self, quantity=5):
        return self.env['alvina.stock.request'].with_user(self.requester).create({
            'department_id': self.department.id, 'company_id': self.company.id,
            'line_ids': [fields.Command.create({'product_id': self.product.id, 'quantity': quantity})],
        })

    def _submitted(self, quantity=5):
        request = self._request(quantity)
        action = request.action_submit()
        wizard = self.env[action['res_model']].with_user(self.requester).browse(action['res_id'])
        self.assertTrue(wizard.image)
        self.assertTrue(wizard.code128_image)
        self.assertRegex(wizard.code128_code, r'^[0-9]{40}$')
        self.assertEqual(wizard.request_id, request)
        self.assertEqual(request.state, 'submitted')
        self.assertFalse(request.sudo().picking_id)
        return request.with_user(self.keeper), wizard.code

    def _code128(self, request):
        action = request.with_user(self.requester).action_show_qr()
        wizard = self.env[action['res_model']].with_user(self.requester).browse(action['res_id'])
        self.assertTrue(wizard.code128_image)
        return wizard.code128_code

    def _scan(self, code, user=None):
        wizard = self.env['alvina.stock.pickup.wizard'].with_user(user or self.keeper).create({'code': code})
        wizard.action_scan()
        return wizard

    def _redeem(self, code, user=None):
        return self._scan(code, user).action_redeem()

    def test_delivery_and_single_use(self):
        before = self.warehouse.int_type_id.read(['default_location_src_id', 'default_location_dest_id', 'create_backorder'])
        request, code = self._submitted()
        code128 = self._code128(request)
        self._redeem(code)
        self.assertEqual(request.state, 'done')
        self.assertEqual(request.picking_id.picking_type_id, self.warehouse.int_type_id)
        self.assertEqual(request.picking_id.location_id, self.warehouse.lot_stock_id)
        self.assertEqual(request.picking_id.location_dest_id, self.destination)
        self.assertEqual(request.picking_id.state, 'done')
        self.assertEqual(request.delivered_by, self.keeper)
        self.assertTrue(request.delivered_at)
        self.assertFalse(request.sudo().token_digest)
        self.assertFalse(request.sudo().token_secret)
        self.assertFalse(request.sudo().token_uid)
        self.assertFalse(request.sudo().code128_secret)
        self.assertFalse(request.sudo().code128_digest)
        self.assertEqual(self.env['stock.quant']._get_available_quantity(self.product, self.destination), 5)
        with self.assertRaises(UserError):
            self._redeem(code)
        with self.assertRaises(UserError):
            self._redeem(code128)
        self.assertEqual(self.env['stock.quant']._get_available_quantity(self.product, self.destination), 5)
        self.assertEqual(before, self.warehouse.int_type_id.read(['default_location_src_id', 'default_location_dest_id', 'create_backorder']))

    def test_requester_and_stock_user_cannot_authorize(self):
        request, code = self._submitted()
        code128 = self._code128(request)
        with self.assertRaises(AccessError):
            self._redeem(code, self.requester)
        with self.assertRaises(AccessError):
            self._redeem(code128, self.requester)
        uid, token = code.split(':')[1:]
        with self.assertRaises(AccessError):
            request.with_user(self.stock_user)._redeem(uid, token)
        with self.assertRaises(AccessError):
            request.with_user(self.stock_user)._redeem(uid, code128, code_type='code128')
        with self.assertRaises(AccessError):
            self._redeem(code, self.env.ref('base.public_user'))
        with self.assertRaises(AccessError):
            self._redeem(code128, self.env.ref('base.public_user'))
        self.assertEqual(request.state, 'submitted')
        self.assertFalse(request.picking_id)

    def test_direct_validation_and_context_bypass_denied(self):
        request, code = self._submitted()
        self._redeem(code)
        picking = request.picking_id
        with self.assertRaises(AccessError):
            picking.button_validate()
        with self.assertRaises(AccessError):
            picking.with_context(_alvina_flow=True).button_validate()
        with self.assertRaises(AccessError):
            picking.action_cancel()
        with self.assertRaises(AccessError):
            picking.write({'alvina_request_id': False})
        with self.assertRaises(AccessError):
            picking.move_ids.write({'quantity': 99})
        with self.assertRaises(AccessError):
            picking.move_line_ids.write({'quantity': 99})
        self.assertEqual(request.state, 'done')

    def test_persistent_requester_qr_and_tampering(self):
        request, code = self._submitted()
        code128 = self._code128(request)
        with self.assertRaises(UserError):
            self._redeem(code[:-1] + ('A' if code[-1] != 'A' else 'B'))
        from unittest.mock import patch
        future = fields.Datetime.now() + timedelta(days=30)
        with patch.object(fields.Datetime, 'now', return_value=future):
            for _ in range(2):
                action = request.with_user(self.requester).action_show_qr()
                wizard = self.env[action['res_model']].with_user(self.requester).browse(action['res_id'])
                self.assertEqual(wizard.code, code)
                self.assertEqual(wizard.code128_code, code128)
                wizard.unlink()
            self._redeem(code)
        self.assertEqual(request.state, 'done')
        with self.assertRaises(UserError):
            request.with_user(self.requester).action_show_qr()
        second_request, second_code = self._submitted()
        self.assertNotEqual(code, second_code)
        self.assertEqual(second_request.state, 'submitted')

    def test_draft_ownership_and_immutable_submission(self):
        request = self._request()
        for field in ('code128_secret', 'code128_digest'):
            with self.assertRaises(AccessError):
                request.write({field: 'forged'})
            with self.assertRaises(AccessError):
                self.env['alvina.stock.request'].with_user(self.requester).create({
                    'department_id': self.department.id, field: 'forged',
                })
        with self.assertRaises(AccessError):
            request.with_user(self.other_requester).read(['name'])
        with self.assertRaises(AccessError):
            request.write({'state': 'done'})
        with self.assertRaises(AccessError):
            request.write({'requester_id': self.other_requester.id})
        with self.assertRaises(AccessError):
            request.line_ids.write({'request_id': self._request().id})
        request.action_submit()
        with self.assertRaises(AccessError):
            request.line_ids.write({'quantity': 2})
        with self.assertRaises(AccessError):
            request.write({'note': 'cambio tardío'})
        with self.assertRaises(AccessError):
            request.unlink()
        with self.assertRaises(UserError):
            request.action_submit()

    def test_shortage_retry_has_one_transfer(self):
        request, code = self._submitted(200)
        code128 = self._code128(request)
        wizard = self._scan(code)
        self.assertEqual(wizard.request_id, request)
        self.assertEqual(wizard.line_ids.product_id, self.product)
        self.assertEqual(wizard.line_ids.quantity, 200)
        self.assertEqual(wizard.line_ids.available_quantity, 100)
        with self.assertRaises(UserError):
            wizard.action_redeem()
        with self.assertRaises(UserError):
            self._scan(code128).action_redeem()
        self.assertEqual(request.state, 'submitted')
        self.assertFalse(request.picking_id)
        self.assertTrue(request.sudo().token_digest)
        self.assertTrue(request.sudo().code128_digest)
        self.assertEqual(request.sudo().code128_secret, code128)
        self.assertEqual(self.env['stock.picking'].search_count([('alvina_request_id', '=', request.id)]), 0)
        self.assertEqual(self.env['stock.quant']._get_available_quantity(self.product, self.warehouse.lot_stock_id), 100)
        action = request.with_user(self.requester).action_show_qr()
        self.assertEqual(self.env[action['res_model']].with_user(self.requester).browse(action['res_id']).code, code)
        self.env['stock.quant']._update_available_quantity(self.product, self.warehouse.lot_stock_id, 200)
        wizard.action_redeem()
        self.assertEqual(request.state, 'done')
        self.assertEqual(request.picking_id.state, 'done')
        self.assertEqual(self.env['stock.picking'].search_count([('alvina_request_id', '=', request.id)]), 1)
        self.assertEqual(self.env['stock.quant']._get_available_quantity(self.product, self.destination), 200)
        with self.assertRaises(UserError):
            self._redeem(code)

    def test_scan_reviews_stock_without_reserving_or_delivering(self):
        request, code = self._submitted()
        wizard = self._scan(code)
        self.assertEqual(wizard.request_id, request)
        self.assertEqual(wizard.line_ids, request.line_ids)
        self.assertEqual(wizard.line_ids.product_id, self.product)
        self.assertEqual(wizard.line_ids.quantity, 5)
        self.assertEqual(wizard.line_ids.available_quantity, 100)
        self.assertEqual(request.state, 'submitted')
        self.assertTrue(request.sudo().token_digest)
        self.assertFalse(request.picking_id)
        self.assertEqual(self.env['stock.picking'].search_count([('alvina_request_id', '=', request.id)]), 0)
        self.assertEqual(self.env['stock.quant']._get_available_quantity(self.product, self.warehouse.lot_stock_id), 100)
        self.assertEqual(self.env['stock.quant']._get_available_quantity(self.product, self.destination), 0)

    def test_stock_rechecked_after_scan(self):
        request, code = self._submitted()
        wizard = self._scan(self._code128(request))
        self.assertEqual(wizard.line_ids.available_quantity, 100)
        self.env['stock.quant']._update_available_quantity(self.product, self.warehouse.lot_stock_id, -98)
        with self.assertRaises(UserError):
            wizard.action_redeem()
        self.assertEqual(request.state, 'submitted')
        self.assertTrue(request.sudo().token_digest)
        self.assertFalse(request.picking_id)
        self.assertEqual(self.env['stock.quant']._get_available_quantity(self.product, self.destination), 0)
        self.assertEqual(self.env['stock.quant']._get_available_quantity(self.product, self.warehouse.lot_stock_id), 2)
        self.env['stock.quant']._update_available_quantity(self.product, self.warehouse.lot_stock_id, 3)
        wizard.action_redeem()
        self.assertEqual(request.state, 'done')
        self.assertEqual(self.env['stock.quant']._get_available_quantity(self.product, self.destination), 5)

    def test_multiline_shortage_rolls_back_every_reservation(self):
        scarce = self.env['product.product'].create({
            'name': 'Insumo sin existencia', 'type': 'consu', 'is_storable': True,
        })
        request = self._request()
        request.line_ids = [fields.Command.create({'product_id': scarce.id, 'quantity': 2})]
        action = request.action_submit()
        code = self.env[action['res_model']].with_user(self.requester).browse(action['res_id']).code
        request = request.with_user(self.keeper)
        wizard = self._scan(code)
        with self.assertRaises(UserError):
            wizard.action_redeem()
        self.assertFalse(request.picking_id)
        self.assertEqual(request.state, 'submitted')
        self.assertTrue(request.sudo().token_digest)
        self.assertEqual(self.env['stock.quant']._get_available_quantity(self.product, self.warehouse.lot_stock_id), 100)
        self.assertEqual(self.env['stock.quant']._get_available_quantity(self.product, self.destination), 0)
        self.env['stock.quant']._update_available_quantity(scarce, self.warehouse.lot_stock_id, 2)
        wizard.action_redeem()
        self.assertEqual(request.state, 'done')
        self.assertEqual(self.env['stock.quant']._get_available_quantity(self.product, self.destination), 5)
        self.assertEqual(self.env['stock.quant']._get_available_quantity(scarce, self.destination), 2)

    def test_authorization_requires_scan_and_cannot_forge_review(self):
        request, code = self._submitted()
        pickups = self.env['alvina.stock.pickup.wizard'].with_user(self.keeper)
        wizard = pickups.with_context(default_request_id=request.id).create({'code': code})
        self.assertFalse(wizard.request_id)
        with self.assertRaises(UserError):
            wizard.action_redeem()
        with self.assertRaises(AccessError):
            pickups.create({'code': code, 'request_id': request.id})
        with self.assertRaises(AccessError):
            wizard.write({'request_id': request.id})
        with self.assertRaises(AccessError):
            wizard.write({'company_id': self.company.id})
        with self.assertRaises(AccessError):
            wizard.write({'line_ids': [fields.Command.clear()]})
        self.assertEqual(request.state, 'submitted')
        self.assertFalse(request.picking_id)
        wizard.action_scan()
        wizard.action_redeem()
        self.assertEqual(request.state, 'done')

    def test_changing_code_requires_new_scan(self):
        request, code = self._submitted()
        second, second_code = self._submitted()
        wizard = self._scan(code)
        wizard.write({'code': self._code128(second)})
        self.assertFalse(wizard.request_id)
        self.assertFalse(wizard.line_ids)
        with self.assertRaises(UserError):
            wizard.action_redeem()
        wizard.action_scan()
        self.assertEqual(wizard.request_id, second)
        wizard.action_redeem()
        self.assertEqual(second.state, 'done')
        self.assertEqual(request.state, 'submitted')
        self._redeem(code)
        self.assertEqual(request.state, 'done')

    def test_two_scans_cannot_authorize_twice(self):
        request, code = self._submitted()
        first = self._scan(code)
        second = self._scan(code)
        first.action_redeem()
        with self.assertRaises(UserError):
            second.action_redeem()
        self.assertEqual(request.state, 'done')
        self.assertEqual(self.env['stock.picking'].search_count([('alvina_request_id', '=', request.id)]), 1)
        self.assertEqual(self.env['stock.quant']._get_available_quantity(self.product, self.destination), 5)

    def test_owner_can_cancel_submitted_and_revoke_qr(self):
        request, code = self._submitted()
        pickup = self._scan(code)
        code128 = self._code128(request)
        barcode_pickup = self._scan(code128)
        submitted_at = request.submitted_at
        lines = request.line_ids
        route = (request.source_location_id, request.destination_location_id, request.picking_type_id)
        previous_messages = set(request.sudo().message_ids.ids)
        cancel = self.env['alvina.stock.cancel.wizard'].with_user(self.requester).with_context(
            default_request_id=request.id,
        ).create({'reason': '  Ya no se requieren los insumos  '})
        cancel.action_cancel()
        self.assertEqual(request.state, 'cancelled')
        self.assertEqual(request.cancelled_by, self.requester)
        self.assertTrue(request.cancelled_at)
        self.assertEqual(request.cancellation_reason, 'Ya no se requieren los insumos')
        for field in ('token_digest', 'token_secret', 'token_uid', 'code128_secret', 'code128_digest'):
            self.assertFalse(request.sudo()[field])
        self.assertFalse(request.delivered_by)
        self.assertFalse(request.delivered_at)
        self.assertFalse(request.picking_id)
        self.assertEqual(self.env['stock.picking'].search_count([('alvina_request_id', '=', request.id)]), 0)
        self.assertEqual(self.env['stock.move'].search_count([
            ('alvina_request_line_id', 'in', request.line_ids.ids)]), 0)
        self.assertEqual(self.env['stock.quant']._get_available_quantity(self.product, self.warehouse.lot_stock_id), 100)
        self.assertEqual(self.env['stock.quant']._get_available_quantity(self.product, self.destination), 0)
        self.assertEqual(request.line_ids, lines)
        self.assertEqual(request.line_ids.quantity, 5)
        self.assertEqual(request.submitted_at, submitted_at)
        self.assertEqual((request.source_location_id, request.destination_location_id, request.picking_type_id), route)
        self.assertTrue(previous_messages.issubset(set(request.sudo().message_ids.ids)))
        self.assertGreater(len(request.sudo().message_ids), len(previous_messages))
        with self.assertRaises(UserError):
            self._redeem(code)
        with self.assertRaises(UserError):
            pickup.action_redeem()
        with self.assertRaises(UserError):
            barcode_pickup.action_redeem()
        with self.assertRaises(UserError):
            self._redeem(code128)
        owner = request.with_user(self.requester)
        with self.assertRaises(AccessError):
            owner.write({'note': 'Reabrir solicitud'})
        with self.assertRaises(AccessError):
            owner.line_ids.write({'quantity': 2})
        with self.assertRaises(AccessError):
            owner.unlink()
        with self.assertRaises(UserError):
            owner.action_submit()
        with self.assertRaises(AccessError):
            owner.action_show_qr()

    def test_submitted_cancellation_requires_request_owner(self):
        request, code = self._submitted()
        audit = request.sudo().read(['token_digest', 'token_secret', 'token_uid', 'code128_secret', 'code128_digest'])[0]
        for user in (self.other_requester, self.keeper, self.stock_user, self.env.ref('base.public_user')):
            with self.assertRaises(AccessError):
                request.with_user(user)._cancel('Anulación no autorizada')
            with self.assertRaises(AccessError):
                self.env['alvina.stock.cancel.wizard'].with_user(user).with_context(
                    default_request_id=request.id,
                ).create({'reason': 'Anulación no autorizada'})
        self.assertEqual(request.state, 'submitted')
        self.assertEqual(request.sudo().read(['token_digest', 'token_secret', 'token_uid', 'code128_secret', 'code128_digest'])[0], audit)
        self.assertFalse(request.cancelled_by)
        self.assertFalse(request.cancelled_at)
        self.assertFalse(request.cancellation_reason)
        self._redeem(code)

    def test_cancellation_respects_active_company_for_owner_and_wizard(self):
        request, code = self._submitted()
        company_b = self.env['res.company'].create({'name': 'Empresa B de anulación'})
        self.requester.company_ids = [fields.Command.set((self.company | company_b).ids)]
        cancel = self.env['alvina.stock.cancel.wizard'].with_user(self.requester).create({
            'request_id': request.id, 'reason': 'Fuera de empresa activa',
        })
        owner_b = request.with_user(self.requester).with_context(allowed_company_ids=company_b.ids)
        with self.assertRaises(AccessError):
            owner_b._cancel('Fuera de empresa activa')
        with self.assertRaises(AccessError):
            cancel.with_context(allowed_company_ids=company_b.ids).action_cancel()
        with self.assertRaises(AccessError):
            self.env['alvina.stock.cancel.wizard'].with_user(self.requester).with_context(
                allowed_company_ids=company_b.ids, default_request_id=request.id,
            ).create({'reason': 'Fuera de empresa activa'})
        self.assertEqual(request.state, 'submitted')
        self.assertTrue(request.sudo().token_digest)
        self.assertFalse(request.cancelled_at)
        self._redeem(code)

    def test_invalid_cancellation_reasons_preserve_request_and_qr(self):
        request, code = self._submitted()
        owner = request.with_user(self.requester)
        secured = request.sudo()
        token = (secured.token_uid, secured.token_digest, secured.token_secret, secured.code128_digest, secured.code128_secret)
        for reason in (False, None, '', '  \t\n', 7, 1.5, [], {'reason': 'No válido'}):
            with self.assertRaises(UserError):
                owner._cancel(reason)
            self.assertEqual(request.state, 'submitted')
            self.assertEqual((secured.token_uid, secured.token_digest, secured.token_secret, secured.code128_digest, secured.code128_secret), token)
            self.assertFalse(request.cancelled_at)
            self.assertFalse(request.cancelled_by)
            self.assertFalse(request.cancellation_reason)
        cancel = self.env['alvina.stock.cancel.wizard'].with_user(self.requester).create({
            'request_id': request.id, 'reason': '  \t\n',
        })
        with self.assertRaises(UserError):
            cancel.action_cancel()
        self.assertEqual(request.state, 'submitted')
        self.assertEqual((secured.token_uid, secured.token_digest, secured.token_secret, secured.code128_digest, secured.code128_secret), token)
        self._redeem(code)

    def test_delivered_cannot_cancel_even_with_preexisting_wizard(self):
        request, code = self._submitted()
        cancel = self.env['alvina.stock.cancel.wizard'].with_user(self.requester).create({
            'request_id': request.id, 'reason': 'Preparado antes de entregar',
        })
        self._redeem(code)
        delivered_at = request.delivered_at
        picking = request.picking_id
        with self.assertRaises(UserError):
            request.with_user(self.requester)._cancel('Intento después de entregar')
        with self.assertRaises(UserError):
            cancel.action_cancel()
        with self.assertRaises(AccessError):
            self.env['alvina.stock.cancel.wizard'].with_user(self.requester).create({
                'request_id': request.id, 'reason': 'Intento después de entregar',
            })
        self.assertEqual(request.state, 'done')
        self.assertEqual(request.picking_id, picking)
        self.assertEqual(picking.state, 'done')
        self.assertEqual(request.delivered_by, self.keeper)
        self.assertEqual(request.delivered_at, delivered_at)
        self.assertFalse(request.cancelled_by)
        self.assertFalse(request.cancelled_at)
        self.assertFalse(request.cancellation_reason)
        self.assertEqual(self.env['stock.quant']._get_available_quantity(self.product, self.destination), 5)
        self.assertEqual(self.env['stock.quant']._get_available_quantity(self.product, self.warehouse.lot_stock_id), 95)

    def test_second_cancellation_does_not_overwrite_audit(self):
        request, code = self._submitted()
        cancellations = self.env['alvina.stock.cancel.wizard'].with_user(self.requester)
        first = cancellations.create({'request_id': request.id, 'reason': 'Primera anulación'})
        second = cancellations.create({'request_id': request.id, 'reason': 'Segunda anulación'})
        first.action_cancel()
        audit = request.read(['cancelled_at', 'cancelled_by', 'cancellation_reason'])[0]
        messages = set(request.sudo().message_ids.ids)
        from unittest.mock import patch
        future = fields.Datetime.now() + timedelta(days=1)
        with patch.object(fields.Datetime, 'now', return_value=future):
            with self.assertRaises(UserError):
                second.action_cancel()
            with self.assertRaises(UserError):
                request.with_user(self.requester)._cancel('Tercera anulación')
        self.assertEqual(request.state, 'cancelled')
        self.assertEqual(request.read(['cancelled_at', 'cancelled_by', 'cancellation_reason'])[0], audit)
        self.assertEqual(set(request.sudo().message_ids.ids), messages)
        with self.assertRaises(UserError):
            self._redeem(code)

    def test_owner_can_cancel_after_failed_stock_reservation(self):
        request, code = self._submitted(200)
        pickup = self._scan(code)
        with self.assertRaises(UserError):
            pickup.action_redeem()
        self.assertEqual(request.state, 'submitted')
        self.assertFalse(request.picking_id)
        cancel = self.env['alvina.stock.cancel.wizard'].with_user(self.requester).create({
            'request_id': request.id, 'reason': 'Insumos ya no necesarios tras falta de stock',
        })
        cancel.action_cancel()
        self.assertEqual(request.state, 'cancelled')
        for field in ('token_digest', 'token_secret', 'token_uid', 'code128_secret', 'code128_digest'):
            self.assertFalse(request.sudo()[field])
        self.assertEqual(self.env['stock.picking'].search_count([('alvina_request_id', '=', request.id)]), 0)
        self.assertEqual(self.env['stock.move'].search_count([
            ('alvina_request_line_id', 'in', request.line_ids.ids)]), 0)
        self.assertEqual(self.env['stock.quant']._get_available_quantity(self.product, self.warehouse.lot_stock_id), 100)
        self.assertEqual(self.env['stock.quant']._get_available_quantity(self.product, self.destination), 0)
        self.env['stock.quant']._update_available_quantity(self.product, self.warehouse.lot_stock_id, 200)
        with self.assertRaises(UserError):
            pickup.action_redeem()
        with self.assertRaises(UserError):
            self._redeem(code)
        self.assertEqual(self.env['stock.quant']._get_available_quantity(self.product, self.destination), 0)

    def test_cancellation_message_failure_rolls_back_state_token_and_audit(self):
        request, code = self._submitted()
        secured = request.sudo()
        token = (secured.token_uid, secured.token_digest, secured.token_secret, secured.code128_digest, secured.code128_secret)
        messages = set(secured.message_ids.ids)
        cancel = self.env['alvina.stock.cancel.wizard'].with_user(self.requester).create({
            'request_id': request.id, 'reason': 'Anulación que falla al registrar el mensaje',
        })
        from unittest.mock import patch
        request_class = type(self.env['alvina.stock.request'])
        original_post = request_class.message_post

        def fail_after_message(records, *args, **kwargs):
            original_post(records, *args, **kwargs)
            raise UserError('Simulated chatter failure after cancellation update')

        # The cancellation action must roll back itself without an outer savepoint.
        with patch.object(request_class, 'message_post', fail_after_message):
            with self.assertRaises(UserError):
                cancel.action_cancel()
        request.invalidate_recordset()
        self.assertEqual(request.state, 'submitted')
        self.assertEqual((secured.token_uid, secured.token_digest, secured.token_secret, secured.code128_digest, secured.code128_secret), token)
        self.assertFalse(request.cancelled_by)
        self.assertFalse(request.cancelled_at)
        self.assertFalse(request.cancellation_reason)
        self.assertEqual(set(secured.message_ids.ids), messages)
        self.assertFalse(request.picking_id)
        self.assertEqual(self.env['stock.quant']._get_available_quantity(self.product, self.warehouse.lot_stock_id), 100)
        self.assertEqual(self.env['stock.quant']._get_available_quantity(self.product, self.destination), 0)
        self._redeem(code)
        self.assertEqual(request.state, 'done')

    def test_company_isolation(self):
        company_b = self.env['res.company'].create({'name': 'ALVIÑA B'})
        warehouse_b = self.env['stock.warehouse'].with_company(company_b).search([('company_id', '=', company_b.id)], limit=1)
        warehouse_b.code = 'INS'
        warehouse_b.lot_stock_id.name = 'Existencias'
        destination_b = self.env['stock.location'].create({'name': 'Departamento B', 'usage': 'internal', 'company_id': company_b.id})
        dep_b = self.env['alvina.stock.department'].with_company(company_b).create({
            'name': 'B', 'company_id': company_b.id, 'warehouse_id': warehouse_b.id,
            'picking_type_id': warehouse_b.int_type_id.id, 'location_id': destination_b.id,
        })
        requester_b = self.env['res.users'].with_context(no_reset_password=True).create({
            'name': 'Solicitante B', 'login': 'alvina-requester-b', 'company_id': company_b.id,
            'company_ids': [fields.Command.set(company_b.ids)],
            'group_ids': [fields.Command.set(self.env.ref('alvina_stock_request.group_requester').ids)],
        })
        request_b = self.env['alvina.stock.request'].with_user(requester_b).with_company(company_b).create({
            'company_id': company_b.id, 'department_id': dep_b.id,
            'line_ids': [fields.Command.create({'product_id': self.product.id, 'quantity': 1})],
        })
        with self.assertRaises(AccessError):
            request_b.with_user(self.keeper).read(['name'])
        with self.assertRaises(AccessError):
            dep_b.with_user(self.requester).read(['name'])
        with self.assertRaises(AccessError):
            self.env['alvina.stock.request'].with_user(self.requester).create({'company_id': company_b.id, 'department_id': dep_b.id})
        with self.assertRaises(UserError), self.cr.savepoint():
            self._request().write({'department_id': dep_b.id})

    def test_drafts_and_departments_cannot_switch_to_unauthorized_company(self):
        company_b = self.env['res.company'].create({'name': 'Empresa no autorizada para edición'})
        warehouse_b = self.env['stock.warehouse'].with_company(company_b).search([
            ('company_id', '=', company_b.id)], limit=1)
        warehouse_b.code = 'INS'
        warehouse_b.lot_stock_id.name = 'Existencias'
        destination_b = self.env['stock.location'].create({
            'name': 'Departamento restringido', 'usage': 'internal', 'company_id': company_b.id,
        })
        department_b = self.env['alvina.stock.department'].with_company(company_b).create({
            'name': 'Departamento restringido', 'company_id': company_b.id,
            'warehouse_id': warehouse_b.id, 'picking_type_id': warehouse_b.int_type_id.id,
            'location_id': destination_b.id,
        })
        request = self._request()
        with self.assertRaises(AccessError):
            request.write({'company_id': company_b.id, 'department_id': department_b.id})
        self.assertEqual(request.company_id, self.company)
        self.assertEqual(request.department_id, self.department)
        self.assertEqual(request.state, 'draft')
        self.assertEqual(request.line_ids.quantity, 5)

        configurator = self.env['res.users'].with_context(no_reset_password=True).create({
            'name': 'Configurador empresa A', 'login': 'alvina-configurator-a',
            'company_id': self.company.id, 'company_ids': [fields.Command.set(self.company.ids)],
            'group_ids': [fields.Command.set(self.env.ref('alvina_stock_request.group_config').ids)],
        })
        with self.assertRaises(AccessError):
            self.department.with_user(configurator).write({
                'company_id': company_b.id, 'warehouse_id': warehouse_b.id,
                'picking_type_id': warehouse_b.int_type_id.id, 'location_id': destination_b.id,
            })
        self.assertEqual(self.department.company_id, self.company)
        self.assertEqual(self.department.warehouse_id, self.warehouse)
        self.assertEqual(self.department.picking_type_id, self.warehouse.int_type_id)
        self.assertEqual(self.department.location_id, self.destination)

    def test_line_defaults_enforce_parent_access_and_quantity(self):
        product = self.env['product.product'].create({
            'name': 'Producto para contexto de línea', 'type': 'consu', 'is_storable': True,
        })
        submitted, _ = self._submitted()
        other_draft = self.env['alvina.stock.request'].with_user(self.other_requester).create({
            'department_id': self.department.id, 'company_id': self.company.id,
        })
        lines = self.env['alvina.stock.request.line'].with_user(self.requester)
        for parent in (submitted, other_draft):
            with self.assertRaises(AccessError):
                lines.with_context(default_request_id=parent.id).create({
                    'product_id': product.id, 'quantity': 1,
                })
        self.assertEqual(submitted.line_ids.product_id, self.product)
        self.assertFalse(other_draft.with_user(self.other_requester).line_ids)

        own_draft = self._request()
        line = lines.with_context(default_request_id=own_draft.id, default_quantity=3).create({
            'product_id': product.id,
        })
        self.assertEqual(line.request_id, own_draft)
        self.assertEqual(line.quantity, 3)
        for quantity in (float('nan'), float('inf'), -1):
            with self.assertRaises(ValidationError):
                lines.with_context(default_request_id=own_draft.id, default_quantity=quantity).create({
                    'product_id': product.id,
                })
        self.assertEqual(own_draft.line_ids.filtered(lambda l: l.product_id == product), line)

    def test_invalid_products_quantities_and_routes(self):
        with self.assertRaises(ValidationError), self.cr.savepoint():
            self._request(0)
        service = self.env['product.product'].create({'name': 'Servicio', 'type': 'service'})
        with self.assertRaises(ValidationError), self.cr.savepoint():
            self._request().line_ids.write({'product_id': service.id})
        with self.assertRaises(ValidationError), self.cr.savepoint():
            self.department.write({'picking_type_id': self.warehouse.out_type_id.id})
        with self.assertRaises(ValidationError), self.cr.savepoint():
            self.department.write({'location_id': self.warehouse.lot_stock_id.id})

    def test_validation_failure_rolls_back_token_and_stock(self):
        request, code = self._submitted()
        code128 = self._code128(request)
        # No outer savepoint: authorization itself must roll back extension failures.
        from unittest.mock import patch
        picking_class = type(self.env['stock.picking'])
        original_validate = picking_class.button_validate

        def fail_after_transfer(picking):
            original_validate(picking)
            raise UserError('Simulated extension failure after stock completion')

        with patch.object(picking_class, 'button_validate', fail_after_transfer):
            for credential in (code, code128):
                with self.assertRaises(UserError):
                    self._redeem(credential)
        request.invalidate_recordset()
        self.assertEqual(request.state, 'submitted')
        self.assertFalse(request.picking_id)
        self.assertTrue(request.sudo().token_digest)
        self.assertTrue(request.sudo().token_secret)
        self.assertTrue(request.sudo().code128_digest)
        self.assertEqual(request.sudo().code128_secret, code128)
        self.assertEqual(self.env['stock.picking'].search_count([('alvina_request_id', '=', request.id)]), 0)
        self.assertEqual(self.env['stock.quant']._get_available_quantity(self.product, self.warehouse.lot_stock_id), 100)
        self.assertEqual(self.env['stock.quant']._get_available_quantity(self.product, self.destination), 0)
        self._redeem(code)
        self.assertEqual(request.state, 'done')

    def test_rpc_defaults_cannot_forge_state_or_audit(self):
        request = self.env['alvina.stock.request'].with_user(self.requester).with_context(
            default_state='done', default_token_digest='forged',
            default_token_secret='forged', default_token_uid='forged',
            default_code128_secret='0' * 40, default_code128_digest='forged',
            default_delivered_by=self.keeper.id, default_requester_id=self.keeper.id,
            default_picking_id=1, default_source_location_id=self.destination.id,
        ).create({'department_id': self.department.id})
        self.assertEqual(request.state, 'draft')
        self.assertEqual(request.requester_id, self.requester)
        self.assertFalse(request.delivered_by)
        self.assertFalse(request.sudo().token_digest)
        self.assertFalse(request.sudo().token_secret)
        self.assertFalse(request.sudo().token_uid)
        self.assertFalse(request.sudo().code128_secret)
        self.assertFalse(request.sudo().code128_digest)
        self.assertFalse(request.sudo().picking_id)
        self.assertFalse(request.source_location_id)

    def test_tracked_stock_and_ordinary_transfer(self):
        self.product.tracking = 'lot'
        # Replace untracked stock with lot-tracked stock in this rolled-back fixture.
        self.env['stock.quant']._update_available_quantity(self.product, self.warehouse.lot_stock_id, -100)
        lot = self.env['stock.lot'].create({'name': 'ALVINA-LOT', 'product_id': self.product.id, 'company_id': self.company.id})
        self.env['stock.quant']._update_available_quantity(self.product, self.warehouse.lot_stock_id, 10, lot_id=lot)
        request, code = self._submitted()
        self._redeem(code)
        self.assertEqual(request.picking_id.move_line_ids.lot_id, lot)
        self.assertEqual(self.env['stock.quant']._get_available_quantity(self.product, self.destination, lot_id=lot), 5)
        ordinary = self.env['stock.picking'].with_user(self.keeper).create({
            'picking_type_id': self.warehouse.int_type_id.id,
            'location_id': self.warehouse.lot_stock_id.id, 'location_dest_id': self.destination.id,
            'move_ids': [fields.Command.create({
                'product_id': self.product.id, 'product_uom_qty': 1,
                'product_uom': self.product.uom_id.id,
                'location_id': self.warehouse.lot_stock_id.id, 'location_dest_id': self.destination.id,
            })],
        })
        ordinary.action_confirm()
        ordinary.action_assign()
        ordinary.move_ids.picked = True
        ordinary.button_validate()
        self.assertEqual(ordinary.state, 'done')

    def test_nonfinite_quantity_denied(self):
        for quantity in (float('nan'), float('inf'), -1):
            with self.assertRaises(ValidationError):
                self._request(quantity)

    def test_draft_cancellation_and_config_freeze(self):
        request = self._request()
        request._cancel('Solicitante desiste')
        self.assertEqual(request.cancelled_by, self.requester)
        pending = self._request()
        pending.action_submit()
        with self.assertRaises(UserError):
            self.department.write({'location_id': self.destination.id})
        with self.assertRaises(AccessError):
            self.department.with_user(self.keeper).write({'name': 'Cambio sin rol'})

    def test_cross_company_qr_denied(self):
        request, code = self._submitted()
        code128 = self._code128(request)
        company_b = self.env['res.company'].create({'name': 'Otra empresa QR'})
        keeper_b = self.env['res.users'].with_context(no_reset_password=True).create({
            'name': 'Almacenero B', 'login': 'alvina-keeper-b',
            'company_id': company_b.id, 'company_ids': [fields.Command.set(company_b.ids)],
            'group_ids': [fields.Command.set(self.env.ref('alvina_stock_request.group_warehouse').ids)],
        })
        with self.assertRaises(AccessError):
            self._redeem(code, keeper_b)
        with self.assertRaises(AccessError):
            self._redeem(code128, keeper_b)
        self.assertEqual(request.state, 'submitted')
        self._redeem(code)

    def test_link_creation_and_defaults_cannot_bypass_guard(self):
        request, code = self._submitted()
        self._redeem(code)
        picking = request.picking_id
        with self.assertRaises(AccessError), self.cr.savepoint():
            self.env['stock.picking'].with_user(self.keeper).with_context(
                default_alvina_request_id=request.id,
            ).create({
                'picking_type_id': self.warehouse.int_type_id.id,
                'location_id': self.warehouse.lot_stock_id.id,
                'location_dest_id': self.destination.id,
            })
        with self.assertRaises(AccessError), self.cr.savepoint():
            self.env['stock.move'].with_user(self.keeper).with_context(
                default_alvina_request_line_id=request.line_ids.id,
            ).create({
                'product_id': self.product.id, 'product_uom_qty': 1,
                'product_uom': self.product.uom_id.id,
                'location_id': self.warehouse.lot_stock_id.id,
                'location_dest_id': self.destination.id,
            })
        with self.assertRaises(AccessError), self.cr.savepoint():
            self.env['stock.move.line'].with_user(self.keeper).with_context(
                default_move_id=picking.move_ids.id,
            ).create({
                'company_id': self.company.id, 'product_id': self.product.id,
                'product_uom_id': self.product.uom_id.id, 'quantity': 0,
                'location_id': self.warehouse.lot_stock_id.id,
                'location_dest_id': self.destination.id,
            })
        self.assertEqual(picking.move_ids.quantity, 5)

    def test_qr_secrets_and_transient_owner_are_private(self):
        request, code = self._submitted()
        for user in (self.requester, self.keeper):
            for field in ('token_digest', 'token_uid', 'token_secret', 'code128_secret', 'code128_digest'):
                with self.assertRaises(AccessError):
                    request.with_user(user).read([field])
        with self.assertRaises(AccessError):
            request.action_show_qr()
        with self.assertRaises(AccessError):
            request.with_user(self.other_requester).action_show_qr()
        action = request.with_user(self.requester).action_show_qr()
        wizard = self.env[action['res_model']].with_user(self.requester).browse(action['res_id'])
        self.assertEqual(wizard.code, code)
        for user in (self.other_requester, self.keeper):
            for field in ('code', 'code128_code', 'code128_image'):
                with self.assertRaises(AccessError):
                    wizard.with_user(user).read([field])

    def test_qr_and_cancel_wizard_creation_cannot_bypass_ownership(self):
        request, code = self._submitted()
        qr = self.env['alvina.stock.qr.wizard']
        for user in (self.other_requester, self.keeper):
            with self.assertRaises(AccessError):
                qr.with_user(user).create({'request_id': request.id})
            with self.assertRaises(AccessError):
                qr.with_user(user).with_context(default_request_id=request.id).create({})
        with self.assertRaises(AccessError):
            qr.with_user(self.requester).create({'request_id': request.id, 'code': 'forged'})
        wizard = qr.with_user(self.requester).with_context(
            default_request_id=request.id, default_code='forged',
        ).create({})
        self.assertEqual(wizard.code, code)
        self.assertEqual(wizard.request_id, request)
        with self.assertRaises(AccessError):
            wizard.write({'code': 'forged'})
        with self.assertRaises(AccessError):
            qr.with_user(self.requester).create({'request_id': request.id, 'code128_code': '0' * 40})
        with self.assertRaises(AccessError):
            wizard.write({'code128_code': '0' * 40})
        with self.assertRaises(AccessError):
            wizard.write({'request_id': self._request().id})
        self.assertEqual(wizard.code, code)
        self.assertEqual(wizard.request_id, request)

        draft = self._request()
        cancellations = self.env['alvina.stock.cancel.wizard']
        for user in (self.other_requester, self.keeper):
            with self.assertRaises(AccessError):
                cancellations.with_user(user).create({'request_id': draft.id, 'reason': 'Ajeno'})
            with self.assertRaises(AccessError):
                cancellations.with_user(user).with_context(default_request_id=draft.id).create({'reason': 'Ajeno'})
        cancel = cancellations.with_user(self.requester).with_context(
            default_request_id=draft.id, default_reason='Ya no se necesita',
        ).create({})
        self.assertEqual(cancel.request_id, draft)
        with self.assertRaises(AccessError):
            cancel.write({'request_id': request.id})
        cancel.write({'reason': 'Solicitante desiste'})
        cancel.action_cancel()
        self.assertEqual(draft.state, 'cancelled')
        self.assertEqual(draft.cancelled_by, self.requester)
        self.assertEqual(draft.cancellation_reason, 'Solicitante desiste')
        self.assertEqual(request.state, 'submitted')
        self.assertTrue(request.sudo().token_digest)

    def test_code128_preserves_leading_zeros_and_reopens_without_rotation(self):
        import base64
        import hashlib
        from unittest.mock import patch

        with patch('secrets.randbelow', return_value=0):
            request, qr_code = self._submitted()
        code128 = self._code128(request)
        self.assertEqual(code128, '0' * 40)
        self.assertEqual(request.sudo().code128_digest, hashlib.sha256(code128.encode()).hexdigest())
        for _ in range(2):
            action = request.with_user(self.requester).action_show_qr()
            wizard = self.env[action['res_model']].with_user(self.requester).browse(action['res_id'])
            self.assertEqual(wizard.code, qr_code)
            self.assertEqual(wizard.code128_code, code128)
            self.assertTrue(base64.b64decode(wizard.code128_image).startswith(b'\x89PNG\r\n\x1a\n'))
            wizard.unlink()
        second, _ = self._submitted()
        self.assertNotEqual(self._code128(second), code128)
        self.assertFalse(request.picking_id)
        self._redeem(code128)
        self.assertEqual(request.state, 'done')

    def test_code128_delivery_invalidates_both_formats_and_prescanned_qr(self):
        request, qr_code = self._submitted()
        code128 = self._code128(request)
        qr_review = self._scan(qr_code)
        barcode_review = self._scan(code128)
        self.assertEqual(qr_review.request_id, request)
        self.assertEqual(barcode_review.request_id, request)
        barcode_review.action_redeem()
        self.assertEqual(request.state, 'done')
        self.assertEqual(request.picking_id.state, 'done')
        self.assertEqual(request.delivered_by, self.keeper)
        for field in ('token_digest', 'token_secret', 'token_uid', 'code128_secret', 'code128_digest'):
            self.assertFalse(request.sudo()[field])
        for credential in (qr_code, code128):
            with self.assertRaises(UserError):
                self._redeem(credential)
        with self.assertRaises(UserError):
            qr_review.action_redeem()
        self.assertEqual(self.env['stock.picking'].search_count([('alvina_request_id', '=', request.id)]), 1)
        self.assertEqual(self.env['stock.quant']._get_available_quantity(self.product, self.destination), 5)

    def test_code128_rejects_tampering_nonascii_and_document_references(self):
        request, _ = self._submitted()
        code128 = self._code128(request)
        changed = ('0' if code128[0] != '0' else '1') + code128[1:]
        arabic_digits = ''.join(chr(0x0660 + int(digit)) for digit in code128)
        fullwidth_digits = ''.join(chr(0xFF10 + int(digit)) for digit in code128)
        invalid = (code128[:-1], code128 + '0', changed, arabic_digits, fullwidth_digits,
                   code128[:20] + ' ' + code128[21:], 'A' + code128[1:], request.name,
                   str(request.id).zfill(40))
        for credential in invalid:
            with self.assertRaises(UserError):
                self._scan(credential)
        review = self._scan(code128)
        review.write({'code': changed})
        self.assertFalse(review.request_id)
        with self.assertRaises(UserError):
            review.action_scan()
        with self.assertRaises(UserError):
            review.action_redeem()
        self.assertEqual(request.state, 'submitted')
        self.assertEqual(request.sudo().code128_secret, code128)
        self.assertFalse(request.picking_id)
        self.assertEqual(self.env['stock.quant']._get_available_quantity(self.product, self.destination), 0)

    def test_legacy_pending_backfill_is_idempotent_and_keeps_original_qr(self):
        import hashlib

        request, qr_code = self._submitted()
        secured = request.sudo()
        legacy = (secured.token_uid, secured.token_digest, secured.token_secret)
        submitted_at = request.submitted_at
        request._set({'code128_secret': False, 'code128_digest': False})
        owner = request.with_user(self.requester)
        owner._ensure_code128()
        first = self._code128(request)
        self.assertRegex(first, r'^[0-9]{40}$')
        self.assertEqual(secured.code128_digest, hashlib.sha256(first.encode()).hexdigest())
        owner._ensure_code128()
        self.assertEqual(self._code128(request), first)
        self.assertEqual((secured.token_uid, secured.token_digest, secured.token_secret), legacy)
        self.assertEqual(request.submitted_at, submitted_at)
        self.assertEqual(request.state, 'submitted')
        self.assertFalse(request.picking_id)
        action = owner.action_show_qr()
        wizard = self.env[action['res_model']].with_user(self.requester).browse(action['res_id'])
        self.assertEqual(wizard.code, qr_code)
        self.assertEqual(wizard.code128_code, first)
        self._redeem(qr_code)
        self.assertEqual(request.state, 'done')
        with self.assertRaises(UserError):
            self._redeem(first)

    def test_code128_inconsistent_pairs_reject_without_rotating_credentials(self):
        import hashlib

        request, qr_code = self._submitted()
        code128 = self._code128(request)
        secured = request.sudo()
        qr = (secured.token_uid, secured.token_digest, secured.token_secret)
        digest = secured.code128_digest
        altered = ('0' if code128[0] != '0' else '1') + code128[1:]
        cases = (
            {'code128_secret': code128, 'code128_digest': False},
            {'code128_secret': False, 'code128_digest': digest},
            {'code128_secret': code128, 'code128_digest': '0' * 64},
            {'code128_secret': altered, 'code128_digest': digest},
            {'code128_secret': 'A' * 40, 'code128_digest': hashlib.sha256(('A' * 40).encode()).hexdigest()},
        )
        for values in cases:
            request._set(values)
            pair = (secured.code128_secret, secured.code128_digest)
            with self.assertRaises(UserError):
                request.with_user(self.requester)._ensure_code128()
            with self.assertRaises(UserError):
                self._scan(code128)
            self.assertEqual((secured.code128_secret, secured.code128_digest), pair)
            self.assertEqual((secured.token_uid, secured.token_digest, secured.token_secret), qr)
            self.assertEqual(request.state, 'submitted')
            self.assertFalse(request.picking_id)
        request._set({'code128_secret': code128, 'code128_digest': digest})
        self._redeem(qr_code)
        self.assertEqual(request.state, 'done')

    def test_legacy_backfill_rejects_invalid_qr_before_creating_code128(self):
        request, _ = self._submitted()
        secured = request.sudo()
        qr = {'token_uid': secured.token_uid, 'token_digest': secured.token_digest,
              'token_secret': secured.token_secret}
        for corruption in ({'token_uid': 'invalid-uuid'}, {'token_digest': False},
                           {'token_secret': 'A' * 43}):
            request._set({**qr, **corruption, 'code128_secret': False, 'code128_digest': False})
            with self.assertRaises(UserError):
                request.with_user(self.requester)._ensure_code128()
            self.assertFalse(secured.code128_secret)
            self.assertFalse(secured.code128_digest)
            for field, value in {**qr, **corruption}.items():
                self.assertEqual(secured[field], value)
            self.assertEqual(request.state, 'submitted')
        request._set(qr)
        request.with_user(self.requester)._ensure_code128()
        self.assertTrue(secured.code128_secret)

    def test_code128_digest_uniqueness_is_enforced_by_postgresql(self):
        from psycopg2.errors import UniqueViolation
        from odoo.tools import mute_logger

        first, _ = self._submitted()
        second, _ = self._submitted()
        original_digest = second.sudo().code128_digest
        self.env.flush_all()
        # Deliberately bypass Python checks to prove the database itself prevents
        # two requests from sharing a credential lookup digest.
        with self.assertRaises(UniqueViolation), mute_logger('odoo.sql_db'), self.cr.savepoint():
            self.cr.execute('UPDATE alvina_stock_request SET code128_digest = %s WHERE id = %s',
                            [first.sudo().code128_digest, second.id])
        second.invalidate_recordset()
        self.assertEqual(second.sudo().code128_digest, original_digest)
        self.assertEqual(first.state, 'submitted')
        self.assertEqual(second.state, 'submitted')

    def test_reader_assets_and_effective_wizard_views_are_connected(self):
        from lxml import etree

        assets, _ = self.env['ir.qweb']._get_asset_content('web.assets_backend')
        paths = {asset['url'].lstrip('/') for asset in assets}
        self.assertTrue({
            'alvina_stock_request/static/src/js/code_reader.js',
            'alvina_stock_request/static/src/js/pickup_code_field.js',
            'alvina_stock_request/static/src/xml/pickup_code_field.xml',
            'alvina_stock_request/static/src/scss/pickup_code_field.scss',
        }.issubset(paths))
        pickup_model = self.env['alvina.stock.pickup.wizard'].with_user(self.keeper)
        pickup = etree.fromstring(pickup_model.get_view(
            self.env.ref('alvina_stock_request.pickup_form').id, view_type='form')['arch'])
        self.assertTrue(pickup.xpath("//field[@name='code' and @widget='alvina_pickup_code']"))
        self.assertTrue(pickup.xpath("//field[@name='state']"))
        self.assertTrue(pickup.xpath("//button[@name='action_scan' and @type='object']"))
        self.assertTrue(pickup.xpath("//button[@name='action_redeem' and @type='object']"))
        self.assertTrue({'product_id', 'quantity', 'product_uom_id', 'available_quantity'}.issubset(
            set(pickup.xpath("//field[@name='line_ids']/list/field/@name"))))
        receipt_model = self.env['alvina.stock.qr.wizard'].with_user(self.requester)
        receipt = etree.fromstring(receipt_model.get_view(
            self.env.ref('alvina_stock_request.qr_form').id, view_type='form')['arch'])
        self.assertTrue(receipt.xpath("//field[@name='image' and @widget='image']"))
        self.assertTrue(receipt.xpath("//field[@name='code128_image' and @widget='image']"))
        self.assertTrue({'product_id', 'quantity', 'product_uom_id'}.issubset(
            set(receipt.xpath("//field[@name='line_ids']/list/field/@name"))))
        action = self.env.ref('alvina_stock_request.action_pickup')
        self.assertEqual(action.name, 'Autorizar retiro')
        self.assertEqual(action.res_model, pickup_model._name)
        self.assertEqual(action.target, 'new')

    def test_serial_numbers_preserved(self):
        self.product.tracking = 'serial'
        self.env['stock.quant']._update_available_quantity(self.product, self.warehouse.lot_stock_id, -100)
        lots = self.env['stock.lot'].create([
            {'name': 'ALVINA-SERIAL-1', 'product_id': self.product.id, 'company_id': self.company.id},
            {'name': 'ALVINA-SERIAL-2', 'product_id': self.product.id, 'company_id': self.company.id},
        ])
        for lot in lots:
            self.env['stock.quant']._update_available_quantity(self.product, self.warehouse.lot_stock_id, 1, lot_id=lot)
        request, code = self._submitted(2)
        self._redeem(code)
        self.assertEqual(request.picking_id.move_line_ids.lot_id, lots)
        self.assertEqual(request.picking_id.state, 'done')
        for lot in lots:
            self.assertEqual(self.env['stock.quant']._get_available_quantity(self.product, self.destination, lot_id=lot), 1)
