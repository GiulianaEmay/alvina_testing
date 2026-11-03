"""One shipment produces one guide; buyer confirmation records its real receipt."""
from copy import deepcopy
from unittest.mock import patch

from odoo import Command
from odoo.exceptions import AccessError, ValidationError
from odoo.tests import TransactionCase, tagged

from .test_supply_companies import SupplyCompanyFixture


@tagged('post_install', '-at_install')
class TestDispatchNote(SupplyCompanyFixture, TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        (cls.supply_company | cls.branch_company).write({
            'account_fiscal_country_id': cls.env.ref('base.pe').id,
        })
        cls.keeper.group_ids = [Command.link(cls.env.ref('sales_team.group_sale_salesman').id)]
        cls.warehouse.delivery_steps = 'ship_only'
        cls.warehouse.out_type_id.create_backorder = 'always'
        cls.receiving_warehouse = cls.env['stock.warehouse'].search([
            ('company_id', '=', cls.branch_company.id)], limit=1)
        if not cls.receiving_warehouse:
            cls.receiving_warehouse = cls.env['stock.warehouse'].create({
                'name': 'UPAO dispatch-note reception', 'code': 'DNRCP',
                'company_id': cls.branch_company.id,
            })
        cls.receiving_warehouse.reception_steps = 'one_step'
        cls.receiving_warehouse.in_type_id.create_backorder = 'always'
        cls.branch.resource_ref.company_id = False
        cls.supply_company.partner_id.company_id = False
        cls.branch.write({
            'branch_code': 'UPAO-DISPATCH-NOTE',
            'purchase_picking_type_id': cls.receiving_warehouse.in_type_id.id,
        })
        cls.shared_product.write({
            'purchase_ok': True, 'sale_ok': True, 'list_price': 2,
            'taxes_id': [Command.clear()], 'supplier_taxes_id': [Command.clear()],
        })
        cls.pricelist = cls.env['product.pricelist'].create({
            'name': 'Industry dispatch-note prices',
            'currency_id': cls.supply_company.currency_id.id,
            'company_id': cls.supply_company.id,
        })
        cls.configuration = cls.env['alvina.supply.configuration'].create({
            'company_id': cls.supply_company.id,
            'sales_user_id': cls.keeper.id, 'pricelist_id': cls.pricelist.id,
            'enabled': True,
        })
        cls.env['stock.quant']._update_available_quantity(
            cls.shared_product, cls.warehouse.lot_stock_id, 100)

    def _request_and_picking(self, requested=10):
        request = self._request(product=self.shared_product, quantity=requested).sudo()
        self.assertEqual(request.sale_order_id.state, 'sale')
        self.assertEqual(request.purchase_order_id.state, 'purchase')
        picking = self._as(request.sale_order_id, self.keeper).picking_ids.filtered(
            lambda item: item.picking_type_code == 'outgoing')
        self.assertEqual(len(picking), 1)
        return request, picking

    def _complete(self, picking, quantity):
        picking.action_assign()
        moves = picking.move_ids.filtered(lambda item: item.product_id == self.shared_product)
        self.assertEqual(len(moves), 1)
        moves.write({'quantity': quantity, 'picked': True})
        picking.with_context(skip_backorder=True).button_validate()
        self.assertEqual(picking.state, 'done')

    def _emit(self, picking):
        action = self._as(picking, self.keeper).action_emit_alvina_dispatch()
        self.assertEqual(action['res_model'], 'alvina.branch.dispatch')
        return self.env['alvina.branch.dispatch'].browse(action['res_id'])

    def _shipment(self, requested=10, shipped=8):
        request, picking = self._request_and_picking(requested)
        self._complete(picking, shipped)
        guide = picking.sudo().alvina_emitted_dispatch_ids
        self.assertEqual(len(guide), 1, 'Completing the shipment must issue its guide automatically')
        self.assertEqual(self._emit(picking), guide)
        return request, picking, guide

    def _receipt_wizard(self, guide, user=None):
        user = user or self.requester
        action = self._as(guide, user).action_open_receipt()
        self.assertEqual(action['res_model'], 'alvina.branch.dispatch.receipt')
        return self._as(self.env['alvina.branch.dispatch.receipt'], user).browse(action['res_id'])

    def _receive(self, guide, quantity, note=None, user=None):
        wizard = self._receipt_wizard(guide, user)
        self.assertEqual(len(wizard.line_ids), 1)
        wizard.line_ids.quantity = quantity
        if note is not None:
            wizard.note = note
        wizard.action_confirm()
        return guide.sudo().request_id.purchase_order_id.picking_ids.filtered(
            lambda picking: picking.alvina_dispatch_id == guide)

    def test_emission_requires_validated_shipment_and_does_not_validate_stock(self):
        request, picking = self._request_and_picking()
        move_states = {move.id: move.state for move in picking.move_ids}
        with self.assertRaises(ValidationError), self.cr.savepoint():
            self._emit(picking)
        self.assertEqual({move.id: move.state for move in picking.move_ids}, move_states)
        self.assertNotEqual(picking.state, 'done')
        self.assertFalse(picking.alvina_emitted_dispatch_ids)
        self.assertEqual(request.line_ids.qty_supplied, 0)

    def test_one_immutable_note_per_shipment_and_no_automatic_accounting(self):
        request, picking, guide = self._shipment()
        snapshot = deepcopy(guide.snapshot_json)
        counts = {model: self.env[model].sudo().search_count([])
                  for model in ('account.move', 'stock.move', 'purchase.order', 'sale.order')}
        self.assertEqual(self._emit(picking), guide)
        self.assertEqual(self.env['alvina.branch.dispatch'].search_count([
            ('picking_id', '=', picking.id)]), 1)
        self.assertEqual(snapshot['lines'][0]['requested'], 10)
        self.assertEqual(snapshot['lines'][0]['shipped'], 8)
        self.assertEqual(snapshot['totals']['total'], 16)
        self.assertEqual([(row['requested'], row['supplied'], row['pending'])
                          for row in snapshot['balances']], [(10, 8, 2)])
        self.assertEqual(request.line_ids.qty_pending, 2)
        self.assertTrue(request.purchase_order_id.picking_ids)
        self.assertNotEqual(request.purchase_order_id.picking_ids.state, 'done')
        for model, count in counts.items():
            self.assertEqual(self.env[model].sudo().search_count([]), count)
        self.assertEqual(guide.snapshot_json, snapshot)

    def test_snapshot_and_pdf_keep_issued_description_and_price(self):
        request, picking, guide = self._shipment()
        snapshot = deepcopy(guide.snapshot_json)
        original_name = self.shared_product.name
        self.shared_product.write({'name': 'Changed after issuing the dispatch note', 'list_price': 999})
        self.env['product.pricelist.item'].create({
            'pricelist_id': self.pricelist.id, 'applied_on': '1_product',
            'product_tmpl_id': self.shared_product.product_tmpl_id.id,
            'compute_price': 'fixed', 'fixed_price': 876,
        })
        payload = self._as(guide, self.keeper)._alvina_dispatch_note_payload()
        self.assertEqual(payload['dispatch'], snapshot)
        self.assertEqual(payload['dispatch']['totals']['total'], 16)
        self.assertEqual(payload['dispatch']['lines'][0]['product'], original_name)
        report = self._as(self.env['ir.actions.report'], self.keeper)
        html, output_type = report._render_qweb_html(
            'alvina_branch_requisition.action_dispatch_note_report', guide.ids)
        self.assertEqual(output_type, 'html')
        self.assertIn(original_name.encode(), html)
        self.assertNotIn(b'Changed after issuing', html)
        self.assertEqual(request.line_ids.qty_pending, 2)

    def test_buyer_can_render_real_guide_pdf_after_receipt_with_differences(self):
        request, picking, guide = self._shipment()
        self._receive(guide, 7, 'Una unidad no llegó a la sucursal.')
        snapshot = deepcopy(guide.snapshot_json)
        confirmation = deepcopy(guide.receipt_confirmation_json)
        report = self._as(self.env['ir.actions.report'], self.requester)
        if report.get_wkhtmltopdf_state() == 'install':
            self.fail('wkhtmltopdf is required; run tools/setup_test_pdf.py')
        pdf, output_type = report.with_context(force_report_rendering=True)._render_qweb_pdf(
            'alvina_branch_requisition.action_dispatch_note_report', guide.ids)
        self.assertEqual(output_type, 'pdf')
        self.assertTrue(pdf.startswith(b'%PDF-'))
        self.assertGreater(len(pdf), 2000)
        self.assertEqual(guide.snapshot_json, snapshot)
        self.assertEqual(guide.receipt_confirmation_json, confirmation)
        self.assertEqual(request.line_ids.qty_pending, 2)

    def test_opening_receipt_does_not_validate_inventory_or_confirm_conformity(self):
        request, picking, guide = self._shipment()
        receipt = request.purchase_order_id.picking_ids
        warehouse_stock = self.shared_product.with_context(
            warehouse_id=self.receiving_warehouse.id).qty_available
        wizard = self._receipt_wizard(guide)
        self.assertEqual(wizard.line_ids.quantity, 0)
        self.assertNotEqual(receipt.state, 'done')
        self.assertFalse(receipt.alvina_dispatch_id)
        self.assertEqual(guide.receipt_state, 'pending')
        self.assertFalse(guide.receipt_confirmation_json)
        self.assertEqual(request.line_ids.qty_received, 0)
        self.assertEqual(self.shared_product.with_context(
            warehouse_id=self.receiving_warehouse.id).qty_available, warehouse_stock)

    def test_eight_shipped_and_received_is_conform_with_two_still_to_dispatch(self):
        request, picking, guide = self._shipment()
        receipt = self._receive(guide, 8)
        self.assertEqual(len(receipt), 1)
        self.assertEqual(receipt.state, 'done')
        self.assertEqual(receipt.company_id, self.branch_company)
        self.assertEqual(guide.receipt_state, 'conform')
        row = self._as(guide, self.requester)._alvina_dispatch_note_payload()['receipt']['lines'][0]
        self.assertEqual((row['shipped'], row['received'], row['difference']), (8, 8, 0))
        self.assertEqual(request.line_ids.qty_pending, 2)
        self.assertEqual(request.line_ids.qty_received, 8)
        self.assertEqual(request.line_ids.qty_supplied, 8)
        self.assertEqual(guide.confirmed_by, self.requester)
        self.assertTrue(guide.confirmed_date)

    def test_native_overdelivery_records_twenty_two_received_without_changing_demand(self):
        request, picking, guide = self._shipment(requested=20, shipped=22)
        source_stock = self.shared_product.with_context(
            warehouse_id=self.warehouse.id).qty_available
        receipt = self._receive(guide, 22)
        self.assertEqual(receipt.state, 'done')
        self.assertEqual(guide.receipt_state, 'conform')
        self.assertEqual(request.line_ids.product_qty, 20)
        self.assertEqual(request.line_ids.qty_supplied, 22)
        self.assertEqual(request.line_ids.qty_received, 22)
        self.assertEqual(request.line_ids.qty_pending, 0)
        self.assertEqual(source_stock, 78)
        self.assertEqual(self.shared_product.with_context(
            warehouse_id=self.warehouse.id).qty_available, source_stock,
            'Buyer receipt must not subtract Industry stock a second time')
        self.assertEqual(self.shared_product.with_context(
            warehouse_id=self.receiving_warehouse.id).qty_available, 22)
        row = guide.receipt_confirmation_json['lines'][0]
        self.assertEqual((row['shipped'], row['received'], row['difference']), (22, 22, 0))

    def test_ten_requested_eight_shipped_seven_received_has_distinct_balances(self):
        request, picking, guide = self._shipment()
        wizard = self._receipt_wizard(guide)
        wizard.line_ids.quantity = 7
        with self.assertRaises(ValidationError), self.cr.savepoint():
            wizard.action_confirm()
        self.assertEqual(request.line_ids.qty_received, 0)
        self.assertEqual(guide.receipt_state, 'pending')
        wizard.note = 'Se recibió una unidad menos que en la guía.'
        wizard.action_confirm()
        self.assertEqual(guide.receipt_state, 'discrepancy')
        row = self._as(guide, self.requester)._alvina_dispatch_note_payload()['receipt']['lines'][0]
        self.assertEqual((row['shipped'], row['received'], row['difference']), (8, 7, 1))
        self.assertEqual(request.line_ids.qty_pending, 2,
                         'Receipt differences must not subtract requested demand again')
        self.assertEqual(request.line_ids.qty_received, 7)
        self.assertEqual(request.line_ids.qty_supplied, 8)
        confirmation = deepcopy(guide.receipt_confirmation_json)
        with self.assertRaises(ValidationError), self.cr.savepoint():
            wizard.action_confirm()
        with self.assertRaises(ValidationError), self.cr.savepoint():
            self._receipt_wizard(guide)
        self.assertEqual(guide.receipt_confirmation_json, confirmation)
        with self.assertRaises(AccessError), self.cr.savepoint():
            self._as(guide, self.requester).receipt_note = 'Alteration after confirmation'

    def test_total_nonreceipt_requires_reason_and_creates_no_done_stock(self):
        request, picking, guide = self._shipment()
        wizard = self._receipt_wizard(guide)
        with self.assertRaises(ValidationError), self.cr.savepoint():
            wizard.action_confirm()
        wizard.note = 'La mercadería no llegó a la sucursal.'
        wizard.action_confirm()
        self.assertEqual(guide.receipt_state, 'discrepancy')
        self.assertFalse(request.purchase_order_id.picking_ids.filtered(
            lambda receipt: receipt.state == 'done'))
        self.assertEqual(request.line_ids.qty_received, 0)
        self.assertEqual(request.line_ids.qty_pending, 2)
        row = guide.receipt_confirmation_json['lines'][0]
        self.assertEqual((row['shipped'], row['received'], row['difference']), (8, 0, 8))

    def test_receiving_more_than_guide_or_invalid_quantity_changes_no_stock(self):
        request, picking, guide = self._shipment()
        wizard = self._receipt_wizard(guide)
        for quantity in (9, -1, float('nan'), float('inf')):
            with self.subTest(quantity=quantity):
                with self.assertRaises(ValidationError), self.cr.savepoint():
                    wizard.line_ids.write({'quantity': quantity})
                    wizard.note = 'No debe permitir cantidades inválidas.'
                    wizard.action_confirm()
                self.assertEqual(guide.receipt_state, 'pending')
                self.assertEqual(request.line_ids.qty_received, 0)
                self.assertFalse(request.purchase_order_id.picking_ids.filtered(
                    lambda receipt: receipt.state == 'done'))

    def test_receipt_preserves_company_memberships_and_creates_no_invoice(self):
        request, picking, guide = self._shipment()
        company_ids = {user.id: user.company_ids.ids for user in (self.requester, self.keeper)}
        groups = self.requester.group_ids
        invoice_count = self.env['account.move'].sudo().search_count([])
        self._receive(guide, 8)
        self.assertEqual(self.requester.group_ids, groups)
        self.assertEqual(self.env['account.move'].sudo().search_count([]), invoice_count)
        self.assertFalse(request.sale_order_id.invoice_ids)
        self.assertFalse(request.purchase_order_id.invoice_ids)
        for user in (self.requester, self.keeper):
            self.assertEqual(user.company_ids.ids, company_ids[user.id])

    def test_emission_and_confirmation_respect_branch_and_company_boundaries(self):
        request, picking, guide = self._shipment()
        for user in (self.outsider, self.second_requester):
            with self.subTest(user=user.login):
                with self.assertRaises(AccessError):
                    self._as(guide, user).read(['name', 'snapshot_json'])
                with self.assertRaises(AccessError), self.cr.savepoint():
                    self._receipt_wizard(guide, user)
        with self.assertRaises(AccessError), self.cr.savepoint():
            self._receipt_wizard(guide, self.keeper)
        with self.assertRaises(AccessError), self.cr.savepoint():
            self._as(picking, self.requester).action_emit_alvina_dispatch()
        self.assertEqual(self.requester.company_ids, self.branch_company)
        self.assertEqual(self.keeper.company_ids, self.supply_company)

    def test_direct_guide_and_history_mutation_is_forbidden(self):
        request, picking, guide = self._shipment()
        for user in (self.requester, self.keeper):
            with self.subTest(user=user.login):
                with self.assertRaises(AccessError), self.cr.savepoint():
                    self._as(self.env['alvina.branch.dispatch'], user).create({
                        'picking_id': picking.id, 'request_id': request.id})
                with self.assertRaises(AccessError), self.cr.savepoint():
                    self._as(guide, user).write({'snapshot_json': {'lines': []}})
                with self.assertRaises(AccessError), self.cr.savepoint():
                    self._as(guide, user).unlink()

    def test_manual_guide_link_and_direct_purchase_validation_cannot_bypass_confirmation(self):
        request, picking, guide = self._shipment()
        receipt = self._as(request.purchase_order_id.picking_ids, self.requester)
        with self.assertRaises(AccessError), self.cr.savepoint():
            receipt.alvina_dispatch_id = guide.id
        with self.assertRaises(AccessError), self.cr.savepoint():
            receipt.move_ids.origin_returned_move_id = picking.move_ids.id
            receipt._action_done()
        with self.assertRaises(AccessError), self.cr.savepoint():
            self._complete(receipt, 8)
        self.assertEqual(guide.receipt_state, 'pending')
        self.assertEqual(request.line_ids.qty_received, 0)
        self.assertFalse(receipt.alvina_dispatch_id)

    def test_direct_inventory_completion_and_forged_context_cannot_bypass_receipt_service(self):
        request, picking, guide = self._shipment()
        receipt = request.purchase_order_id.picking_ids
        with self.assertRaises(AccessError), self.cr.savepoint():
            receipt.with_context(
                alvina_receiving=True, alvina_dispatch_receipt=True,
                default_alvina_dispatch_id=guide.id)._action_done()
        with self.assertRaises(AccessError), self.cr.savepoint():
            receipt.alvina_dispatch_id = guide.id
        self.assertNotEqual(receipt.state, 'done')
        self.assertEqual(request.line_ids.qty_received, 0)
        self.assertEqual(guide.receipt_state, 'pending')

    def test_wrong_order_or_source_operation_cannot_claim_guide(self):
        request, picking, guide = self._shipment()
        with self.assertRaises(AccessError), self.cr.savepoint():
            picking.alvina_dispatch_id = guide.id
        another = self._request(product=self.shared_product, quantity=4).sudo()
        with self.assertRaises(AccessError), self.cr.savepoint():
            self._as(another.purchase_order_id.picking_ids, self.requester).alvina_dispatch_id = guide.id
        self.assertFalse(another.purchase_order_id.picking_ids.alvina_dispatch_id)

    def test_receipt_backorders_and_copies_do_not_claim_same_guide(self):
        request, picking, guide = self._shipment()
        receipt = self._receive(guide, 7, 'Falta una unidad respecto de la guía.')
        self.assertTrue(receipt.backorder_ids)
        self.assertFalse(receipt.backorder_ids.alvina_dispatch_id)
        copied = receipt.copy()
        self.assertFalse(copied.alvina_dispatch_id)
        copied.action_cancel()
        self.assertEqual(guide.line_ids.qty_received, 7)
        self.assertEqual(guide.line_ids.qty_difference, 1)

    def test_later_shipment_and_receipt_keep_the_first_snapshot_immutable(self):
        request, picking, first_guide = self._shipment()
        self._receive(first_guide, 8)
        first_snapshot = deepcopy(first_guide.snapshot_json)
        first_confirmation = deepcopy(first_guide.receipt_confirmation_json)
        backorder = picking.backorder_ids
        self.assertEqual(len(backorder), 1)
        self._complete(backorder, 2)
        second_guide = self._emit(backorder)
        self._receive(second_guide, 2)
        self.assertNotEqual(second_guide, first_guide)
        self.assertEqual(second_guide.snapshot_json['lines'][0]['shipped'], 2)
        self.assertEqual(second_guide.snapshot_json['totals']['total'], 4)
        self.assertEqual([(row['requested'], row['supplied'], row['pending'])
                          for row in second_guide.snapshot_json['balances']], [(10, 10, 0)])
        self.assertEqual(first_guide.snapshot_json, first_snapshot)
        self.assertEqual(first_guide.receipt_confirmation_json, first_confirmation)
        self.assertEqual(request.line_ids.qty_received, 10)
        self.assertEqual(request.line_ids.qty_pending, 0)

    def test_snapshot_balances_include_products_not_dispatched_at_all(self):
        second_product = self.env['product.product'].create({
            'name': 'Supplies not included in this shipment',
            'default_code': 'GUIDE-NOT-SHIPPED',
            'company_id': False, 'categ_id': self.shared_product.categ_id.id,
            'sale_ok': True, 'purchase_ok': True, 'alvina_requisition_enabled': True,
            'is_storable': True, 'list_price': 2,
            'taxes_id': [Command.clear()], 'supplier_taxes_id': [Command.clear()],
        })
        self.assertTrue(second_product.exists())
        request = self._request(product=self.shared_product, line_ids=[
            Command.create({'product_id': self.shared_product.id, 'product_qty': 10}),
            Command.create({'product_id': second_product.id, 'product_qty': 5}),
        ]).sudo()
        picking = self._as(request.sale_order_id.picking_ids, self.keeper)
        untouched = picking.move_ids.filtered(lambda move: move.product_id == second_product)
        untouched.write({'quantity': 0, 'picked': True})
        self._complete(picking, 8)
        guide = self._emit(picking)
        balances = {row['code']: row for row in guide.snapshot_json['balances']}
        self.assertEqual((balances[self.shared_product.default_code]['requested'],
                          balances[self.shared_product.default_code]['supplied'],
                          balances[self.shared_product.default_code]['pending']), (10, 8, 2))
        self.assertEqual((balances[second_product.default_code]['requested'],
                          balances[second_product.default_code]['supplied'],
                          balances[second_product.default_code]['pending']), (5, 0, 5))
        self.assertEqual(len(guide.snapshot_json['lines']), 1)
        self.assertEqual(guide.snapshot_json['lines'][0]['product_id'], self.shared_product.id)
        self.assertEqual(guide.snapshot_json['totals']['total'], 16)

    def test_validated_receipt_cannot_be_reassigned_to_different_guide(self):
        request, picking, first_guide = self._shipment()
        receipt = self._receive(first_guide, 8)
        self._complete(picking.backorder_ids, 2)
        second_guide = self._emit(picking.backorder_ids)
        with self.assertRaises(AccessError), self.cr.savepoint():
            receipt.alvina_dispatch_id = second_guide.id
        self.assertEqual(receipt.alvina_dispatch_id, first_guide)

    def test_requester_purchase_user_can_receive_without_inventory_role(self):
        request, picking, guide = self._shipment()
        self.requester.group_ids = [Command.set([
            self.env.ref('base.group_user').id,
            self.env.ref('purchase.group_purchase_user').id,
            self.env.ref('alvina_branch_requisition.group_requester').id,
        ])]
        self.assertFalse(self.requester.has_group('stock.group_stock_user'))
        self._receive(guide, 8)
        self.assertEqual(guide.receipt_state, 'conform')
        self.assertEqual(request.line_ids.qty_received, 8)
        self.assertFalse(self.requester.has_group('stock.group_stock_user'))

    def test_requester_without_purchase_role_cannot_confirm_receipt(self):
        request, picking, guide = self._shipment()
        self.requester.group_ids = [Command.set([
            self.env.ref('base.group_user').id,
            self.env.ref('alvina_branch_requisition.group_requester').id,
        ])]
        self.assertFalse(self.requester.has_group('purchase.group_purchase_user'))
        with self.assertRaises(AccessError), self.cr.savepoint():
            self._receipt_wizard(guide)
        self.assertEqual(guide.receipt_state, 'pending')

    def test_wizard_cannot_reassign_guide_or_borrow_line_from_another_guide(self):
        request, picking, first_guide = self._shipment()
        other_request, other_picking, other_guide = self._shipment(requested=4, shipped=4)
        wizard = self._receipt_wizard(first_guide)
        with self.assertRaises(AccessError), self.cr.savepoint():
            wizard.dispatch_id = other_guide.id
        with self.assertRaises(AccessError), self.cr.savepoint():
            wizard.line_ids.dispatch_line_id = other_guide.line_ids.id
        with self.assertRaises(AccessError), self.cr.savepoint():
            wizard.write({'line_ids': [Command.create({
                'dispatch_line_id': other_guide.line_ids.id, 'quantity': 1,
            })]})
        self.assertEqual(wizard.line_ids.dispatch_line_id, first_guide.line_ids)
        self.assertEqual(first_guide.receipt_state, 'pending')
        self.assertEqual(other_guide.receipt_state, 'pending')

    def test_missing_guide_line_and_another_users_wizard_are_rejected(self):
        request, picking, guide = self._shipment()
        wizard = self._receipt_wizard(guide)
        with self.assertRaises(AccessError), self.cr.savepoint():
            self._as(wizard, self.outsider).write({'note': 'Unauthorized'})
        with self.assertRaises(AccessError), self.cr.savepoint():
            self._as(wizard, self.outsider).action_confirm()
        wizard.write({'line_ids': [Command.clear()]})
        with self.assertRaises(ValidationError), self.cr.savepoint():
            wizard.action_confirm()
        self.assertEqual(guide.receipt_state, 'pending')
        self.assertEqual(request.line_ids.qty_received, 0)

    def test_second_pending_wizard_cannot_duplicate_receipt(self):
        request, picking, guide = self._shipment()
        first = self._receipt_wizard(guide)
        second = self._receipt_wizard(guide)
        first.line_ids.quantity = 8
        second.line_ids.quantity = 8
        first.action_confirm()
        stock_quantity = request.line_ids.qty_received
        with self.assertRaises(ValidationError), self.cr.savepoint():
            second.action_confirm()
        self.assertEqual(request.line_ids.qty_received, stock_quantity)
        self.assertEqual(stock_quantity, 8)
        self.assertEqual(len(request.purchase_order_id.picking_ids.filtered(
            lambda receipt: receipt.state == 'done')), 1)

    def test_inventory_failure_rolls_back_receipt_and_conformity_together(self):
        request, picking, guide = self._shipment()
        wizard = self._receipt_wizard(guide)
        wizard.line_ids.quantity = 8
        original = type(self.env['stock.picking']).button_validate

        def fail_after_receipt(picking):
            result = original(picking)
            if picking.company_id == self.branch_company:
                raise ValidationError('Simulated receipt failure after inventory validation')
            return result

        with patch.object(type(self.env['stock.picking']), 'button_validate', fail_after_receipt):
            with self.assertRaises(ValidationError), self.cr.savepoint():
                wizard.action_confirm()
        self.assertEqual(guide.receipt_state, 'pending')
        self.assertFalse(guide.receipt_confirmation_json)
        self.assertEqual(request.line_ids.qty_received, 0)
        self.assertFalse(request.purchase_order_id.picking_ids.filtered(
            lambda receipt: receipt.state == 'done'))
        self.assertFalse(request.purchase_order_id.picking_ids.alvina_dispatch_id)

    def test_guide_emission_failure_rolls_back_physical_dispatch(self):
        request, picking = self._request_and_picking()
        source_stock = self.shared_product.with_context(
            warehouse_id=self.warehouse.id).qty_available
        with patch.object(type(self.env['alvina.branch.dispatch']), 'create',
                          side_effect=ValidationError('Simulated guide emission failure')):
            # The action itself owns atomicity: a failed guide must not leave
            # a done stock operation, even if its caller catches the exception.
            with self.assertRaises(ValidationError):
                self._complete(picking, 8)
        self.assertNotEqual(picking.state, 'done')
        self.assertFalse(picking.alvina_emitted_dispatch_ids)
        self.assertEqual(request.line_ids.qty_supplied, 0)
        self.assertEqual(self.shared_product.with_context(
            warehouse_id=self.warehouse.id).qty_available, source_stock)
