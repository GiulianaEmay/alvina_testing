"""Confirmed documents, reservations, physical deliveries and receipts stay distinct."""
from odoo import Command
from odoo.exceptions import AccessError, ValidationError
from odoo.tests import TransactionCase, tagged

from .test_supply_companies import SupplyCompanyFixture


@tagged('post_install', '-at_install')
class TestBranchFulfillment(SupplyCompanyFixture, TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        (cls.supply_company | cls.branch_company).write({
            'account_fiscal_country_id': cls.env.ref('base.pe').id,
        })
        cls.keeper.write({'group_ids': [Command.link(
            cls.env.ref('sales_team.group_sale_salesman').id)]})
        cls.warehouse.delivery_steps = 'ship_only'
        cls.warehouse.out_type_id.create_backorder = 'always'
        cls.receiving_warehouse = cls.env['stock.warehouse'].search([
            ('company_id', '=', cls.branch_company.id)], limit=1)
        if not cls.receiving_warehouse:
            cls.receiving_warehouse = cls.env['stock.warehouse'].create({
                'name': 'UPAO receipt fulfillment test', 'code': 'UFLT',
                'company_id': cls.branch_company.id,
            })
        cls.receiving_warehouse.in_type_id.create_backorder = 'always'
        cls.branch.resource_ref.company_id = False
        cls.supply_company.partner_id.company_id = False
        cls.branch.write({
            'branch_code': 'UPAO-FULFILLMENT',
            'generate_purchase_rfq': True,
            'purchase_picking_type_id': cls.receiving_warehouse.in_type_id.id,
        })
        cls.shared_product.write({
            'purchase_ok': True, 'sale_ok': True, 'list_price': 2,
            'taxes_id': [Command.clear()], 'supplier_taxes_id': [Command.clear()],
        })
        cls.pricelist = cls.env['product.pricelist'].create({
            'name': 'Industry product prices for fulfillment',
            'currency_id': cls.supply_company.currency_id.id,
            'company_id': cls.supply_company.id,
        })
        cls.supply_configuration = cls.env['alvina.supply.configuration'].create({
            'company_id': cls.supply_company.id,
            'sales_user_id': cls.keeper.id, 'pricelist_id': cls.pricelist.id,
            'enabled': True,
        })
        cls.env['stock.quant']._update_available_quantity(
            cls.shared_product, cls.warehouse.lot_stock_id, 100)

    def _flow_request(self, quantity=20):
        request = self._request(product=self.shared_product, quantity=quantity).sudo()
        self.assertTrue(request.supply_flow)
        self.assertEqual(request.operation_mode, 'intercompany')
        self.assertTrue(request.sale_order_id)
        self.assertTrue(request.purchase_order_id)
        return request

    def _outgoing(self, request):
        sale = self._as(request.sale_order_id, self.keeper)
        picking = sale.picking_ids.filtered(lambda item: item.picking_type_code == 'outgoing')
        self.assertEqual(len(picking), 1)
        return picking

    def _complete(self, picking, quantity):
        picking.action_assign()
        move = picking.move_ids.filtered(lambda item: item.product_id == self.shared_product)
        self.assertEqual(len(move), 1)
        move.write({'quantity': quantity, 'picked': True})
        picking.with_context(skip_backorder=True).button_validate()
        self.assertEqual(picking.state, 'done')
        return move

    def _return(self, picking, quantity):
        wizard = picking.env['stock.return.picking'].with_context(
            active_id=picking.id, active_ids=picking.ids,
            active_model='stock.picking').create({'picking_id': picking.id})
        return_line = wizard.product_return_moves.filtered(
            lambda item: item.product_id == self.shared_product)
        self.assertEqual(len(return_line), 1)
        return_line.quantity = quantity
        action = wizard.action_create_returns()
        returned = picking.env['stock.picking'].browse(action['res_id'])
        # Physical returns must reduce net fulfillment even when the operator
        # intentionally does not update the standard invoicing quantity.
        returned.move_ids.to_refund = False
        self._complete(returned, quantity)
        return returned

    def test_confirmed_documents_and_reservations_are_not_deliveries(self):
        request = self._flow_request()
        line = request.line_ids
        self.assertEqual(request.sale_order_id.state, 'sale')
        self.assertEqual(request.purchase_order_id.state, 'purchase')
        self.assertEqual(line.qty_supplied, 0)
        self.assertEqual(line.qty_received, 0)
        self.assertEqual(line.qty_prepared, 20)
        self.assertEqual(request.delivery_state, 'preparing')
        picking = self._outgoing(request)
        picking.action_assign()
        self.assertEqual(line.qty_prepared, 20)
        self.assertEqual(line.qty_supplied, 0)
        self.assertEqual(line.qty_pending, 20)
        self.assertEqual(request.delivery_state, 'preparing')
        picking.move_ids.picked = True
        self.assertEqual(line.qty_supplied, 0, 'Picked is indicative; only done is delivered')
        self.assertEqual(request.purchase_order_id.state, 'purchase')
        self.assertEqual(line.qty_received, 0)

    def test_twenty_requested_fifteen_delivered_five_backordered(self):
        request = self._flow_request()
        picking = self._outgoing(request)
        self._complete(picking, 15)
        line = request.line_ids
        self.assertEqual(line.product_qty, 20)
        self.assertEqual(line.qty_supplied, 15)
        self.assertEqual(line.qty_pending, 5)
        self.assertEqual(request.delivery_state, 'partial')
        backorder = picking.backorder_ids
        self.assertEqual(len(backorder), 1)
        self.assertEqual(backorder.move_ids.product_uom_qty, 5)
        self.assertNotEqual(backorder.state, 'done')
        self.assertEqual(backorder.move_ids.sale_line_id.alvina_request_line_id, line)
        self.assertEqual(request.purchase_order_id.state, 'purchase')
        self.assertTrue(request.purchase_order_id.picking_ids)
        self.assertNotEqual(request.purchase_order_id.picking_ids.state, 'done')
        self.assertEqual(line.qty_received, 0)
        self._complete(backorder, 5)
        self.assertEqual(line.qty_supplied, 20)
        self.assertEqual(line.qty_pending, 0)
        self.assertEqual(request.delivery_state, 'done')
        self.assertFalse(request.sale_order_id.invoice_ids)
        self.assertFalse(request.purchase_order_id.invoice_ids)

    def test_consolidation_does_not_subtract_its_own_reservations_twice(self):
        self.env['stock.quant']._update_available_quantity(
            self.shared_product, self.warehouse.lot_stock_id, -85)
        request = self._flow_request()
        wizard = self._as(self.env['alvina.branch.consolidation'], self.keeper).create({})
        wizard.action_refresh()
        self.assertEqual(wizard.line_ids.suggested_qty, 5)
        picking = self._outgoing(request)
        picking.action_assign()
        wizard.action_refresh()
        self.assertEqual(wizard.line_ids.product_qty, 20)
        self.assertEqual(wizard.line_ids.free_qty, 0)
        self.assertEqual(wizard.line_ids.reserved_qty, 15)
        self.assertEqual(wizard.line_ids.suggested_qty, 5)
        self._complete(picking, 15)
        wizard.action_refresh()
        self.assertEqual(wizard.line_ids.product_qty, 5)
        self.assertEqual(wizard.line_ids.reserved_qty, 0)
        self.assertEqual(wizard.line_ids.suggested_qty, 5)

    def test_consolidation_keeps_unrelated_reservations_unavailable(self):
        unrelated = self.env['stock.picking'].create({
            'picking_type_id': self.warehouse.out_type_id.id,
            'location_id': self.warehouse.lot_stock_id.id,
            'location_dest_id': self.env.ref('stock.stock_location_customers').id,
            'move_ids': [Command.create({
                'product_id': self.shared_product.id, 'product_uom_qty': 30,
                'product_uom': self.shared_product.uom_id.id,
                'location_id': self.warehouse.lot_stock_id.id,
                'location_dest_id': self.env.ref('stock.stock_location_customers').id})]})
        unrelated.action_confirm()
        unrelated.action_assign()
        self._outgoing(self._flow_request(20)).action_assign()
        self._flow_request(60)
        wizard = self._as(self.env['alvina.branch.consolidation'], self.keeper).create({})
        wizard.action_refresh()
        self.assertEqual(wizard.line_ids.product_qty, 80)
        self.assertEqual(wizard.line_ids.free_qty, 0)
        self.assertEqual(wizard.line_ids.reserved_qty, 70)
        self.assertEqual(wizard.line_ids.suggested_qty, 10)

    def test_returns_and_returns_of_returns_follow_physical_done_moves(self):
        request = self._flow_request()
        picking = self._outgoing(request)
        self._complete(picking, 20)
        returned = self._return(picking, 3)
        self.assertEqual(request.line_ids.qty_supplied, 17)
        self.assertEqual(request.line_ids.qty_pending, 3)
        self.assertEqual(request.delivery_state, 'partial')
        self._return(returned, 2)
        self.assertEqual(request.line_ids.qty_supplied, 19)
        self.assertEqual(request.line_ids.qty_pending, 1)
        self.assertEqual(request.line_ids.qty_received, 0)

    def test_standard_overdelivery_keeps_actual_quantity(self):
        request = self._flow_request()
        self._complete(self._outgoing(request), 22)
        self.assertEqual(request.line_ids.qty_supplied, 22)
        self.assertEqual(request.line_ids.qty_pending, 0)
        self.assertEqual(request.delivery_state, 'done')
        self.env.flush_all()  # SQL constraint must also allow the true quantity.

    def test_dispatch_cannot_silently_create_negative_source_stock(self):
        self.env['stock.quant']._update_available_quantity(
            self.shared_product, self.warehouse.lot_stock_id, -95)
        request = self._flow_request()
        picking = self._outgoing(request)
        picking.move_ids.write({'quantity': 20, 'picked': True})
        with self.assertRaises(ValidationError), self.cr.savepoint():
            picking.button_validate()
        self.assertNotEqual(picking.state, 'done')
        self.assertEqual(request.line_ids.qty_supplied, 0)

    def test_untracked_goods_use_validated_moves_without_inventing_stock(self):
        self.env['stock.quant']._update_available_quantity(
            self.shared_product, self.warehouse.lot_stock_id, -100)
        self.shared_product.is_storable = False
        request = self._flow_request()
        picking = self._outgoing(request)
        self._complete(picking, 15)
        self.assertEqual(request.line_ids.qty_supplied, 15)
        self.assertEqual(request.line_ids.qty_pending, 5)
        self.assertEqual(self.shared_product.with_context(warehouse_id=self.warehouse.id).qty_available, 0)

    def test_buyer_confirmation_records_actual_receipt_and_standard_return(self):
        request = self._flow_request()
        outgoing = self._outgoing(request)
        self._complete(outgoing, 15)
        receipt = request.purchase_order_id.picking_ids
        self.assertEqual(len(receipt), 1)
        self.assertNotEqual(receipt.state, 'done')
        self.assertEqual(request.line_ids.qty_received, 0)
        guide = outgoing.alvina_emitted_dispatch_ids
        self.assertEqual(len(guide), 1)
        action = self._as(guide, self.requester).action_open_receipt()
        wizard = self._as(self.env['alvina.branch.dispatch.receipt'], self.requester).browse(action['res_id'])
        wizard.line_ids.quantity = 12
        wizard.note = 'Se recibieron doce de las quince unidades despachadas.'
        wizard.action_confirm()
        self.assertEqual(receipt.state, 'done')
        self.assertEqual(receipt.alvina_dispatch_id, guide)
        self.assertEqual(request.line_ids.qty_received, 12)
        self.assertEqual(request.line_ids.qty_supplied, 15)
        returned = self._return(receipt, 2)
        self.assertEqual(request.line_ids.qty_received, 10)
        self.assertEqual(request.line_ids.qty_supplied, 15)
        self._return(returned, 1)
        self.assertEqual(request.line_ids.qty_received, 11)
        self.assertEqual(guide.line_ids.qty_received, 11)

    def test_cancelled_reserved_dispatch_never_counts_as_delivered(self):
        request = self._flow_request()
        picking = self._outgoing(request)
        picking.action_assign()
        self.assertEqual(request.line_ids.qty_prepared, 20)
        picking.action_cancel()
        self.assertEqual(request.line_ids.qty_prepared, 0)
        self.assertEqual(request.line_ids.qty_supplied, 0)
        self.assertEqual(request.line_ids.qty_pending, 20)

    def test_same_company_stub_never_creates_a_fictitious_transaction(self):
        request = self._flow_request()
        before = {
            model: self.env[model].search_count([])
            for model in ('res.company', 'purchase.order', 'sale.order', 'stock.picking')
        }
        with self.assertRaises(ValidationError):
            request._create_internal_dispatch()
        self.assertEqual(before, {
            model: self.env[model].search_count([]) for model in before
        })

    def _cancel_balance(self, request, quantity, reason='Sin reposición aprobada', user=None):
        return self._as(self.env['alvina.branch.balance.cancellation'],
                        user or self.keeper).create({
            'request_line_id': request.line_ids.id,
            'quantity': quantity, 'reason': reason,
        })

    def test_partial_remainder_closes_with_reason_without_inventing_delivery(self):
        request = self._flow_request()
        picking = self._outgoing(request)
        self._complete(picking, 15)
        with self.assertRaises(ValidationError):
            self._cancel_balance(request, 5)
        picking.backorder_ids.action_cancel()
        request.purchase_order_id.picking_ids.action_cancel()
        move_states = {move.id: move.state for move in request.line_ids._alvina_delivery_moves()}
        audit = self._cancel_balance(request, 5, 'Industria no repondrá estas cinco unidades')
        line = request.line_ids
        self.assertEqual(line.product_qty, 20)
        self.assertEqual(line.qty_supplied, 15)
        self.assertEqual(line.qty_cancelled, 5)
        self.assertEqual(line.qty_pending, 0)
        self.assertEqual(request.delivery_state, 'closed')
        self.assertEqual(audit.user_id, self.keeper)
        self.assertTrue(audit.date)
        self.assertEqual(audit.reason, 'Industria no repondrá estas cinco unidades')
        self.assertEqual(move_states, {
            move.id: move.state for move in line._alvina_delivery_moves()})
        self.assertEqual(line.qty_received, 0)

    def test_balance_cancellation_requires_reason_and_available_balance(self):
        request = self._flow_request()
        # Closing demand is explicit; cancel both pending stock operations first.
        request.sale_order_id.picking_ids.action_cancel()
        request.purchase_order_id.picking_ids.action_cancel()
        with self.assertRaises(ValidationError):
            self._cancel_balance(request, 1, '   ')
        for quantity in (0, -1, 21, float('nan'), float('inf')):
            with self.subTest(quantity=quantity):
                with self.assertRaises(ValidationError):
                    self._cancel_balance(request, quantity)
        self._cancel_balance(request, 12)
        self.assertEqual(request.line_ids.qty_pending, 8)
        with self.assertRaises(ValidationError):
            self._cancel_balance(request, 9)
        self._cancel_balance(request, 8)
        self.assertEqual(request.line_ids.qty_cancelled, 20)
        self.assertEqual(request.line_ids.qty_supplied, 0)

    def test_batch_cancellation_cannot_overdraw_the_same_line(self):
        request = self._flow_request()
        request.sale_order_id.picking_ids.action_cancel()
        request.purchase_order_id.picking_ids.action_cancel()
        ledger = self._as(self.env['alvina.branch.balance.cancellation'], self.keeper)
        with self.assertRaises(ValidationError):
            ledger.create([{
                'request_line_id': request.line_ids.id, 'quantity': 12, 'reason': 'Primer saldo',
            }, {
                'request_line_id': request.line_ids.id, 'quantity': 12, 'reason': 'Segundo saldo',
            }])
        self.assertEqual(request.line_ids.qty_cancelled, 0)
        self.assertEqual(request.line_ids.qty_pending, 20)

    def test_balance_history_cannot_be_forged_edited_or_deleted(self):
        request = self._flow_request()
        for user in (self.requester, self.other_keeper):
            with self.subTest(user=user.login):
                with self.assertRaises(AccessError):
                    self._cancel_balance(request, 1, user=user)
        ledger = self._as(self.env['alvina.branch.balance.cancellation'], self.keeper)
        with self.assertRaises(AccessError):
            ledger.create({
                'request_line_id': request.line_ids.id, 'quantity': 1,
                'reason': 'Intento de suplantación', 'user_id': self.requester.id,
            })
        request.sale_order_id.picking_ids.action_cancel()
        request.purchase_order_id.picking_ids.action_cancel()
        audit = self._cancel_balance(request, 1)
        with self.assertRaises(AccessError):
            audit.write({'quantity': 2})
        with self.assertRaises(AccessError):
            audit.unlink()
