# Copyright 2026 Vlassis Emmanouil
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
import logging

from odoo import fields, models

from .viva_client import VivaClient

_logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 60


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

    def _viva_get_client(self):
        """Single factory for VivaClient so every caller (sync, cron, setup
        wizard) shares the credential access pattern and the configured
        timeout."""
        self.ensure_one()
        company = self.sudo()
        raw_timeout = self.env['ir.config_parameter'].sudo().get_param(
            'account_online_viva.timeout', str(DEFAULT_TIMEOUT))
        try:
            timeout = int(raw_timeout)
        except (TypeError, ValueError):
            _logger.warning(
                "Invalid 'account_online_viva.timeout' value %r; "
                "falling back to %s seconds.", raw_timeout, DEFAULT_TIMEOUT)
            timeout = DEFAULT_TIMEOUT
        return VivaClient(
            company.viva_client_id, company.viva_client_secret,
            company.viva_environment, timeout=timeout)
