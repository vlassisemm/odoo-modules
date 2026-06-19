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
