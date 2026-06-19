# Copyright 2026 Vlassis Emmanouil
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
from dateutil.relativedelta import relativedelta

from odoo import _, fields, models

from .viva_client import VivaClient

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
        label = ' - '.join([p for p in (counterpart, reference) if p]) \
            or _('Viva %(t)s/%(s)s', t=type_id, s=subtype_id)
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
        return [m for m in mapped if m['viva_transaction_id'] not in existing]

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
