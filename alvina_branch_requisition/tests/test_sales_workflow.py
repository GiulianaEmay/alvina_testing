"""Independent logistics confirmation and explicit valued delivery guides."""
from copy import deepcopy

from lxml import etree

from odoo import Command
from odoo.exceptions import AccessError, ValidationError
from odoo.tests import TransactionCase, tagged
from odoo.tools.safe_eval import safe_eval

from .test_purchase_workflow import ManualSupplyFixture


@tagged('post_install', '-at_install')
class TestSalesWorkflow(ManualSupplyFixture, TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env['stock.quant']._update_available_quantity(
            cls.shared_product, cls.warehouse.lot_stock_id, 100)

    def _quotation(self):
        request = self._request(product=self.shared_product, quantity=10, confirm_supply=False)
        self._as(request.sudo().purchase_order_id, self.requester).button_confirm()
        return request, self._as(request.sudo().sale_order_id, self.keeper)

    def _delivery(self):
        request, sale = self._quotation()
        sale.action_alvina_sale_confirm()
        picking = sale.picking_ids.filtered(lambda p: p.picking_type_code == 'outgoing')
        self.assertEqual(len(picking), 1)
        return request, sale, picking

    def _done(self):
        request, sale, picking = self._delivery()
        picking.action_alvina_delivery_autofill()
        picking.action_alvina_delivery_validate()
        self.assertEqual(picking.state, 'done')
        return request, sale, picking

    def test_buyer_confirmation_leaves_supplier_quotation_without_delivery(self):
        request, sale = self._quotation()
        self.assertEqual(request.sudo().purchase_order_id.state, 'purchase')
        self.assertEqual(sale.state, 'draft')
        self.assertFalse(sale.picking_ids)
        self.assertFalse(sale.invoice_ids)

    def test_logistics_confirmation_enables_real_delivery_only(self):
        request, sale, picking = self._delivery()
        self.assertEqual(sale.state, 'sale')
        self.assertEqual(sale.action_view_delivery()['res_model'], 'stock.picking')
        self.assertNotEqual(picking.state, 'done')
        self.assertEqual(request.line_ids.qty_supplied, 0)
        self.assertFalse(sale.invoice_ids)

    def test_another_branch_or_unassigned_keeper_cannot_confirm(self):
        request, sale = self._quotation()
        for user in (self.outsider, self.other_keeper):
            with self.subTest(user=user.login), self.assertRaises(AccessError):
                self._as(sale, user).action_alvina_sale_confirm()
        self.assertEqual(sale.state, 'draft')

    def test_logistics_role_confirms_without_extra_branch_or_company_assignment(self):
        request, sale = self._quotation()
        user = self.env['res.users'].create({
            'name': 'Logistics confirmation user', 'login': 'logistics-manual-confirm',
            'company_id': self.supply_company.id,
            'company_ids': [Command.set(self.supply_company.ids)],
            'group_ids': [Command.set([self.env.ref('base.group_user').id,
                self.env.ref('alvina_branch_requisition.group_industry_logistics').id])],
        })
        assigned = self.branch.user_ids.ids
        self._as(sale, user).action_alvina_sale_confirm()
        self.assertEqual(sale.state, 'sale')
        self.assertEqual(user.company_ids, self.supply_company)
        self.assertEqual(self.branch.user_ids.ids, assigned)

    def test_checking_availability_and_autofill_do_not_dispatch(self):
        request, sale, picking = self._delivery()
        picking.action_alvina_delivery_assign()
        picking.action_alvina_delivery_autofill()
        self.assertEqual(picking.move_ids.quantity, 10)
        self.assertNotEqual(picking.state, 'done')
        self.assertEqual(request.line_ids.qty_supplied, 0)
        self.assertFalse(picking.alvina_emitted_dispatch_ids)

    def test_validation_dispatches_without_generating_guide_or_invoice(self):
        request, sale, picking = self._done()
        self.assertEqual(request.line_ids.qty_supplied, 10)
        self.assertEqual(request.line_ids.qty_received, 0)
        self.assertFalse(picking.alvina_emitted_dispatch_ids)
        self.assertFalse(sale.invoice_ids)

    def test_guide_and_print_require_validated_delivery(self):
        request, sale, picking = self._delivery()
        for method in ('action_alvina_delivery_guide', 'action_alvina_delivery_print'):
            with self.subTest(method=method), self.assertRaises(ValidationError):
                getattr(picking, method)()
        self.assertNotEqual(picking.state, 'done')

    def test_generate_guide_once_and_keep_original_prices_and_inventory(self):
        request, sale, picking = self._done()
        stock = self.env['stock.quant']._get_available_quantity(self.shared_product, self.warehouse.lot_stock_id)
        action = picking.action_alvina_delivery_guide()
        guide = picking.alvina_emitted_dispatch_ids
        snapshot = deepcopy(guide.snapshot_json)
        self.assertEqual(len(guide), 1)
        self.assertEqual(action['type'], 'ir.actions.report')
        self.assertEqual(guide.source, 'dispatch')
        self.assertEqual(guide.line_ids.quantity, 10)
        self.assertEqual(guide.snapshot_json['totals']['total'], 35)
        sale.order_line.price_unit = 99
        picking.action_alvina_delivery_guide()
        self.assertEqual(picking.alvina_emitted_dispatch_ids, guide)
        self.assertEqual(guide.snapshot_json, snapshot)
        self.assertEqual(self.env['stock.quant']._get_available_quantity(
            self.shared_product, self.warehouse.lot_stock_id), stock)

    def test_partial_delivery_preserves_remaining_quantity_and_valued_guide(self):
        request, sale, picking = self._delivery()
        picking.move_ids.write({'quantity': 4, 'picked': True})
        picking.action_alvina_delivery_validate()
        self.assertEqual(request.line_ids.qty_pending, 6)
        self.assertEqual(picking.backorder_ids.move_ids.product_uom_qty, 6)
        picking.action_alvina_delivery_guide()
        self.assertEqual(picking.alvina_emitted_dispatch_ids.line_ids.quantity, 4)
        self.assertEqual(picking.alvina_emitted_dispatch_ids.snapshot_json['totals']['total'], 14)

    def test_cancel_pending_delivery_keeps_original_request_and_stock(self):
        request, sale, picking = self._delivery()
        picking.action_alvina_delivery_cancel()
        self.assertEqual(picking.state, 'cancel')
        self.assertEqual(request.line_ids.product_qty, 10)
        self.assertEqual(request.line_ids.qty_supplied, 0)
        self.assertFalse(picking.alvina_emitted_dispatch_ids)

    def test_validated_delivery_rejects_pending_actions_even_over_rpc(self):
        request, sale, picking = self._done()
        for method in ('action_alvina_delivery_assign', 'action_alvina_delivery_validate',
                       'action_alvina_delivery_autofill', 'action_alvina_delivery_cancel'):
            with self.subTest(method=method), self.assertRaises(ValidationError):
                getattr(picking, method)()

    def test_buyer_cannot_prepare_or_validate_supplier_delivery(self):
        request, sale, picking = self._delivery()
        buyer = self._as(picking, self.requester)
        for method in ('action_alvina_delivery_assign', 'action_alvina_delivery_autofill',
                       'action_alvina_delivery_validate', 'action_alvina_delivery_guide'):
            with self.subTest(method=method), self.assertRaises(AccessError):
                getattr(buyer, method)()

    def test_sale_header_has_requested_actions_in_order(self):
        view = self._as(self.env['sale.order'], self.keeper).get_view(
            self.env.ref('sale.view_order_form').id, 'form')
        arch = etree.fromstring(view['arch'])
        values = dict.fromkeys(self.env['sale.order']._fields, False)
        values.update(alvina_manual_sale=True, state='draft', context={}, id=1)
        visible = [b.get('name') for b in arch.xpath('//form/header/button')
                   if not safe_eval(b.get('invisible', 'False'), values)]
        self.assertEqual(visible, ['action_alvina_sale_send', 'action_alvina_sale_print',
            'action_alvina_sale_confirm', 'action_alvina_sale_preview', 'action_alvina_sale_cancel'])

    def test_delivery_header_before_and_after_validation(self):
        view = self._as(self.env['stock.picking'], self.keeper).get_view(
            self.env.ref('stock.view_picking_form').id, 'form')
        arch = etree.fromstring(view['arch'])
        values = dict.fromkeys(self.env['stock.picking']._fields, False)
        values.update(alvina_sale_delivery=True, alvina_purchase_receipt=False, context={}, id=1)
        for state, expected in (
            ('assigned', ['action_alvina_delivery_assign', 'action_alvina_delivery_validate',
                          'action_alvina_delivery_cancel', 'action_alvina_delivery_autofill']),
            ('done', ['action_alvina_delivery_print', 'action_alvina_delivery_guide']),
        ):
            values['state'] = state
            visible = [b.get('name') for b in arch.xpath('//form/header/button')
                       if not safe_eval(b.get('invisible', 'False'), values)]
            self.assertEqual(visible, expected)

    def test_delivery_guide_renders_real_pdf(self):
        request, sale, picking = self._done()
        picking.action_alvina_delivery_guide()
        guide = picking.alvina_emitted_dispatch_ids
        report = self._as(self.env['ir.actions.report'], self.keeper).with_context(force_report_rendering=True)
        pdf, kind = report._render_qweb_pdf('alvina_branch_requisition.action_dispatch_note_report', guide.ids)
        self.assertEqual(kind, 'pdf')
        self.assertTrue(pdf.startswith(b'%PDF-'))

    def test_send_preview_and_print_keep_sale_unconfirmed(self):
        request, sale = self._quotation()
        self.assertEqual(sale.action_alvina_sale_send()['res_model'], 'mail.compose.message')
        self.assertEqual(sale.action_alvina_sale_preview()['type'], 'ir.actions.act_url')
        self.assertEqual(sale.action_alvina_sale_print()['type'], 'ir.actions.report')
        self.assertEqual(sale.state, 'draft')
        self.assertFalse(sale.picking_ids)

    def test_ordinary_sale_retains_standard_confirmation(self):
        sale = self.env['sale.order'].create({'partner_id': self.vendor.id,
            'order_line': [Command.create({'product_id': self.shared_product.id,
                'product_uom_qty': 1, 'price_unit': 2})]})
        self.assertFalse(sale.alvina_manual_sale)
        sale.action_confirm()
        self.assertEqual(sale.state, 'sale')
