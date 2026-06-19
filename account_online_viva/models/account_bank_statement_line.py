# Copyright 2026 Vlassis Emmanouil
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
from odoo import fields, models, tools


class AccountBankStatementLine(models.Model):
    _inherit = 'account.bank.statement.line'

    viva_transaction_id = fields.Char(
        string='Viva Transaction ID', index=True, readonly=True, copy=False)

    def _auto_init(self):
        res = super()._auto_init()
        tools.create_index(
            self._cr,
            'account_bank_statement_line_viva_txn_uniq',
            self._table,
            ['journal_id', 'viva_transaction_id'],
            where='viva_transaction_id IS NOT NULL',
            unique=True,
        )
        return res
