# Copyright 2026 Vlassis Emmanouil
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
from datetime import date
from unittest.mock import MagicMock, patch

from psycopg2 import IntegrityError

from odoo.exceptions import AccessError, UserError as OdooUserError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase
from odoo.tools import mute_logger

from odoo.addons.account_online_viva.models.viva_client import VivaClient, VivaApiError

VIVA_PATH = 'odoo.addons.account_online_viva.models.viva_client.http_requests'


def _resp(json_data, status=200):
    r = MagicMock()
    r.status_code = status
    if status == 204:
        # 204 No Content has an empty body: .json() raises.
        r.json.side_effect = ValueError('No JSON object could be decoded')
    else:
        r.json.return_value = json_data
    r.raise_for_status.side_effect = None if status < 400 else Exception('http %s' % status)
    return r


@tagged('post_install', '-at_install', 'account_online_viva')
class VivaTransactionCase(TransactionCase):
    pass


class TestModuleInstall(VivaTransactionCase):
    def test_module_installed(self):
        module = self.env['ir.module.module'].search([('name', '=', 'account_online_viva')])
        self.assertEqual(module.state, 'installed')


class TestCompanyCredentials(VivaTransactionCase):
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


class TestVivaClient(VivaTransactionCase):
    @patch(VIVA_PATH)
    def test_token_uses_demo_host_and_caches(self, req):
        req.post.return_value = _resp({'access_token': 'tok', 'expires_in': 3600})
        client = VivaClient('cid', 'sec', 'demo')
        self.assertEqual(client._token(), 'tok')
        self.assertEqual(client._token(), 'tok')  # cached
        self.assertEqual(req.post.call_count, 1)
        url = req.post.call_args.args[0] if req.post.call_args.args else req.post.call_args.kwargs['url']
        self.assertIn('demo-accounts.vivapayments.com/connect/token', url)
        # Data Services scope, verified live against production (2026-07-07).
        data = req.post.call_args.kwargs['data']
        self.assertEqual(data['scope'], 'urn:viva:payments:biservices:datafileapi')

    @patch(VIVA_PATH)
    def test_token_refreshes_after_expiry(self, req):
        req.post.return_value = _resp({'access_token': 'tok', 'expires_in': 3600})
        client = VivaClient('cid', 'sec', 'demo')
        with patch('odoo.addons.account_online_viva.models.viva_client.time') as t:
            t.monotonic.side_effect = iter([0, 10 ** 6, 10 ** 6, 10 ** 6, 10 ** 6])
            client._token()
            client._token()  # cached token is long expired -> must re-request
        self.assertEqual(req.post.call_count, 2)

    @patch(VIVA_PATH)
    def test_list_wallets_requests_wallet_scope(self, req):
        req.post.return_value = _resp({'access_token': 'tok', 'expires_in': 3600})
        req.get.return_value = _resp([])
        client = VivaClient('cid', 'sec', 'demo')
        client.list_wallets()
        # The Wallet API is core_api-audience: it needs its own scope, not the
        # Data Services one.
        data = req.post.call_args.kwargs['data']
        self.assertEqual(data['scope'], 'urn:viva:payments:core:api:merchants:wallets')

    @patch(VIVA_PATH)
    def test_list_wallets_maps_fields(self, req):
        req.post.return_value = _resp({'access_token': 'tok', 'expires_in': 3600})
        req.get.return_value = _resp([
            {'walletId': 'W1', 'iban': 'GR16...', 'currencyCode': 978,
             'available': 12.5, 'friendlyName': 'Main'},
        ])
        client = VivaClient('cid', 'sec', 'demo')
        wallets = client.list_wallets()
        self.assertEqual(wallets[0]['wallet_id'], 'W1')
        self.assertEqual(wallets[0]['currency_code'], 978)
        self.assertEqual(wallets[0]['balance'], 12.5)
        self.assertEqual(wallets[0]['name'], 'Main')

    @patch(VIVA_PATH)
    def test_list_wallets_normalizes_numeric_wallet_id(self, req):
        req.post.return_value = _resp({'access_token': 'tok', 'expires_in': 3600})
        req.get.return_value = _resp([
            {'walletId': 123456789012, 'iban': 'GR16...', 'currencyCode': 978},
        ])
        client = VivaClient('cid', 'sec', 'demo')
        wallets = client.list_wallets()
        self.assertEqual(wallets[0]['wallet_id'], '123456789012')

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
            _resp({
                'currentPage': 1,
                'pageSize': 500,
                'totalPages': 1,
                'data': [{'accountTransactionId': 'T1', 'amount': -5.0}],
            }),
        ]
        client = VivaClient('cid', 'sec', 'production')
        txns = client.search_transactions('W1', date(2026, 1, 1), date(2026, 1, 31))
        self.assertEqual(txns[0]['accountTransactionId'], 'T1')
        search_url = req.post.call_args.args[0]
        # v2: the v1 path returns 403 even with a valid token (verified live).
        self.assertIn('/dataservices/v2/accounttransactions/Search', search_url)
        body = req.post.call_args.kwargs['json']
        params = req.post.call_args.kwargs['params']
        self.assertEqual(body['WalletId'], 'W1')
        self.assertEqual(body['DateFrom'], '2026-01-01')
        self.assertEqual(body['DateTo'], '2026-01-31')
        self.assertEqual(params['PageSize'], 500)
        self.assertEqual(params['Page'], 1)
        self.assertEqual(params['OrderBy'], 'Ascending')

    @patch(VIVA_PATH)
    def test_search_transactions_pages_until_204(self, req):
        # totalPages is DEPRECATED in the Viva spec: the client must keep
        # paginating (full pages) until HTTP 204 No Content, ignoring totalPages.
        req.post.side_effect = [
            _resp({'access_token': 'tok', 'expires_in': 3600}),
            _resp({'data': [{'accountTransactionId': 'T1', 'amount': -5.0}]}),
            _resp({'data': [{'accountTransactionId': 'T2', 'amount': 7.0}]}),
            _resp(None, status=204),
        ]
        client = VivaClient('cid', 'sec', 'production')
        txns = client.search_transactions(
            '123456789012', date(2026, 1, 1), date(2026, 1, 31), page_size=1)
        self.assertEqual([txn['accountTransactionId'] for txn in txns], ['T1', 'T2'])
        self.assertEqual(req.post.call_args_list[1].kwargs['json']['WalletId'], 123456789012)
        pages = [call.kwargs['params']['Page'] for call in req.post.call_args_list[1:]]
        self.assertEqual(pages, [1, 2, 3])

    @patch(VIVA_PATH)
    def test_search_transactions_empty_window_returns_no_rows(self, req):
        # A window with zero transactions answers 204 No Content (verified
        # live); that is an empty result, not an error.
        req.post.side_effect = [
            _resp({'access_token': 'tok', 'expires_in': 3600}),
            _resp(None, status=204),
        ]
        client = VivaClient('cid', 'sec', 'production')
        txns = client.search_transactions('W1', date(2026, 1, 1), date(2026, 1, 31))
        self.assertEqual(txns, [])

    @patch(VIVA_PATH)
    def test_search_transactions_raises_at_page_safety_cap(self, req):
        # Exceeding the safety cap must abort loudly instead of silently
        # importing a truncated window (the watermark would then advance past
        # transactions that were never fetched).
        def post_side_effect(*args, **kwargs):
            url = args[0] if args else kwargs.get('url', '')
            if url.endswith('/connect/token'):
                return _resp({'access_token': 'tok', 'expires_in': 3600})
            page = kwargs['params']['Page']
            return _resp({'data': [{'accountTransactionId': 'T%s' % page}]})
        req.post.side_effect = post_side_effect
        client = VivaClient('cid', 'sec', 'production')
        with patch(
                'odoo.addons.account_online_viva.models.viva_client.MAX_PAGES', 3):
            with self.assertRaises(VivaApiError):
                client.search_transactions(
                    '123', date(2026, 1, 1), date(2026, 1, 31), page_size=1)
        # One token request + exactly MAX_PAGES page requests (not endless).
        self.assertEqual(req.post.call_count, 1 + 3)


