# Copyright 2026 Vlassis Emmanouil
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
from dateutil.relativedelta import relativedelta

from odoo import _, fields, models
from odoo.exceptions import AccessError


class VivaFetchWizard(models.TransientModel):
    _name = 'viva.fetch.wizard'
    _description = 'Viva Manual Fetch'

    viva_account_id = fields.Many2one('viva.account', required=True)
    date_from = fields.Date(
        required=True,
        default=lambda self: fields.Date.context_today(self) - relativedelta(days=30))
    date_to = fields.Date(required=True, default=fields.Date.context_today)

    def action_fetch(self):
        self.ensure_one()
        if not self.env.su and not self.env.user.has_group(
                'account.group_account_basic'):
            raise AccessError(_('You are not allowed to fetch Viva transactions.'))
        self.viva_account_id._viva_sync_one(
            date_from=self.date_from,
            date_to=self.date_to,
            update_watermark=False,
        )
        return self.viva_account_id._viva_reconcile_action()
