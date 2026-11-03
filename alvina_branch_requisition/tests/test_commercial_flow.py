from unittest.mock import patch
from uuid import uuid4
import runpy
from datetime import timedelta

from psycopg2 import IntegrityError
from odoo import Command, fields
from odoo.exceptions import AccessError, ValidationError, UserError
from odoo.tests import TransactionCase, tagged
from odoo.tools import file_path

from .test_supply_companies import SupplyCompanyFixture


@tagged('post_install', '-at_install')
class TestCommercialFlow(SupplyCompanyFixture, TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.supply_company.account_fiscal_country_id = cls.env.ref('base.pe')
        cls.branch_company.account_fiscal_country_id = cls.env.ref('base.pe')
        cls.keeper.group_ids = [Command.link(cls.env.ref('sales_team.group_sale_salesman').id)]
        cls.branch.resource_ref.company_id = False
        cls.supply_company.partner_id.company_id = False
        warehouse = cls.env['stock.warehouse'].search([('company_id', '=', cls.branch_company.id)], limit=1)
        if not warehouse:
            warehouse = cls.env['stock.warehouse'].create({
                'name': 'Commercial buyer receipt', 'code': 'CBUY', 'company_id': cls.branch_company.id})
        cls.branch.write({'branch_code': 'UPAO-COMMERCIAL',
                          'purchase_picking_type_id': warehouse.in_type_id.id})
        cls.shared_product.write({'list_price': 99, 'sale_ok': True, 'purchase_ok': True,
                                  'taxes_id': [Command.clear()], 'supplier_taxes_id': [Command.clear()]})
        cls.pricelist = cls.env['product.pricelist'].create({
            'name': 'Industry branches agreement', 'company_id': cls.supply_company.id,
            'currency_id': cls.supply_company.currency_id.id,
            'item_ids': [Command.create({'applied_on': '1_product',
                'product_tmpl_id': cls.shared_product.product_tmpl_id.id,
                'compute_price': 'fixed', 'fixed_price': 3.5})]})
        cls.configuration = cls.env['alvina.supply.configuration'].create({
            'company_id': cls.supply_company.id, 'sales_user_id': cls.keeper.id,
            'pricelist_id': cls.pricelist.id, 'enabled': True})

    def _flow(self, **values):
        values.setdefault('required_date', fields.Date.context_today(
            self.env['alvina.branch.request'].with_context(tz='America/Lima')) + timedelta(days=2))
        return self._request(product=self.shared_product, quantity=20, **values)

    def test_uniform_documents_without_legacy_branch_opt_in(self):
        self.assertFalse(self.branch.generate_purchase_rfq)
        record = self._flow()
        sale, purchase = record.sudo().sale_order_id, record.sudo().purchase_order_id
        self.assertTrue(record.supply_flow)
        self.assertEqual(record.operation_mode, 'intercompany')
        self.assertEqual((sale.state, purchase.state), ('sale', 'purchase'))
        self.assertEqual(sale.company_id, self.supply_company)
        self.assertEqual(purchase.company_id, self.branch_company)
        self.assertEqual(sale.partner_id, self.branch_company.partner_id)
        self.assertEqual(purchase.partner_id, self.supply_company.partner_id)
        self.assertEqual(sale.user_id, self.keeper)
        self.assertEqual(sale.warehouse_id, self.warehouse)
        self.assertEqual(sale.order_line.alvina_request_line_id, record.line_ids)
        self.assertEqual(purchase.order_line.alvina_request_line_id, record.line_ids)
        self.assertEqual(sale.alvina_purchase_order_id, purchase)
        self.assertEqual(purchase.alvina_sale_order_id, sale)
        self.assertTrue(sale.picking_ids)
        self.assertTrue(purchase.picking_ids)
        self.assertTrue(all(picking.state not in ('done', 'cancel')
                            for picking in sale.picking_ids | purchase.picking_ids))
        self.assertEqual(record.line_ids.qty_supplied, 0)
        self.assertEqual(record.line_ids.qty_received, 0)
        self.assertFalse(sale.invoice_ids | purchase.invoice_ids)

    def test_upgrade_refuses_the_alternative_branch_schema_without_mapping(self):
        migration = runpy.run_path(file_path(
            'alvina_branch_requisition/migrations/19.0.2.0.0/pre-migration.py'))['migrate']
        self.cr.execute('CREATE TABLE alvina_binding_keeper_rel (binding_id integer, user_id integer)')
        with self.assertRaisesRegex(RuntimeError, 'central-supply-multicompany'):
            migration(self.cr, '19.0.1.2.0')

    def test_branch_contact_stays_private_and_stock_transit_corresponds(self):
        self.branch.resource_ref.company_id = self.branch_company
        record = self._flow()
        sale, purchase = record.sudo().sale_order_id, record.sudo().purchase_order_id
        self.assertEqual(sale.partner_id, self.branch_company.partner_id)
        self.assertEqual(self.branch.resource_ref.company_id, self.branch_company)
        outgoing_destination = sale.partner_shipping_id.with_company(self.supply_company).property_stock_customer
        receipt_source = purchase.partner_id.with_company(self.branch_company).property_stock_supplier
        self.assertEqual(outgoing_destination, receipt_source)
        self.assertEqual(outgoing_destination.usage, 'transit')

    def test_pricelist_instead_of_public_price(self):
        record = self._flow()
        self.assertEqual(record.line_ids.price_unit, 3.5)
        self.assertEqual(record.sudo().sale_order_id.amount_total, 70)
        self.assertEqual(record.sudo().purchase_order_id.amount_total, 70)
        self.assertEqual(record.amount_total, 70)
        self.assertEqual(self.shared_product.list_price, 99)

    def test_standard_sale_taxes_are_calculated(self):
        tax = self.env['account.tax'].create({'name': 'Sale tax 18 test',
            'tax_group_id': self.env['account.tax.group'].create({'name': 'Commercial test taxes', 'company_id': self.supply_company.id}).id,
            'company_id': self.supply_company.id, 'type_tax_use': 'sale', 'amount': 18})
        self.shared_product.taxes_id = [Command.set(tax.ids)]
        record = self._flow()
        sale = record.sudo().sale_order_id
        self.assertEqual(sale.amount_untaxed, 70)
        self.assertEqual(sale.amount_tax, 12.6)
        self.assertEqual(sale.amount_total, 82.6)

    def test_tax_included_sale_maps_to_tax_excluded_purchase_without_double_tax(self):
        self.branch_company.account_fiscal_country_id = self.env.ref('base.pe')
        sale_tax = self.env['account.tax'].create({
            'name': 'Included 18 sale test', 'amount': 18, 'type_tax_use': 'sale',
            'company_id': self.supply_company.id, 'price_include_override': 'tax_included',
            'tax_group_id': self.env['account.tax.group'].create({
                'name': 'Industry VAT test', 'company_id': self.supply_company.id}).id})
        purchase_tax = self.env['account.tax'].with_company(self.branch_company).create({
            'name': 'Excluded 18 purchase test', 'amount': 18, 'type_tax_use': 'purchase',
            'company_id': self.branch_company.id,
            'tax_group_id': self.env['account.tax.group'].create({
                'name': 'Branch VAT test', 'company_id': self.branch_company.id}).id})
        self.shared_product.write({'taxes_id': [Command.set(sale_tax.ids)],
                                   'supplier_taxes_id': [Command.set(purchase_tax.ids)]})
        record = self._flow()
        sale, purchase = record.sudo().sale_order_id, record.sudo().purchase_order_id
        self.assertEqual(sale.amount_total, 70)
        self.assertEqual(purchase.amount_total, sale.amount_total)
        self.assertEqual(purchase.amount_untaxed, sale.amount_untaxed)

    def test_repeated_private_creation_does_not_duplicate_pairs(self):
        record = self._flow()
        counts = (self.env['sale.order'].search_count([]), self.env['purchase.order'].search_count([]))
        record._create_supply_documents()
        self.assertEqual(counts, (self.env['sale.order'].search_count([]), self.env['purchase.order'].search_count([])))

    def test_duplicate_token_keeps_one_pair(self):
        token = str(uuid4())
        first = self._flow(submission_key=token)
        with self.assertRaises(IntegrityError), self.cr.savepoint():
            self._flow(submission_key=token)
        self.assertEqual(self.env['sale.order'].search_count([('origin', '=', first.name)]), 1)
        self.assertEqual(self.env['purchase.order'].search_count([('origin', '=', first.name)]), 1)

    def test_failure_of_purchase_does_not_commit_a_partial_pair(self):
        counts = {model: self.env[model].search_count([]) for model in
                  ('alvina.branch.request', 'sale.order', 'purchase.order')}
        model_class = type(self.env['purchase.order'])
        with patch.object(model_class, 'create', side_effect=ValidationError('Controlled test failure')):
            with self.assertRaises(ValidationError), self.cr.savepoint():
                self._flow()
        for model, count in counts.items():
            self.assertEqual(self.env[model].search_count([]), count)

    def test_pending_native_module_upgrade_blocks_activation_and_submission(self):
        Module = type(self.env['ir.module.module'])
        original = Module.search_count
        def installed(modules, domain, *args, **kwargs):
            if (('name', '=', 'account_inter_company_rules') in domain
                    and ('state', 'in', ['to upgrade', 'to remove']) in domain):
                return 1
            return original(modules, domain, *args, **kwargs)
        with patch.object(Module, 'search_count', installed):
            with self.assertRaises(ValidationError):
                self.configuration._assert_supported()
            with self.assertRaises(ValidationError), self.cr.savepoint():
                self._flow()

    def test_native_guard_covers_both_purchase_confirmation_paths(self):
        # Build the old draft stage only for this guard regression. Reach
        # 'to approve' through core's real two-step confirmation, not a state
        # assignment, before simulating the newly enabled native module.
        with patch.object(type(self.env['alvina.branch.request']), '_confirm_supply_documents', return_value=None):
            record = self._flow()
        purchase = record.sudo().purchase_order_id
        self.branch_company.write({'po_double_validation': 'two_step', 'po_double_validation_amount': 0})
        purchase.button_confirm()
        self.assertEqual(purchase.state, 'to approve')
        # Simulate an installed native module after the pair was created.
        # Confirmation must require the private capability even when no module
        # upgrade is pending; an RPC context cannot supply that capability.
        Module = type(self.env['ir.module.module'])
        original = Module.search_count

        def installed(modules, domain, *args, **kwargs):
            if (('name', '=', 'account_inter_company_rules') in domain
                    and ('state', '=', 'installed') in domain):
                return 1
            return original(modules, domain, *args, **kwargs)

        with patch.object(Module, 'search_count', installed):
            with self.assertRaises(ValidationError), self.cr.savepoint():
                purchase.button_confirm()
            self.assertEqual(purchase.state, 'to approve')
            with self.assertRaises(ValidationError), self.cr.savepoint():
                purchase.button_approve(force=True)
            self.assertEqual(purchase.state, 'to approve')
            self.assertFalse(purchase.picking_ids)
            with self.assertRaises(ValidationError), self.cr.savepoint():
                record.sudo().sale_order_id.action_confirm()
            self.assertFalse(record.sudo().sale_order_id.picking_ids)

    def test_sale_keeps_the_buyers_delivery_and_invoice_partner(self):
        record = self._flow()
        sale = record.sudo().sale_order_id
        third_party = self.env['res.partner'].create({'name': 'Unrelated destination'})
        for field in ('partner_shipping_id', 'partner_invoice_id'):
            with self.subTest(field=field):
                with self.assertRaises(ValidationError), self.cr.savepoint():
                    sale.write({field: third_party.id})
                self.assertEqual(sale[field], self.branch_company.partner_id)

    def test_purchase_cannot_redirect_the_branch_receipt(self):
        record = self._flow()
        purchase = record.sudo().purchase_order_id
        operation = self.env['stock.picking.type'].create({
            'name': 'Unrelated branch receipt', 'code': 'incoming',
            'company_id': self.branch_company.id,
            'sequence_code': 'OTHER-RECEIPT',
            'default_location_src_id': self.env.ref('stock.stock_location_suppliers').id,
            'default_location_dest_id': purchase.picking_type_id.default_location_dest_id.id,
        })
        with self.assertRaises(ValidationError), self.cr.savepoint():
            purchase.picking_type_id = operation
        self.assertEqual(purchase.picking_type_id, self.branch.purchase_picking_type_id)
        with self.assertRaises(ValidationError), self.cr.savepoint():
            self.branch.purchase_picking_type_id = operation
        self.assertEqual(purchase.picking_type_id, self.branch.purchase_picking_type_id)

    def test_identification_and_industry_are_mandatory(self):
        self.branch.branch_code = False
        with self.assertRaises(ValidationError), self.cr.savepoint():
            self._flow()
        self.branch.branch_code = 'RESTORED'
        self.branch.write({'supply_company_id': self.unrelated_company.id,
                           'user_ids': [Command.set(self.requester.ids)],
                           'warehouse_id': self.env['stock.warehouse'].search([
                               ('company_id', '=', self.unrelated_company.id)], limit=1).id})
        with self.assertRaises(ValidationError), self.cr.savepoint():
            self._flow()

    def test_client_cannot_choose_flow_or_companies(self):
        for values in ({'supply_flow': False}, {'operation_mode': 'internal'},
                       {'supply_config_id': self.configuration.id},
                       {'supply_company_id': self.unrelated_company.id}):
            with self.subTest(values=values):
                with self.assertRaises(AccessError), self.cr.savepoint():
                    self._flow(**values)

    def test_industry_sales_user_prints_without_buyer_company_or_request_role(self):
        record = self._flow()
        self.keeper.group_ids = [Command.set([
            self.env.ref('base.group_user').id, self.env.ref('sales_team.group_sale_salesman').id])]
        sale = self._as(record.sudo().sale_order_id, self.keeper)
        sale.check_access('read')
        self.assertEqual(self.keeper.company_ids, self.supply_company)
        values = self._as(self.env['report.alvina_branch_requisition.dispatch_sale'], self.keeper)._get_report_values(sale.ids)
        self.assertEqual(values['documents'][0]['totals']['requested']['total'], 70)
        with self.assertRaises(AccessError):
            self._as(record, self.keeper).check_access('read')

    def test_unassigned_sales_user_and_branch_user_cannot_read_sale(self):
        record = self._flow()
        self.other_keeper.group_ids = [Command.link(self.env.ref('sales_team.group_sale_salesman_all_leads').id)]
        for user in (self.requester, self.other_keeper):
            with self.assertRaises(AccessError):
                self._as(record.sudo().sale_order_id, user).check_access('read')
            with self.assertRaises(AccessError):
                self._as(record.sudo().sale_order_id.order_line, user).check_access('read')
        self.assertEqual(self.requester.company_ids, self.branch_company)

    def test_original_quantities_and_link_identity_are_immutable(self):
        record = self._flow()
        line = record.sudo().sale_order_id.order_line
        with self.assertRaises(ValidationError), self.cr.savepoint():
            line.product_uom_qty = 15
        self.assertEqual(record.line_ids.product_qty, 20)
        with self.assertRaises(AccessError):
            self._as(self.env['sale.order'], self.keeper).with_context(
                default_alvina_branch_request_id=record.id).create({'partner_id': self.vendor.id})

    def test_double_validation_is_completed_for_only_the_automatic_purchase(self):
        self.branch_company.write({'po_double_validation': 'two_step', 'po_double_validation_amount': 0})
        companies = self.requester.company_ids
        groups = self.requester.group_ids
        record = self._flow()
        self.assertEqual(record.sudo().purchase_order_id.state, 'purchase')
        self.assertEqual(record.sudo().sale_order_id.state, 'sale')
        self.assertFalse(record.sudo().purchase_order_id.invoice_ids)
        self.assertEqual(self.requester.company_ids, companies)
        self.assertEqual(self.requester.group_ids, groups)
        self.assertFalse(self.requester.has_group('purchase.group_purchase_manager'))

        ordinary = self.env['purchase.order'].with_user(self.requester).sudo().with_company(
            self.branch_company).create({
                'partner_id': self.supply_company.partner_id.id,
                'order_line': [Command.create({
                    'product_id': self.shared_product.id, 'product_qty': 1,
                    'product_uom_id': self.shared_product.uom_id.id,
                    'price_unit': 100,
                })],
            })
        ordinary.button_confirm()
        self.assertEqual(ordinary.state, 'to approve')
        ordinary.with_context(alvina_automated_purchase_approval=ordinary.ids).button_approve(force=True)
        self.assertEqual(ordinary.state, 'to approve', 'Public context cannot acquire private technical approval')
        self.assertFalse(ordinary.picking_ids)

    def test_confirmation_failure_rolls_back_request_pair_and_pickings(self):
        models = ('alvina.branch.request', 'alvina.branch.request.line',
                  'sale.order', 'purchase.order', 'stock.picking', 'stock.move')
        counts = {model: self.env[model].search_count([]) for model in models}
        with patch.object(type(self.env['purchase.order']), 'button_confirm',
                          side_effect=UserError('Controlled fiscal validation failure')):
            # No savepoint supplied by this caller: the service owns the full
            # rollback boundary, including the already-confirmed sale picking.
            with self.assertRaisesRegex(ValidationError, 'Controlled fiscal validation failure'):
                self._flow()
        for model, count in counts.items():
            with self.subTest(model=model):
                self.assertEqual(self.env[model].search_count([]), count)

    def test_missing_buyer_fiscal_country_blocks_before_any_document(self):
        self.branch_company.account_fiscal_country_id = False
        models = ('alvina.branch.request', 'sale.order', 'purchase.order', 'stock.picking')
        counts = {model: self.env[model].search_count([]) for model in models}
        with self.assertRaisesRegex(ValidationError, 'país fiscal'):
            self._flow()
        for model, count in counts.items():
            self.assertEqual(self.env[model].search_count([]), count)

    def _logistics_successor(self, login, company=None):
        company = company or self.supply_company
        return self.env['res.users'].create({
            'name': login, 'login': login, 'company_id': company.id,
            'company_ids': [Command.set(company.ids)],
            'group_ids': [Command.set([
                self.env.ref('base.group_user').id,
                self.env.ref('alvina_branch_requisition.group_industry_logistics').id,
            ])],
        })

    def test_selected_active_responsible_is_retained_with_several_logistics_users(self):
        self._logistics_successor('alvina-successor-one')
        self._logistics_successor('alvina-successor-two')
        self.assertEqual(self.configuration._get_sales_responsible(), self.keeper)
        self.assertEqual(self._flow().sudo().sale_order_id.user_id, self.keeper)

    def test_unique_logistics_successor_replaces_archived_assignee_without_membership_changes(self):
        successor = self._logistics_successor('alvina-successor-unique')
        self.keeper.active = False
        self.assertNotIn(successor, self.branch.user_ids)
        memberships = successor.company_ids
        configured = self.configuration.sales_user_id
        record = self._flow()
        self.assertEqual(record.sudo().sale_order_id.user_id, successor)
        self.assertEqual(self.configuration.sales_user_id, configured)
        self.assertEqual(successor.company_ids, memberships)
        self.assertNotIn(successor, self.branch.user_ids)
        self.assertEqual(record.sudo().purchase_order_id.state, 'purchase')

    def test_absent_assignee_uses_unique_authorized_logistics_user(self):
        successor = self._logistics_successor('alvina-successor-no-selection')
        self.configuration.sales_user_id = False
        self.assertEqual(self.configuration._get_sales_responsible(), successor)
        self.assertFalse(self.configuration.sales_user_id)
        self.assertEqual(self._flow().sudo().sale_order_id.user_id, successor)

    def test_selected_logistics_responsible_does_not_require_branch_membership(self):
        responsible = self._logistics_successor('alvina-responsible-logistics-selected')
        self.configuration.sales_user_id = responsible
        self.assertNotIn(responsible, self.branch.user_ids)
        record = self._flow()
        self.assertEqual(record.sudo().sale_order_id.user_id, responsible)
        self.assertEqual(record.sudo().purchase_order_id.state, 'purchase')
        self.assertEqual(responsible.company_ids, self.supply_company)

    def test_no_or_multiple_successors_require_an_explicit_selection(self):
        self.keeper.active = False
        for count in (0, 2):
            if count:
                self._logistics_successor('alvina-successor-ambiguous-one')
                self._logistics_successor('alvina-successor-ambiguous-two')
            with self.subTest(count=count):
                with self.assertRaisesRegex(ValidationError, 'exactamente un usuario'):
                    self._flow()
                self.assertFalse(self.env['sale.order'].search([('alvina_branch_request_id', '!=', False)]))

    def test_foreign_company_logistics_user_is_not_a_successor(self):
        self._logistics_successor('alvina-successor-foreign', self.unrelated_company)
        self.keeper.active = False
        with self.assertRaisesRegex(ValidationError, 'exactamente un usuario'):
            self._flow()

    def test_active_invalid_assignee_is_not_silently_replaced(self):
        self._logistics_successor('alvina-successor-ignored')
        self.keeper.group_ids = [Command.clear()]
        with self.assertRaisesRegex(ValidationError, 'responsable seleccionado'):
            self._flow()

    def test_unassigned_standard_sales_user_still_cannot_be_responsible_for_a_branch(self):
        self.branch.user_ids = [Command.unlink(self.keeper.id)]
        with self.assertRaisesRegex(ValidationError, 'Asigne al responsable'):
            self._flow()
