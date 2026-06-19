# Copyright 2026 Vlassis Emmanouil
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
from odoo import _, fields, models


class AccountJournal(models.Model):
    _inherit = 'account.journal'

    viva_account_id = fields.Many2one(
        'viva.account', compute='_compute_viva_account_id', string='Viva Account',
        compute_sudo=True)

    def _compute_viva_account_id(self):
        accounts = self.env['viva.account'].search([('journal_id', 'in', self.ids)])
        by_journal = {a.journal_id.id: a for a in accounts}
        for journal in self:
            journal.viva_account_id = by_journal.get(journal.id, False)

    def __get_bank_statements_available_sources(self):
        rslt = super(AccountJournal, self).__get_bank_statements_available_sources()
        rslt.append(('viva', _('Viva.com')))
        return rslt
