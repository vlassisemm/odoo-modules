# Copyright 2026 Vlassis Emmanouil
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
import logging

from dateutil.relativedelta import relativedelta
from psycopg2 import errors as pg_errors

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

from .viva_client import pick

_logger = logging.getLogger(__name__)
BATCH = 100
OVERLAP_DAYS = 7

# Viva account-transaction typeId: 20 = change wallet balance (a real ledger
# movement), 21 = change wallet overdraft, 32 = change wallet *available*
# balance (reserve/unreserve holds around card authorisations, sale
# settlements and obligations). Only balance movements are bank
# transactions; holds always net to zero and must not become statement lines.
BALANCE_TYPE_IDS = {20, 21}

# subTypeId -> human label (Viva "Account transaction created" webhook docs).
SUBTYPE_LABELS = {
    2: 'Chargeback fee', 3: 'Card payout fee', 4: 'IBAN payout fee',
    5: 'Sales commission', 6: 'Cash top-up fee', 7: 'DIAS top-up fee',
    8: 'Card top-up fee', 9: 'Voucher top-up fee', 10: 'Wallet charge fee',
    11: 'Cash commission', 12: 'DIAS commission', 13: 'Card commission',
    14: 'Wallet commission', 15: 'AliPay commission',
    16: 'On-demand clearance fee', 18: 'Withdrawal fee',
    19: 'Alternative payments commission',
    20: 'Cash top-up', 21: 'DIAS payment received', 22: 'Card top-up',
    23: 'Voucher top-up', 24: 'Smart Money top-up', 25: 'IBAN transfer received',
    30: 'Transfer to IBAN', 31: 'Transfer to card', 32: 'SEPA direct debit',
    80: 'Clearance (other)', 81: 'Clearance (cash)', 82: 'Clearance (DIAS)',
    83: 'Card payments clearance', 84: 'Wallet payments clearance',
    85: 'Reseller fee clearance', 86: 'AliPay clearance',
    87: 'Alternative payments clearance', 88: 'Payconiq clearance',
    100: 'Card purchase', 104: 'Card purchase with cashback',
    108: 'ATM withdrawal', 112: 'Cash disbursement', 116: 'Card refund',
    117: 'Card payment received', 130: 'Payconiq commission',
    140: 'Wallet transfer', 141: 'Wallet charge', 142: 'Wallet refund',
    146: 'Manual adjustment', 149: 'Funds confiscation',
    150: 'Smart Money voucher issued', 151: 'Smart Money service fee',
    152: 'Device subscription', 154: 'Package subscription',
    156: 'Pricing cashback', 159: 'Obligation captured',
    160: 'Issuing partner settlement', 164: 'Manual transfer to IBAN',
    165: 'Manual IBAN transfer fee', 166: 'Manual adjustment',
    167: 'Manual fee adjustment', 168: 'Manual deposit via bank transfer',
    169: 'Manual chargeback', 170: 'POS terminal subscription',
    171: '3G subscription', 176: 'Card order fee', 177: 'Express card order fee',
    178: 'Reseller fee', 179: 'Account maintenance fee', 180: 'Bill payment fee',
    181: 'Termination fee', 182: 'Minimum clearing fee',
    183: 'ISV acquiring commission', 184: 'PayPal processing fee',
    190: 'Prepayment loan disbursement', 191: 'Prepayment loan instalment',
    192: 'Cash advance disbursement', 193: 'Cash advance repayment',
    197: 'IRIS purchase', 200: 'Platform amount settlement',
    201: 'Platform fee settlement', 202: 'Platform amount clearance',
    203: 'Platform fee clearance',
}


class VivaLockedError(UserError):
    """Another transaction is currently syncing this Viva account."""


