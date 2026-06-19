# Copyright 2026 Vlassis Emmanouil
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
from odoo import fields, models


class ResCompany(models.Model):
    _inherit = 'res.company'

    viva_client_id = fields.Char(string='Viva Client ID')
    viva_client_secret = fields.Char(
        string='Viva Client Secret',
        groups='base.group_system',
        copy=False,
    )
    viva_environment = fields.Selection(
        selection=[('demo', 'Demo'), ('production', 'Production')],
        string='Viva Environment',
        default='demo',
        required=True,
    )
