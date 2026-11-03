"""Website company selection must not hide explicitly assigned branches."""
import json
from unittest.mock import patch

from bs4 import BeautifulSoup

from odoo import Command, api, fields
from odoo.exceptions import AccessError
from odoo.tests import HttpCase, tagged

from .test_supply_companies import SupplyCompanyFixture
from ..models.dates import minimum_required_date


@tagged('post_install', '-at_install')
class TestWebsiteCompany(SupplyCompanyFixture, HttpCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.website = cls.env['website'].search([], limit=1)
        cls.website.company_id = cls.supply_company

    def _other_default_company(self):
        self.requester.write({
            'company_ids': [Command.set((self.branch_company | self.second_company).ids)],
            'company_id': self.second_company.id,
        })

    def _form(self):
        self.authenticate(self.requester.login, 'local-test-only')
        response = self.url_open('/branch/requisition/new')
        self.assertEqual(response.status_code, 200)
        soup = BeautifulSoup(response.content, 'html.parser')
        self.assertIsNotNone(soup.select_one('#alvina-request-form'))
        values = {node['name']: node.get('value', '')
                  for node in soup.select('#alvina-request-form input[name]')}
        values.update(required_date=str(minimum_required_date(fields.Datetime.now())),
                      lines=json.dumps([{'id': self.product.id, 'qty': '3'}]))
        return soup, values

    def _rpc_search(self, model, domain, company_ids):
        response = self.url_open('/web/dataset/call_kw', data=json.dumps({
            'jsonrpc': '2.0', 'method': 'call', 'id': 1,
            'params': {
                'model': model, 'method': 'search_read', 'args': [domain],
                'kwargs': {'fields': ['id'], 'context': {'allowed_company_ids': company_ids}},
            },
        }), headers={'Content-Type': 'application/json'})
        self.assertEqual(response.status_code, 200)
        return response.json()

    def _enable_rfq(self):
        warehouse = self.env['stock.warehouse'].search([
            ('company_id', '=', self.branch_company.id)], limit=1)
        self.assertTrue(warehouse)
        self.branch.write({
            'generate_purchase_rfq': True,
            'purchase_picking_type_id': warehouse.in_type_id.id,
        })
        self.shared_product.write({
            'purchase_ok': True, 'list_price': 2,
            'supplier_taxes_id': [Command.clear()],
        })

    def test_single_company_user_sees_branch_on_supplier_website(self):
        soup, values = self._form()
        self.assertTrue(soup.select_one('#alvina-branch').has_attr('disabled'))
        self.assertEqual(values['branch_id'], str(self.branch.id))
        self.assertEqual(self.requester.company_ids, self.branch_company)
        self.assertNotIn(self.supply_company, self.requester.company_ids)
        catalog = json.loads(soup.select_one('#alvina-catalog')['value'])
        self.assertEqual(set(catalog), {str(self.branch.id)})
        self.assertIn(self.product.id, {product['id'] for product in catalog[str(self.branch.id)]})
        with self.assertRaises(AccessError):
            self._as(self.purchase_order, self.requester).check_access('read')

    def test_other_default_company_does_not_hide_assigned_branch(self):
        self._other_default_company()
        soup, values = self._form()
        self.assertEqual(values['branch_id'], str(self.branch.id))
        self.assertTrue(soup.select_one('#alvina-branch').has_attr('disabled'))
        self.assertNotIn('No tienes sucursales asignadas', soup.get_text())
        self.assertEqual(self.requester.company_id, self.second_company)
        self.assertEqual(set(self.requester.company_ids.ids),
                         {self.branch_company.id, self.second_company.id})

    def test_submit_selects_branch_company_without_sudo(self):
        self._other_default_company()
        _soup, values = self._form()
        model = type(self.env['alvina.branch.request'])
        original_create = model.create
        calls = []

        @api.model_create_multi
        def guarded_create(records, vals_list):
            calls.append((records.env.uid, records.env.su, records.env.company.id,
                          set(records.env.companies.ids)))
            return original_create(records, vals_list)

        with patch.object(model, 'create', guarded_create):
            response = self.url_open('/branch/requisition/submit', data=values)
        self.assertEqual(response.status_code, 200)
        self.assertIn('Requerimiento recibido', response.text)
        self.assertEqual(calls, [(self.requester.id, False, self.branch_company.id,
                                 {self.branch_company.id, self.second_company.id})])
        record = self.env['alvina.branch.request'].search([('user_id', '=', self.requester.id)])
        self.assertEqual(len(record), 1)
        self.assertEqual(record.company_id, self.branch_company)
        self.assertEqual(record.supply_company_id, self.supply_company)
        self.assertEqual(self.requester.company_id, self.second_company)
        self.assertNotIn(self.supply_company, self.requester.company_ids)

    def test_unassigned_branch_rejected_even_in_permitted_company(self):
        self._other_default_company()
        _soup, values = self._form()
        values.update(branch_id=str(self.second_branch.id),
                      company_id=self.supply_company.id,
                      allowed_company_ids=json.dumps([self.supply_company.id]))
        response = self.url_open('/branch/requisition/submit', data=values)
        self.assertEqual(response.status_code, 200)
        self.assertIn('Sucursal no autorizada', response.text)
        self.assertFalse(self.env['alvina.branch.request'].search([
            ('user_id', '=', self.requester.id)]))
        self.assertNotIn(self.supply_company, self.requester.company_ids)

    def test_unassigned_user_still_has_no_branches(self):
        self.authenticate(self.outsider.login, 'local-test-only')
        response = self.url_open('/branch/requisition/new')
        self.assertEqual(response.status_code, 200)
        soup = BeautifulSoup(response.content, 'html.parser')
        self.assertIsNone(soup.select_one('#alvina-request-form'))
        self.assertIn('No tienes sucursales asignadas', soup.get_text())

    def test_web_route_does_not_expand_standard_rpc_or_backend_scope(self):
        self._other_default_company()
        self._form()
        # Standard RPC must continue to respect its own active company context,
        # even after visiting our route. The permitted but inactive UPAO branch
        # must remain hidden in that standard context.
        result = self._rpc_search('alvina.branch.binding', [('id', '=', self.branch.id)],
                                  [self.second_company.id])
        self.assertEqual(result.get('result'), [], result)
        result = self._rpc_search('purchase.order', [('id', '=', self.purchase_order.id)],
                                  [self.branch_company.id, self.second_company.id])
        self.assertEqual(result.get('result'), [], result)
        result = self._rpc_search('purchase.order', [('id', '=', self.purchase_order.id)],
                                  [self.supply_company.id])
        self.assertIn('error', result)
        self.assertEqual(result['error']['data']['name'], 'odoo.exceptions.AccessError')
        backend = self.env['alvina.branch.binding'].with_user(self.requester).with_context(
            allowed_company_ids=[self.second_company.id])
        self.assertFalse(backend.search([('id', '=', self.branch.id)]))
        self.assertNotIn(self.supply_company, self.requester.company_ids)

    def test_rfq_catalog_excludes_private_and_non_purchasable_products(self):
        self._enable_rfq()
        non_purchasable = self.env['product.product'].create({
            'name': 'Shared product excluded from purchase',
            'categ_id': self.shared_product.categ_id.id,
            'purchase_ok': False, 'alvina_requisition_enabled': True,
        })
        soup, values = self._form()
        catalog = json.loads(soup.select_one('#alvina-catalog')['value'])
        product_ids = {product['id'] for product in catalog[str(self.branch.id)]}
        self.assertIn(self.shared_product.id, product_ids)
        self.assertNotIn(self.product.id, product_ids)
        self.assertNotIn(non_purchasable.id, product_ids)
        # The same restriction is enforced on a hand-crafted POST, rather than
        # relying on the browser's catalog visibility alone.
        response = self.url_open('/branch/requisition/submit', data=values)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('Requerimiento recibido', response.text)
        self.assertFalse(self.env['alvina.branch.request'].search([
            ('user_id', '=', self.requester.id)]))

    def test_web_submission_creates_buyer_rfq_even_with_other_default_company(self):
        self._other_default_company()
        self._enable_rfq()
        _soup, values = self._form()
        values['lines'] = json.dumps([{'id': self.shared_product.id, 'qty': '3'}])
        response = self.url_open('/branch/requisition/submit', data=values)
        self.assertEqual(response.status_code, 200)
        self.assertIn('Requerimiento recibido', response.text)
        record = self.env['alvina.branch.request'].search([('user_id', '=', self.requester.id)])
        self.assertEqual(len(record), 1)
        order = record.purchase_order_id
        self.assertEqual(order.company_id, self.branch_company)
        self.assertEqual(order.partner_id, self.supply_company.partner_id)
        self.assertEqual(order.state, 'draft')
        self.assertEqual(order.order_line.product_id, self.shared_product)
        self.assertEqual(order.order_line.price_unit, 2)
        self.assertEqual(order.amount_untaxed, 6)
        self.assertEqual(self.requester.company_id, self.second_company)
        self.assertNotIn(self.supply_company, self.requester.company_ids)