class VivaCommon(VivaTransactionCase):
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

    @mute_logger('odoo.sql_db')
    def test_journal_unique(self):
        with self.assertRaises(Exception):
            self.env['viva.account'].create({
                'name': 'dup', 'journal_id': self.journal.id, 'wallet_id': 'W2'})

    @mute_logger('odoo.sql_db')
    def test_wallet_unique_per_company(self):
        journal2 = self.env['account.journal'].create({
            'name': 'Viva Wallet Dup', 'type': 'bank', 'code': 'VWDUP'})
        with self.assertRaises(Exception):
            self.env['viva.account'].create({
                'name': 'dup wallet', 'journal_id': journal2.id, 'wallet_id': 'W1'})
            self.env.flush_all()

    def test_create_sets_journal_bank_statements_source(self):
        journal = self.env['account.journal'].create({
            'name': 'Viva Src', 'type': 'bank', 'code': 'VSRC1'})
        self.assertNotEqual(journal.bank_statements_source, 'viva')
        self.env['viva.account'].create({
            'name': 'src', 'journal_id': journal.id, 'wallet_id': 'WSRC'})
        self.assertEqual(journal.bank_statements_source, 'viva')

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

    def test_company_must_match_journal_company(self):
        other_company = self.env['res.company'].create({'name': 'Mismatch Co'})
        other_journal = self.env['account.journal'].create({
            'name': 'Mismatch Journal',
            'type': 'bank',
            'code': 'MMJ',
            'company_id': other_company.id,
        })
        with self.assertRaises(OdooUserError):
            self.env['viva.account'].create({
                'name': 'bad',
                'journal_id': other_journal.id,
                'wallet_id': 'BAD',
                'company_id': self.company.id,
            })


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

    def test_journal_fetch_action_delegates_to_viva_account(self):
        with patch.object(type(self.account), 'action_viva_fetch_now') as fetch_now:
            fetch_now.return_value = {'type': 'ir.actions.act_window'}
            action = self.journal.action_viva_fetch_now()
        self.assertEqual(action['type'], 'ir.actions.act_window')
        fetch_now.assert_called_once()


