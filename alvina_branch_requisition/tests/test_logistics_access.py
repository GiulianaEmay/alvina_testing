"""The reusable Industry logistics role sees branch supplies, never ordinary sales."""
from datetime import timedelta
from uuid import uuid4

from odoo import Command, fields
from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, tagged

from .test_supply_companies import SupplyCompanyFixture


@tagged('post_install', '-at_install')
class TestLogisticsAccess(SupplyCompanyFixture, TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        (cls.supply_company | cls.branch_company | cls.second_company).write({
            'account_fiscal_country_id': cls.env.ref('base.pe').id,
        })
        cls.keeper.group_ids = [Command.link(
            cls.env.ref('sales_team.group_sale_salesman').id)]
        cls.supply_company.partner_id.company_id = False
        cls.shared_product.write({
            'list_price': 2, 'sale_ok': True, 'purchase_ok': True,
            'taxes_id': [Command.clear()], 'supplier_taxes_id': [Command.clear()],
        })
        cls.required_date = fields.Date.context_today(
            cls.env['alvina.branch.request'].with_context(tz='America/Lima')) + timedelta(days=2)
        for branch, code in ((cls.branch, 'LOG-UPAO'),
                             (cls.second_branch, 'LOG-SECOND')):
            warehouse = cls.env['stock.warehouse'].search([
                ('company_id', '=', branch.company_id.id)], limit=1)
            if not warehouse:
                warehouse = cls.env['stock.warehouse'].create({
                    'name': code + ' receipt', 'code': code[-5:],
                    'company_id': branch.company_id.id,
                })
            branch.write({
                'branch_code': code,
                'purchase_picking_type_id': warehouse.in_type_id.id,
            })

        def request(branch, user, quantity):
            return cls.env['alvina.branch.request'].with_user(user).with_context(
                allowed_company_ids=user.company_ids.ids).create({
                    'branch_id': branch.id, 'required_date': cls.required_date,
                    'submission_key': str(uuid4()),
                    'line_ids': [Command.create({
                        'product_id': cls.shared_product.id,
                        'product_qty': quantity,
                    })],
                })

        # A historical link alone is insufficient: logistics requires the
        # explicitly enabled, validated supply flow, not any linked sale.
        cls.legacy_request = request(cls.branch, cls.requester, 1)
        cls.pricelist = cls.env['product.pricelist'].create({
            'name': 'Logistics access Industry prices',
            'company_id': cls.supply_company.id,
            'currency_id': cls.supply_company.currency_id.id,
        })
        cls.configuration = cls.env['alvina.supply.configuration'].create({
            'company_id': cls.supply_company.id,
            'sales_user_id': cls.keeper.id, 'pricelist_id': cls.pricelist.id,
            'enabled': True,
        })
        cls.first_request = request(cls.branch, cls.requester, 2)
        cls.second_request = request(cls.second_branch, cls.second_requester, 3)
        cls.supply_requests = (cls.first_request | cls.second_request).sudo()
        cls.supply_sales = cls.supply_requests.sale_order_id
        cls.supply_lines = cls.supply_sales.order_line

        def user(login, company, groups):
            return cls.env['res.users'].create({
                'name': login, 'login': login, 'company_id': company.id,
                'company_ids': [Command.set(company.ids)],
                'group_ids': [Command.set([
                    cls.env.ref('base.group_user').id,
                    *(cls.env.ref(group).id for group in groups),
                ])],
            })

        role = 'alvina_branch_requisition.group_industry_logistics'
        cls.logistics = user('alvina-logistics-industry', cls.supply_company, [role])
        cls.foreign_logistics = user('alvina-logistics-foreign', cls.unrelated_company, [role])
        cls.commercial_user = user('alvina-logistics-commercial-control',
                                  cls.supply_company,
                                  ['sales_team.group_sale_salesman_all_leads'])

        def ordinary_sale(salesperson, company=None, **extra):
            company = company or cls.supply_company
            values = {
                'partner_id': cls.vendor.id, 'company_id': company.id,
                'user_id': salesperson.id if salesperson else False,
                'order_line': [Command.create({
                    'product_id': cls.shared_product.id,
                    'product_uom_qty': 1, 'price_unit': 7,
                })],
            }
            values.update(extra)
            return cls.env['sale.order'].with_company(company).create(values)

        cls.ordinary_own = ordinary_sale(cls.logistics)
        cls.ordinary_unassigned = ordinary_sale(None)
        cls.ordinary_other = ordinary_sale(cls.keeper)
        cls.foreign_sale = ordinary_sale(None, cls.unrelated_company)
        cls.legacy_sale = ordinary_sale(
            cls.logistics, partner_id=cls.branch_company.partner_id.id,
            warehouse_id=cls.warehouse.id,
            alvina_branch_request_id=cls.legacy_request.id,
            order_line=[Command.create({
                'alvina_request_line_id': cls.legacy_request.line_ids.id,
                'product_id': cls.shared_product.id,
                'product_uom_id': cls.shared_product.uom_id.id,
                'product_uom_qty': cls.legacy_request.line_ids.product_qty,
                'price_unit': 7,
            })])
        cls.excluded_sales = (cls.ordinary_own | cls.ordinary_unassigned
                              | cls.ordinary_other | cls.foreign_sale
                              | cls.legacy_sale)
        cls.all_test_sales = cls.supply_sales | cls.excluded_sales

    def _logistics(self, records):
        return self._as(records, self.logistics)

    def _assert_sale_scope(self):
        orders = self._logistics(self.env['sale.order'])
        domain = [('id', 'in', self.all_test_sales.ids)]
        self.assertEqual(set(orders.search(domain).ids), set(self.supply_sales.ids))
        self.assertEqual(orders.search_count(domain), 2)
        for order in self.excluded_sales:
            with self.subTest(excluded=order.name):
                with self.assertRaises(AccessError):
                    self._logistics(order).read(['name', 'amount_total'])

    def test_role_reads_every_branch_supply_without_membership_or_ownership(self):
        self.assertTrue(self.logistics.has_group(
            'alvina_branch_requisition.group_keeper'))
        self.assertTrue(self.logistics.has_group('sales_team.group_sale_salesman'))
        self.assertNotIn(self.logistics, self.branch.user_ids | self.second_branch.user_ids)
        self.assertNotIn(self.logistics, self.supply_sales.user_id)
        self._assert_sale_scope()
        requests = self._logistics(self.env['alvina.branch.request'])
        self.assertEqual(set(requests.search([
            ('id', 'in', self.supply_requests.ids)]).ids), set(self.supply_requests.ids))
        rows = self._logistics(self.supply_requests).read(['name', 'branch_id'])
        self.assertEqual(len(rows), 2)
        self.assertEqual(len(self._logistics(self.supply_requests.line_ids).read([
            'product_qty', 'qty_supplied', 'qty_pending'])), 2)

    def test_ordinary_and_historical_sales_stay_hidden_from_search_read_and_web(self):
        self._assert_sale_scope()
        self.assertFalse(self.legacy_request.supply_flow)
        rows = self._logistics(self.env['sale.order']).search_read(
            [('id', 'in', self.all_test_sales.ids)], ['name'])
        self.assertEqual({row['id'] for row in rows}, set(self.supply_sales.ids))
        with self.assertRaises(AccessError):
            self._logistics(self.ordinary_own).web_read({'name': {}})
        allowed = self._logistics(self.supply_sales).web_read({
            'name': {}, 'order_line': {'fields': {'product_uom_qty': {}}},
        })
        self.assertEqual({row['id'] for row in allowed}, set(self.supply_sales.ids))

    def test_extra_all_sales_stock_and_settings_groups_do_not_expand_role(self):
        for group in ('sales_team.group_sale_salesman_all_leads',
                      'sales_team.group_sale_manager', 'base.group_system'):
            self.logistics.group_ids = [Command.link(self.env.ref(group).id)]
            with self.subTest(group=group):
                self.assertFalse(self._logistics(self.env['sale.order']).env.su)
                self._assert_sale_scope()
                self._assert_line_scope()

    def _assert_line_scope(self):
        lines = self._logistics(self.env['sale.order.line'])
        all_lines = self.all_test_sales.order_line
        self.assertEqual(set(lines.search([
            ('id', 'in', all_lines.ids)]).ids), set(self.supply_lines.ids))
        for line in self.excluded_sales.order_line:
            with self.subTest(line=line.id):
                with self.assertRaises(AccessError):
                    self._logistics(line).read(['name', 'price_unit'])

    def test_lines_have_same_scope_even_with_standard_stock_read_rule(self):
        self.assertTrue(self.logistics.has_group('stock.group_stock_user'))
        self._assert_line_scope()
        self.assertEqual(len(self._logistics(self.supply_lines).read([
            'product_uom_qty', 'price_unit'])), 2)

    def test_aggregated_orders_do_not_reveal_ordinary_totals(self):
        groups = self._logistics(self.env['sale.order']).formatted_read_group(
            [('id', 'in', self.all_test_sales.ids)], ['company_id'],
            ['amount_untaxed:sum'])
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]['company_id'][0], self.supply_company.id)
        self.assertEqual(groups[0]['amount_untaxed:sum'], 10)

    def test_sales_analysis_excludes_ordinary_sales_and_cannot_be_bypassed(self):
        self.env.flush_all()
        reports = self._logistics(self.env['sale.report'])
        domain = [('alvina_sale_order_id', 'in', self.all_test_sales.ids)]
        expected = set(self.supply_sales.ids)
        for all_sales in (False, True):
            if all_sales:
                self.logistics.group_ids = [Command.link(
                    self.env.ref('sales_team.group_sale_salesman_all_leads').id)]
            with self.subTest(all_sales=all_sales):
                rows = reports.search(domain)
                self.assertEqual(set(rows.alvina_sale_order_id.ids), expected)
                self.assertEqual(reports.search_count(domain), 2)
                grouped = reports._read_group(domain, ['alvina_sale_order_id'], ['__count'])
                self.assertEqual({order.id for order, _count in grouped}, expected)
                self.assertEqual(sum(count for _order, count in grouped), 2)
        excluded = self.env['sale.report'].search([
            ('alvina_sale_order_id', 'in', self.excluded_sales.ids)])
        self.assertTrue(excluded, 'The negative fixture must contain real analysis rows')
        with self.assertRaises(AccessError):
            self._logistics(excluded).read(['price_total'])

    def test_dispatch_reports_work_without_branch_assignment(self):
        sale_report = self._logistics(
            self.env['report.alvina_branch_requisition.dispatch_sale'])
        request_report = self._logistics(
            self.env['report.alvina_branch_requisition.dispatch_request'])
        sales = sale_report._get_report_values(self.supply_sales.ids)
        requests = request_report._get_report_values(self.supply_requests.ids)
        self.assertEqual(len(sales['documents']), 2)
        self.assertEqual(len(requests['documents']), 2)
        self.assertEqual(sum(row['totals']['requested']['total']
                             for row in sales['documents']), 10)
        html, kind = self._logistics(self.env['ir.actions.report'])._render_qweb_html(
            'alvina_branch_requisition.action_dispatch_sale_report',
            self.supply_sales.ids)
        self.assertEqual(kind, 'html')
        self.assertIn(self.first_request.name.encode(), html)

    def test_known_ordinary_ids_cannot_be_printed_through_either_report(self):
        for sale in (self.ordinary_own | self.ordinary_unassigned | self.legacy_sale):
            with self.subTest(sale=sale.name):
                with self.assertRaises(AccessError):
                    self._logistics(self.env[
                        'report.alvina_branch_requisition.dispatch_sale'])._get_report_values(sale.ids)
                with self.assertRaises(AccessError):
                    self._logistics(self.env['ir.actions.report'])._render_qweb_html(
                        'sale.action_report_saleorder', sale.ids)

    def test_role_does_not_grant_access_to_buyer_purchases_or_other_providers(self):
        self.assertEqual(self.logistics.company_ids, self.supply_company)
        for purchase in self.supply_requests.purchase_order_id:
            with self.assertRaises(AccessError):
                self._logistics(purchase).read(['name', 'amount_total'])
        foreign_sales = self._as(self.env['sale.order'], self.foreign_logistics)
        self.assertFalse(foreign_sales.search([
            ('id', 'in', self.supply_sales.ids)]))
        with self.assertRaises(AccessError):
            self._as(self.supply_requests, self.foreign_logistics).read(['name'])
        with self.assertRaises(AccessError):
            self._logistics(self.foreign_sale).read(['name'])

    def test_forged_allowed_company_context_does_not_expand_permissions(self):
        model = self._logistics(self.env['sale.order']).with_context(
            allowed_company_ids=[self.unrelated_company.id])
        with self.assertRaises(AccessError):
            model.search([('id', 'in', self.all_test_sales.ids)])
        self.assertEqual(self.logistics.company_ids, self.supply_company)

    def test_authorized_companies_still_follow_current_company_selection(self):
        self.logistics.write({'company_ids': [Command.link(self.unrelated_company.id)]})
        sales = self._logistics(self.env['sale.order']).with_context(
            allowed_company_ids=self.unrelated_company.ids)
        self.assertFalse(sales.search([('id', 'in', self.all_test_sales.ids)]))
        with self.assertRaises(AccessError):
            self._logistics(self.supply_sales).with_context(
                allowed_company_ids=self.unrelated_company.ids).read(['name'])

    def test_role_cannot_create_or_copy_manual_sales(self):
        sales = self._logistics(self.env['sale.order'])
        values = {'partner_id': self.vendor.id, 'company_id': self.supply_company.id}
        with self.assertRaises(AccessError), self.cr.savepoint():
            sales.create(values)
        with self.assertRaises(AccessError), self.cr.savepoint():
            self._logistics(self.supply_sales[:1]).copy()
        with self.assertRaises(AccessError), self.cr.savepoint():
            sales.create(dict(values,
                              alvina_branch_request_id=self.first_request.id))
        with self.assertRaises(AccessError), self.cr.savepoint():
            sales.with_context(default_alvina_branch_request_id=self.first_request.id).create(values)

    def test_service_keeps_creating_pairs_and_does_not_reassign_users_or_history(self):
        company_snapshot = {user.id: user.company_ids.ids for user in
                            self.requester | self.second_requester | self.logistics | self.keeper}
        assignment_snapshot = {branch.id: branch.user_ids.ids
                               for branch in self.branch | self.second_branch}
        links = [(request.id, request.sale_order_id.id, request.purchase_order_id.id)
                 for request in self.supply_requests]
        new_request = self._request(product=self.shared_product, quantity=4,
                                    required_date=self.required_date)
        self.assertTrue(new_request.sudo().sale_order_id)
        self.assertEqual(new_request.sudo().sale_order_id.state, 'sale')
        self.assertEqual(new_request.sudo().purchase_order_id.state, 'purchase')
        self.assertTrue(new_request.sudo().sale_order_id.picking_ids)
        self.assertTrue(new_request.sudo().purchase_order_id.picking_ids)
        self.assertFalse(new_request.sudo().sale_order_id.invoice_ids)
        self.assertFalse(new_request.sudo().purchase_order_id.invoice_ids)
        self._logistics(new_request).check_access('read')
        for user in self.requester | self.second_requester | self.logistics | self.keeper:
            self.assertEqual(user.company_ids.ids, company_snapshot[user.id])
        for branch in self.branch | self.second_branch:
            self.assertEqual(branch.user_ids.ids, assignment_snapshot[branch.id])
        self.assertEqual([(request.id, request.sale_order_id.id, request.purchase_order_id.id)
                          for request in self.supply_requests], links)

    def test_logistics_confirmation_keeps_existing_pairs_and_inventory_operations(self):
        sale = self._logistics(self.supply_sales[:1])
        self.assertNotEqual(sale.user_id, self.logistics)
        self.assertEqual(sale.state, 'sale')
        picking_ids = sale.picking_ids.ids
        purchase = sale.sudo().alvina_branch_request_id.purchase_order_id
        links = (sale.id, purchase.id, purchase.picking_ids.ids)
        # Standard Odoo refuses confirming an already confirmed order. The
        # logistics role must not turn this into another pair or another move.
        with self.assertRaises(UserError), self.cr.savepoint():
            sale.action_confirm()
        self.assertEqual(sale.state, 'sale')
        self.assertTrue(sale.picking_ids)
        self.assertEqual(sale.picking_ids.ids, picking_ids)
        self.assertEqual((sale.id, purchase.id, purchase.picking_ids.ids), links)
        self.assertEqual(sale.picking_ids.company_id, self.supply_company)
        self.assertFalse(sale.invoice_ids)
        self.assertEqual(self.logistics.company_ids, self.supply_company)
        self.assertEqual(self.legacy_sale.state, 'draft')
        self.assertTrue(all(order.state == 'draft' for order in self.excluded_sales))

    def test_normal_sales_user_without_role_retains_ordinary_workflow(self):
        self.assertFalse(self.commercial_user.has_group(
            'alvina_branch_requisition.group_industry_logistics'))
        ordinary = self.ordinary_own | self.ordinary_unassigned | self.ordinary_other
        self.assertEqual(set(self._as(self.env['sale.order'], self.commercial_user).search([
            ('id', 'in', ordinary.ids)]).ids), set(ordinary.ids))
        self._as(ordinary, self.commercial_user).read(['name', 'amount_total'])
        created = self._as(self.env['sale.order'], self.commercial_user).create({
            'partner_id': self.vendor.id, 'company_id': self.supply_company.id,
            'order_line': [Command.create({
                'product_id': self.shared_product.id,
                'product_uom_qty': 1, 'price_unit': 7,
            })],
        })
        self.assertFalse(created.alvina_branch_request_id)
        self.assertEqual(created.state, 'draft')
        self.assertEqual(created.amount_untaxed, 7)
