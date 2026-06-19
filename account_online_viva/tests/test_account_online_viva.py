# Copyright 2026 Vlassis Emmanouil
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
from odoo.tests.common import TransactionCase


class TestModuleInstall(TransactionCase):
    def test_module_installed(self):
        module = self.env['ir.module.module'].search([('name', '=', 'account_online_viva')])
        self.assertEqual(module.state, 'installed')


class TestCompanyCredentials(TransactionCase):
    def test_fields_exist_and_default(self):
        company = self.env.company
        company.viva_client_id = 'cid'
        company.viva_client_secret = 'secret'
        self.assertEqual(company.viva_environment, 'demo')

    def test_secret_is_system_only_and_not_copied(self):
        field = self.env['res.company']._fields['viva_client_secret']
        self.assertEqual(field.groups, 'base.group_system')
        self.assertFalse(field.copy)

    def test_config_settings_roundtrip(self):
        settings = self.env['res.config.settings'].create({'viva_client_id': 'abc'})
        settings.execute()
        self.assertEqual(self.env.company.viva_client_id, 'abc')
