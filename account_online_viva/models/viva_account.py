# Copyright 2026 Vlassis Emmanouil
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
from dateutil.relativedelta import relativedelta

from odoo import fields, models

from .viva_client import VivaClient


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