class TestVivaMapping(VivaCommon):
    def test_currency_numeric_and_alpha(self):
        eur = self.env.ref('base.EUR')
        self.assertEqual(self.account._viva_currency_from_code(978), eur)
        self.assertEqual(self.account._viva_currency_from_code('978'), eur)
        self.assertEqual(self.account._viva_currency_from_code('EUR'), eur)
        self.assertFalse(self.account._viva_currency_from_code('ZZZ'))

    def test_currency_numeric_resolves_any_iso_code(self):
        # Any ISO-4217 numeric code must resolve via base data (res.currency
        # iso_numeric), not a hand-maintained subset.
        try_currency = self.env['res.currency'].with_context(
            active_test=False).search([('name', '=', 'TRY')], limit=1)
        self.assertTrue(try_currency)
        self.assertEqual(self.account._viva_currency_from_code(949), try_currency)
        self.assertEqual(self.account._viva_currency_from_code('949'), try_currency)

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

    def _note_count(self):
        return self.env['mail.message'].search_count([
            ('model', '=', 'viva.account'), ('res_id', '=', self.account.id)])

    def test_no_chatter_note_when_nothing_imported(self):
        before = self._note_count()
        with self._patch_client([]):
            self.account._viva_sync_one(
                date_from=date(2026, 1, 1), date_to=date(2026, 3, 31))
        self.assertEqual(self._note_count(), before)

    def test_chatter_note_reports_skipped_transactions(self):
        txns = [{'accountTransactionId': 'A3', 'amount': -10.0,
                 'valueDate': '2026-03-01', 'counterPart': 'X', 'currencyCode': 840}]
        self.journal.currency_id = self.env.ref('base.EUR')
        before = self._note_count()
        with self._patch_client(txns):
            self.account._viva_sync_one(
                date_from=date(2026, 1, 1), date_to=date(2026, 3, 31))
        self.assertEqual(self._note_count(), before + 1)

    @mute_logger('odoo.addons.account_online_viva.models.viva_account')
    def test_non_duplicate_db_error_propagates(self):
        from psycopg2 import errors as pg_errors
        txns = [{'accountTransactionId': 'B1', 'amount': -1.0,
                 'valueDate': '2026-03-01', 'counterPart': 'X', 'currencyCode': 978}]
        BSL = self.env.registry['account.bank.statement.line']
        with self._patch_client(txns), \
                patch.object(BSL, 'create',
                             side_effect=pg_errors.NotNullViolation('boom')), \
                self.assertRaises(pg_errors.NotNullViolation):
            self.account._viva_sync_one(
                date_from=date(2026, 1, 1), date_to=date(2026, 3, 31))


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
        # Set last_successful_to to a known point; sync_start_date is much earlier
        known_dt = date(2026, 4, 10)
        self.account.last_successful_to = known_dt

        client = self._make_client()
        with patch.object(type(self.account), '_viva_get_client', return_value=client):
            self.account._viva_sync_one()

        call_kwargs = client.search_transactions.call_args
        date_from_used = call_kwargs[0][1] if call_kwargs[0] else call_kwargs[1]['date_from']
        expected = known_dt - timedelta(days=7)
        self.assertEqual(date_from_used, expected)

    def test_incremental_clamped_to_sync_start_date(self):
        from datetime import timedelta
        # last_successful_to - 7d would be before sync_start_date; must clamp.
        # Set last_successful_to such that last_successful_to.date() - 7d < sync_start_date
        self.account.sync_start_date = date(2026, 3, 1)
        # last_successful_to = 2026-03-05, so overlap window is before sync_start_date.
        self.account.last_successful_to = date(2026, 3, 5)

        client = self._make_client()
        with patch.object(type(self.account), '_viva_get_client', return_value=client):
            self.account._viva_sync_one()

        call_kwargs = client.search_transactions.call_args
        date_from_used = call_kwargs[0][1] if call_kwargs[0] else call_kwargs[1]['date_from']
        self.assertEqual(date_from_used, date(2026, 3, 1))

    def test_explicit_range_before_sync_start_is_honored(self):
        # The date-range wizard exists to backfill history: explicit dates
        # must not be clamped to sync_start_date (which would invert the
        # window and silently fetch nothing).
        self.account.sync_start_date = date(2026, 3, 1)
        client = self._make_client()
        with patch.object(type(self.account), '_viva_get_client', return_value=client):
            self.account._viva_sync_one(
                date_from=date(2026, 1, 1), date_to=date(2026, 1, 31),
                update_watermark=False)
        args = client.search_transactions.call_args[0]
        self.assertEqual(args[1], date(2026, 1, 1))
        self.assertEqual(args[2], date(2026, 1, 31))

    def test_inverted_window_raises(self):
        client = self._make_client()
        with patch.object(type(self.account), '_viva_get_client', return_value=client):
            with self.assertRaises(OdooUserError):
                self.account._viva_sync_one(
                    date_from=date(2026, 5, 1), date_to=date(2026, 4, 1))
        client.search_transactions.assert_not_called()


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

    @mute_logger('odoo.addons.account_online_viva.models.viva_account')
    def test_one_account_failure_isolated(self):
        ok_txn = [{'accountTransactionId': 'OK1', 'amount': -1.0,
                   'valueDate': '2026-03-01', 'counterPart': 'X', 'currencyCode': 978}]

        # The cron shares one client per company: fail per wallet instead.
        client = MagicMock()

        def search(wallet_id, date_from, date_to, **kwargs):
            if wallet_id == 'W2':
                raise ValueError('boom')
            return ok_txn

        client.search_transactions.side_effect = search
        with patch.object(type(self.company), '_viva_get_client',
                          return_value=client), \
                patch.object(self.env.cr, 'commit', lambda: None):
            self.env['viva.account']._cron_viva_fetch()

        self.assertTrue(self.env['account.bank.statement.line'].search_count([
            ('journal_id', '=', self.journal.id), ('viva_transaction_id', '=', 'OK1')]))
        self.assertTrue(self.account2.last_error)
        self.account.invalidate_recordset()
        self.assertFalse(self.account.last_error)
        self.assertTrue(self.account.last_successful_to)

    def test_cron_lock_contention_skips_without_error(self):
        # A concurrently-locked account is skipped quietly: writing last_error
        # would both block on the very row lock and overwrite the winning
        # sync's status with a stale 'already running' message.
        from odoo.addons.account_online_viva.models import viva_account as va_module
        locked_exc = getattr(va_module, 'VivaLockedError', None)
        self.assertIsNotNone(
            locked_exc, 'VivaLockedError must exist for lock-contention handling')
        with patch.object(type(self.account), '_viva_lock',
                          side_effect=locked_exc('busy')), \
                patch.object(self.env.cr, 'commit', lambda: None):
            self.env['viva.account']._cron_viva_fetch()
        self.account.invalidate_recordset()
        self.account2.invalidate_recordset()
        self.assertFalse(self.account.last_error)
        self.assertFalse(self.account2.last_error)


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

    def test_fetch_wizard_requires_accounting_group(self):
        user = self.env['res.users'].create({
            'name': 'PlainW', 'login': 'plainw_viva',
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id])]})
        # Grant the plain user wizard ACL so the in-method group check (not
        # the ACL) is what this test exercises.
        self.env['ir.model.access'].create({
            'name': 'viva.fetch.wizard test full access',
            'model_id': self.env['ir.model']._get('viva.fetch.wizard').id,
            'group_id': self.env.ref('base.group_user').id,
            'perm_read': True, 'perm_write': True,
            'perm_create': True, 'perm_unlink': True,
        })
        wizard = self.env['viva.fetch.wizard'].with_user(user).create({
            'viva_account_id': self.account.id,
            'date_from': date(2026, 2, 1), 'date_to': date(2026, 2, 28)})
        with patch.object(type(self.account), '_viva_sync_one') as sync, \
                patch.object(type(self.account), '_viva_reconcile_action',
                             return_value={'type': 'ir.actions.act_window'}):
            with self.assertRaises(AccessError):
                wizard.action_fetch()
        sync.assert_not_called()

    def test_date_range_wizard_does_not_advance_incremental_watermark(self):

        old_watermark = date(2026, 6, 1)
        self.account.last_successful_to = old_watermark
        txns = [{'accountTransactionId': 'HIST1', 'amount': -5.0,
                 'valueDate': '2026-02-01', 'counterPart': 'Y', 'currencyCode': 978}]
        client = MagicMock()
        client.search_transactions.return_value = txns
        wizard = self.env['viva.fetch.wizard'].create({
            'viva_account_id': self.account.id,
            'date_from': date(2026, 2, 1),
            'date_to': date(2026, 2, 28),
        })
        with patch.object(type(self.account), '_viva_get_client', return_value=client):
            wizard.action_fetch()
        self.account.invalidate_recordset(['last_successful_to'])
        self.assertEqual(self.account.last_successful_to, old_watermark)


