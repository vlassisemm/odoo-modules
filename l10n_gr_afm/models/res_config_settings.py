# Copyright 2026 Vlassis Emmanouil
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    l10n_gr_afm_aade_username = fields.Char(
        related='company_id.l10n_gr_afm_aade_username',
        readonly=False,
    )
    l10n_gr_afm_aade_password = fields.Char(
        related='company_id.l10n_gr_afm_aade_password',
        readonly=False,
        groups='base.group_system',
    )
