# Copyright 2026 Vlassis Emmanouil
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
from odoo import fields, models


class L10nGrAfmLookupWizard(models.TransientModel):
    _name = 'l10n_gr_afm.lookup.wizard'
    _description = 'AADE AFM Lookup Preview'

    partner_id = fields.Many2one('res.partner', required=True, ondelete='cascade')

    # Fields that map to partner on apply
    afm_name = fields.Char(string='Company Name', readonly=True)
    afm_street = fields.Char(string='Street Address', readonly=True)
    afm_zip = fields.Char(string='Postal Code', readonly=True)
    afm_city = fields.Char(string='City', readonly=True)
    afm_doy = fields.Char(string='Tax Office (DOY)', readonly=True)
    afm_kad_code = fields.Char(string='Main Activity Code (KAD)', readonly=True)
    afm_kad_descr = fields.Char(string='Main Activity Description', readonly=True)

    # Display-only fields
    afm_legal_status = fields.Char(string='Legal Form', readonly=True)
    afm_vat_status = fields.Char(string='VAT Status', readonly=True)
    afm_entity_type = fields.Char(string='Entity Type', readonly=True)
    afm_business_status = fields.Char(string='Business Status', readonly=True)
    afm_regist_date = fields.Date(string='Registration Date', readonly=True)

    # Warning driver
    afm_is_inactive = fields.Boolean()

    def action_apply(self):
        """Write the fetched AADE data to the linked partner record."""
        self.ensure_one()
        vals = {
            'street': self.afm_street,
            'zip': self.afm_zip,
            'city': self.afm_city,
            'l10n_gr_afm_doy': self.afm_doy,
            'l10n_gr_afm_kad_descr': self.afm_kad_descr,
        }
        # Only overwrite name if AADE returned one (name is required on res.partner)
        if self.afm_name:
            vals['name'] = self.afm_name
        self.partner_id.write(vals)
        return {'type': 'ir.actions.act_window_close'}