class TestVivaSetupWizard(VivaCommon):
    def setUp(self):
        super().setUp()
        self.company.viva_client_id = 'cid'
        self.company.sudo().viva_client_secret = 'sec'

    def _discover(self, wallets):
        client = MagicMock()
        client.list_wallets.return_value = wallets
        wiz = self.env['viva.setup.wizard'].create({})
        with patch.object(type(self.company), '_viva_get_client',
                          return_value=client):
            wiz.action_discover()

    def test_discover_creates_account_and_journal(self):
        self._discover([{'wallet_id': 'NEW1', 'iban': 'GR1601...', 'currency_code': 978,
                         'balance': 0.0, 'name': 'New Wallet'}])
        acc = self.env['viva.account'].search([('wallet_id', '=', 'NEW1')])
        self.assertEqual(len(acc), 1)
        self.assertEqual(acc.journal_id.type, 'bank')
        self.assertEqual(acc.iban, 'GR1601...')

    def test_discover_skips_archived_wallet_accounts(self):
        self.account.active = False
        self._discover([{'wallet_id': 'W1', 'iban': False, 'currency_code': 978,
                         'balance': 0.0, 'name': 'Main'}])
        accounts = self.env['viva.account'].with_context(active_test=False).search(
            [('wallet_id', '=', 'W1')])
        self.assertEqual(len(accounts), 1, 'archived account must not be re-created')

    def test_discover_reuses_existing_partner_bank(self):
        self.env['res.partner.bank'].create({
            'acc_number': 'GR9901', 'partner_id': self.company.partner_id.id,
            'company_id': self.company.id})
        self._discover([{'wallet_id': 'NEWB', 'iban': 'GR9901', 'currency_code': 978,
                         'balance': 0.0, 'name': 'B'}])
        acc = self.env['viva.account'].search([('wallet_id', '=', 'NEWB')])
        self.assertEqual(len(acc), 1)
        self.assertEqual(acc.journal_id.bank_account_id.sanitized_acc_number, 'GR9901')

    def test_journal_code_avoids_archived_journal_code(self):
        archived = self.env['account.journal'].create({
            'name': 'Old Viva', 'type': 'bank', 'code': 'VABCD'})
        archived.active = False
        wiz = self.env['viva.setup.wizard'].create({})
        journal = wiz._create_bank_journal(
            {'wallet_id': 'XABCD', 'name': 'A'}, self.env['res.currency'])
        self.assertNotEqual(journal.code, 'VABCD')

    def test_discover_requires_credentials(self):
        self.company.viva_client_id = False
        wiz = self.env['viva.setup.wizard'].create({})
        with self.assertRaises(OdooUserError):
            wiz.action_discover()

    def test_setup_wizard_uses_company_client_helper(self):
        # Wallet discovery must build the client through the shared company
        # helper (which applies the configured timeout), not construct
        # VivaClient inline.
        self.env['ir.config_parameter'].sudo().set_param(
            'account_online_viva.timeout', '123')
        captured = []

        def fake_client(company):
            captured.append(company)
            client = MagicMock()
            client.list_wallets.return_value = []
            return client

        wiz = self.env['viva.setup.wizard'].create({})
        with patch.object(type(self.company), '_viva_get_client', fake_client):
            wiz.action_discover()
        self.assertEqual(len(captured), 1)

    def test_unique_journal_code_no_collision_for_shared_last4(self):
        """Two wallet ids sharing last 4 chars must produce distinct journal codes."""
        # wallet ids that produce the same base_code 'VABCD' via [-4:] slicing
        wallet_a = {'wallet_id': 'XABCD', 'name': 'Wallet A'}
        wallet_b = {'wallet_id': 'YABCD', 'name': 'Wallet B'}
        wiz = self.env['viva.setup.wizard'].create({})
        no_currency = self.env['account.journal'].browse()
        journal_a = wiz._create_bank_journal(wallet_a, no_currency)
        journal_b = wiz._create_bank_journal(wallet_b, no_currency)
        self.assertNotEqual(
            journal_a.code, journal_b.code,
            'Journals created from wallets sharing last 4 chars must have distinct codes'
        )