class VivaAccount(models.Model):
    _name = 'viva.account'
    _description = 'Viva Wallet Connection'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _check_company_auto = True

    name = fields.Char(required=True)
    company_id = fields.Many2one(
        'res.company', required=True, default=lambda self: self.env.company)
    journal_id = fields.Many2one(
        'account.journal', required=True, ondelete='cascade',
        check_company=True,
        domain="[('type', '=', 'bank'), ('company_id', '=', company_id)]")
    wallet_id = fields.Char(required=True, help='Viva WalletId polled for transactions')
    iban = fields.Char(related='journal_id.bank_account_id.acc_number')
    currency_id = fields.Many2one(related='journal_id.currency_id')
    sync_start_date = fields.Date(
        string='Earliest Import Date',
        default=lambda self: fields.Date.context_today(self) - relativedelta(days=90),
        help='Earliest date to import; transactions before this are ignored.')
    last_successful_to = fields.Date(
        string='Fetched Until', readonly=True,
        help='End of the last successful automatic fetch window. The next '
             'fetch restarts 7 days before this date.')
    last_error = fields.Text(string='Last Error', readonly=True)
    active = fields.Boolean(default=True)

    _journal_uniq = models.Constraint(
        'UNIQUE(journal_id)',
        'A Viva account already exists for this journal.',
    )
    _wallet_company_uniq = models.Constraint(
        'UNIQUE(company_id, wallet_id)',
        'A Viva account already exists for this wallet in this company.',
    )

    @api.model_create_multi
    def create(self, vals_list):
        accounts = super().create(vals_list)
        # Flag the journal as Viva-fed right away so the dashboard "Fetch
        # Viva" button appears without waiting for a first non-empty sync.
        for journal in accounts.journal_id:
            if journal.bank_statements_source != 'viva':
                journal.sudo().bank_statements_source = 'viva'
        return accounts

    def _viva_get_client(self):
        self.ensure_one()
        return self.company_id._viva_get_client()

    def _viva_currency_from_code(self, code):
        raw = str(code or '').strip()
        if not raw:
            return self.env['res.currency']
        Currency = self.env['res.currency'].with_context(active_test=False)
        if raw.isdigit():
            # ISO-4217 numeric code; base data ships iso_numeric for all
            # currencies, most of which are archived by default.
            return Currency.search([('iso_numeric', '=', int(raw))], limit=1)
        return Currency.search([('name', '=', raw.upper())], limit=1)

    @staticmethod
    def _viva_is_balance_movement(raw):
        """False for available-balance holds (typeId 32 & co.), which are
        not bank transactions. A missing typeId is treated as a movement."""
        type_id = pick(raw, 'typeId', 'TypeId')
        if type_id is None:
            return True
        try:
            return int(type_id) in BALANCE_TYPE_IDS
        except (TypeError, ValueError):
            return True

    @staticmethod
    def _viva_subtype_label(subtype_id):
        try:
            return SUBTYPE_LABELS.get(int(subtype_id))
        except (TypeError, ValueError):
            return None

    def _viva_map_transaction(self, raw):
        txn_id = pick(raw, 'accountTransactionId', 'AccountTransactionId')
        amount = pick(raw, 'amount', 'Amount', default=0.0) or 0.0
        date_str = (pick(raw, 'valueDate', 'ValueDate')
                    or pick(raw, 'created', 'Created') or '')
        counterpart = (pick(raw, 'counterPart', 'CounterPart') or '').strip()
        description = (pick(raw, 'userDescription', 'UserDescription') or '').strip()
        reference = (pick(raw, 'reference', 'Reference') or '').strip()
        type_id = pick(raw, 'typeId', 'TypeId')
        subtype_id = pick(raw, 'subTypeId', 'SubTypeId')
        # Label: what the bank would print. Merchant/counterparty text first,
        # then the transaction kind so fees and clearances read as such.
        kind = self._viva_subtype_label(subtype_id)
        text = ' - '.join(p for p in (counterpart, description, reference) if p)
        if text and kind:
            label = '%s (%s)' % (text, kind)
        elif text:
            label = text
        elif kind:
            label = kind
        elif type_id is not None or subtype_id is not None:
            label = _('Viva %(t)s/%(s)s', t=type_id, s=subtype_id)
        else:
            label = _('Viva transaction')
        currency = self.journal_id.currency_id or self.company_id.currency_id
        return {
            'viva_transaction_id': txn_id and str(txn_id),
            'amount': currency.round(float(amount)),
            'date': fields.Date.to_date(date_str[:10]) if date_str else False,
            'payment_ref': label,
            'partner_name': counterpart or False,
            'currency_code': pick(raw, 'currencyCode', 'CurrencyCode'),
            'transaction_details': raw,
        }

    def _viva_filter_new(self, mapped):
        self.ensure_one()
        ids = [m['viva_transaction_id'] for m in mapped if m.get('viva_transaction_id')]
        if not ids:
            return list(mapped)
        existing = set(self.env['account.bank.statement.line'].search_fetch([
            ('journal_id', '=', self.journal_id.id),
            ('viva_transaction_id', 'in', ids),
        ], ['viva_transaction_id']).mapped('viva_transaction_id'))
        return [m for m in mapped if m.get('viva_transaction_id') not in existing]

    def _viva_line_vals(self, mapped):
        self.ensure_one()
        return {
            'journal_id': self.journal_id.id,
            'date': mapped['date'],
            'amount': mapped['amount'],
            'payment_ref': mapped['payment_ref'],
            'partner_name': mapped['partner_name'],
            'viva_transaction_id': mapped['viva_transaction_id'],
            'transaction_details': mapped['transaction_details'],
        }

    def _viva_lock(self):
        self.ensure_one()
        # SKIP LOCKED (no NOWAIT/savepoint dance): a locked row means another
        # transaction is already syncing this account.
        if not self.try_lock_for_update():
            raise VivaLockedError(
                _('A sync for this Viva account is already running.'))

    def _viva_create_lines(self, mapped_new):
        self.ensure_one()
        BSL = self.env['account.bank.statement.line']
        create_model = BSL.sudo().with_company(self.journal_id.company_id)
        vals_list = [self._viva_line_vals(m) for m in mapped_new]
        created = BSL
        for i in range(0, len(vals_list), BATCH):
            chunk = vals_list[i:i + BATCH]
            try:
                with self.env.cr.savepoint():
                    created |= BSL.browse(create_model.create(chunk).ids)
            except pg_errors.UniqueViolation:
                # Concurrent insert created some ids; fall back per-line,
                # skipping only duplicates — any other error must propagate.
                for vals in chunk:
                    try:
                        with self.env.cr.savepoint():
                            created |= BSL.browse(create_model.create(vals).ids)
                    except pg_errors.UniqueViolation:
                        _logger.info(
                            'Viva %s: transaction %s imported concurrently; '
                            'skipped.', self.id, vals.get('viva_transaction_id'))
                        continue
        return created

    def _viva_window(self, date_from, date_to):
        self.ensure_one()
        date_to = date_to or fields.Date.context_today(self)
        if date_from is None:
            if self.last_successful_to:
                date_from = self.last_successful_to - relativedelta(days=OVERLAP_DAYS)
            else:
                date_from = self.sync_start_date or (date_to - relativedelta(days=90))
            # sync_start_date only bounds the automatic window; explicit dates
            # (the backfill wizard) are honored as given.
            if self.sync_start_date:
                date_from = max(date_from, self.sync_start_date)
        if date_from > date_to:
            raise UserError(_(
                'The Viva fetch window is empty: %(date_from)s is after %(date_to)s.',
                date_from=date_from, date_to=date_to))
        return date_from, date_to

    def _viva_sync_one(self, date_from=None, date_to=None, update_watermark=True,
                       client=None):
        self.ensure_one()
        self._viva_lock()
        company = self.company_id
        journal_currency = self.journal_id.currency_id or company.currency_id
        lock_date = company._get_user_fiscal_lock_date(self.journal_id)
        date_from, date_to = self._viva_window(date_from, date_to)

        client = client or self._viva_get_client()
        raws = client.search_transactions(self.wallet_id, date_from, date_to)

        kept = []
        skipped_currency = skipped_lock_date = skipped_hold = 0
        currency_cache = {}
        for raw in raws:
            if not self._viva_is_balance_movement(raw):
                skipped_hold += 1
                continue
            m = self._viva_map_transaction(raw)
            if not m['viva_transaction_id'] or not m['date']:
                continue
            code_key = str(m['currency_code'] or '')
            if code_key not in currency_cache:
                currency_cache[code_key] = self._viva_currency_from_code(
                    m['currency_code'])
            if currency_cache[code_key] != journal_currency:
                skipped_currency += 1
                _logger.info('Viva %s: skip txn %s (currency %s != %s)',
                             self.id, m['viva_transaction_id'], m['currency_code'],
                             journal_currency.name)
                continue
            if lock_date and m['date'] <= lock_date:
                skipped_lock_date += 1
                _logger.info('Viva %s: skip txn %s before lock date %s',
                             self.id, m['viva_transaction_id'], lock_date)
                continue
            kept.append(m)

        new = self._viva_filter_new(kept)
        created = self._viva_create_lines(new)

        # Every transaction skipped for currency and nothing kept means the
        # journal currency is wrong, not that the window is empty: surface it
        # as an error and hold the watermark so nothing is silently lost.
        currency_mismatch = bool(skipped_currency) and not kept
        state_vals = {'last_error': False}
        if currency_mismatch:
            wallet_codes = sorted({
                currency_cache[code].name if currency_cache.get(code) else code
                for code in currency_cache} - {''})
            state_vals['last_error'] = _(
                '%(n)s transaction(s) skipped: their currency (%(wallet)s) does '
                'not match the journal currency (%(journal)s). Fix the journal '
                'currency, then fetch again.',
                n=skipped_currency, wallet=', '.join(wallet_codes) or '?',
                journal=journal_currency.name)
        elif update_watermark:
            state_vals['last_successful_to'] = date_to
        self.sudo().write(state_vals)
        if created or skipped_currency or skipped_lock_date:
            self.sudo().message_post(
                body=_('Viva sync: imported %(n)s transaction(s) '
                       '(%(f)s → %(t)s; %(sc)s skipped for currency, '
                       '%(sl)s skipped for lock date, %(sh)s balance holds ignored).',
                       n=len(created), f=date_from, t=date_to,
                       sc=skipped_currency, sl=skipped_lock_date, sh=skipped_hold),
                subtype_xmlid='mail.mt_note')
        else:
            _logger.info('Viva %s: no transactions in window %s → %s '
                         '(%s balance holds ignored).',
                         self.id, date_from, date_to, skipped_hold)
        return created

    def action_viva_fetch_now(self):
        self.ensure_one()
        if not self.env.su and not self.env.user.has_group('account.group_account_basic'):
            raise AccessError(_('You are not allowed to fetch Viva transactions.'))
        self._viva_sync_one()
        return self._viva_reconcile_action()

    def action_viva_reset_sync(self):
        """Forget the incremental watermark so the next fetch restarts from
        the earliest import date (the cron then re-imports; dedup makes this
        safe)."""
        if not self.env.su and not self.env.user.has_group('account.group_account_manager'):
            raise AccessError(_('Only accounting managers can reset a Viva sync.'))
        self.sudo().write({'last_successful_to': False, 'last_error': False})
        for account in self.sudo():
            account.message_post(
                body=_('Viva sync reset: the next fetch restarts from %s.',
                       account.sync_start_date or _('the default window')),
                subtype_xmlid='mail.mt_note')

    def action_viva_open_journal(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'account.journal',
            'res_id': self.journal_id.id,
            'view_mode': 'form',
            'target': 'current',
        }

    def _viva_reconcile_action(self):
        self.ensure_one()
        journal = self.journal_id
        if hasattr(journal, 'action_open_reconcile'):
            # Enterprise (account_accountant): the bank reconciliation widget,
            # exactly where the native "Fetch Transactions" lands.
            return journal.action_open_reconcile()
        # Community: the journal's statement lines.
        return {
            'type': 'ir.actions.act_window',
            'name': _('Bank Transactions'),
            'res_model': 'account.bank.statement.line',
            'view_mode': 'list,form',
            'domain': [('journal_id', '=', journal.id)],
            'context': {'create': False, 'default_journal_id': journal.id},
        }

    @api.model
    def _cron_viva_fetch(self):
        accounts = self.search([])
        clients = {}  # one client (and OAuth token) per company
        remaining = len(accounts)
        for account in accounts:
            remaining -= 1
            company = account.company_id
            if company.id not in clients:
                sudo_company = company.sudo()
                if not (sudo_company.viva_client_id and sudo_company.viva_client_secret):
                    # Warn once per company; flag the accounts without
                    # spamming their chatter every run.
                    _logger.warning('Viva cron: company %s has no Viva credentials; '
                                    'skipping its accounts.', company.name)
                    clients[company.id] = None
                else:
                    clients[company.id] = company._viva_get_client()
            if clients[company.id] is None:
                account.sudo().last_error = _(
                    'Viva credentials are not configured for company %s.', company.name)
                self.env['ir.cron']._commit_progress(processed=1, remaining=remaining)
                continue
            try:
                with self.env.cr.savepoint():
                    account._viva_sync_one(client=clients[company.id])
            except VivaLockedError:
                # Do not write last_error: the write would block on the very
                # row lock, then overwrite the winning sync's status.
                _logger.info('Viva cron: account %s is being synced by another '
                             'transaction; skipping.', account.id)
                continue
            except Exception as exc:  # noqa: BLE001 - isolate per account
                _logger.exception('Viva cron failed for account %s', account.id)
                account.sudo().last_error = str(exc)
                account.sudo().message_post(
                    body=_('Viva sync failed: %s', exc), subtype_xmlid='mail.mt_note')
            self.env['ir.cron']._commit_progress(processed=1, remaining=remaining)
