# Copyright 2026 Vlassis Emmanouil
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
from datetime import date
from unittest.mock import MagicMock, patch

from psycopg2 import IntegrityError

from odoo.exceptions import AccessError, UserError as OdooUserError
from odoo.tests.common import TransactionCase
from odoo.tools import mute_logger

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
        cls.company.currency_id = cls.env.ref('base.EUR')
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


class TestJournalSource(VivaCommon):
    def test_viva_in_available_sources(self):
        sources = dict(self.journal._get_bank_statements_available_sources())
        self.assertIn('viva', sources)

    def test_viva_account_id_computed(self):
        self.assertEqual(self.journal.viva_account_id, self.account)


class TestVivaMapping(VivaCommon):
    def test_currency_numeric_and_alpha(self):
        eur = self.env.ref('base.EUR')
        self.assertEqual(self.account._viva_currency_from_code(978), eur)
        self.assertEqual(self.account._viva_currency_from_code('978'), eur)
        self.assertEqual(self.account._viva_currency_from_code('EUR'), eur)
        self.assertFalse(self.account._viva_currency_from_code('ZZZ'))

    def test_map_transaction(self):
        raw = {'accountTransactionId': 'T7', 'amount': -12.345,
               'valueDate': '2026-02-03T10:00:00', 'counterPart': 'ACME',
               'currencyCode': 978, 'typeId': 5, 'subTypeId': 9}
        m = self.account._viva_map_transaction(raw)
        self.assertEqual(m['viva_transaction_id'], 'T7')
        self.assertEqual(m['amount'], -12.35)
        self.assertEqual(str(m['date']), '2026-02-03')
        self.assertEqual(m['partner_name'], 'ACME')
        self.assertIn('ACME', m['payment_ref'])
        self.assertEqual(m['transaction_details'], raw)

    def test_map_transaction_no_label_fields_yields_viva_transaction(self):
        raw = {'accountTransactionId': 'T8', 'amount': 5.0,
               'valueDate': '2026-03-01T00:00:00'}
        m = self.account._viva_map_transaction(raw)
        self.assertEqual(m['payment_ref'], 'Viva transaction')

    def test_filter_new_drops_existing(self):
        self.env['account.bank.statement.line'].create({
            'journal_id': self.journal.id, 'amount': 1.0,
            'payment_ref': 'x', 'viva_transaction_id': 'EXIST'})
        mapped = [{'viva_transaction_id': 'EXIST'}, {'viva_transaction_id': 'NEW'}]
        out = self.account._viva_filter_new(mapped)
        self.assertEqual([m['viva_transaction_id'] for m in out], ['NEW'])


class TestVivaSync(VivaCommon):
    def setUp(self):
        super().setUp()
        self.company.viva_client_id = 'cid'
        self.company.sudo().viva_client_secret = 'sec'
        self.account.sync_start_date = date(2026, 1, 1)

    def _patch_client(self, txns):
        client = MagicMock()
        client.search_transactions.return_value = txns
        return patch.object(type(self.account), '_viva_get_client', return_value=client)

    def test_sync_creates_lines_and_sets_source(self):
        txns = [{'accountTransactionId': 'A1', 'amount': -10.0,
                 'valueDate': '2026-03-01', 'counterPart': 'X', 'currencyCode': 978}]
        with self._patch_client(txns):
            lines = self.account._viva_sync_one(
                date_from=date(2026, 1, 1), date_to=date(2026, 3, 31))
        self.assertEqual(len(lines), 1)
        self.assertEqual(self.journal.bank_statements_source, 'viva')
        self.assertTrue(self.account.last_successful_to)

    def test_sync_idempotent(self):
        txns = [{'accountTransactionId': 'A1', 'amount': -10.0,
                 'valueDate': '2026-03-01', 'counterPart': 'X', 'currencyCode': 978}]
        with self._patch_client(txns):
            self.account._viva_sync_one(date_from=date(2026, 1, 1), date_to=date(2026, 3, 31))
            self.account._viva_sync_one(date_from=date(2026, 1, 1), date_to=date(2026, 3, 31))
        count = self.env['account.bank.statement.line'].search_count([
            ('journal_id', '=', self.journal.id), ('viva_transaction_id', '=', 'A1')])
        self.assertEqual(count, 1)

    def test_currency_mismatch_skipped(self):
        txns = [{'accountTransactionId': 'A2', 'amount': -10.0,
                 'valueDate': '2026-03-01', 'counterPart': 'X', 'currencyCode': 840}]
        self.journal.currency_id = self.env.ref('base.EUR')
        with self._patch_client(txns):
            lines = self.account._viva_sync_one(
                date_from=date(2026, 1, 1), date_to=date(2026, 3, 31))
        self.assertEqual(len(lines), 0)


