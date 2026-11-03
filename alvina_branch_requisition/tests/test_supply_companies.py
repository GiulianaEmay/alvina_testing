"""Supply-company access must not grant a branch access to the central company."""
import json
from unittest.mock import patch
from uuid import uuid4

from bs4 import BeautifulSoup

from odoo import Command, api, fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import HttpCase, TransactionCase, tagged

from ..models.dates import minimum_required_date


class SupplyCompanyFixture:
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.supply_company = cls.env.company
        cls.branch_company = cls.env['res.company'].create({'name': 'UPAO requester company'})
        cls.second_company = cls.env['res.company'].create({'name': 'Second requester company'})
        cls.unrelated_company = cls.env['res.company'].create({'name': 'Unrelated supplier company'})
        cls.warehouse = cls.env['stock.warehouse'].search([
            ('company_id', '=', cls.supply_company.id)], limit=1)
        if not cls.warehouse:
            cls.warehouse = cls.env['stock.warehouse'].create({
                'name': 'Central supply warehouse', 'code': 'SCEN',
                'company_id': cls.supply_company.id,
            })

        def user(login, company, group):
            return cls.env['res.users'].create({
                'name': login, 'login': login, 'password': 'local-test-only',
                'company_id': company.id, 'company_ids': [Command.set(company.ids)],
                'group_ids': [Command.set([
                    cls.env.ref('base.group_user').id,
                    # Existing branch accounts also have standard stock/purchase
                    # access. Company rules must still protect central records.
                    cls.env.ref('stock.group_stock_user').id,
                    cls.env.ref('purchase.group_purchase_user').id,
                    cls.env.ref('alvina_branch_requisition.' + group).id,
                ])],
            })

        cls.requester = user('alvina-supply-upao', cls.branch_company, 'group_requester')
        cls.second_requester = user('alvina-supply-second', cls.second_company, 'group_requester')
        cls.outsider = user('alvina-supply-outsider', cls.branch_company, 'group_requester')
        cls.keeper = user('alvina-supply-keeper', cls.supply_company, 'group_keeper')
        cls.other_keeper = user('alvina-supply-other-keeper', cls.supply_company, 'group_keeper')

        def branch(name, company, requester):
            contact = cls.env['res.partner'].create({'name': name, 'company_id': company.id})
            return cls.env['alvina.branch.binding'].create({
                'resource_ref': f'res.partner,{contact.id}',
                'company_id': company.id, 'supply_company_id': cls.supply_company.id,
                'warehouse_id': cls.warehouse.id,
                'user_ids': [Command.set((requester | cls.keeper).ids)],
            })

        cls.branch = branch('UPAO existing branch', cls.branch_company, cls.requester)
        cls.second_branch = branch('Second existing branch', cls.second_company, cls.second_requester)
        category = cls.env['product.category'].create({'name': 'Comerciales'})

        def product(name, company, **extra):
            values = {
                'name': name, 'categ_id': category.id,
                'company_id': company.id if company else False,
                'is_storable': True, 'alvina_requisition_enabled': True,
            }
            values.update(extra)
            return cls.env['product.product'].create(values)

        cls.product = product('Enabled central product', cls.supply_company, default_code='SC-10')
        cls.shared_product = product('Enabled shared product', None, default_code='SC-20')
        cls.unrelated_product = product('Private unrelated product', cls.unrelated_company)
        cls.disabled_product = product('Disabled central product', cls.supply_company,
                                       alvina_requisition_enabled=False)
        for item in (cls.product, cls.shared_product, cls.unrelated_product, cls.disabled_product):
            assert item.exists(), 'Supply-company fixtures require real product variants'
        cls.vendor = cls.env['res.partner'].create({'name': 'Central supplier'})
        cls.purchase_order = cls.env['purchase.order'].with_company(cls.supply_company).create({
            'partner_id': cls.vendor.id, 'company_id': cls.supply_company.id,
        })
        cls.env['stock.quant']._update_available_quantity(
            cls.product, cls.warehouse.lot_stock_id, 10)
        cls.quant = cls.env['stock.quant'].search([
            ('product_id', '=', cls.product.id),
            ('location_id', '=', cls.warehouse.lot_stock_id.id),
        ], limit=1)

    def _as(self, records, user):
        return records.with_user(user).with_context(allowed_company_ids=user.company_ids.ids)

    def _request(self, user=None, branch=None, product=None, quantity=5, **extra):
        user = user or self.requester
        product = self.product if product is None else product
        values = {
            'branch_id': (branch or self.branch).id,
            'required_date': minimum_required_date(fields.Datetime.now()), 'submission_key': str(uuid4()),
            'line_ids': [Command.create({
                'product_id': product.id, 'product_qty': quantity,
            })],
        }
        values.update(extra)
        return self._as(self.env['alvina.branch.request'], user).create(values)