class TestCompanyClient(VivaTransactionCase):
    def setUp(self):
        super().setUp()
        self.env.company.viva_client_id = 'cid'
        self.env.company.sudo().viva_client_secret = 'sec'

    def test_client_uses_configured_timeout(self):
        self.env['ir.config_parameter'].sudo().set_param(
            'account_online_viva.timeout', '123')
        client = self.env.company._viva_get_client()
        self.assertEqual(client.timeout, 123)

    @mute_logger('odoo.addons.account_online_viva.models.res_company')
    def test_client_invalid_timeout_falls_back_to_default(self):
        self.env['ir.config_parameter'].sudo().set_param(
            'account_online_viva.timeout', '60s')
        client = self.env.company._viva_get_client()
        self.assertEqual(client.timeout, 60)


class TestVivaJournalIntegration(VivaCommon):
    """Native-look redesign (1.1.0): journal-level fields and actions."""

    def test_last_successful_to_is_a_date(self):
        field = self.env['viva.account']._fields['last_successful_to']
        self.assertEqual(field.type, 'date')

    def test_journal_related_fields_reflect_account(self):
        self.account.write({'last_successful_to': date(2026, 4, 1),
                            'last_error': 'boom'})
        self.assertEqual(self.journal.viva_wallet_id, 'W1')
        self.assertEqual(self.journal.viva_last_successful_to, date(2026, 4, 1))
        self.assertEqual(self.journal.viva_last_error, 'boom')

    def test_journal_wallet_write_updates_existing_account(self):
        self.journal.viva_wallet_id = 'W1-new'
        self.assertEqual(self.account.wallet_id, 'W1-new')

    def test_journal_wallet_write_creates_account_when_missing(self):
        journal = self.env['account.journal'].create({
            'name': 'Viva Bank 3', 'type': 'bank', 'code': 'VIVA3',
            'bank_statements_source': 'viva'})
        self.assertFalse(journal.viva_account_id)
        journal.viva_wallet_id = 'W3'
        self.assertTrue(journal.viva_account_id)
        self.assertEqual(journal.viva_account_id.wallet_id, 'W3')
        self.assertEqual(journal.viva_account_id.name, 'Viva Bank 3')
        self.assertEqual(journal.bank_statements_source, 'viva')

    def test_journal_wallet_write_empty_without_account_is_noop(self):
        journal = self.env['account.journal'].create({
            'name': 'Viva Bank 4', 'type': 'bank', 'code': 'VIVA4'})
        journal.viva_wallet_id = False
        self.assertFalse(journal.viva_account_id)

    def test_reset_sync_clears_watermark_and_error(self):
        self.account.write({'last_successful_to': date(2026, 4, 1),
                            'last_error': 'boom'})
        self.account.action_viva_reset_sync()
        self.assertFalse(self.account.last_successful_to)
        self.assertFalse(self.account.last_error)

    def test_journal_fetch_range_action_targets_wizard(self):
        action = self.journal.action_viva_fetch_range()
        self.assertEqual(action['res_model'], 'viva.fetch.wizard')
        self.assertEqual(action['target'], 'new')
        self.assertEqual(action['context']['default_viva_account_id'], self.account.id)

    def test_fetch_result_routes_to_native_journal_action(self):
        action = self.account._viva_reconcile_action()
        journal = self.journal
        if hasattr(journal, 'action_open_reconcile'):
            self.assertEqual(action, journal.action_open_reconcile())
        else:
            self.assertEqual(action['res_model'], 'account.bank.statement.line')
            self.assertIn(('journal_id', '=', journal.id), action['domain'])

    def test_setup_wizard_group_check_is_access_error(self):
        user = self.env['res.users'].create({
            'name': 'Plain', 'login': 'viva_plain_setup',
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id])]})
        wizard = self.env['viva.setup.wizard'].sudo().create({
            'company_id': self.company.id})
        with self.assertRaises(AccessError):
            wizard.with_user(user).action_discover()