class TestVivaWindow(VivaCommon):
    """Direct tests of _viva_window incremental path."""

    def setUp(self):
        super().setUp()
        self.company.viva_client_id = 'cid'
        self.company.sudo().viva_client_secret = 'sec'
        self.account.sync_start_date = date(2026, 1, 1)

    def _make_client(self):
        c = MagicMock()
        c.search_transactions.return_value = []
        return c

    def test_incremental_uses_last_successful_to_minus_overlap(self):
        from datetime import timedelta
        from odoo import fields as odoo_fields
        # Set last_successful_to to a known point; sync_start_date is much earlier
        known_dt = date(2026, 4, 10)
        self.account.last_successful_to = odoo_fields.Datetime.to_datetime(str(known_dt))

        client = self._make_client()
        with patch.object(type(self.account), '_viva_get_client', return_value=client):
            self.account._viva_sync_one()

        call_kwargs = client.search_transactions.call_args
        date_from_used = call_kwargs[0][1] if call_kwargs[0] else call_kwargs[1]['date_from']
        expected = known_dt - timedelta(days=7)
        self.assertEqual(date_from_used, expected)

    def test_incremental_clamped_to_sync_start_date(self):
        from datetime import timedelta
        from odoo import fields as odoo_fields
        # last_successful_to - 7d would be before sync_start_date → must clamp to sync_start_date
        # Set last_successful_to such that last_successful_to.date() - 7d < sync_start_date
        self.account.sync_start_date = date(2026, 3, 1)
        # last_successful_to = 2026-03-05, so overlap window = 2026-02-26 < sync_start_date
        self.account.last_successful_to = odoo_fields.Datetime.to_datetime('2026-03-05 00:00:00')

        client = self._make_client()
        with patch.object(type(self.account), '_viva_get_client', return_value=client):
            self.account._viva_sync_one()

        call_kwargs = client.search_transactions.call_args
        date_from_used = call_kwargs[0][1] if call_kwargs[0] else call_kwargs[1]['date_from']
        self.assertEqual(date_from_used, date(2026, 3, 1))


class TestVivaCron(VivaCommon):
    def setUp(self):
        super().setUp()
        self.company.viva_client_id = 'cid'
        self.company.sudo().viva_client_secret = 'sec'
        self.journal2 = self.env['account.journal'].create({
            'name': 'Viva Bank 2', 'type': 'bank', 'code': 'VIVA2'})
        self.account2 = self.env['viva.account'].create({
            'name': 'wallet 2', 'journal_id': self.journal2.id, 'wallet_id': 'W2',
            'sync_start_date': date(2026, 1, 1)})
        self.account.sync_start_date = date(2026, 1, 1)

    def test_one_account_failure_isolated(self):
        ok_txn = [{'accountTransactionId': 'OK1', 'amount': -1.0,
                   'valueDate': '2026-03-01', 'counterPart': 'X', 'currencyCode': 978}]

        # account1 succeeds, account2 raises via client
        clients = {self.account.id: ok_txn, self.account2.id: ok_txn}

        def get_client(self_rec):
            c = MagicMock()
            if self_rec.id == self.account2.id:
                c.search_transactions.side_effect = ValueError('boom')
            else:
                c.search_transactions.return_value = clients[self_rec.id]
            return c

        with patch.object(type(self.account), '_viva_get_client', get_client), \
                patch.object(self.env.cr, 'commit', lambda: None):
            self.env['viva.account']._cron_viva_fetch()

        self.assertTrue(self.env['account.bank.statement.line'].search_count([
            ('journal_id', '=', self.journal.id), ('viva_transaction_id', '=', 'OK1')]))
        self.assertTrue(self.account2.last_error)
        self.account.invalidate_recordset()
        self.assertFalse(self.account.last_error)
        self.assertTrue(self.account.last_successful_to)