@tagged('post_install', '-at_install')
class TestSupplyCompanies(SupplyCompanyFixture, TransactionCase):
    def test_requester_submits_without_access_to_central_company(self):
        record = self._request()
        self.assertFalse(record.env.su)
        self.assertEqual(record.env.uid, self.requester.id)
        self.assertEqual(record.company_id, self.branch_company)
        self.assertEqual(record.supply_company_id, self.supply_company)
        self.assertEqual(record.line_ids.company_id, self.branch_company)
        self.assertEqual(record.line_ids.supply_company_id, self.supply_company)
        self.assertEqual(record.line_ids.qty_pending, 5)
        self.assertEqual(self.requester.company_ids, self.branch_company)
        self.assertEqual(self.keeper.company_ids, self.supply_company)
        for private in (self.product, self.purchase_order, self.quant):
            with self.subTest(model=private._name):
                with self.assertRaises(AccessError):
                    self._as(private, self.requester).check_access('read')

    def test_shared_products_can_also_be_requested(self):
        record = self._request(product=self.shared_product)
        self.assertEqual(record.line_ids.product_id, self.shared_product)
        self.assertEqual(record.supply_company_id, self.supply_company)

    def test_central_keeper_reads_and_processes_assigned_branch(self):
        record = self._request()
        keeper_record = self._as(record, self.keeper)
        header = keeper_record.read(['name', 'branch_id', 'company_id', 'supply_company_id'])[0]
        line = keeper_record.line_ids.read(['product_id', 'branch_id', 'product_qty'])[0]
        self.assertIn('UPAO existing branch', header['branch_id'][1])
        self.assertIn('Enabled central product', line['product_id'][1])
        keeper_record.action_review()
        self.assertEqual(record.state, 'review')
        keeper_record.action_cancel()
        self.assertEqual(record.state, 'cancel')
        self.assertNotIn(self.branch_company, self.keeper.company_ids)
        branch_purchase = self.env['purchase.order'].with_company(self.branch_company).create({
            'partner_id': self.vendor.id, 'company_id': self.branch_company.id,
        })
        with self.assertRaises(AccessError):
            self._as(branch_purchase, self.keeper).check_access('read')

    def test_backend_views_render_related_names_without_opening_private_records(self):
        record = self._request()
        specification = {
            'name': {},
            'branch_id': {'fields': {'display_name': {}}},
            'company_id': {'fields': {'display_name': {}}},
            'supply_company_id': {'fields': {'display_name': {}}},
            'line_ids': {'fields': {
                'product_qty': {},
                'product_id': {'fields': {'display_name': {}}},
            }},
        }
        for user in (self.requester, self.keeper):
            with self.subTest(user=user.login):
                row = self._as(record, user).web_read(specification)[0]
                self.assertEqual(row['company_id']['id'], self.branch_company.id)
                self.assertEqual(row['supply_company_id']['id'], self.supply_company.id)
                self.assertIn('Enabled central product',
                              row['line_ids'][0]['product_id']['display_name'])
        with self.assertRaises(AccessError):
            self._as(self.product, self.requester).read(['name', 'standard_price'])
        with self.assertRaises(AccessError):
            self._as(record, self.requester).web_read({
                'line_ids': {'fields': {
                    'product_id': {'fields': {'standard_price': {}}},
                }},
            })
        with self.assertRaises(AccessError):
            self._as(self.branch_company, self.keeper).read(['name'])

    def test_consolidation_combines_companies_at_same_supply_warehouse(self):
        first = self._request(quantity=8)
        second = self._request(user=self.second_requester, branch=self.second_branch, quantity=7)
        wizard = self._as(self.env['alvina.branch.consolidation'], self.keeper).create({})
        wizard.action_refresh()
        rows = wizard.line_ids.filtered(lambda row: row.product_id == self.product)
        self.assertEqual(len(rows), 1, 'One supply warehouse must share one stock calculation')
        self.assertEqual(rows.company_id, self.supply_company)
        self.assertEqual(rows.warehouse_id, self.warehouse)
        self.assertEqual(rows.product_qty, 15)
        self.assertEqual(rows.free_qty, 10)
        self.assertEqual(rows.suggested_qty, 5)
        self.assertEqual(set(rows.source_line_ids.ids), set((first | second).line_ids.ids))
        self.assertEqual(set(rows.source_line_ids.mapped('company_id').ids),
                         {self.branch_company.id, self.second_company.id})
        groups = self._as(self.env['alvina.branch.request.line'], self.keeper).formatted_read_group(
            [('id', 'in', (first | second).line_ids.ids)],
            ['company_id', 'branch_id', 'product_id'], ['qty_pending:sum'])
        self.assertEqual(len(groups), 2)
        self.assertEqual(sum(group['qty_pending:sum'] for group in groups), 15)
        self.assertEqual({group['company_id'][0] for group in groups},
                         {self.branch_company.id, self.second_company.id})
        second_keeper = self._as(second, self.keeper)
        second_keeper.action_cancel()
        wizard.action_refresh()
        self.assertEqual(wizard.line_ids.product_qty, 8)
        self.assertEqual(wizard.line_ids.suggested_qty, 0)

    def test_unassigned_users_cannot_read_or_process_requests(self):
        record = self._request()
        for user in (self.outsider, self.other_keeper, self.second_requester):
            with self.subTest(user=user.login):
                for private in (self.branch, record, record.line_ids):
                    with self.assertRaises(AccessError):
                        self._as(private, user).check_access('read')
                with self.assertRaises(AccessError):
                    self._as(record, user).action_cancel()
        wizard = self._as(self.env['alvina.branch.consolidation'], self.other_keeper).create({})
        wizard.action_refresh()
        self.assertFalse(wizard.line_ids)

    def test_assignments_do_not_allow_requesting_for_another_company(self):
        with self.assertRaises(AccessError):
            self._request(user=self.keeper)
        with self.assertRaises(AccessError):
            self._request(branch=self.second_branch)
        with self.assertRaises(AccessError):
            self._request(user=self.outsider)

    def test_supplier_and_user_company_boundaries_are_enforced(self):
        branch_owned_product = self.env['product.product'].create({
            'name': 'Branch owned product', 'company_id': self.branch_company.id,
            'categ_id': self.product.categ_id.id, 'alvina_requisition_enabled': True,
        })
        for product in (self.unrelated_product, self.disabled_product, branch_owned_product):
            with self.subTest(product=product.name):
                with self.assertRaises(UserError), self.cr.savepoint():
                    self._request(product=product)
        partner = self.env['res.partner'].create({'name': 'Invalid assignee branch'})
        with self.assertRaises(ValidationError), self.cr.savepoint():
            self.env['alvina.branch.binding'].create({
                'resource_ref': f'res.partner,{partner.id}',
                'company_id': self.branch_company.id,
                'supply_company_id': self.supply_company.id,
                'warehouse_id': self.warehouse.id,
                'user_ids': [Command.set(self.second_requester.ids)],
            })

    def test_supplier_owned_contact_can_represent_the_requesting_branch(self):
        self.branch.resource_ref.company_id = self.supply_company
        self.assertTrue(self.branch._resource_is_available())
        self.assertEqual(self._request().branch_id, self.branch)
        self.branch.resource_ref.company_id = self.unrelated_company
        self.assertFalse(self.branch._resource_is_available())
        with self.assertRaises(ValidationError), self.cr.savepoint():
            self._request()

    def test_warehouse_must_belong_to_configured_supplier(self):
        partner = self.env['res.partner'].create({'name': 'Invalid warehouse branch'})
        with self.assertRaises(ValidationError), self.cr.savepoint():
            self.env['alvina.branch.binding'].create({
                'resource_ref': f'res.partner,{partner.id}',
                'company_id': self.branch_company.id,
                'supply_company_id': self.unrelated_company.id,
                'warehouse_id': self.warehouse.id,
                'user_ids': [Command.set(self.requester.ids)],
            })


