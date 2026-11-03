"""Branch RFQs keep the buyer, public sale price, and company boundaries intact."""
from uuid import uuid4

from psycopg2 import IntegrityError

from odoo import Command, fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import TransactionCase, tagged

from .test_supply_companies import SupplyCompanyFixture
from ..models.dates import minimum_required_date


@tagged('post_install', '-at_install')
class TestPurchaseRfq(SupplyCompanyFixture, TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.receiving_warehouse = cls.env['stock.warehouse'].search([
            ('company_id', '=', cls.branch_company.id)], limit=1)
        if not cls.receiving_warehouse:
            cls.receiving_warehouse = cls.env['stock.warehouse'].create({
                'name': 'UPAO receiving warehouse', 'code': 'URCV',
                'company_id': cls.branch_company.id,
            })
        cls.shared_product.write({
            'list_price': 2.5, 'standard_price': 0.4,
            'purchase_ok': True, 'supplier_taxes_id': [Command.clear()],
        })
        # The company partner represents the supplier. It is shared so that
        # standard purchase documents can belong to each independent buyer.
        cls.supply_company.partner_id.company_id = False

    def _enable_rfq(self, branch=None, picking_type=None):
        branch = branch or self.branch
        branch.write({
            'generate_purchase_rfq': True,
            'purchase_picking_type_id': (
                picking_type or self.receiving_warehouse.in_type_id).id,
        })
        return branch

    def test_existing_request_only_configuration_stays_opt_in(self):
        self.assertFalse(self.branch.generate_purchase_rfq)
        record = self._request(product=self.shared_product, quantity=4)
        self.assertFalse(record.purchase_order_id)
        self.assertEqual(record.currency_id, self.branch_company.currency_id)
        self.assertEqual(record.line_ids.price_unit, 2.5)
        self.assertEqual(record.line_ids.price_subtotal, 10)
        self.assertEqual(record.amount_total, 10)
        self._enable_rfq()
        self.assertFalse(record.purchase_order_id,
                         'Enabling future RFQs must not create historical purchases')

    def test_rfq_appears_in_requesting_company_with_industry_as_supplier(self):
        self._enable_rfq()
        record = self._request(product=self.shared_product, quantity=4)
        order = record.purchase_order_id.sudo()
        self.assertTrue(order)
        self.assertEqual(order.company_id, self.branch_company)
        self.assertEqual(order.partner_id, self.supply_company.partner_id)
        self.assertEqual(order.picking_type_id, self.receiving_warehouse.in_type_id)
        self.assertEqual(order.currency_id, self.branch_company.currency_id)
        self.assertEqual(order.alvina_branch_request_id, record)
        self.assertEqual(order.origin, record.name)
        self.assertEqual(order.state, 'draft')
        self.assertEqual(len(order.order_line), 1)
        self.assertEqual(order.order_line.product_id, self.shared_product)
        self.assertEqual(order.order_line.product_uom_id, record.line_ids.product_uom_id)
        self.assertEqual(order.order_line.product_qty, 4)
        self.assertTrue(order.order_line.name)
        self.assertEqual(order.order_line.price_unit, 2.5)
        self.assertEqual(order.amount_untaxed, 10)
        self.assertEqual(self.requester.company_ids, self.branch_company)
        self.assertNotEqual(order.company_id, self.supply_company)

    def test_creation_does_not_confirm_or_dispatch_or_invoice_or_email(self):
        self._enable_rfq()
        outgoing_mail_before = self.env['mail.mail'].search_count([])
        record = self._request(product=self.shared_product)
        order = record.purchase_order_id.sudo()
        self.assertEqual(order.state, 'draft')
        self.assertFalse(order.picking_ids)
        self.assertFalse(order.invoice_ids)
        self.assertFalse(self.env['stock.move'].search([
            ('purchase_line_id', 'in', order.order_line.ids)]))
        self.assertEqual(self.env['mail.mail'].search_count([]), outgoing_mail_before)
        if 'sale.order' in self.env:
            self.assertFalse(self.env['sale.order'].search([('origin', '=', record.name)]))

    def test_snapshot_uses_sale_price_and_survives_product_price_change(self):
        self._enable_rfq()
        self.env['product.supplierinfo'].create({
            'partner_id': self.supply_company.partner_id.id,
            'product_tmpl_id': self.shared_product.product_tmpl_id.id,
            'company_id': self.branch_company.id,
            'price': 99, 'min_qty': 0,
            'currency_id': self.branch_company.currency_id.id,
        })
        record = self._request(product=self.shared_product, quantity=4)
        order = record.purchase_order_id.sudo()
        self.shared_product.list_price = 18
        self.shared_product.standard_price = 51
        record.invalidate_recordset()
        record.line_ids.invalidate_recordset()
        order.order_line.invalidate_recordset()
        self.assertEqual(record.line_ids.price_unit, 2.5)
        self.assertEqual(record.line_ids.price_subtotal, 10)
        self.assertEqual(record.amount_total, 10)
        self.assertEqual(order.order_line.price_unit, 2.5)
        self.assertEqual(order.amount_untaxed, 10)

    def test_zero_sale_price_does_not_fall_back_to_supplier_price_or_cost(self):
        self._enable_rfq()
        self.shared_product.write({'list_price': 0, 'standard_price': 12})
        self.env['product.supplierinfo'].create({
            'partner_id': self.supply_company.partner_id.id,
            'product_tmpl_id': self.shared_product.product_tmpl_id.id,
            'company_id': self.branch_company.id,
            'price': 99, 'min_qty': 0,
            'currency_id': self.branch_company.currency_id.id,
        })
        record = self._request(product=self.shared_product, quantity=4)
        self.assertTrue(record.price_snapshot_set)
        self.assertTrue(record.line_ids.price_snapshot_set)
        self.assertEqual(record.line_ids.price_unit, 0)
        self.assertEqual(record.amount_total, 0)
        self.assertEqual(record.purchase_order_id.sudo().order_line.price_unit, 0)

    def test_currency_and_line_rounding_are_in_the_buying_company(self):
        currency = self.env['res.currency'].create({
            'name': 'ZBQ', 'symbol': 'Z', 'rounding': 0.01,
            'position': 'before', 'active': True,
        })
        self.branch_company.currency_id = currency
        # Conversion is from the product price currency, not the supplying
        # company's currency or a purchase vendor price.
        self.env['res.currency.rate'].create({
            'currency_id': currency.id, 'name': fields.Date.today(),
            'company_id': self.branch_company.id, 'rate': 2,
        })
        self.shared_product.write({
            'list_price': 1.11,
            'uom_id': self.env.ref('uom.product_uom_kgm').id,
        })
        self._enable_rfq()
        record = self._request(product=self.shared_product, quantity=3.33)
        expected = self.shared_product.currency_id._convert(
            self.shared_product.lst_price, currency, self.branch_company,
            fields.Date.to_date(record.request_date), round=False)
        self.assertEqual(record.currency_id, currency)
        self.assertAlmostEqual(record.line_ids.price_unit, expected)
        self.assertEqual(record.line_ids.price_subtotal, currency.round(3.33 * expected))
        self.assertEqual(record.amount_total, record.line_ids.price_subtotal)
        self.assertEqual(record.purchase_order_id.sudo().currency_id, currency)

    def test_requester_without_purchase_acl_can_submit_only_validated_request(self):
        self._enable_rfq()
        self.requester.group_ids = [Command.set([
            self.env.ref('base.group_user').id,
            self.env.ref('alvina_branch_requisition.group_requester').id,
        ])]
        self.assertFalse(self.requester.has_group('purchase.group_purchase_user'))
        with self.assertRaises(AccessError), self.cr.savepoint():
            self._as(self.env['purchase.order'], self.requester).create({
                'partner_id': self.supply_company.partner_id.id,
                'company_id': self.branch_company.id,
                'picking_type_id': self.receiving_warehouse.in_type_id.id,
            })
        record = self._request(product=self.shared_product)
        self.assertFalse(record.env.su)
        self.assertEqual(record.user_id, self.requester)
        self.assertTrue(record.purchase_order_id.sudo().exists())
        self.assertEqual(self.requester.company_ids, self.branch_company)
        with self.assertRaises(AccessError):
            self._as(record.purchase_order_id, self.requester).read(['amount_total'])

    def test_portal_requester_can_submit_rfq_without_internal_stock_or_purchase_access(self):
        portal = self.env['res.users'].create({
            'name': 'UPAO portal requisitioner', 'login': 'alvina-rfq-portal',
            'company_id': self.branch_company.id,
            'company_ids': [Command.set(self.branch_company.ids)],
            'group_ids': [Command.set([
                self.env.ref('base.group_portal').id,
                self.env.ref('alvina_branch_requisition.group_requester').id,
            ])],
        })
        self.branch.user_ids = [Command.link(portal.id)]
        self._enable_rfq()
        self.assertFalse(portal.has_group('base.group_user'))
        self.assertFalse(portal.has_group('stock.group_stock_user'))
        self.assertFalse(portal.has_group('purchase.group_purchase_user'))
        with self.assertRaises(AccessError):
            self._as(self.receiving_warehouse.in_type_id, portal).read(['code'])
        record = self._request(user=portal, product=self.shared_product, quantity=4)
        self.assertFalse(record.env.su)
        self.assertEqual(record.env.uid, portal.id)
        self.assertEqual(record.user_id, portal)
        order = record.purchase_order_id.sudo()
        self.assertTrue(order.exists())
        self.assertEqual(order.state, 'draft')
        self.assertEqual(order.company_id, self.branch_company)
        self.assertEqual(order.amount_untaxed, 10)
        self.assertEqual(portal.company_ids, self.branch_company)
        with self.assertRaises(AccessError):
            self._as(order, portal).read(['amount_total'])

    def test_private_supplier_product_cannot_be_inserted_in_buyers_rfq(self):
        # The original request-only mode still supports a narrow supplier
        # catalog without exposing central private products to buyer records.
        private_request = self._request(product=self.product)
        self.assertFalse(private_request.purchase_order_id)
        self._enable_rfq()
        with self.assertRaises(UserError), self.cr.savepoint():
            self._request(product=self.product)

    def test_product_not_available_for_purchase_rejects_entire_rfq(self):
        self._enable_rfq()
        self.shared_product.purchase_ok = False
        before = self.env['purchase.order'].search_count([])
        with self.assertRaises(UserError), self.cr.savepoint():
            self._request(product=self.shared_product)
        self.assertEqual(self.env['purchase.order'].search_count([]), before)

    def test_receiving_operation_must_be_incoming_and_owned_by_requester(self):
        for operation in (self.warehouse.in_type_id,
                          self.receiving_warehouse.out_type_id):
            with self.subTest(operation=operation.display_name):
                with self.assertRaises(ValidationError), self.cr.savepoint():
                    self._enable_rfq(picking_type=operation)
        with self.assertRaises(ValidationError), self.cr.savepoint():
            self.branch.write({
                'generate_purchase_rfq': True, 'purchase_picking_type_id': False,
            })

    def test_supplier_contact_owned_by_another_company_is_rejected(self):
        self.supply_company.partner_id.company_id = self.supply_company
        with self.assertRaises(ValidationError), self.cr.savepoint():
            self._enable_rfq()

    def test_rfq_rechecks_supplier_contact_if_changed_after_configuration(self):
        self._enable_rfq()
        self.supply_company.partner_id.company_id = self.supply_company
        with self.assertRaises(UserError), self.cr.savepoint():
            self._request(product=self.shared_product)

    def test_unassigned_requester_cannot_create_purchase_as_side_effect(self):
        self._enable_rfq()
        before = self.env['purchase.order'].search_count([])
        with self.assertRaises(AccessError), self.cr.savepoint():
            self._request(user=self.outsider, product=self.shared_product)
        self.assertEqual(self.env['purchase.order'].search_count([]), before)

    def test_forged_amount_currency_or_purchase_link_is_rejected(self):
        self._enable_rfq()
        forged = (
            {'amount_total': 1}, {'currency_id': self.supply_company.currency_id.id},
            {'purchase_order_id': self.purchase_order.id},
        )
        for values in forged:
            with self.subTest(values=values):
                with self.assertRaises(AccessError), self.cr.savepoint():
                    self._request(product=self.shared_product, **values)
        for field in ('price_unit', 'price_subtotal', 'price_snapshot_set'):
            with self.subTest(field=field):
                with self.assertRaises(AccessError), self.cr.savepoint():
                    self._request(line_ids=[Command.create({
                        'product_id': self.shared_product.id, 'product_qty': 4,
                        field: 0,
                    })])

    def test_purchase_user_cannot_attach_existing_request_through_rpc_defaults(self):
        record = self._request(product=self.shared_product)
        orders = self._as(self.env['purchase.order'], self.requester)
        values = {
            'partner_id': self.supply_company.partner_id.id,
            'company_id': self.branch_company.id,
            'picking_type_id': self.receiving_warehouse.in_type_id.id,
        }
        with self.assertRaises(UserError), self.cr.savepoint():
            orders.create(dict(values, alvina_branch_request_id=record.id))
        with self.assertRaises(UserError), self.cr.savepoint():
            orders.with_context(default_alvina_branch_request_id=record.id).create(values)
        ordinary_order = orders.create(values)
        with self.assertRaises(UserError), self.cr.savepoint():
            ordinary_order.write({'alvina_branch_request_id': record.id})
        self.assertFalse(record.purchase_order_id)

    def test_request_context_defaults_cannot_spoof_price_or_purchase_link(self):
        self._enable_rfq()
        requests = self._as(self.env['alvina.branch.request'], self.requester).with_context(
            default_purchase_order_id=self.purchase_order.id,
            default_purchase_order_ids=[Command.link(self.purchase_order.id)],
            default_price_unit=0.01, default_amount_total=0.01,
            default_currency_id=self.unrelated_company.currency_id.id,
            default_alvina_branch_request_id=987654321,
        )
        record = requests.create({
            'branch_id': self.branch.id, 'required_date': minimum_required_date(fields.Datetime.now()),
            'submission_key': str(uuid4()),
            'line_ids': [Command.create({
                'product_id': self.shared_product.id, 'product_qty': 4,
            })],
        })
        self.assertEqual(record.amount_total, 10)
        self.assertEqual(record.line_ids.price_unit, 2.5)
        self.assertEqual(record.currency_id, self.branch_company.currency_id)
        self.assertNotEqual(record.purchase_order_id, self.purchase_order)
        self.assertEqual(record.purchase_order_id.sudo().alvina_branch_request_id, record)

    def test_forged_uom_context_cannot_change_base_unit_sale_price(self):
        self._enable_rfq()
        requests = self._as(self.env['alvina.branch.request'], self.requester).with_context(
            uom=self.env.ref('uom.product_uom_dozen').id)
        record = requests.create({
            'branch_id': self.branch.id, 'required_date': minimum_required_date(fields.Datetime.now()),
            'submission_key': str(uuid4()),
            'line_ids': [Command.create({
                'product_id': self.shared_product.id, 'product_qty': 4,
            })],
        })
        self.assertEqual(record.line_ids.product_uom_id, self.shared_product.uom_id)
        self.assertEqual(record.line_ids.price_unit, 2.5)
        self.assertEqual(record.amount_total, 10)
        self.assertEqual(record.purchase_order_id.sudo().order_line.price_unit, 2.5)

    def test_linked_rfq_keeps_buyer_supplier_and_history(self):
        self._enable_rfq()
        record = self._request(product=self.shared_product)
        order = record.purchase_order_id.sudo()
        for values in ({'company_id': self.second_company.id},
                       {'partner_id': self.vendor.id}):
            with self.subTest(values=values):
                with self.assertRaises(UserError), self.cr.savepoint():
                    order.write(values)
        with self.assertRaises(UserError), self.cr.savepoint():
            order.unlink()
        order.button_cancel()
        with self.assertRaises(UserError), self.cr.savepoint():
            order.unlink()
        self.assertEqual(record.purchase_order_id, order)
        ordinary = self.env['purchase.order'].with_company(self.branch_company).create({
            'partner_id': self.vendor.id, 'company_id': self.branch_company.id,
            'picking_type_id': self.receiving_warehouse.in_type_id.id,
        })
        ordinary.button_cancel()
        ordinary.unlink()
        self.assertFalse(ordinary.exists(), 'Unlinked standard orders keep their cancellation workflow')

    def test_duplicate_submission_does_not_leave_another_rfq(self):
        self._enable_rfq()
        token = str(uuid4())
        first = self._request(product=self.shared_product, submission_key=token)
        with self.assertRaises(IntegrityError), self.cr.savepoint():
            self._request(product=self.shared_product, submission_key=token)
        orders = self.env['purchase.order'].search([
            ('alvina_branch_request_id.submission_key', '=', token)])
        self.assertEqual(orders, first.purchase_order_id.sudo())

    def test_request_cancellation_requires_standard_rfq_cancellation_first(self):
        self._enable_rfq()
        record = self._request(product=self.shared_product)
        keeper_record = self._as(record, self.keeper)
        order = record.purchase_order_id.sudo()
        with self.assertRaises(UserError), self.cr.savepoint():
            keeper_record.action_cancel()
        self.assertEqual(order.state, 'draft')
        self.assertEqual(record.state, 'pending')
        order.button_cancel()
        keeper_record.action_cancel()
        self.assertEqual(record.state, 'cancel')
        self.assertEqual(order.state, 'cancel')
        self.assertFalse(order.picking_ids)

    def test_two_buyers_create_separate_rfqs_while_central_demand_combines(self):
        second_warehouse = self.env['stock.warehouse'].search([
            ('company_id', '=', self.second_company.id)], limit=1)
        if not second_warehouse:
            second_warehouse = self.env['stock.warehouse'].create({
                'name': 'Second buyer receiving', 'code': 'SRCV',
                'company_id': self.second_company.id,
            })
        self._enable_rfq()
        self._enable_rfq(self.second_branch, second_warehouse.in_type_id)
        self.env['stock.quant']._update_available_quantity(
            self.shared_product, self.warehouse.lot_stock_id, 10)
        first = self._request(product=self.shared_product, quantity=8)
        second = self._request(user=self.second_requester, branch=self.second_branch,
                               product=self.shared_product, quantity=7)
        self.assertNotEqual(first.purchase_order_id, second.purchase_order_id)
        self.assertEqual(first.purchase_order_id.sudo().company_id, self.branch_company)
        self.assertEqual(second.purchase_order_id.sudo().company_id, self.second_company)
        wizard = self._as(self.env['alvina.branch.consolidation'], self.keeper).create({})
        wizard.action_refresh()
        row = wizard.line_ids.filtered(lambda line: line.product_id == self.shared_product)
        self.assertEqual(len(row), 1)
        self.assertEqual(row.product_qty, 15)
        self.assertEqual(row.free_qty, 10)
        self.assertEqual(row.suggested_qty, 5)