class TestVivaCronCredentials(VivaCommon):
    @mute_logger('odoo.addons.account_online_viva.models.viva_account')
    def test_cron_skips_company_without_credentials(self):
        self.company.viva_client_id = False
        self.company.sudo().viva_client_secret = False
        with patch.object(VivaClient, 'search_transactions') as search, \
                patch.object(self.env.cr, 'commit', lambda: None):
            self.env['viva.account']._cron_viva_fetch()
        search.assert_not_called()
        self.account.invalidate_recordset()
        self.assertTrue(self.account.last_error)
        self.assertIn('credentials', self.account.last_error.lower())


class TestVivaTransactionSemantics(VivaCommon):
    """Live-data findings (2026-09-04): balance holds and readable labels."""

    def setUp(self):
        super().setUp()
        self.company.viva_client_id = 'cid'
        self.company.sudo().viva_client_secret = 'sec'
        self.journal.currency_id = self.env.ref('base.EUR')
        self.account.sync_start_date = date(2026, 1, 1)

    def _patch_client(self, txns):
        client = MagicMock()
        client.search_transactions.return_value = txns
        return patch.object(type(self.account), '_viva_get_client', return_value=client)

    def test_available_balance_holds_are_not_imported(self):
        txns = [
            {'accountTransactionId': 'P1', 'typeId': 20, 'subTypeId': 100,
             'amount': -90.0, 'created': '2026-03-01T10:00:00+03:00',
             'counterPart': 'ANTHROPIC* CLAUDE SUB', 'currencyCode': 978},
            {'accountTransactionId': 'R1', 'typeId': 32, 'subTypeId': 101,
             'amount': -90.0, 'created': '2026-03-01T10:00:00+03:00',
             'counterPart': 'ANTHROPIC* CLAUDE SUB', 'currencyCode': 978},
            {'accountTransactionId': 'U1', 'typeId': 32, 'subTypeId': 103,
             'amount': 90.0, 'created': '2026-03-01T10:00:00+03:00',
             'counterPart': 'ANTHROPIC* CLAUDE SUB', 'currencyCode': 978},
        ]
        with self._patch_client(txns):
            lines = self.account._viva_sync_one(
                date_from=date(2026, 1, 1), date_to=date(2026, 3, 31))
        self.assertEqual(lines.mapped('viva_transaction_id'), ['P1'])
        note = self.env['mail.message'].search([
            ('model', '=', 'viva.account'), ('res_id', '=', self.account.id)],
            order='id desc', limit=1)
        self.assertIn('2 balance holds ignored', note.body)

    def test_missing_type_id_is_imported(self):
        txns = [{'accountTransactionId': 'N1', 'amount': 1.0,
                 'valueDate': '2026-03-01', 'currencyCode': 978}]
        with self._patch_client(txns):
            lines = self.account._viva_sync_one(
                date_from=date(2026, 1, 1), date_to=date(2026, 3, 31))
        self.assertEqual(len(lines), 1)

    def test_labels_use_subtype_names(self):
        fee = self.account._viva_map_transaction(
            {'accountTransactionId': 'F', 'typeId': 20, 'subTypeId': 13,
             'amount': -2.5, 'created': '2026-03-01T10:00:00+03:00'})
        self.assertEqual(fee['payment_ref'], 'Card commission')
        clearance = self.account._viva_map_transaction(
            {'accountTransactionId': 'C', 'typeId': 20, 'subTypeId': 83,
             'amount': 100.0, 'created': '2026-03-01T10:00:00+03:00'})
        self.assertEqual(clearance['payment_ref'], 'Card payments clearance')
        purchase = self.account._viva_map_transaction(
            {'accountTransactionId': 'P', 'typeId': 20, 'subTypeId': 100,
             'amount': -90.0, 'created': '2026-03-01T10:00:00+03:00',
             'counterPart': 'ANTHROPIC* CLAUDE SUB'})
        self.assertEqual(purchase['payment_ref'], 'ANTHROPIC* CLAUDE SUB (Card purchase)')
        self.assertEqual(purchase['partner_name'], 'ANTHROPIC* CLAUDE SUB')
        unknown = self.account._viva_map_transaction(
            {'accountTransactionId': 'X', 'typeId': 20, 'subTypeId': 999,
             'amount': 1.0, 'created': '2026-03-01T10:00:00+03:00'})
        self.assertEqual(unknown['payment_ref'], 'Viva 20/999')

    def test_all_skipped_for_currency_holds_watermark_and_flags_error(self):
        self.journal.currency_id = self.env.ref('base.USD')
        txns = [{'accountTransactionId': 'E%s' % i, 'typeId': 20, 'subTypeId': 83,
                 'amount': 10.0, 'valueDate': '2026-03-01', 'currencyCode': 978}
                for i in range(3)]
        with self._patch_client(txns):
            lines = self.account._viva_sync_one(
                date_from=date(2026, 1, 1), date_to=date(2026, 3, 31))
        self.assertFalse(lines)
        self.assertFalse(self.account.last_successful_to)
        self.assertIn('3 transaction(s) skipped', self.account.last_error)
        self.assertIn('EUR', self.account.last_error)
        self.assertIn('USD', self.account.last_error)

    def test_partial_currency_skip_still_advances_watermark(self):
        txns = [
            {'accountTransactionId': 'OK', 'typeId': 20, 'subTypeId': 83,
             'amount': 10.0, 'valueDate': '2026-03-01', 'currencyCode': 978},
            {'accountTransactionId': 'USD', 'typeId': 20, 'subTypeId': 83,
             'amount': 10.0, 'valueDate': '2026-03-01', 'currencyCode': 840},
        ]
        with self._patch_client(txns):
            lines = self.account._viva_sync_one(
                date_from=date(2026, 1, 1), date_to=date(2026, 3, 31))
        self.assertEqual(len(lines), 1)
        self.assertEqual(self.account.last_successful_to, date(2026, 3, 31))
        self.assertFalse(self.account.last_error)
