"""Commercial values and physical quantities in the Spanish dispatch PDF."""
from odoo import Command, fields
from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, tagged

from .test_supply_companies import SupplyCompanyFixture


@tagged('post_install', '-at_install')
class TestDispatchReport(SupplyCompanyFixture, TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        (cls.supply_company | cls.branch_company | cls.second_company).write({
            'account_fiscal_country_id': cls.env.ref('base.pe').id,
        })
        cls.keeper.group_ids = [Command.link(cls.env.ref('sales_team.group_sale_salesman').id)]
        cls.shared_product.write({
            'list_price': 2.0, 'purchase_ok': True, 'sale_ok': True,
            'taxes_id': [Command.clear()], 'supplier_taxes_id': [Command.clear()],
        })
        cls.supply_company.partner_id.company_id = False
        cls.branch.resource_ref.company_id = False
        cls.second_branch.resource_ref.company_id = False
        for branch, code in ((cls.branch, 'PDF-UPAO'), (cls.second_branch, 'PDF-SECOND')):
            warehouse = cls.env['stock.warehouse'].search([
                ('company_id', '=', branch.company_id.id)], limit=1)
            if not warehouse:
                warehouse = cls.env['stock.warehouse'].create({
                    'name': code + ' receipt', 'code': code[-5:],
                    'company_id': branch.company_id.id,
                })
            branch.write({'branch_code': code,
                          'purchase_picking_type_id': warehouse.in_type_id.id})
        cls.pricelist = cls.env['product.pricelist'].create({
            'name': 'Industry dispatch sale price',
            'company_id': cls.supply_company.id,
            'currency_id': cls.supply_company.currency_id.id,
        })
        cls.configuration = cls.env['alvina.supply.configuration'].create({
            'company_id': cls.supply_company.id, 'pricelist_id': cls.pricelist.id,
            'sales_user_id': cls.keeper.id, 'enabled': True,
        })

    def _dispatch_request(self, quantity=20):
        return self._request(product=self.shared_product, quantity=quantity)

    def _payload(self, record, user=None):
        return self._as(record, user or self.keeper)._alvina_dispatch_payload()

    def _deliver(self, record, quantity):
        sale = record.sudo().sale_order_id.with_company(self.supply_company)
        self.env['stock.quant']._update_available_quantity(
            self.shared_product, self.warehouse.lot_stock_id, 100)
        picking = sale.picking_ids.filtered(lambda item: item.state != 'cancel')
        self.assertEqual(len(picking), 1)
        picking.move_ids.quantity = quantity
        picking.move_ids.picked = True
        picking.with_context(cancel_backorder=False)._action_done()
        record.invalidate_recordset()
        record.line_ids.invalidate_recordset()
        return picking

    def test_confirmed_documents_have_requested_value_and_zero_delivered(self):
        record = self._dispatch_request()
        payload = self._payload(record)
        self.assertEqual(payload['branch'], self.branch.name)
        self.assertEqual(payload['requesting_company'], self.branch_company.name)
        self.assertEqual(payload['company'], self.supply_company.name)
        self.assertEqual(payload['totals']['requested']['total'], 40)
        self.assertEqual(payload['totals']['delivered']['total'], 0)
        self.assertEqual(payload['totals']['pending']['total'], 40)
        self.assertEqual(payload['lines'][0]['delivered'], 0)
        self.assertEqual(payload['lines'][0]['pending'], 20)
        self.assertTrue(payload['sale'])
        self.assertTrue(payload['purchase'])

    def test_partial_inventory_delivery_has_twenty_fifteen_five_and_notes(self):
        record = self._dispatch_request()
        picking = self._deliver(record, 15)
        picking.write({'alvina_dispatch_note': 'Despacho de útiles para UPAO.',
                       'alvina_shortage_note': 'Cinco unidades pendientes de reposición.'})
        payload = self._payload(record)
        row = payload['lines'][0]
        self.assertEqual((row['requested'], row['delivered'], row['pending']), (20, 15, 5))
        self.assertEqual(row['received'], 0, 'Dispatch is not the buyer receipt')
        self.assertEqual(payload['totals']['delivered']['total'], 30)
        self.assertEqual(payload['totals']['pending']['total'], 10)
        self.assertEqual(len(payload['notes']), 2)
        self.assertIn('Cinco unidades', payload['notes'][1]['text'])

    def test_report_uses_sale_discount_and_standard_excluded_taxes(self):
        tax = self.env['account.tax'].create({
            'name': 'IGV dispatch test', 'amount_type': 'percent', 'amount': 18,
            'tax_group_id': self.env['account.tax.group'].create({'name': 'Dispatch test taxes', 'company_id': self.supply_company.id}).id,
            'type_tax_use': 'sale', 'company_id': self.supply_company.id,
        })
        record = self._dispatch_request()
        sale_line = record.sudo().sale_order_id.order_line
        sale_line.write({'discount': 10, 'tax_ids': [Command.set(tax.ids)]})
        payload = self._payload(record)
        self.assertEqual(payload['lines'][0]['discount'], 10)
        self.assertEqual(payload['totals']['requested']['untaxed'], 36)
        self.assertEqual(payload['totals']['requested']['tax'], 6.48)
        self.assertEqual(payload['totals']['requested']['total'], 42.48)
        self.assertEqual(payload['totals']['requested']['total'], sale_line.order_id.amount_total)

    def test_included_tax_and_global_rounding_match_standard_sale_totals(self):
        self.supply_company.tax_calculation_rounding_method = 'round_globally'
        tax = self.env['account.tax'].create({
            'name': 'Included dispatch tax', 'amount_type': 'percent', 'amount': 18,
            'tax_group_id': self.env['account.tax.group'].create({'name': 'Included test taxes', 'company_id': self.supply_company.id}).id,
            'type_tax_use': 'sale', 'company_id': self.supply_company.id,
            'price_include_override': 'tax_included',
        })
        second_product = self.env['product.product'].create({
            'name': 'Second PDF rounding product', 'categ_id': self.shared_product.categ_id.id,
            'list_price': 21.53, 'is_storable': True, 'purchase_ok': True,
            'sale_ok': True, 'alvina_requisition_enabled': True,
        })
        record = self._request(product=self.shared_product, line_ids=[
            Command.create({'product_id': self.shared_product.id, 'product_qty': 1}),
            Command.create({'product_id': second_product.id, 'product_qty': 1}),
        ])
        sale = record.sudo().sale_order_id
        sale.order_line.write({'price_unit': 21.53, 'tax_ids': [Command.set(tax.ids)]})
        payload = self._payload(record)
        self.assertEqual(payload['totals']['requested']['total'], sale.amount_total)
        self.assertEqual(payload['totals']['requested']['untaxed'], sale.amount_untaxed)
        self.assertEqual(payload['totals']['requested']['tax'], sale.amount_tax)
        self.assertEqual(payload['totals']['requested']['total'], 43.06)

    def test_requester_can_print_own_projection_without_sales_or_cost_access(self):
        record = self._dispatch_request()
        payload = self._payload(record, self.requester)
        self.assertEqual(payload['lines'][0]['price_unit'], 2)
        self.assertEqual(payload['lines'][0]['requested'], 20)
        self.assertNotIn('standard_price', repr(payload))
        self.assertNotIn('account.move', repr(payload))
        self.assertNotIn('res.partner(', repr(payload))
        with self.assertRaises(AccessError):
            self._as(record.sudo().sale_order_id, self.requester).check_access('read')

    def test_outsiders_and_unassigned_keepers_cannot_print_guessed_ids(self):
        record = self._dispatch_request()
        request_report = self.env['report.alvina_branch_requisition.dispatch_request']
        sale_report = self.env['report.alvina_branch_requisition.dispatch_sale']
        for user in (self.outsider, self.other_keeper):
            with self.subTest(user=user.login):
                with self.assertRaises(AccessError):
                    self._as(request_report, user)._get_report_values(record.ids)
                with self.assertRaises(AccessError):
                    self._as(sale_report, user)._get_report_values(record.sudo().sale_order_id.ids)

    def test_sale_print_action_renders_spanish_without_mutation(self):
        record = self._dispatch_request()
        sale = record.sudo().sale_order_id
        counts_before = {model: self.env[model].sudo().search_count([])
                         for model in ('sale.order', 'purchase.order', 'stock.move', 'account.move')}
        report = self._as(self.env['ir.actions.report'], self.keeper)
        html, output_type = report._render_qweb_html(
            'alvina_branch_requisition.action_dispatch_sale_report', sale.ids)
        self.assertEqual(output_type, 'html')
        self.assertIn('ORDEN DE DESPACHO VALORIZADA'.encode(), html)
        self.assertIn('Cantidades por producto'.encode(), html)
        self.assertIn('Total valorizado'.encode(), html)
        self.assertIn(self.branch.name.encode(), html)
        self.assertNotIn(b'standard_price', html)
        self.assertEqual(sale.state, 'sale')
        for model, count in counts_before.items():
            self.assertEqual(self.env[model].sudo().search_count([]), count)

    def test_real_pdf_from_partial_delivery_if_wkhtmltopdf_available(self):
        report = self._as(self.env['ir.actions.report'], self.keeper)
        if report.get_wkhtmltopdf_state() == 'install':
            self.fail('wkhtmltopdf is required; run tools/setup_test_pdf.py and configure PATH/LD_LIBRARY_PATH')
        record = self._dispatch_request()
        self._deliver(record, 15)
        pdf, output_type = report.with_context(force_report_rendering=True)._render_qweb_pdf(
            'alvina_branch_requisition.action_dispatch_request_report', record.ids)
        self.assertEqual(output_type, 'pdf')
        self.assertTrue(pdf.startswith(b'%PDF-'))
        self.assertGreater(len(pdf), 2000)