class TestVivaManualFetch(VivaCommon):
    def setUp(self):
        super().setUp()
        self.company.viva_client_id = 'cid'
        self.company.sudo().viva_client_secret = 'sec'
        self.account.sync_start_date = date(2026, 1, 1)

    def test_fetch_now_runs_sync(self):
        txns = [{'accountTransactionId': 'M1', 'amount': -3.0,
                 'valueDate': '2026-03-01', 'counterPart': 'X', 'currencyCode': 978}]
        client = MagicMock()
        client.search_transactions.return_value = txns
        with patch.object(type(self.account), '_viva_get_client', return_value=client):
            action = self.account.action_viva_fetch_now()
        self.assertEqual(action['res_model'], 'account.bank.statement.line')
        self.assertTrue(self.env['account.bank.statement.line'].search_count([
            ('viva_transaction_id', '=', 'M1')]))

    def test_fetch_now_blocked_for_non_accounting_user(self):
        user = self.env['res.users'].create({
            'name': 'Plain', 'login': 'plain_viva',
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id])]})
        with self.assertRaises(AccessError):
            self.account.with_user(user).action_viva_fetch_now()

    def test_fetch_now_allowed_for_accounting_user(self):
        user = self.env['res.users'].create({
            'name': 'Accountant', 'login': 'accountant_viva',
            'group_ids': [(6, 0, [self.env.ref('account.group_account_user').id])]})
        txns = [{'accountTransactionId': 'M2', 'amount': -5.0,
                 'valueDate': '2026-03-01', 'counterPart': 'Y', 'currencyCode': 978}]
        client = MagicMock()
        client.search_transactions.return_value = txns
        with patch.object(type(self.account), '_viva_get_client', return_value=client):
            action = self.account.with_user(user).action_viva_fetch_now()
        self.assertEqual(action['res_model'], 'account.bank.statement.line')


class TestVivaSetupWizard(VivaCommon):
    def setUp(self):
        super().setUp()
        self.company.viva_client_id = 'cid'
        self.company.sudo().viva_client_secret = 'sec'

    def test_discover_creates_account_and_journal(self):
        wallets = [{'wallet_id': 'NEW1', 'iban': 'GR1601...', 'currency_code': 978,
                    'balance': 0.0, 'name': 'New Wallet'}]
        client = MagicMock()
        client.list_wallets.return_value = wallets
        wiz = self.env['viva.setup.wizard'].create({})
        with patch('odoo.addons.account_online_viva.wizard.viva_setup_wizard.VivaClient',
                   return_value=client):
            wiz.action_discover()
        acc = self.env['viva.account'].search([('wallet_id', '=', 'NEW1')])
        self.assertEqual(len(acc), 1)
        self.assertEqual(acc.journal_id.type, 'bank')
        self.assertEqual(acc.iban, 'GR1601...')

    def test_discover_requires_credentials(self):
        self.company.viva_client_id = False
        wiz = self.env['viva.setup.wizard'].create({})
        with self.assertRaises(OdooUserError):
            wiz.action_discover()
