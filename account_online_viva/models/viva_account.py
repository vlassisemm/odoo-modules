# Copyright 2026 Vlassis Emmanouil
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
import logging

from dateutil.relativedelta import relativedelta
from psycopg2 import IntegrityError, OperationalError

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

from .viva_client import VivaClient

_logger = logging.getLogger(__name__)
BATCH = 100
OVERLAP_DAYS = 7

# ISO-4217 numeric -> alpha for currencies Viva commonly uses (verify/extend).
ISO_NUMERIC_TO_ALPHA = {
    '978': 'EUR', '826': 'GBP', '840': 'USD', '203': 'CZK', '208': 'DKK',
    '348': 'HUF', '946': 'RON', '975': 'BGN', '985': 'PLN', '752': 'SEK',
    '578': 'NOK', '756': 'CHF',
}


class VivaAccount(models.Model):
    _name = 'viva.account'
    _description = 'Viva Wallet Connection'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    name = fields.Char(required=True)
    company_id = fields.Many2one(
        'res.company', required=True, default=lambda self: self.env.company)
    journal_id = fields.Many2one(
        'account.journal', required=True, ondelete='cascade',
        domain="[('type', '=', 'bank'), ('company_id', '=', company_id)]")
    wallet_id = fields.Char(required=True, help='Viva WalletId polled for transactions')
    iban = fields.Char()
    currency_id = fields.Many2one('res.currency')
    sync_start_date = fields.Date(
        default=lambda self: fields.Date.context_today(self) - relativedelta(days=90),
        help='Earliest date to import; transactions before this are ignored.')
    last_successful_to = fields.Datetime(readonly=True)
    last_error = fields.Text(readonly=True)
    active = fields.Boolean(default=True)

    _journal_uniq = models.Constraint(
        'UNIQUE(journal_id)',
        'A Viva account already exists for this journal.',
    )

    def _viva_get_client(self):
        self.ensure_one()
        company = self.company_id.sudo()
        timeout = int(self.env['ir.config_parameter'].sudo().get_param(
            'account_online_viva.timeout', 60))
        return VivaClient(
            company.viva_client_id, company.viva_client_secret,
            company.viva_environment, timeout=timeout)

    def _viva_currency_from_code(self, code):
        raw = str(code or '').strip()
        if raw.isdigit():
            alpha = ISO_NUMERIC_TO_ALPHA.get(raw)
        else:
            alpha = raw.upper() or None
        if not alpha:
            return self.env['res.currency']
        return self.env['res.currency'].with_context(active_test=False).search(
            [('name', '=', alpha)], limit=1)

    def _viva_map_transaction(self, raw):
        txn_id = raw.get('accountTransactionId') or raw.get('AccountTransactionId')
        amount = raw.get('amount', raw.get('Amount')) or 0.0
        date_str = (raw.get('valueDate') or raw.get('ValueDate')
                    or raw.get('created') or raw.get('Created') or '')
        counterpart = raw.get('counterPart') or raw.get('CounterPart') or ''
        reference = raw.get('reference') or raw.get('Reference') or ''
        type_id = raw.get('typeId', raw.get('TypeId'))
        subtype_id = raw.get('subTypeId', raw.get('SubTypeId'))
        label = ' - '.join([p for p in (counterpart, reference) if p])
        if not label:
            if type_id is not None or subtype_id is not None:
                label = _('Viva %(t)s/%(s)s', t=type_id, s=subtype_id)
            else:
                label = _('Viva transaction')
        return {
            'viva_transaction_id': txn_id and str(txn_id),
            # NOTE (verify): amount is signed decimal; confirm not minor-units.
            'amount': round(float(amount), 2),
            'date': fields.Date.to_date(date_str[:10]) if date_str else False,
            'payment_ref': label,
            'partner_name': counterpart or False,
            'currency_code': raw.get('currencyCode', raw.get('CurrencyCode')),
            'transaction_details': raw,
        }

    def _viva_filter_new(self, mapped):
        self.ensure_one()
        ids = [m['viva_transaction_id'] for m in mapped if m.get('viva_transaction_id')]
        if not ids:
            return list(mapped)
        existing = set(self.env['account.bank.statement.line'].search([
            ('journal_id', '=', self.journal_id.id),
            ('viva_transaction_id', 'in', ids),
        ]).mapped('viva_transaction_id'))
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
        try:
            self.env.cr.execute(
                'SELECT id FROM viva_account WHERE id = %s FOR UPDATE NOWAIT', [self.id])
        except OperationalError as exc:
            raise UserError(_('A sync for this Viva account is already running.')) from exc

    def _viva_create_lines(self, mapped_new):
        self.ensure_one()
        BSL = self.env['account.bank.statement.line']
        vals_list = [self._viva_line_vals(m) for m in mapped_new]
        created = BSL
        for i in range(0, len(vals_list), BATCH):
            chunk = vals_list[i:i + BATCH]
            try:
                with self.env.cr.savepoint():
                    created |= BSL.create(chunk)
            except IntegrityError:
                # Concurrent insert created some ids; fall back per-line, skipping dups.
                for vals in chunk:
                    try:
                        with self.env.cr.savepoint():
                            created |= BSL.create(vals)
                    except IntegrityError:
                        continue
        return created

    def _viva_window(self, date_from, date_to):
        self.ensure_one()
        date_to = date_to or fields.Date.context_today(self)
        if date_from is None:
            if self.last_successful_to:
                date_from = fields.Datetime.to_datetime(self.last_successful_to).date() \
                    - relativedelta(days=OVERLAP_DAYS)
            else:
                date_from = self.sync_start_date or (date_to - relativedelta(days=90))
        if self.sync_start_date:
            date_from = max(date_from, self.sync_start_date)
        return date_from, date_to

    def _viva_sync_one(self, date_from=None, date_to=None):
        self.ensure_one()
        self._viva_lock()
        company = self.company_id
        journal_currency = self.journal_id.currency_id or company.currency_id
        lock_date = max(
            [d for d in (company.user_fiscalyear_lock_date, company.user_hard_lock_date) if d],
            default=None)
        date_from, date_to = self._viva_window(date_from, date_to)

        client = self._viva_get_client()
        raws = client.search_transactions(self.wallet_id, date_from, date_to)

        kept = []
        for raw in raws:
            m = self._viva_map_transaction(raw)
            if not m['viva_transaction_id'] or not m['date']:
                continue
            currency = self._viva_currency_from_code(m['currency_code'])
            if currency != journal_currency:
                _logger.info('Viva %s: skip txn %s (currency %s != %s)',
                             self.id, m['viva_transaction_id'], m['currency_code'],
                             journal_currency.name)
                continue
            if lock_date and m['date'] <= lock_date:
                _logger.info('Viva %s: skip txn %s before lock date %s',
                             self.id, m['viva_transaction_id'], lock_date)
                continue
            kept.append(m)

        new = self._viva_filter_new(kept)
        created = self._viva_create_lines(new)

        if created and self.journal_id.bank_statements_source != 'viva':
            self.journal_id.sudo().bank_statements_source = 'viva'
        self.sudo().write({'last_successful_to': fields.Datetime.now(), 'last_error': False})
        self.sudo().message_post(
            body=_('Viva sync: imported %(n)s transaction(s) (%(f)s → %(t)s).',
                   n=len(created), f=date_from, t=date_to),
            subtype_xmlid='mail.mt_note')
        return created

    def action_viva_fetch_now(self):
        self.ensure_one()
        if not self.env.su and not self.env.user.has_group('account.group_account_user'):
            raise AccessError(_('You are not allowed to fetch Viva transactions.'))
        self._viva_sync_one()
        return self._viva_reconcile_action()

    def _viva_reconcile_action(self):
        self.ensure_one()
        # Edition-agnostic: open the journal's statement lines. On Enterprise you may
        # route to the bank reconciliation widget instead.
        return {
            'type': 'ir.actions.act_window',
            'name': _('Viva Transactions'),
            'res_model': 'account.bank.statement.line',
            'view_mode': 'list,form',
            'domain': [('journal_id', '=', self.journal_id.id)],
            'context': {'create': False},
        }

    @api.model
    def _cron_viva_fetch(self):
        accounts = self.search([('journal_id', '!=', False)])
        for account in accounts:
            try:
                with self.env.cr.savepoint():
                    account._viva_sync_one()
                self.env.cr.commit()
            except Exception as exc:  # noqa: BLE001 - isolate per account
                _logger.exception('Viva cron failed for account %s', account.id)
                account.sudo().last_error = str(exc)
                account.sudo().message_post(
                    body=_('Viva sync failed: %s', exc), subtype_xmlid='mail.mt_note')
                self.env.cr.commit()
