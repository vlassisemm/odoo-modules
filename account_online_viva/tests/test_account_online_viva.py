# Copyright 2026 Vlassis Emmanouil
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
from datetime import date
from unittest.mock import MagicMock, patch

from odoo.tests.common import TransactionCase

from odoo.addons.account_online_viva.models.viva_client import VivaClient, VivaApiError

VIVA_PATH = 'odoo.addons.account_online_viva.models.viva_client.http_requests'


def _resp(json_data, status=200):
    r = MagicMock()
    r.status_code = status
    r.json.return_value = json_data
    r.raise_for_status.side_effect = None if status < 400 else Exception('http %s' % status)
    return r


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


class TestVivaClient(TransactionCase):
    @patch(VIVA_PATH)
    def test_token_uses_demo_host_and_caches(self, req):
        req.post.return_value = _resp({'access_token': 'tok', 'expires_in': 3600})
        client = VivaClient('cid', 'sec', 'demo')
        self.assertEqual(client._token(), 'tok')
        self.assertEqual(client._token(), 'tok')  # cached
        self.assertEqual(req.post.call_count, 1)
        url = req.post.call_args.args[0] if req.post.call_args.args else req.post.call_args.kwargs['url']
        self.assertIn('demo-accounts.vivapayments.com/connect/token', url)

    @patch(VIVA_PATH)
    def test_list_wallets_maps_fields(self, req):
        req.post.return_value = _resp({'access_token': 'tok', 'expires_in': 3600})
        req.get.return_value = _resp([
            {'walletId': 'W1', 'iban': 'GR16...', 'currencyCode': 978,
             'availableBalance': 12.5, 'friendlyName': 'Main'},
        ])
        client = VivaClient('cid', 'sec', 'demo')
        wallets = client.list_wallets()
        self.assertEqual(wallets[0]['wallet_id'], 'W1')
        self.assertEqual(wallets[0]['currency_code'], 978)
        self.assertEqual(wallets[0]['name'], 'Main')

    @patch(VIVA_PATH)
    def test_token_failure_not_rewrapped_by_list_wallets(self, req):
        req.post.side_effect = Exception('connection refused')
        client = VivaClient('cid', 'sec', 'demo')
        with self.assertRaises(VivaApiError) as ctx:
            client.list_wallets()
        self.assertTrue(
            str(ctx.exception).startswith('OAuth token request failed'),
            'Expected token error to propagate unchanged, got: %s' % ctx.exception,
        )

    @patch(VIVA_PATH)
    def test_search_transactions_posts_date_range(self, req):
        req.post.side_effect = [
            _resp({'access_token': 'tok', 'expires_in': 3600}),
            _resp([{'accountTransactionId': 'T1', 'amount': -5.0}]),
        ]
        client = VivaClient('cid', 'sec', 'production')
        txns = client.search_transactions('W1', date(2026, 1, 1), date(2026, 1, 31))
        self.assertEqual(txns[0]['accountTransactionId'], 'T1')
        body = req.post.call_args.kwargs['json']
        self.assertEqual(body['WalletId'], 'W1')
        self.assertEqual(body['DateFrom'], '2026-01-01')
        self.assertEqual(body['DateTo'], '2026-01-31')



class VivaCommon(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.journal = cls.env['account.journal'].create({
            'name': 'Viva Bank', 'type': 'bank', 'code': 'VIVA1',
        })
        cls.account = cls.env['viva.account'].create({
            'name': 'Main wallet', 'journal_id': cls.journal.id, 'wallet_id': 'W1',
        })


class TestVivaAccountModel(VivaCommon):
    def test_default_sync_start_date_90_days(self):
        self.assertTrue(self.account.sync_start_date)

    def test_journal_unique(self):
        with self.assertRaises(Exception):
            self.env['viva.account'].create({
                'name': 'dup', 'journal_id': self.journal.id, 'wallet_id': 'W2'})

    def test_get_client_reads_company_creds(self):
        self.company.viva_client_id = 'cid'
        self.company.sudo().viva_client_secret = 'sec'
        client = self.account._viva_get_client()
        self.assertEqual(client.client_id, 'cid')
        self.assertEqual(client.environment, 'demo')


class TestVivaAccountMultiCompany(VivaCommon):
    def test_other_company_record_hidden(self):
        other_company = self.env['res.company'].create({'name': 'Other Co'})
        other_journal = self.env['account.journal'].create({
            'name': 'OJ', 'type': 'bank', 'code': 'OJ1', 'company_id': other_company.id})
        other_acc = self.env['viva.account'].create({
            'name': 'other', 'journal_id': other_journal.id, 'wallet_id': 'WX',
            'company_id': other_company.id})
        user = self.env['res.users'].create({
            'name': 'Acct', 'login': 'acct_viva',
            'company_id': self.company.id, 'company_ids': [(6, 0, self.company.ids)],
            'group_ids': [(4, self.env.ref('account.group_account_user').id)]})
        visible = self.env['viva.account'].with_user(user).search([])
        self.assertNotIn(other_acc.id, visible.ids)


from psycopg2 import IntegrityError
from odoo.tools import mute_logger


class TestStatementLineDedup(VivaCommon):
    def _line(self, viva_id):
        return self.env['account.bank.statement.line'].create({
            'journal_id': self.journal.id, 'amount': 1.0,
            'payment_ref': 'x', 'viva_transaction_id': viva_id})

    def test_field_exists(self):
        line = self._line('T1')
        self.assertEqual(line.viva_transaction_id, 'T1')

    @mute_logger('odoo.sql_db')
    def test_duplicate_viva_id_rejected(self):
        self._line('T1')
        self.env.flush_all()
        with self.assertRaises(IntegrityError):
            self._line('T1')
            self.env.flush_all()

    def test_null_viva_id_allowed_multiple(self):
        a = self.env['account.bank.statement.line'].create({
            'journal_id': self.journal.id, 'amount': 1.0, 'payment_ref': 'a'})
        b = self.env['account.bank.statement.line'].create({
            'journal_id': self.journal.id, 'amount': 2.0, 'payment_ref': 'b'})
        self.env.flush_all()
        self.assertTrue(a.id and b.id)
