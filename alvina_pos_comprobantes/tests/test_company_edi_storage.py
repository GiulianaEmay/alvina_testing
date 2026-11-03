"""A code-only restart must work before the company schema is upgraded."""
from odoo import Command
from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install', 'alvina')
class TestCompanyEdiStorage(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.first_format = cls.env['account.edi.format'].create({
            'name': 'Compatibility first provider', 'code': 'alvina_compat_first'})
        cls.second_format = cls.env['account.edi.format'].create({
            'name': 'Compatibility second provider', 'code': 'alvina_compat_second'})

    def test_company_prefetch_works_without_legacy_column(self):
        self.env.flush_all()
        self.cr.execute('ALTER TABLE res_company DROP COLUMN IF EXISTS alvina_edi_format_id')
        self.env.invalidate_all()
        self.assertTrue(self.env.company.name)
        self.assertTrue(self.env.company.currency_id)
        self.assertFalse(self.env.company.alvina_edi_format_id)
        self.env.company.alvina_edi_format_id = self.first_format
        self.env.company.invalidate_recordset(['alvina_edi_format_id'])
        self.assertEqual(self.env.company.alvina_edi_format_id, self.first_format)
        self.assertFalse(self.env['res.company']._fields['alvina_edi_format_id'].store)

    def test_existing_column_is_preserved_and_parameter_takes_precedence(self):
        self.cr.execute('ALTER TABLE res_company ADD COLUMN IF NOT EXISTS alvina_edi_format_id integer')
        self.cr.execute('UPDATE res_company SET alvina_edi_format_id=%s WHERE id=%s',
                        [self.first_format.id, self.env.company.id])
        self.env.company.invalidate_recordset(['alvina_edi_format_id'])
        self.assertEqual(self.env.company.alvina_edi_format_id, self.first_format)
        self.env.company.alvina_edi_format_id = self.second_format
        self.env.company.invalidate_recordset(['alvina_edi_format_id'])
        self.assertEqual(self.env.company.alvina_edi_format_id, self.second_format)
        self.cr.execute('SELECT alvina_edi_format_id FROM res_company WHERE id=%s', [self.env.company.id])
        self.assertEqual(self.cr.fetchone()[0], self.first_format.id)

    def test_clear_does_not_restore_legacy_selection(self):
        self.cr.execute('ALTER TABLE res_company ADD COLUMN IF NOT EXISTS alvina_edi_format_id integer')
        self.cr.execute('UPDATE res_company SET alvina_edi_format_id=%s WHERE id=%s',
                        [self.first_format.id, self.env.company.id])
        self.env.company.invalidate_recordset(['alvina_edi_format_id'])
        self.assertEqual(self.env.company.alvina_edi_format_id, self.first_format)
        self.env.company.alvina_edi_format_id = False
        self.env.company.invalidate_recordset(['alvina_edi_format_id'])
        self.assertFalse(self.env.company.alvina_edi_format_id)

    def test_selection_is_isolated_by_company(self):
        second = self.env['res.company'].create({'name': 'Other EDI compatibility company'})
        self.env.company.alvina_edi_format_id = self.first_format
        second.alvina_edi_format_id = self.second_format
        (self.env.company | second).invalidate_recordset(['alvina_edi_format_id'])
        self.assertEqual(self.env.company.alvina_edi_format_id, self.first_format)
        self.assertEqual(second.alvina_edi_format_id, self.second_format)

    def test_nonadministrator_cannot_change_the_selection(self):
        user = self.env['res.users'].create({
            'name': 'Ordinary compatibility user', 'login': 'alvina-edi-ordinary',
            'company_id': self.env.company.id, 'company_ids': [Command.set(self.env.company.ids)],
            'group_ids': [Command.set([self.env.ref('base.group_user').id])],
        })
        with self.assertRaises(AccessError):
            self.env.company.with_user(user).write({'alvina_edi_format_id': self.first_format.id})

    def test_form_onchange_preserves_selection_without_writing_virtual_changes(self):
        self.env.company.alvina_edi_format_id = self.first_format
        draft = self.env['res.company'].new({'name': 'Unsaved company name'}, origin=self.env.company)
        self.assertEqual(draft.alvina_edi_format_id, self.first_format)
        draft.alvina_edi_format_id = self.second_format
        self.env.company.invalidate_recordset(['alvina_edi_format_id'])
        self.assertEqual(self.env.company.alvina_edi_format_id, self.first_format)
