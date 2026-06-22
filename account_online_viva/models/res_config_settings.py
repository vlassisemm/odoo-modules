# Copyright 2026 Vlassis Emmanouil
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    viva_client_id = fields.Char(
        related='company_id.viva_client_id', readonly=False)
    viva_client_secret = fields.Char(
        related='company_id.viva_client_secret', readonly=False,
        groups='base.group_system',
        copy=False)
    viva_environment = fields.Selection(
        related='company_id.viva_environment', readonly=False)
