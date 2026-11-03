"""Regression checks against RPC context spoofing and access outside the user's scope."""
from uuid import uuid4

from odoo import Command, fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import TransactionCase, tagged

from ..models.dates import minimum_required_date


@tagged('post_install', '-at_install')
class TestRequisitionSecurity(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.warehouse = cls.env['stock.warehouse'].search([
            ('company_id', '=', cls.company.id)], limit=1)
        requester_group = cls.env.ref('alvina_branch_requisition.group_requester')
        internal_group = cls.env.ref('base.group_user')
        cls.user = cls.env['res.users'].create({
            'name': 'Security requester', 'login': 'alvina-security-requester',
            'company_id': cls.company.id,
            'company_ids': [Command.set(cls.company.ids)],
            'group_ids': [Command.set((requester_group | internal_group).ids)],
        })
        cls.other_user = cls.env['res.users'].create({
            'name': 'Other security requester', 'login': 'alvina-security-other',
            'company_id': cls.company.id,
            'company_ids': [Command.set(cls.company.ids)],
            'group_ids': [Command.set((requester_group | internal_group).ids)],
        })
        partner = cls.env['res.partner'].create({'name': 'Security existing branch'})
        cls.branch = cls.env['alvina.branch.binding'].create({
            'resource_ref': f'res.partner,{partner.id}',
            'company_id': cls.company.id, 'warehouse_id': cls.warehouse.id,
            'user_ids': [Command.set(cls.user.ids)],
        })
        category = cls.env['product.category'].create({'name': 'Comerciales'})
        cls.product = cls.env['product.product'].create({
            'name': 'Security product', 'categ_id': category.id,
            'alvina_requisition_enabled': True,
        })

    def _request(self, user=None, branch=None, context=None, **extra):
        vals = {
            'branch_id': (branch or self.branch).id,
            'required_date': minimum_required_date(fields.Datetime.now()), 'submission_key': str(uuid4()),
            'line_ids': [Command.create({
                'product_id': self.product.id, 'product_qty': 5,
            })],
        }
        vals.update(extra)
        return self.env['alvina.branch.request'].with_user(user or self.user).with_context(
            **(context or {})).create(vals)

    def test_context_cannot_create_completed_or_cancelled_request(self):
        before = self.env['alvina.branch.request'].search_count([])
        for state in ('done', 'cancel'):
            with self.subTest(state=state):
                with self.assertRaises(AccessError), self.cr.savepoint():
                    self._request(context={'default_state': state})
        self.assertEqual(self.env['alvina.branch.request'].search_count([]), before)

    def test_context_cannot_forge_supplied_or_pending_quantities(self):
        for context in ({'default_qty_supplied': 5}, {'default_qty_pending': 0}):
            with self.subTest(context=context):
                record = self._request(context=context)
                self.assertEqual(record.state, 'pending')
                self.assertEqual(record.line_ids.qty_supplied, 0)
                self.assertEqual(record.line_ids.qty_pending, 5)

    def test_context_cannot_impersonate_author_or_override_sequence(self):
        record = self._request(context={
            'default_user_id': self.other_user.id,
            'default_name': 'FORGED-REFERENCE',
            'default_request_date': '2000-01-01 00:00:00',
        })
        self.assertEqual(record.user_id, self.user)
        self.assertNotEqual(record.name, 'FORGED-REFERENCE')
        self.assertGreater(record.request_date, fields.Datetime.to_datetime('2000-01-01'))

    def test_requester_cannot_run_keeper_actions(self):
        with self.assertRaises(AccessError):
            self.env['alvina.branch.consolidation'].with_user(self.user).create({})
        wizard = self.env['alvina.branch.consolidation'].create({}).with_user(self.user)
        with self.assertRaises(AccessError):
            wizard.action_refresh()
        with self.assertRaises(AccessError):
            wizard.action_branch_detail()

    def test_keeper_still_cannot_read_or_submit_for_unassigned_branch(self):
        self.user.group_ids = [Command.link(
            self.env.ref('alvina_branch_requisition.group_keeper').id)]
        partner = self.env['res.partner'].create({'name': 'Unassigned existing branch'})
        branch = self.env['alvina.branch.binding'].create({
            'resource_ref': f'res.partner,{partner.id}',
            'company_id': self.company.id, 'warehouse_id': self.warehouse.id,
            'user_ids': [Command.set(self.other_user.ids)],
        })
        record = self._request(user=self.other_user, branch=branch)
        with self.assertRaises(AccessError):
            record.with_user(self.user).check_access('read')
        with self.assertRaises(AccessError):
            record.line_ids.with_user(self.user).check_access('read')
        with self.assertRaises(AccessError):
            self._request(branch=branch)

    def test_company_context_restricts_even_an_assigned_multicompany_user(self):
        other_company = self.env['res.company'].create({'name': 'Security other company'})
        warehouse = self.env['stock.warehouse'].search([
            ('company_id', '=', other_company.id)], limit=1)
        if not warehouse:
            warehouse = self.env['stock.warehouse'].create({
                'name': 'Security other warehouse', 'code': 'SECO',
                'company_id': other_company.id,
            })
        self.user.company_ids = [Command.link(other_company.id)]
        partner = self.env['res.partner'].create({
            'name': 'Other company existing branch', 'company_id': other_company.id,
        })
        branch = self.env['alvina.branch.binding'].create({
            'resource_ref': f'res.partner,{partner.id}',
            'company_id': other_company.id, 'warehouse_id': warehouse.id,
            'user_ids': [Command.set(self.user.ids)],
        })
        record = self._request(branch=branch, context={
            'allowed_company_ids': other_company.ids,
        })
        current_company = {'allowed_company_ids': self.company.ids}
        with self.assertRaises(AccessError):
            record.with_user(self.user).with_context(**current_company).check_access('read')
        with self.assertRaises(AccessError):
            record.line_ids.with_user(self.user).with_context(**current_company).check_access('read')
        with self.assertRaises(AccessError):
            self._request(branch=branch, context=current_company)

    def test_product_of_another_company_is_rejected(self):
        other_company = self.env['res.company'].create({'name': 'Security product company'})
        product = self.env['product.product'].create({
            'name': 'Security other-company product', 'company_id': other_company.id,
            'categ_id': self.product.categ_id.id, 'alvina_requisition_enabled': True,
        })
        self.assertTrue(product.exists())
        with self.assertRaises(UserError), self.cr.savepoint():
            self._request(line_ids=[Command.create({
                'product_id': product.id, 'product_qty': 5,
            })])

    def test_configuration_cannot_expose_private_models_as_branch_names(self):
        self.user.group_ids = [Command.link(
            self.env.ref('alvina_branch_requisition.group_admin').id)]
        private = self.env['ir.config_parameter'].create({
            'key': 'alvina_security_private_record', 'value': 'local-test-only',
        })
        self.assertFalse(private.with_user(self.user).has_access('read'))
        with self.assertRaises(ValueError), self.cr.savepoint():
            self.env['alvina.branch.binding'].with_user(self.user).create({
                'resource_ref': f'ir.config_parameter,{private.id}',
                'company_id': self.company.id, 'warehouse_id': self.warehouse.id,
                'user_ids': [Command.set(self.user.ids)],
            })

    def test_cannot_configure_an_archived_contact_as_a_branch(self):
        partner = self.env['res.partner'].create({
            'name': 'Archived existing branch', 'active': False,
        })
        with self.assertRaises(ValidationError), self.cr.savepoint():
            self.env['alvina.branch.binding'].create({
                'resource_ref': f'res.partner,{partner.id}',
                'company_id': self.company.id, 'warehouse_id': self.warehouse.id,
                'user_ids': [Command.set(self.user.ids)],
            })

    def test_archiving_an_assigned_contact_blocks_subsequent_requests(self):
        self.branch.resource_ref.active = False
        self.assertFalse(self.branch._resource_is_available())
        with self.assertRaises(ValidationError), self.cr.savepoint():
            self._request()

    def test_moving_an_assigned_contact_to_another_company_blocks_requests(self):
        other_company = self.env['res.company'].create({'name': 'Moved branch company'})
        self.branch.resource_ref.company_id = other_company
        self.assertFalse(self.branch._resource_is_available())
        with self.assertRaises(ValidationError), self.cr.savepoint():
            self._request()
