# Copyright 2026 Vlassis Emmanouil
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
from odoo import fields, models


class VivaFetchWizard(models.TransientModel):
    _name = 'viva.fetch.wizard'
    _description = 'Viva Manual Fetch'

    viva_account_id = fields.Many2one('viva.account', required=True)
    date_from = fields.Date(required=True)
    date_to = fields.Date(required=True, default=fields.Date.context_today)

    def action_fetch(self):
        self.ensure_one()
        self.viva_account_id._viva_sync_one(
            date_from=self.date_from, date_to=self.date_to)
        return self.viva_account_id._viva_reconcile_action()