@tagged('post_install', '-at_install')
class TestSupplyCompanyPortal(SupplyCompanyFixture, HttpCase):
    def _form(self):
        self.authenticate(self.requester.login, 'local-test-only')
        response = self.url_open('/branch/requisition/new')
        self.assertEqual(response.status_code, 200)
        soup = BeautifulSoup(response.content, 'html.parser')
        self.assertIsNotNone(soup.select_one('#alvina-request-form'))
        values = {node['name']: node.get('value', '') for node in soup.select('input[name]')}
        values['required_date'] = str(minimum_required_date(fields.Datetime.now()))
        return soup, values

    def test_supplier_catalog_is_narrow_and_request_is_not_created_with_sudo(self):
        soup, values = self._form()
        catalog = json.loads(soup.select_one('#alvina-catalog')['value'])
        product_ids = {row['id'] for row in catalog[str(self.branch.id)]}
        self.assertIn(self.product.id, product_ids)
        self.assertIn(self.shared_product.id, product_ids)
        self.assertNotIn(self.unrelated_product.id, product_ids)
        self.assertNotIn(self.disabled_product.id, product_ids)
        self.assertEqual(set(catalog), {str(self.branch.id)})
        self.assertTrue(soup.select_one('#alvina-branch').has_attr('disabled'))
        values['lines'] = json.dumps([{'id': self.product.id, 'qty': '3'}])
        model = type(self.env['alvina.branch.request'])
        original_create = model.create
        calls = []

        @api.model_create_multi
        def guarded_create(records, vals_list):
            calls.append((records.env.uid, records.env.su))
            return original_create(records, vals_list)

        with patch.object(model, 'create', guarded_create):
            response = self.url_open('/branch/requisition/submit', data=values)
        self.assertEqual(response.status_code, 200)
        self.assertIn('Requerimiento recibido', response.text)
        self.assertEqual(calls, [(self.requester.id, False)])
        record = self.env['alvina.branch.request'].search([('user_id', '=', self.requester.id)])
        self.assertEqual(len(record), 1)
        self.assertEqual(record.company_id, self.branch_company)
        self.assertEqual(record.supply_company_id, self.supply_company)
        self.assertEqual(record.line_ids.product_id, self.product)
        self.assertEqual(record.line_ids.product_qty, 3)
        self.assertEqual(self.requester.company_ids, self.branch_company)

    def test_forged_supplier_product_and_branch_are_rejected_over_http(self):
        _soup, values = self._form()
        values['lines'] = json.dumps([{'id': self.unrelated_product.id, 'qty': '2'}])
        response = self.url_open('/branch/requisition/submit', data=values)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('Requerimiento recibido', response.text)
        self.assertFalse(self.env['alvina.branch.request'].search([('user_id', '=', self.requester.id)]))
        _soup, values = self._form()
        values.update(branch_id=self.second_branch.id,
                      lines=json.dumps([{'id': self.product.id, 'qty': '2'}]))
        response = self.url_open('/branch/requisition/submit', data=values)
        self.assertEqual(response.status_code, 200)
        self.assertIn('Sucursal no autorizada', response.text)
        self.assertFalse(self.env['alvina.branch.request'].search([('user_id', '=', self.requester.id)]))
