"""Native ORM-result contract, not a substitute for the Enterprise test runtime."""
from contextlib import contextmanager
from unittest.mock import patch

from odoo import Command
from odoo.exceptions import AccessError, ValidationError
from odoo.tests import TransactionCase, tagged

from .test_supply_companies import SupplyCompanyFixture
from ..models.supply_context import NATIVE_SUPPLY


@tagged('post_install', '-at_install')
class TestNativeSupply(SupplyCompanyFixture, TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        (cls.supply_company | cls.branch_company).account_fiscal_country_id = cls.env.ref('base.pe')
        cls.keeper.group_ids = [Command.link(cls.env.ref('alvina_branch_requisition.group_industry_logistics').id)]
        cls.branch.resource_ref.company_id = False
        cls.supply_company.partner_id.company_id = False
        receipt = cls.env['stock.warehouse'].search([('company_id', '=', cls.branch_company.id)], limit=1)
        receipt = receipt or cls.env['stock.warehouse'].create({
            'name': 'Native buyer receipt', 'code': 'NBUY', 'company_id': cls.branch_company.id})
        receipt.reception_steps = 'one_step'
        receipt.in_type_id.create_backorder = 'always'
        cls.warehouse.out_type_id.create_backorder = 'always'
        cls.branch.write({'branch_code': 'NATIVE-UPAO', 'purchase_picking_type_id': receipt.in_type_id.id})
        cls.shared_product.write({'list_price': 3.5, 'sale_ok': True, 'purchase_ok': True,
                                 'taxes_id': [Command.clear()], 'supplier_taxes_id': [Command.clear()]})
        pricelist = cls.env['product.pricelist'].create({
            'name': 'Native Industry agreement', 'company_id': cls.supply_company.id,
            'currency_id': cls.supply_company.currency_id.id})
        cls.configuration = cls.env['alvina.supply.configuration'].create({
            'company_id': cls.supply_company.id, 'sales_user_id': cls.keeper.id,
            'pricelist_id': pricelist.id, 'enabled': True})

    @contextmanager
    def native_result(self, *, duplicate=False, wrong_company=False, quantity=None,
                      auto_confirm=False, generate=True, missing_line=False, separate_lines=False,
                      invoice=False, reverse_purchase=False, altered_price=False):
        Purchase = type(self.env['purchase.order'])
        original = Purchase.button_approve
        test = self

        def approve(orders, *args, **kwargs):
            result = original(orders, *args, **kwargs)
            scope = NATIVE_SUPPLY.get()
            if scope and generate and not scope['sale_id']:
                purchase = orders.filtered(lambda item: item.id == scope['purchase_id'])
                if purchase.state == 'purchase':
                    vals = {
                        'company_id': (test.unrelated_company if wrong_company else test.supply_company).id,
                        'partner_id': test.branch_company.partner_id.id,
                        'origin': purchase.name, 'client_order_ref': purchase.name,
                        'order_line': [] if missing_line else [Command.create({
                            'product_id': line.product_id.id, 'product_uom_id': line.product_uom_id.id,
                            'product_uom_qty': line.product_qty if quantity is None else quantity,
                            'price_unit': 999,
                        }) for line in purchase.order_line],
                    }
                    commands = vals.pop('order_line') if separate_lines else []
                    sale = test._as(test.env['sale.order'], test.keeper).create(vals)
                    if separate_lines:
                        for command in commands:
                            test._as(test.env['sale.order.line'], test.keeper).create(
                                dict(command[2], order_id=sale.id))
                    if auto_confirm:
                        sale.action_confirm()
                    if altered_price:
                        sale.sudo().order_line.price_unit = 1000
                    if invoice:
                        test.env['account.move'].sudo().create({
                            'move_type': 'out_invoice', 'company_id': test.supply_company.id,
                            'partner_id': test.branch_company.partner_id.id})
                    if reverse_purchase:
                        test.env['purchase.order'].sudo().create({
                            'company_id': test.branch_company.id,
                            'partner_id': test.supply_company.partner_id.id})
                    if duplicate:
                        test._as(test.env['sale.order'], test.keeper).create(vals)
            return result

        with patch.object(type(self.configuration), '_native_intercompany_installed', return_value=True), \
                patch.object(Purchase, 'button_approve', approve):
            yield

    def flow(self, **kwargs):
        return self._request(product=self.shared_product, quantity=10, **kwargs).sudo()

    def counts(self):
        return {model: self.env[model].sudo().search_count([]) for model in (
            'alvina.branch.request', 'purchase.order', 'sale.order', 'stock.picking', 'account.move')}

    def test_installed_native_is_allowed_to_activate_without_an_invented_flag(self):
        with patch.object(type(self.configuration), '_native_intercompany_installed', return_value=True):
            self.assertTrue(self.configuration._assert_supported())
            self.configuration._check_configuration()

    def test_native_creates_exactly_one_pair_with_industry_prices_and_responsible(self):
        before = self.counts()
        companies = self.requester.company_ids.ids
        with self.native_result():
            record = self.flow()
        self.assertEqual(record.purchase_order_id.state, 'purchase')
        self.assertEqual(record.sale_order_id.state, 'sale')
        self.assertEqual(record.sale_order_id.user_id, self.keeper)
        self.assertEqual(record.sale_order_id.order_line.price_unit, 3.5)
        self.assertEqual(record.purchase_order_id.order_line.price_unit, 3.5)
        self.assertEqual(record.sale_order_id.origin, record.purchase_order_id.name)
        self.assertEqual(record.sale_order_id.client_order_ref, record.purchase_order_id.name)
        self.assertEqual(record.sale_order_id.order_line.alvina_request_line_id, record.line_ids)
        self.assertEqual(self.requester.company_ids.ids, companies)
        self.assertEqual(self.counts()['sale.order'] - before['sale.order'], 1)
        self.assertEqual(self.counts()['purchase.order'] - before['purchase.order'], 1)
        self.assertEqual(self.counts()['account.move'], before['account.move'])
        self.assertFalse((record.sale_order_id.picking_ids | record.purchase_order_id.picking_ids).filtered(
            lambda item: item.state == 'done'))
        visible = self._as(self.env['sale.order'], self.keeper).search([
            ('alvina_branch_request_id', '=', record.id)])
        self.assertEqual(visible, record.sale_order_id)
        self.assertIsNone(NATIVE_SUPPLY.get())

    def test_native_may_confirm_its_generated_sale_before_returning(self):
        with self.native_result(auto_confirm=True):
            record = self.flow()
        self.assertEqual(record.sale_order_id.state, 'sale')
        self.assertEqual(len(record.sale_order_id.picking_ids), 1)

    def test_native_may_create_header_and_product_lines_separately(self):
        with self.native_result(separate_lines=True, auto_confirm=True):
            record = self.flow()
        self.assertEqual(record.sale_order_id.order_line.alvina_request_line_id, record.line_ids)
        self.assertEqual(record.sale_order_id.order_line.price_unit, 3.5)

    def test_native_reverse_purchase_and_automatic_invoice_roll_back_the_pair(self):
        for changes in ({'invoice': True}, {'reverse_purchase': True}, {'altered_price': True}):
            with self.subTest(changes=changes):
                before = self.counts()
                with self.native_result(**changes), self.assertRaises(ValidationError):
                    self.flow()
                self.assertEqual(self.counts(), before)

    def test_native_virtual_pricing_maps_tax_included_sale_to_buyer_purchase(self):
        sale_tax = self.env['account.tax'].create({
            'name': 'Native included VAT', 'amount': 18, 'type_tax_use': 'sale',
            'company_id': self.supply_company.id, 'price_include_override': 'tax_included',
            'tax_group_id': self.env['account.tax.group'].create({
                'name': 'Native supplier VAT', 'company_id': self.supply_company.id}).id})
        purchase_tax = self.env['account.tax'].with_company(self.branch_company).create({
            'name': 'Native excluded VAT', 'amount': 18, 'type_tax_use': 'purchase',
            'company_id': self.branch_company.id,
            'tax_group_id': self.env['account.tax.group'].create({
                'name': 'Native buyer VAT', 'company_id': self.branch_company.id}).id})
        self.shared_product.write({'taxes_id': [Command.set(sale_tax.ids)],
                                   'supplier_taxes_id': [Command.set(purchase_tax.ids)]})
        with self.native_result(separate_lines=True):
            record = self.flow()
        self.assertEqual(record.sale_order_id.amount_total, 35)
        self.assertEqual(record.purchase_order_id.amount_total, record.sale_order_id.amount_total)
        self.assertEqual(record.purchase_order_id.amount_untaxed, record.sale_order_id.amount_untaxed)

    def test_missing_native_rule_rolls_back_request_and_purchase_without_caller_savepoint(self):
        before = self.counts()
        with self.native_result(generate=False), self.assertRaisesRegex(ValidationError, 'Odoo no generó'):
            self.flow()
        self.assertEqual(self.counts(), before)

    def test_duplicate_wrong_company_and_missing_or_changed_lines_roll_back_everything(self):
        for changes in ({'duplicate': True}, {'wrong_company': True}, {'quantity': 9}, {'missing_line': True}):
            with self.subTest(changes=changes):
                before = self.counts()
                with self.native_result(**changes), self.assertRaises(ValidationError):
                    self.flow()
                self.assertEqual(self.counts(), before)
                self.assertIsNone(NATIVE_SUPPLY.get())

    def test_disabled_config_does_not_silently_register_request_without_sale(self):
        self.configuration.enabled = False
        before = self.counts()
        with self.assertRaisesRegex(ValidationError, 'desactivado'):
            self.flow()
        self.assertEqual(self.counts(), before)

    def test_ordinary_purchases_and_sales_remain_outside_the_private_capability(self):
        with self.native_result():
            purchase = self.env['purchase.order'].with_company(self.branch_company).create({
                'partner_id': self.supply_company.partner_id.id, 'company_id': self.branch_company.id})
            sale = self.env['sale.order'].create({'partner_id': self.branch_company.partner_id.id})
        self.assertFalse(purchase.alvina_branch_request_id)
        self.assertFalse(sale.alvina_branch_request_id)

    def test_native_two_step_purchase_is_approved_only_for_the_created_pair(self):
        self.branch_company.write({'po_double_validation': 'two_step', 'po_double_validation_amount': 0})
        with self.native_result():
            record = self.flow()
        self.assertEqual(record.purchase_order_id.state, 'purchase')
        self.assertEqual(record.sale_order_id.state, 'sale')

    def pending_legacy(self):
        # The request predates the central configuration; it is not fabricated
        # by changing immutable submitted lines or writing a technical state.
        with patch.object(type(self.configuration), '_get_configuration',
                          return_value=self.env['alvina.supply.configuration']):
            return self._request(product=self.shared_product, quantity=10)

    def test_pending_request_can_be_sent_once_without_recreating_or_repricing_its_history(self):
        record = self.pending_legacy()
        before = record.read(['name', 'request_date', 'required_date', 'amount_total'])[0]
        count = self.counts()['alvina.branch.request']
        with self.native_result():
            record.action_send_to_industry()
            record.action_send_to_industry()
        self.assertEqual(record.read(['name', 'request_date', 'required_date', 'amount_total'])[0], before)
        self.assertEqual(self.counts()['alvina.branch.request'], count)
        self.assertEqual(len(record.sudo().purchase_order_ids), 1)
        self.assertEqual(len(record.sudo().sale_order_ids), 1)

    def test_failed_resend_restores_pending_history_without_partial_documents(self):
        record = self.pending_legacy()
        before = self.counts()
        with self.native_result(generate=False), self.assertRaises(ValidationError):
            record.action_send_to_industry()
        self.assertEqual(self.counts(), before)
        self.assertFalse(record.supply_flow)
        self.assertFalse(record.sudo().purchase_order_id)

    def test_another_branch_user_cannot_send_an_existing_request(self):
        record = self.pending_legacy()
        with self.assertRaises(AccessError):
            self._as(record, self.outsider).action_send_to_industry()

    def test_native_pair_supports_partial_dispatch_pdf_snapshot_and_buyer_difference(self):
        self.env['stock.quant']._update_available_quantity(
            self.shared_product, self.warehouse.lot_stock_id, 8)
        with self.native_result():
            record = self.flow()
        picking = self._as(record.sale_order_id.picking_ids, self.keeper)
        picking.move_ids.write({'quantity': 8, 'picked': True})
        picking.button_validate()
        guide = picking.sudo().alvina_emitted_dispatch_ids
        self.assertEqual(len(guide), 1)
        self.assertEqual(guide.snapshot_json['totals']['total'], 28)
        action = self._as(guide, self.requester).action_open_receipt()
        wizard = self._as(self.env['alvina.branch.dispatch.receipt'], self.requester).browse(action['res_id'])
        wizard.line_ids.quantity = 7
        wizard.note = 'Falta una unidad respecto de la guía.'
        wizard.action_confirm()
        self.assertEqual(record.line_ids.qty_supplied, 8)
        self.assertEqual(record.line_ids.qty_received, 7)
        self.assertEqual(record.line_ids.qty_pending, 2)
        self.assertEqual(guide.receipt_confirmation_json['lines'][0]['difference'], 1)
        self.assertEqual(self.env['stock.quant']._get_available_quantity(
            self.shared_product, self.warehouse.lot_stock_id), 0)
        self.assertFalse(record.purchase_order_id.invoice_ids | record.sale_order_id.invoice_ids)
