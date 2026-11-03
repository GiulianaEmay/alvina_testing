"""The web request is an RFQ; only explicit buyer actions move real stock."""
from copy import deepcopy

from lxml import etree

from odoo import Command
from odoo.exceptions import AccessError, ValidationError
from odoo.tests import TransactionCase, tagged
from odoo.tools.safe_eval import safe_eval

from .test_supply_companies import SupplyCompanyFixture


class ManualSupplyFixture(SupplyCompanyFixture):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        (cls.supply_company | cls.branch_company).account_fiscal_country_id = cls.env.ref('base.pe')
        cls.keeper.group_ids = [Command.link(cls.env.ref('sales_team.group_sale_salesman').id)]
        cls.supply_company.partner_id.company_id = False
        cls.warehouse.out_type_id.create_backorder = 'always'
        cls.receiving_warehouse = cls.env['stock.warehouse'].search([
            ('company_id', '=', cls.branch_company.id)], limit=1)
        cls.receiving_warehouse = cls.receiving_warehouse or cls.env['stock.warehouse'].create({
            'name': 'Manual branch reception', 'code': 'MBR', 'company_id': cls.branch_company.id})
        cls.receiving_warehouse.in_type_id.create_backorder = 'always'
        cls.branch.write({'branch_code': 'MANUAL-UPAO',
                          'purchase_picking_type_id': cls.receiving_warehouse.in_type_id.id})
        cls.shared_product.write({'list_price': 3.5, 'purchase_ok': True, 'sale_ok': True,
                                 'taxes_id': [Command.clear()], 'supplier_taxes_id': [Command.clear()]})
        cls.pricelist = cls.env['product.pricelist'].create({
            'name': 'Manual supply agreement', 'company_id': cls.supply_company.id,
            'currency_id': cls.supply_company.currency_id.id})
        cls.configuration = cls.env['alvina.supply.configuration'].create({
            'company_id': cls.supply_company.id, 'sales_user_id': cls.keeper.id,
            'pricelist_id': cls.pricelist.id, 'enabled': True})


