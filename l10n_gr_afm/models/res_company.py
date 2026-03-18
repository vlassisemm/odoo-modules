# Copyright 2026 Vlassis Emmanouil
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
from odoo import fields, models


class ResCompany(models.Model):
    _inherit = 'res.company'

    l10n_gr_afm_aade_username = fields.Char(string='AADE Username')
    l10n_gr_afm_aade_password = fields.Char(
        string='AADE Password',
        groups='base.group_system',
        copy=False,
    )
