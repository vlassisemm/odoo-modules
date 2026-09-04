# Copyright 2026 Vlassis Emmanouil
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
from odoo import _, api, fields, models
from odoo.exceptions import UserError


class AccountJournal(models.Model):
    _inherit = 'account.journal'

    viva_account_id = fields.Many2one(
        'viva.account', compute='_compute_viva_account_id',
        search='_search_viva_account_id', string='Viva Account', compute_sudo=True)
    # Surfaced on the journal form / dashboard so a Viva-fed journal is
    # configured and monitored where Odoo's own bank feeds are: on the journal.
    viva_wallet_id = fields.Char(
        string='Viva Wallet', compute='_compute_viva_wallet_id',
        inverse='_inverse_viva_wallet_id', compute_sudo=True,
        help='Viva WalletId polled for transactions. Setting it on a journal '
             'without a Viva account creates one.')
    viva_sync_start_date = fields.Date(
        related='viva_account_id.sync_start_date', readonly=False)
    viva_last_successful_to = fields.Date(
        related='viva_account_id.last_successful_to')
    viva_last_error = fields.Text(related='viva_account_id.last_error')

    def _compute_viva_account_id(self):
        accounts = self.env['viva.account'].search([('journal_id', 'in', self.ids)])
        by_journal = {a.journal_id.id: a for a in accounts}
        for journal in self:
            journal.viva_account_id = by_journal.get(journal.id, False)

    def _search_viva_account_id(self, operator, value):
        VivaAccount = self.env['viva.account'].sudo()
        if operator in ('=', '!=', 'in', 'not in') and not value or (
                isinstance(value, (list, tuple)) and set(value) <= {False}):
            # "has (no) Viva account"
            linked = VivaAccount.search([]).journal_id.ids
            return [('id', 'in' if operator in ('!=', 'not in') else 'not in', linked)]
        accounts = VivaAccount.search([('id', operator, value)])
        return [('id', 'in', accounts.journal_id.ids)]

    @api.depends('viva_account_id.wallet_id')
    def _compute_viva_wallet_id(self):
        for journal in self:
            journal.viva_wallet_id = journal.viva_account_id.wallet_id

    def _inverse_viva_wallet_id(self):
        for journal in self:
            account = journal.viva_account_id
            if account:
                if account.wallet_id != journal.viva_wallet_id:
                    account.wallet_id = journal.viva_wallet_id
            elif journal.viva_wallet_id:
                self.env['viva.account'].create({
                    'name': journal.name,
                    'company_id': journal.company_id.id,
                    'journal_id': journal.id,
                    'wallet_id': journal.viva_wallet_id,
                })
                journal.invalidate_recordset(['viva_account_id'])

    def __get_bank_statements_available_sources(self):
        rslt = super(AccountJournal, self).__get_bank_statements_available_sources()
        rslt.append(('viva', _('Viva.com')))
        return rslt

    def _viva_require_account(self):
        self.ensure_one()
        if not self.viva_account_id:
            raise UserError(_('Set the Viva wallet on this journal before fetching transactions.'))
        return self.viva_account_id

    def action_viva_fetch_now(self):
        return self._viva_require_account().action_viva_fetch_now()

    def action_viva_fetch_range(self):
        account = self._viva_require_account()
        action = self.env['ir.actions.act_window']._for_xml_id(
            'account_online_viva.viva_fetch_wizard_action')
        action['context'] = {'default_viva_account_id': account.id}
        return action

    def action_viva_open_account(self):
        account = self._viva_require_account()
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'viva.account',
            'res_id': account.id,
            'view_mode': 'form',
            'target': 'current',
        }