@tagged('post_install', '-at_install')
class TestPurchaseWorkflow(ManualSupplyFixture, TransactionCase):
    def _draft(self):
        return self._request(product=self.shared_product, quantity=10, confirm_supply=False)

    def _receipt(self):
        request = self._draft()
        purchase = self._as(request.sudo().purchase_order_id, self.requester)
        purchase.button_confirm()
        receipt = self._as(purchase.picking_ids, self.requester)
        self.assertEqual(len(receipt), 1)
        return request, purchase, receipt

    def test_web_menu_opens_new_tab(self):
        self.assertEqual(self.env.ref('alvina_branch_requisition.action_web').target, 'new')

    def test_submission_keeps_rfq_and_sale_draft_without_stock_or_invoices(self):
        request = self._draft().sudo()
        self.assertTrue(request.manual_purchase_flow)
        self.assertEqual(request.purchase_order_id.state, 'draft')
        self.assertEqual(request.sale_order_id.state, 'draft')
        self.assertFalse(request.purchase_order_id.picking_ids | request.sale_order_id.picking_ids)
        self.assertFalse(request.purchase_order_id.invoice_ids | request.sale_order_id.invoice_ids)

    def test_explicit_confirmation_creates_receipt_once(self):
        request, purchase, receipt = self._receipt()
        self.assertEqual(purchase.state, 'purchase')
        self.assertEqual(request.sudo().sale_order_id.state, 'draft')
        self.assertFalse(request.sudo().sale_order_id.picking_ids)
        self.assertNotEqual(receipt.state, 'done')
        purchase.button_confirm()
        self.assertEqual(purchase.picking_ids, receipt)
        self.assertEqual(request.line_ids.qty_received, 0)

    def test_send_rfq_opens_composer_without_confirming(self):
        purchase = self._as(self._draft().sudo().purchase_order_id, self.requester)
        action = purchase.action_rfq_send()
        self.assertEqual(action['res_model'], 'mail.compose.message')
        self.assertEqual(purchase.state, 'draft')
        self.assertFalse(purchase.picking_ids)

    def test_sale_cannot_be_confirmed_before_buyer(self):
        request = self._draft().sudo()
        with self.assertRaisesRegex(ValidationError, 'Confirme primero'):
            request.sale_order_id.action_confirm()

    def test_two_step_approval_is_not_automatically_bypassed(self):
        self.branch_company.write({'po_double_validation': 'two_step', 'po_double_validation_amount': 0})
        request = self._draft().sudo()
        purchase = self._as(request.purchase_order_id, self.requester)
        purchase.button_confirm()
        self.assertEqual(purchase.state, 'to approve')
        self.assertEqual(request.sale_order_id.state, 'draft')
        self.assertFalse(purchase.picking_ids)
        self.requester.group_ids = [Command.link(self.env.ref('purchase.group_purchase_manager').id)]
        purchase.button_approve()
        self.assertEqual((purchase.state, request.sale_order_id.state), ('purchase', 'draft'))
        self.assertTrue(purchase.picking_ids)

    def test_another_branch_cannot_confirm_purchase(self):
        purchase = self._draft().sudo().purchase_order_id
        with self.assertRaises(AccessError):
            self._as(purchase, self.outsider).button_confirm()
        self.assertEqual(purchase.state, 'draft')

    def test_autofill_does_not_validate_or_change_stock(self):
        request, purchase, receipt = self._receipt()
        before = self.env['stock.quant']._get_available_quantity(
            self.shared_product, self.receiving_warehouse.lot_stock_id)
        receipt.action_alvina_autofill()
        self.assertEqual(receipt.move_ids.quantity, 10)
        self.assertNotEqual(receipt.state, 'done')
        self.assertEqual(request.line_ids.qty_received, 0)
        self.assertEqual(self.env['stock.quant']._get_available_quantity(
            self.shared_product, self.receiving_warehouse.lot_stock_id), before)

    def test_validate_receipt_uses_inventory_without_requiring_supplier_guide(self):
        request, purchase, receipt = self._receipt()
        receipt.action_alvina_autofill()
        receipt.action_alvina_validate_receipt()
        self.assertEqual(receipt.state, 'done')
        self.assertEqual(request.line_ids.qty_received, 10)
        self.assertEqual(request.line_ids.qty_supplied, 0)
        self.assertFalse(receipt.alvina_emitted_dispatch_ids)
        self.assertFalse(purchase.invoice_ids)

    def test_partial_receipt_keeps_backorder_and_guide_uses_received_quantities(self):
        request, purchase, receipt = self._receipt()
        receipt.move_ids.write({'quantity': 7, 'picked': True})
        receipt.action_alvina_validate_receipt()
        self.assertEqual(receipt.state, 'done')
        pending = purchase.picking_ids.filtered(lambda p: p.state not in ('done', 'cancel'))
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending.move_ids.product_uom_qty, 3)
        receipt.action_alvina_receipt_guide()
        guide = receipt.sudo().alvina_emitted_dispatch_ids
        self.assertEqual(guide.source, 'receipt')
        self.assertEqual(guide.line_ids.quantity, 7)
        self.assertEqual(guide.line_ids.qty_received, 7)
        self.assertEqual(guide.snapshot_json['totals']['total'], 24.5)
        self.assertEqual(guide.snapshot_json['balances'][0]['pending'], 3)
        self.assertEqual(request.line_ids.qty_supplied, 0)

    def test_guide_requires_validated_receipt(self):
        request, purchase, receipt = self._receipt()
        with self.assertRaises(ValidationError):
            receipt.action_alvina_receipt_guide()
        self.assertNotEqual(receipt.state, 'done')
        self.assertFalse(receipt.alvina_emitted_dispatch_ids)

    def test_reprinting_guide_is_idempotent_and_keeps_original_prices(self):
        request, purchase, receipt = self._receipt()
        receipt.action_alvina_autofill()
        receipt.action_alvina_validate_receipt()
        action = receipt.action_alvina_receipt_guide()
        self.assertEqual(action['type'], 'ir.actions.report')
        guide = receipt.sudo().alvina_emitted_dispatch_ids
        snapshot = deepcopy(guide.snapshot_json)
        purchase.sudo().order_line.price_unit = 99
        receipt.action_alvina_receipt_guide()
        self.assertEqual(receipt.sudo().alvina_emitted_dispatch_ids, guide)
        self.assertEqual(guide.snapshot_json, snapshot)

    def test_other_branch_cannot_autofill_validate_or_generate_guide(self):
        request, purchase, receipt = self._receipt()
        stranger = self._as(receipt, self.outsider)
        for method in ('action_alvina_autofill', 'action_alvina_validate_receipt', 'action_alvina_receipt_guide'):
            with self.subTest(method=method), self.assertRaises(AccessError):
                getattr(stranger, method)()

    def test_completed_receipt_cannot_be_autofilled_or_validated_again(self):
        request, purchase, receipt = self._receipt()
        receipt.action_alvina_autofill()
        receipt.action_alvina_validate_receipt()
        for method in ('action_alvina_autofill', 'action_alvina_validate_receipt'):
            with self.subTest(method=method), self.assertRaises(ValidationError):
                getattr(receipt, method)()

    def test_receipt_header_actions_follow_actual_state(self):
        view = self._as(self.env['stock.picking'], self.requester).get_view(
            self.env.ref('stock.view_picking_form').id, 'form')
        arch = etree.fromstring(view['arch'])
        values = dict.fromkeys(self.env['stock.picking']._fields, False)
        values.update(alvina_purchase_receipt=True, id=1, context={})
        for state, expected in (
            ('assigned', {'action_alvina_validate_receipt', 'action_alvina_print_receipt', 'action_alvina_autofill'}),
            ('done', {'action_alvina_print_receipt', 'action_alvina_receipt_guide'}),
        ):
            values['state'] = state
            visible = {b.get('name') for b in arch.xpath('//form/header/button')
                       if not safe_eval(b.get('invisible', 'False'), values)}
            self.assertEqual(visible, expected)

    def test_ordinary_purchases_keep_standard_confirmation(self):
        self.assertFalse(self.purchase_order.alvina_manual_purchase)
        self.purchase_order.button_confirm()
        self.assertEqual(self.purchase_order.state, 'purchase')

    def test_receipt_guide_renders_real_pdf_with_reception_title(self):
        request, purchase, receipt = self._receipt()
        receipt.action_alvina_autofill()
        receipt.action_alvina_validate_receipt()
        receipt.action_alvina_receipt_guide()
        guide = receipt.sudo().alvina_emitted_dispatch_ids
        report = self._as(self.env['ir.actions.report'], self.requester)
        html, _ = report._render_qweb_html('alvina_branch_requisition.action_dispatch_note_report', guide.ids)
        self.assertIn('GUÍA INTERNA DE RECEPCIÓN VALORIZADA', html.decode())
        self.assertNotIn('GUÍA INTERNA DE DESPACHO VALORIZADA', html.decode())
        pdf, kind = report.with_context(force_report_rendering=True)._render_qweb_pdf(
            'alvina_branch_requisition.action_dispatch_note_report', guide.ids)
        self.assertEqual(kind, 'pdf')
        self.assertTrue(pdf.startswith(b'%PDF-'))
        self.assertGreater(len(pdf), 5000)

    def test_rfq_uses_standard_purchase_header_and_receipt_appears_after_confirmation(self):
        request = self._draft()
        purchase = self._as(request.sudo().purchase_order_id, self.requester)
        view = purchase.get_view(self.env.ref('purchase.purchase_order_form').id, 'form')
        arch = etree.fromstring(view['arch'])
        values = dict.fromkeys(self.env['purchase.order']._fields, False)
        values.update(state='draft', id=purchase.id, context={}, alvina_manual_purchase=True)
        visible = {b.get('name') for b in arch.xpath('//form/header/button')
                   if not safe_eval(b.get('invisible', 'False'), values)}
        self.assertEqual(visible, {'action_rfq_send', 'button_confirm', 'print_quotation', 'button_cancel'})
        self.assertFalse(purchase.picking_ids)
        purchase.button_confirm()
        self.assertTrue(purchase.picking_ids)
        self.assertEqual(purchase.action_view_picking()['res_model'], 'stock.picking')

    def test_receipt_guide_calculates_purchase_taxes_on_actual_quantity(self):
        tax = self.env['account.tax'].with_company(self.branch_company).create({
            'name': 'Branch VAT 18', 'amount': 18, 'type_tax_use': 'purchase',
            'company_id': self.branch_company.id,
            'tax_group_id': self.env['account.tax.group'].create({
                'name': 'Branch VAT', 'company_id': self.branch_company.id}).id})
        self.shared_product.supplier_taxes_id = [Command.set(tax.ids)]
        request, purchase, receipt = self._receipt()
        receipt.move_ids.write({'quantity': 7, 'picked': True})
        receipt.action_alvina_validate_receipt()
        receipt.action_alvina_receipt_guide()
        guide = receipt.sudo().alvina_emitted_dispatch_ids
        self.assertEqual(guide.snapshot_json['totals'], {'untaxed': 24.5, 'tax': 4.41, 'total': 28.91})
