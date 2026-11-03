"""A code-only restart must work before the company schema is upgraded."""
from odoo import Command
from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, tagged
from psycopg2 import sql


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
        with self.assertRaises(AccessError):
            self.env.company.with_user(user).write({'alvina_jsonpe_profile': 'custom'})
        with self.assertRaises(AccessError):
            self.env.company.with_user(user).read(['alvina_jsonpe_token'])

    def test_form_onchange_preserves_selection_without_writing_virtual_changes(self):
        self.env.company.alvina_edi_format_id = self.first_format
        draft = self.env['res.company'].new({'name': 'Unsaved company name'}, origin=self.env.company)
        self.assertEqual(draft.alvina_edi_format_id, self.first_format)
        draft.alvina_edi_format_id = self.second_format
        self.env.company.invalidate_recordset(['alvina_edi_format_id'])
        self.assertEqual(self.env.company.alvina_edi_format_id, self.first_format)

    def test_prefetch_and_settings_work_with_all_pos_company_columns_absent(self):
        names = ['alvina_emission_mode', 'alvina_edi_format_id', 'alvina_jsonpe_enabled',
                 'alvina_jsonpe_profile', 'alvina_jsonpe_dni_url', 'alvina_jsonpe_ruc_url',
                 'alvina_jsonpe_method', 'alvina_jsonpe_parameter', 'alvina_jsonpe_name_path',
                 'alvina_jsonpe_number_path', 'alvina_jsonpe_street_path', 'alvina_jsonpe_auth_header',
                 'alvina_jsonpe_auth_prefix', 'alvina_jsonpe_token']
        self.env.flush_all()
        for name in names:
            self.cr.execute(sql.SQL('ALTER TABLE res_company DROP COLUMN IF EXISTS {}').format(sql.Identifier(name)))
        self.env.invalidate_all()
        company = self.env.company
        self.assertTrue(company.name)
        self.assertTrue(company.currency_id)
        self.assertFalse(company.alvina_jsonpe_enabled)
        self.assertEqual(company.alvina_jsonpe_profile, 'official')
        self.assertEqual(company.alvina_jsonpe_method, 'POST')
        self.assertEqual(company.alvina_jsonpe_parameter, 'numero')
        self.assertEqual(company.alvina_jsonpe_auth_header, 'Authorization')
        self.assertFalse(company.alvina_jsonpe_token)
        company.write({'alvina_jsonpe_enabled': True, 'alvina_jsonpe_profile': 'custom',
                       'alvina_jsonpe_token': 'unit-test-placeholder', 'alvina_jsonpe_method': 'GET'})
        company.invalidate_recordset(names)
        self.assertTrue(company.alvina_jsonpe_enabled)
        self.assertEqual(company.alvina_jsonpe_profile, 'custom')
        self.assertEqual(company.alvina_jsonpe_method, 'GET')
        self.assertEqual(company.alvina_jsonpe_token, 'unit-test-placeholder')
        self.assertTrue(all(not company._fields[name].store for name in names))

    def test_jsonpe_legacy_values_are_preserved_and_explicit_disable_wins(self):
        self.cr.execute('ALTER TABLE res_company ADD COLUMN IF NOT EXISTS alvina_jsonpe_enabled boolean')
        self.cr.execute('ALTER TABLE res_company ADD COLUMN IF NOT EXISTS alvina_jsonpe_profile varchar')
        self.cr.execute('ALTER TABLE res_company ADD COLUMN IF NOT EXISTS alvina_jsonpe_token varchar')
        self.cr.execute('UPDATE res_company SET alvina_jsonpe_enabled=true, alvina_jsonpe_profile=%s, '
                        'alvina_jsonpe_token=%s WHERE id=%s',
                        ['custom', 'legacy-unit-test-placeholder', self.env.company.id])
        names = ['alvina_jsonpe_enabled', 'alvina_jsonpe_profile', 'alvina_jsonpe_token']
        for name in names:
            self.env['ir.config_parameter'].sudo().set_param(self.env.company._alvina_setting_key(name), False)
        self.env.company.invalidate_recordset(names)
        self.assertTrue(self.env.company.alvina_jsonpe_enabled)
        self.assertEqual(self.env.company.alvina_jsonpe_profile, 'custom')
        self.assertEqual(self.env.company.alvina_jsonpe_token, 'legacy-unit-test-placeholder')
        self.env.company.write({'alvina_jsonpe_enabled': False, 'alvina_jsonpe_token': False})
        self.env.company.invalidate_recordset(names)
        self.assertFalse(self.env.company.alvina_jsonpe_enabled)
        self.assertFalse(self.env.company.alvina_jsonpe_token)
        self.assertEqual(self.env.company.alvina_jsonpe_profile, 'custom')

    def test_editing_one_jsonpe_setting_keeps_other_values_and_company_isolation(self):
        other = self.env['res.company'].create({'name': 'Isolated JSONPE company'})
        self.env.company.write({'alvina_jsonpe_profile': 'custom',
            'alvina_jsonpe_token': 'first-unit-test-placeholder', 'alvina_jsonpe_method': 'GET'})
        other.write({'alvina_jsonpe_profile': 'official', 'alvina_jsonpe_token': 'second-unit-test-placeholder'})
        self.env.company.alvina_jsonpe_enabled = True
        names = ['alvina_jsonpe_enabled', 'alvina_jsonpe_profile', 'alvina_jsonpe_token', 'alvina_jsonpe_method']
        (self.env.company | other).invalidate_recordset(names)
        self.assertEqual(self.env.company.alvina_jsonpe_profile, 'custom')
        self.assertEqual(self.env.company.alvina_jsonpe_method, 'GET')
        self.assertEqual(self.env.company.alvina_jsonpe_token, 'first-unit-test-placeholder')
        self.assertFalse(other.alvina_jsonpe_enabled)
        self.assertEqual(other.alvina_jsonpe_profile, 'official')
        self.assertEqual(other.alvina_jsonpe_token, 'second-unit-test-placeholder')

    def test_jsonpe_settings_survive_onchange_without_persisting_draft_changes(self):
        self.env.company.alvina_jsonpe_profile = 'custom'
        draft = self.env['res.company'].new({'name': 'Unsaved JSONPE settings'}, origin=self.env.company)
        self.assertEqual(draft.alvina_jsonpe_profile, 'custom')
        draft.alvina_jsonpe_profile = 'official'
        self.env.company.invalidate_recordset(['alvina_jsonpe_profile'])
        self.assertEqual(self.env.company.alvina_jsonpe_profile, 'custom')

    def test_cleared_legacy_char_settings_are_not_replaced_with_defaults(self):
        self.cr.execute('ALTER TABLE res_company ADD COLUMN IF NOT EXISTS alvina_jsonpe_auth_prefix varchar')
        self.cr.execute('UPDATE res_company SET alvina_jsonpe_auth_prefix=NULL WHERE id=%s', [self.env.company.id])
        self.env['ir.config_parameter'].sudo().set_param(
            self.env.company._alvina_setting_key('alvina_jsonpe_auth_prefix'), False)
        self.env.company.invalidate_recordset(['alvina_jsonpe_auth_prefix'])
        self.assertFalse(self.env.company.alvina_jsonpe_auth_prefix)

    def test_malformed_json_selection_cannot_break_company_reads(self):
        self.env['ir.config_parameter'].sudo().set_param(
            self.env.company._alvina_setting_key('alvina_jsonpe_profile'), '{}')
        self.env.company.invalidate_recordset(['alvina_jsonpe_profile'])
        self.assertEqual(self.env.company.alvina_jsonpe_profile, 'official')
