from bs4 import BeautifulSoup
from datetime import datetime
import json
from unittest.mock import patch
from odoo import Command, fields
from odoo.tests import HttpCase, tagged

from ..models.dates import minimum_required_date


@tagged('post_install', '-at_install')
class TestPortal(HttpCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user = cls.env['res.users'].create({
            'name': 'Portal solicitante', 'login': 'alvina-portal-test',
            'password': 'local-test-only',
            'group_ids': [Command.set([cls.env.ref('base.group_portal').id,
                cls.env.ref('alvina_branch_requisition.group_requester').id])]})
        cls.warehouse = cls.env['stock.warehouse'].search([('company_id', '=', cls.env.company.id)], limit=1)
        cls.partner = cls.env['res.partner'].create({'name': 'Sucursal portal prueba'})
        cls.branch = cls.env['alvina.branch.binding'].create({
            'resource_ref': 'res.partner,%s' % cls.partner.id, 'company_id': cls.env.company.id,
            'warehouse_id': cls.warehouse.id, 'user_ids': [Command.set(cls.user.ids)]})
        category = cls.env['product.category'].create({'name': 'Comerciales'})
        cls.product = cls.env['product.product'].create({'name': 'Producto portal',
            'categ_id': category.id, 'alvina_requisition_enabled': True})

    def form(self):
        self.authenticate('alvina-portal-test', 'local-test-only')
        response = self.url_open('/branch/requisition/new')
        self.assertEqual(response.status_code, 200)
        return BeautifulSoup(response.content, 'html.parser')

    def test_anonymous_requires_login(self):
        response = self.url_open('/branch/requisition/new')
        self.assertIn('/web/login', response.url)

    def test_single_branch_locked_and_submit(self):
        import json
        soup = self.form()
        self.assertTrue(soup.select_one('#alvina-branch').has_attr('disabled'))
        values = {i['name']: i.get('value', '') for i in soup.select('input[name]')}
        values.update(required_date=str(minimum_required_date(fields.Datetime.now())),
                      lines=json.dumps([{'id': self.product.id, 'qty': '2'}]))
        response = self.url_open('/branch/requisition/submit', data=values)
        self.assertEqual(response.status_code, 200)
        self.assertIn('Requerimiento recibido', response.text)
        record = self.env['alvina.branch.request'].search([('user_id', '=', self.user.id)])
        self.assertEqual(len(record), 1)
        self.assertEqual(record.line_ids.product_qty, 2)
        self.url_open('/branch/requisition/submit', data=values)
        self.assertEqual(self.env['alvina.branch.request'].search_count([('user_id', '=', self.user.id)]), 1)

    def test_form_defaults_to_peru_minimum_and_rejects_a_manipulated_post(self):
        with patch.object(fields.Datetime, 'now', return_value=datetime(2026, 10, 22, 0, 30)):
            soup = self.form()
            date_input = soup.select_one('#alvina-date')
            self.assertEqual(date_input['min'], '2026-10-23')
            self.assertEqual(date_input['value'], '2026-10-23')
            values = {node['name']: node.get('value', '') for node in soup.select('input[name]')}
            values.update(required_date='2026-10-22',
                          lines=json.dumps([{'id': self.product.id, 'qty': '2'}]))
            response = self.url_open('/branch/requisition/submit', data=values)
            self.assertEqual(response.status_code, 200)
            self.assertNotIn('Requerimiento recibido', response.text)
            self.assertIn('dos días calendario', response.text)
            self.assertFalse(self.env['alvina.branch.request'].search([
                ('user_id', '=', self.user.id)]))
            # The exact earliest date is valid even though UTC has a different day.
            values['required_date'] = '2026-10-23'
            response = self.url_open('/branch/requisition/submit', data=values)
            self.assertIn('Requerimiento recibido', response.text)
            record = self.env['alvina.branch.request'].search([('user_id', '=', self.user.id)])
            self.assertEqual(str(record.required_date), '2026-10-23')

    def test_rpc_create_cannot_bypass_minimum_with_timezone_or_default_context(self):
        self.form()
        with patch.object(fields.Datetime, 'now', return_value=datetime(2026, 10, 22, 5, 30)):
            response = self.url_open('/web/dataset/call_kw', data=json.dumps({
                'jsonrpc': '2.0', 'method': 'call', 'id': 1,
                'params': {
                    'model': 'alvina.branch.request', 'method': 'create',
                    'args': [[{
                        'branch_id': self.branch.id, 'required_date': '2026-10-23',
                        'submission_key': 'alvina-rpc-invalid-date',
                        'line_ids': [[0, 0, {'product_id': self.product.id, 'product_qty': 1}]],
                    }]],
                    'kwargs': {'context': {
                        'tz': 'Pacific/Honolulu', 'default_required_date': '2099-01-01',
                        'allowed_company_ids': self.user.company_ids.ids,
                    }},
                },
            }), headers={'Content-Type': 'application/json'})
            self.assertEqual(response.status_code, 200)
            data = response.json()
            self.assertIn('error', data)
            self.assertEqual(data['error']['data']['name'], 'odoo.exceptions.ValidationError')
            self.assertIn('dos días calendario', data['error']['data']['message'])
            self.assertFalse(self.env['alvina.branch.request'].search([
                ('user_id', '=', self.user.id)]))

    def test_no_branch(self):
        self.branch.user_ids = [Command.clear()]
        soup = self.form()
        self.assertIsNone(soup.select_one('#alvina-request-form'))
        self.assertIn('No tienes sucursales asignadas', soup.get_text())

    def test_multiple_branches(self):
        partner = self.env['res.partner'].create({'name': 'Segunda sucursal portal'})
        self.env['alvina.branch.binding'].create({
            'resource_ref': 'res.partner,%s' % partner.id, 'company_id': self.env.company.id,
            'warehouse_id': self.warehouse.id, 'user_ids': [Command.set(self.user.ids)]})
        soup = self.form()
        self.assertFalse(soup.select_one('#alvina-branch').has_attr('disabled'))
        self.assertEqual(len(soup.select('#alvina-branch option')), 2)

    def test_csrf_required(self):
        self.authenticate('alvina-portal-test', 'local-test-only')
        response = self.url_open('/branch/requisition/submit', data={'branch_id': self.branch.id})
        self.assertEqual(response.status_code, 400)

    def test_browser_assets_categories_clear_quantity_and_submit(self):
        # HttpCase browser_js authenticates with login == password for test users.
        self.user.password = self.user.login
        self.product.default_code = '10'
        self.env['product.product'].create({
            'name': 'Orden anterior', 'default_code': '2',
            'categ_id': self.product.categ_id.id, 'alvina_requisition_enabled': True})
        cleaning = self.env['product.category'].create({'name': 'Útiles de limpieza'})
        self.env['product.product'].create({
            'name': 'Jabón navegador', 'categ_id': cleaning.id,
            'alvina_requisition_enabled': True})
        self.browser_js('/branch/requisition/new', r'''
            (async () => {
                const assert = (condition, message) => { if (!condition) throw new Error(message); };
                const form = document.querySelector('#alvina-request-form');
                const category = document.querySelector('#alvina-category');
                const changeCategory = value => {
                    category.value = value;
                    category.dispatchEvent(new Event('change', {bubbles: true}));
                };
                const quantity = name => [...document.querySelectorAll('#alvina-products tr')]
                    .find(row => row.textContent.includes(name)).querySelector('input');
                const setQuantity = (name, value) => {
                    const input = quantity(name);
                    input.value = value;
                    input.dispatchEvent(new Event('input', {bubbles: true}));
                };
                assert(document.querySelector('#alvina-branch').disabled, 'Single branch must be locked');
                assert(!form.querySelector('[name="company_id"]'), 'Company must not be shown');
                assert(category.options.length === 4, 'Four approved categories required');
                assert(getComputedStyle(document.querySelector('.alvina-requisition h2')).color === 'rgb(104, 20, 18)', 'Corporate heading color');
                const rows = document.querySelectorAll('#alvina-products tr');
                assert(rows[0].textContent.includes('Orden anterior'), 'Natural order 2 before 10');
                setQuantity('Producto portal', '3');
                changeCategory('Útiles de limpieza');
                setQuantity('Jabón navegador', '5');
                changeCategory('Comerciales');
                assert(quantity('Producto portal').value === '3', 'Quantity must persist across categories');
                setQuantity('Producto portal', '');
                changeCategory('Útiles de limpieza');
                assert(quantity('Jabón navegador').value === '5', 'Other category quantity must persist');
                const requiredDate = document.querySelector('#alvina-date');
                assert(requiredDate.value === requiredDate.min, 'Server must default to the earliest permitted Peru date');
                assert(form.checkValidity(), 'Form must be valid');
                const event = new Event('submit', {bubbles: true, cancelable: true});
                form.dispatchEvent(event);
                assert(!event.defaultPrevented, 'Cleared quantity must not block remaining products');
                const lines = JSON.parse(document.querySelector('#alvina-lines').value);
                assert(lines.length === 1 && lines[0].qty === '5', 'Only positive remaining product must be sent');
                const response = await fetch(form.action, {method: 'POST', body: new FormData(form)});
                const html = await response.text();
                assert(response.ok && html.includes('Requerimiento recibido'), 'Real browser POST must succeed');
                console.log('test successful');
            })().catch(error => { console.error(error.stack || error); });
        ''', login=self.user.login,
            ready="document.querySelector('#alvina-request-form')?.dataset.ready === '1'", timeout=90)
        record = self.env['alvina.branch.request'].search([('user_id', '=', self.user.id)])
        self.assertEqual(len(record), 1)
        self.assertEqual(len(record.line_ids), 1)
        self.assertEqual(record.line_ids.product_qty, 5)
