# Copyright 2026 Vlassis Emmanouil
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
from odoo import fields, models


class AccountBankStatementLine(models.Model):
    _inherit = 'account.bank.statement.line'

    viva_transaction_id = fields.Char(
        string='Viva Transaction ID', readonly=True, copy=False)

    # Partial unique index: dedup per journal; NULLs (non-Viva lines) exempt.
    # The attribute name keeps the SQL name identical to the index previously
    # built in _auto_init, so existing databases keep their index untouched.
    _viva_txn_uniq = models.UniqueIndex(
        '(journal_id, viva_transaction_id) WHERE viva_transaction_id IS NOT NULL',
        'This Viva transaction was already imported in this journal.',
    )
