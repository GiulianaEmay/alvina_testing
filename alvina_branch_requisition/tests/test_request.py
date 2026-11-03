from uuid import uuid4
from datetime import date, datetime, timedelta
from unittest.mock import patch
from odoo import Command, fields
from odoo.tests import TransactionCase, tagged
from odoo.exceptions import AccessError, ValidationError

from ..models.dates import minimum_required_date

@tagged('post_install', '-at_install')
class TestRequisition(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.warehouse = cls.env['stock.warehouse'].search([('company_id', '=', cls.company.id)], limit=1)
        cls.user = cls.env['res.users'].create({
            'name': 'Solicitante prueba', 'login': 'alvina-test',
            'company_id': cls.company.id, 'company_ids': [Command.set(cls.company.ids)],
            'group_ids': [Command.set([cls.env.ref('base.group_user').id,
                cls.env.ref('alvina_branch_requisition.group_requester').id])]})
        cls.partner = cls.env['res.partner'].create({'name': 'Sucursal existente de prueba'})
        cls.branch = cls.env['alvina.branch.binding'].create({
            'resource_ref': 'res.partner,%s' % cls.partner.id, 'company_id': cls.company.id,
            'warehouse_id': cls.warehouse.id, 'user_ids': [Command.set(cls.user.ids)]})
        cls.category = cls.env['product.category'].search([('name', '=', 'Comerciales')], limit=1)
        if not cls.category:
            cls.category = cls.env['product.category'].create({'name': 'Comerciales'})
        cls.product = cls.env['product.product'].create({'name': 'Producto prueba',
            'categ_id': cls.category.id, 'is_storable': True, 'alvina_requisition_enabled': True})

    def create_request(self, **extra):
        vals = {'branch_id': self.branch.id, 'required_date': minimum_required_date(fields.Datetime.now()),
            'submission_key': str(uuid4()), 'line_ids': [Command.create({
                'product_id': self.product.id, 'product_qty': 5})]}
        vals.update(extra)
        return self.env['alvina.branch.request'].with_user(self.user).create(vals)

    def test_zero_stock_request_and_pending(self):
        rec = self.create_request()
        self.assertEqual(rec.state, 'pending')
        self.assertEqual(rec.line_ids.qty_pending, 5)
        self.assertFalse(self.env['purchase.order'].search([('origin', '=', rec.name)]))

    def test_required_date_uses_two_peru_calendar_days_and_accepts_later_dates(self):
        # UTC is already the 22nd, while Peru is still on the 21st.
        with patch.object(fields.Datetime, 'now', return_value=datetime(2026, 10, 22, 0, 30)):
            minimum = date(2026, 10, 23)
            for invalid in (minimum - timedelta(days=3), minimum - timedelta(days=2),
                            minimum - timedelta(days=1)):
                with self.subTest(required_date=invalid):
                    with self.assertRaisesRegex(ValidationError, 'dos días calendario'), self.cr.savepoint():
                        self.create_request(required_date=invalid)
            for allowed in (minimum, minimum + timedelta(days=1), minimum + timedelta(days=30)):
                with self.subTest(required_date=allowed):
                    self.assertEqual(self.create_request(required_date=allowed).required_date, allowed)

    def test_required_date_server_default_cannot_be_changed_by_client_timezone(self):
        with patch.object(fields.Datetime, 'now', return_value=datetime(2026, 10, 22, 0, 30)):
            for timezone in ('Pacific/Kiritimati', 'Pacific/Honolulu', 'America/Lima'):
                with self.subTest(timezone=timezone):
                    request = self.env['alvina.branch.request'].with_user(self.user).with_context(
                        tz=timezone, default_required_date='2000-01-01').create({
                            'branch_id': self.branch.id, 'submission_key': str(uuid4()),
                            'line_ids': [Command.create({
                                'product_id': self.product.id, 'product_qty': 1,
                            })],
                        })
                    self.assertEqual(request.required_date, date(2026, 10, 23))

    def test_opposite_client_timezones_cannot_lower_the_peru_minimum(self):
        with patch.object(fields.Datetime, 'now', return_value=datetime(2026, 10, 22, 5, 30)):
            for timezone in ('Pacific/Kiritimati', 'Pacific/Honolulu'):
                with self.subTest(timezone=timezone):
                    with self.assertRaises(ValidationError), self.cr.savepoint():
                        self.env['alvina.branch.request'].with_user(self.user).with_context(
                            tz=timezone, default_required_date='2099-01-01').create({
                                'branch_id': self.branch.id, 'required_date': '2026-10-23',
                                'submission_key': str(uuid4()),
                                'line_ids': [Command.create({
                                    'product_id': self.product.id, 'product_qty': 1,
                                })],
                            })

    def test_historical_required_date_is_not_revalidated_by_unrelated_review(self):
        self.user.group_ids = [Command.link(self.env.ref('alvina_branch_requisition.group_keeper').id)]
        with patch.object(fields.Datetime, 'now', return_value=datetime(2026, 10, 22, 0, 30)):
            request = self.create_request()
        with patch.object(fields.Datetime, 'now', return_value=datetime(2026, 11, 22, 0, 30)):
            request.action_review()
        self.assertEqual(request.required_date, date(2026, 10, 23))
        self.assertEqual(request.state, 'review')

    def test_no_branch_assignment(self):
        self.branch.user_ids = [Command.clear()]
        with self.assertRaises(AccessError):
            self.create_request()

    def test_immutable_lines_and_state(self):
        rec = self.create_request()
        with self.assertRaises(AccessError):
            rec.line_ids.write({'qty_supplied': 1})
        with self.assertRaises(AccessError):
            rec.write({'state': 'done'})
        with self.assertRaises(AccessError):
            rec.action_cancel()
        with self.assertRaises(AccessError):
            self.env['alvina.branch.request.line'].with_user(self.user).create({
                'request_id': rec.id, 'product_id': self.product.id, 'product_qty': 2})

    def test_record_rules_cover_header_and_lines(self):
        rec = self.create_request()
        line_id = rec.line_ids.id
        self.branch.user_ids = [Command.clear()]
        self.assertFalse(self.env['alvina.branch.request'].with_user(self.user).search([('id', '=', rec.id)]))
        self.assertFalse(self.env['alvina.branch.request.line'].with_user(self.user).search([('id', '=', line_id)]))

    def test_disabled_product(self):
        self.product.alvina_requisition_enabled = False
        with self.assertRaises(ValidationError), self.cr.savepoint():
            self.create_request()

    def test_negative_quantity(self):
        with self.assertRaises(ValidationError), self.cr.savepoint():
            self.create_request(line_ids=[Command.create({'product_id': self.product.id, 'product_qty': -1})])

    def test_duplicate_product(self):
        from psycopg2 import IntegrityError
        with self.assertRaises(IntegrityError), self.cr.savepoint():
            self.create_request(line_ids=[Command.create({'product_id': self.product.id, 'product_qty': 1}),
                                          Command.create({'product_id': self.product.id, 'product_qty': 2})])

    def test_other_company_is_rejected(self):
        other = self.env['res.company'].create({'name': 'Otra compañía prueba'})
        partner = self.env['res.partner'].create({
            'name': 'Sucursal otra compañía', 'company_id': other.id})
        # A branch cannot assign a user without access to its company.
        with self.assertRaises(ValidationError), self.cr.savepoint():
            self.env['alvina.branch.binding'].create({
                'resource_ref': 'res.partner,%s' % partner.id, 'company_id': other.id,
                'warehouse_id': self.warehouse.id, 'user_ids': [Command.set(self.user.ids)]})

    def test_consolidation_and_cancel_exclusion(self):
        self.user.group_ids = [Command.link(self.env.ref('alvina_branch_requisition.group_keeper').id)]
        first = self.create_request()
        second = self.create_request()
        wizard = self.env['alvina.branch.consolidation'].with_user(self.user).create({})
        wizard.action_refresh()
        rows = wizard.line_ids.filtered(lambda row: row.product_id == self.product)
        self.assertEqual(sum(rows.mapped('product_qty')), 10)
        self.assertEqual(set(rows.source_line_ids.ids), set((first | second).line_ids.ids))
        second.action_cancel()
        wizard.action_refresh()
        self.assertEqual(sum(wizard.line_ids.filtered(lambda row: row.product_id == self.product).mapped('product_qty')), 5)

    def test_negative_stock_does_not_block(self):
        self.env['stock.quant']._update_available_quantity(self.product, self.warehouse.lot_stock_id, -10)
        rec = self.create_request()
        self.assertEqual(rec.line_ids.product_qty, 5)
        self.assertEqual(self.product.with_context(warehouse_id=self.warehouse.id).qty_available, -10)

    def test_multiple_branches_and_categories(self):
        partner = self.env['res.partner'].create({'name': 'Otra sucursal existente'})
        branch = self.env['alvina.branch.binding'].create({
            'resource_ref': 'res.partner,%s' % partner.id, 'company_id': self.company.id,
            'warehouse_id': self.warehouse.id, 'user_ids': [Command.set(self.user.ids)]})
        category = self.env['product.category'].create({'name': 'Útiles de limpieza'})
        product = self.env['product.product'].create({'name': 'Limpieza', 'categ_id': category.id,
            'alvina_requisition_enabled': True})
        rec = self.create_request(branch_id=branch.id, line_ids=[
            Command.create({'product_id': self.product.id, 'product_qty': 1}),
            Command.create({'product_id': product.id, 'product_qty': 2})])
        self.assertEqual(len(rec.line_ids), 2)
        self.assertEqual(rec.branch_id, branch)

    def test_duplicate_submission(self):
        from psycopg2 import IntegrityError
        token = str(uuid4())
        self.create_request(submission_key=token)
        with self.assertRaises(IntegrityError), self.cr.savepoint():
            self.create_request(submission_key=token)

    def test_empty_command_cannot_create_empty_request(self):
        with self.assertRaises(ValidationError), self.cr.savepoint():
            self.create_request(line_ids=[Command.clear()])

    def test_purchase_menu_integration(self):
        parent = self.env.ref('purchase.menu_procurement_management')
        for xmlid in ('menu_web', 'menu_requests', 'menu_consolidation'):
            menu = self.env.ref('alvina_branch_requisition.' + xmlid)
            self.assertEqual(menu.parent_id, parent)
            self.assertFalse(menu.web_icon)
        self.assertEqual(self.env.ref('purchase.menu_purchase_rfq').parent_id, parent)
        self.assertEqual(self.env.ref('purchase.menu_purchase_form_action').parent_id, parent)
        module = self.env['ir.module.module'].search([('name', '=', 'alvina_branch_requisition')])
        self.assertFalse(module.application)

    def test_uom_snapshot_prevents_incompatible_stock_calculation(self):
        rec = self.create_request()
        original_uom = rec.line_ids.product_uom_id
        self.product.uom_id = self.env.ref('uom.product_uom_dozen')
        self.assertEqual(rec.line_ids.product_uom_id, original_uom)
        self.user.group_ids = [Command.link(self.env.ref('alvina_branch_requisition.group_keeper').id)]
        wizard = self.env['alvina.branch.consolidation'].with_user(self.user).create({})
        with self.assertRaises(ValidationError):
            wizard.action_refresh()

    def test_stock_reservations_incoming_and_warehouse_scope(self):
        self.user.group_ids = [Command.link(self.env.ref('alvina_branch_requisition.group_keeper').id)]
        self.create_request(line_ids=[Command.create({'product_id': self.product.id, 'product_qty': 20})])
        self.env['stock.quant']._update_available_quantity(self.product, self.warehouse.lot_stock_id, 10)
        self.env['stock.quant']._update_reserved_quantity(self.product, self.warehouse.lot_stock_id, 4)
        other = self.env['stock.warehouse'].create({'name': 'Otro almacén prueba', 'code': 'ALTST', 'company_id': self.company.id})
        self.env['stock.quant']._update_available_quantity(self.product, other.lot_stock_id, 100)
        move = self.env['stock.move'].create({
            'product_id': self.product.id,
            'product_uom_qty': 7, 'product_uom': self.product.uom_id.id,
            'location_id': self.env.ref('stock.stock_location_suppliers').id,
            'location_dest_id': self.warehouse.lot_stock_id.id,
            'picking_type_id': self.warehouse.in_type_id.id, 'company_id': self.company.id})
        move._action_confirm()
        before_moves = self.env['stock.move'].search_count([])
        before_orders = self.env['purchase.order'].search_count([])
        wizard = self.env['alvina.branch.consolidation'].with_user(self.user).create({})
        wizard.action_refresh()
        row = wizard.line_ids.filtered(lambda row: row.product_id == self.product)
        self.assertEqual(row.free_qty, 6)
        self.assertEqual(row.incoming_qty, 7)
        self.assertEqual(row.suggested_qty, 7)
        wizard.action_refresh()
        self.assertEqual(wizard.line_ids.suggested_qty, 7)
        self.assertEqual(self.env['stock.move'].search_count([]), before_moves)
        self.assertEqual(self.env['purchase.order'].search_count([]), before_orders)

    def test_consolidation_uses_product_precision_for_fractional_quantities(self):
        self.env.ref('uom.decimal_product_uom').write({'digits': 3})
        self.product.uom_id.invalidate_recordset(['rounding'])
        self.user.group_ids = [Command.link(self.env.ref('alvina_branch_requisition.group_keeper').id)]
        self.create_request(line_ids=[Command.create({'product_id': self.product.id, 'product_qty': 0.015})])
        self.env['stock.quant']._update_available_quantity(self.product, self.warehouse.lot_stock_id, 0.005)
        move = self.env['stock.move'].create({
            'product_id': self.product.id, 'product_uom_qty': 0.003,
            'product_uom': self.product.uom_id.id,
            'location_id': self.env.ref('stock.stock_location_suppliers').id,
            'location_dest_id': self.warehouse.lot_stock_id.id,
            'picking_type_id': self.warehouse.in_type_id.id, 'company_id': self.company.id})
        move._action_confirm()
        wizard = self.env['alvina.branch.consolidation'].with_user(self.user).create({})
        wizard.action_refresh()
        row = wizard.line_ids.filtered(lambda line: line.product_id == self.product)
        for name, value in [('product_qty', 0.015), ('free_qty', 0.005),
                            ('incoming_qty', 0.003), ('suggested_qty', 0.007)]:
            self.assertAlmostEqual(row[name], value, places=3)
            self.assertEqual(row.fields_get([name])[name]['digits'], (16, 3))
