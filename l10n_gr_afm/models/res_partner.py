# Copyright 2026 Vlassis Emmanouil
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
import logging
import re

import requests as http_requests
from lxml import etree

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools.sql import column_exists, create_column

_logger = logging.getLogger(__name__)

AADE_ENDPOINT = 'https://www1.gsis.gr/wsaade/RgWsPublic2/RgWsPublic2'
AADE_DEFAULT_TIMEOUT = 30

_NS_SOAP = 'http://www.w3.org/2003/05/soap-envelope'
_NS_WSSE = 'http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd'
_NS_SVC = 'http://rgwspublic2/RgWsPublic2Service'
_NS_TYPES = 'http://rgwspublic2/RgWsPublic2'
_NS_XSI = 'http://www.w3.org/2001/XMLSchema-instance'

_RESPONSE_NS = {'ns': _NS_TYPES}


class ResPartner(models.Model):
    _inherit = 'res.partner'

    l10n_gr_afm_doy = fields.Char(string='Tax Office (DOY)')
    l10n_gr_afm_kad_descr = fields.Char(string='Main Activity Description')
    l10n_gr_afm_can_fetch = fields.Boolean(
        compute='_compute_l10n_gr_afm_can_fetch',
    )

    @api.depends('country_code', 'vat')
    def _compute_l10n_gr_afm_can_fetch(self):
        for partner in self:
            vat = (partner.vat or '').strip().upper()
            partner.l10n_gr_afm_can_fetch = (
                partner.country_code == 'GR'
                or vat.startswith('EL')
                or bool(re.match(r'^\d{9}$', vat))
            )

    @api.onchange('vat')
    def _onchange_l10n_gr_afm_prefill_name(self):
        """Pre-fill name and country on new Greek partners to allow saving before AADE fetch."""
        if self.vat and self.l10n_gr_afm_can_fetch:
            if not self.country_id:
                self.country_id = self.env.ref('base.gr')
            if not self.name:
                self.name = self.vat

    def _auto_init(self):
        for col, col_type in [
            ('l10n_gr_afm_doy', 'varchar'),
            ('l10n_gr_afm_kad_descr', 'varchar'),
        ]:
            if not column_exists(self.env.cr, 'res_partner', col):
                create_column(self.env.cr, 'res_partner', col, col_type)
        return super()._auto_init()

    # --- VAT extraction ---

    @api.model
    def _l10n_gr_afm_extract_vat(self, vat):
        """Extract bare 9-digit AFM from a VAT string.

        Strips 'EL' prefix (case-insensitive), validates 9 digits.
        Greek VATs use the 'EL' prefix (not 'GR') per EU convention.
        """
        if not vat:
            raise UserError(_("Please enter a VAT number before fetching from AADE."))
        cleaned = re.sub(r'^EL', '', vat.strip(), flags=re.IGNORECASE)
        if not re.match(r'^\d{9}$', cleaned):
            raise UserError(_("AADE lookup is only available for Greek VAT numbers."))
        return cleaned

    # --- SOAP envelope builder ---

    @api.model
    def _l10n_gr_afm_build_envelope(self, username, password, afm):
        """Build SOAP 1.2 envelope with WS-Security using lxml (XML-injection safe)."""
        envelope = etree.Element(f'{{{_NS_SOAP}}}Envelope', nsmap={
            'env': _NS_SOAP,
            'ns1': _NS_WSSE,
            'ns2': _NS_SVC,
            'ns3': _NS_TYPES,
        })

        # Header with WS-Security UsernameToken
        header = etree.SubElement(envelope, f'{{{_NS_SOAP}}}Header')
        security = etree.SubElement(header, f'{{{_NS_WSSE}}}Security')
        token = etree.SubElement(security, f'{{{_NS_WSSE}}}UsernameToken')
        user_el = etree.SubElement(token, f'{{{_NS_WSSE}}}Username')
        user_el.text = username
        pass_el = etree.SubElement(token, f'{{{_NS_WSSE}}}Password')
        pass_el.text = password

        # Body
        body = etree.SubElement(envelope, f'{{{_NS_SOAP}}}Body')
        method = etree.SubElement(body, f'{{{_NS_SVC}}}rgWsPublic2AfmMethod')
        input_rec = etree.SubElement(method, f'{{{_NS_SVC}}}INPUT_REC')
        # afm_called_by left empty: service defaults to token owner's AFM
        etree.SubElement(input_rec, f'{{{_NS_TYPES}}}afm_called_by')
        called_for = etree.SubElement(input_rec, f'{{{_NS_TYPES}}}afm_called_for')
        called_for.text = afm

        return etree.tostring(envelope, xml_declaration=True, encoding='UTF-8')

    # --- Response parsing ---

    @staticmethod
    def _l10n_gr_afm_get_text(element, path):
        """Get text from an XML element, returning None for xsi:nil values."""
        el = element.find(path, _RESPONSE_NS)
        if el is None:
            return None
        if el.get(f'{{{_NS_XSI}}}nil') == 'true':
            return None
        return el.text

    @api.model
    def _l10n_gr_afm_parse_response(self, content):
        """Parse AADE SOAP response XML bytes into a dict.

        Returns a dict with all fields needed by the lookup wizard.
        Raises UserError on AADE-level errors.
        """
        try:
            tree = etree.fromstring(content)
        except etree.XMLSyntaxError:
            raise UserError(_("Unexpected response from AADE. Please contact support."))

        result = tree.find('.//ns:rg_ws_public2_result_rtType', _RESPONSE_NS)
        if result is None:
            raise UserError(_("Unexpected response from AADE. Please contact support."))

        # Check for AADE errors
        error_code = self._l10n_gr_afm_get_text(result, 'ns:error_rec/ns:error_code')
        if error_code:
            error_descr = self._l10n_gr_afm_get_text(result, 'ns:error_rec/ns:error_descr')
            if error_descr:
                raise UserError(error_descr)
            raise UserError(_("AADE returned error: %s", error_code))

        get = lambda path: self._l10n_gr_afm_get_text(result, path)

        # Build street from address + number
        street = get('ns:basic_rec/ns:postal_address') or ''
        street_no = get('ns:basic_rec/ns:postal_address_no') or ''
        full_street = f"{street} {street_no}".strip()

        # Extract primary activity (firm_act_kind == '1')
        kad_code = None
        kad_descr = None
        for item in result.findall('ns:firm_act_tab/ns:item', _RESPONSE_NS):
            kind = self._l10n_gr_afm_get_text(item, 'ns:firm_act_kind')
            if kind == '1':
                kad_code = self._l10n_gr_afm_get_text(item, 'ns:firm_act_code')
                kad_descr = self._l10n_gr_afm_get_text(item, 'ns:firm_act_descr')
                break

        # Parse registration date
        regist_date_str = get('ns:basic_rec/ns:regist_date')
        regist_date = regist_date_str if regist_date_str else False

        deactivation_flag = get('ns:basic_rec/ns:deactivation_flag')

        return {
            'afm_name': get('ns:basic_rec/ns:onomasia'),
            'afm_street': full_street,
            'afm_zip': get('ns:basic_rec/ns:postal_zip_code'),
            'afm_city': get('ns:basic_rec/ns:postal_area_description'),
            'afm_doy': get('ns:basic_rec/ns:doy_descr'),
            'afm_kad_code': kad_code,
            'afm_kad_descr': kad_descr,
            'afm_legal_status': get('ns:basic_rec/ns:legal_status_descr'),
            'afm_vat_status': get('ns:basic_rec/ns:deactivation_flag_descr'),
            'afm_entity_type': get('ns:basic_rec/ns:i_ni_flag_descr'),
            'afm_business_status': get('ns:basic_rec/ns:firm_flag_descr'),
            'afm_regist_date': regist_date,
            'afm_is_inactive': deactivation_flag == '2',
        }

    # --- AADE call ---

    @api.model
    def _l10n_gr_afm_call_aade(self, afm, username, password):
        """Call the AADE RgWsPublic2 service and return parsed result dict."""
        envelope = self._l10n_gr_afm_build_envelope(username, password, afm)

        timeout = int(
            self.env['ir.config_parameter'].sudo().get_param(
                'l10n_gr_afm.aade_timeout', AADE_DEFAULT_TIMEOUT
            )
        )

        try:
            response = http_requests.post(
                AADE_ENDPOINT,
                data=envelope,
                headers={'Content-Type': 'application/soap+xml; charset=utf-8'},
                timeout=timeout,
            )
        except http_requests.exceptions.RequestException:
            _logger.warning("AADE AFM lookup failed for AFM %s", afm, exc_info=True)
            raise UserError(_("Could not connect to AADE. Please try again later."))

        if response.status_code in (401, 403):
            _logger.warning("AADE auth failed (HTTP %s) for AFM %s", response.status_code, afm)
            raise UserError(
                _("AADE authentication failed. Please check your credentials in Settings.")
            )
        if response.status_code >= 500:
            _logger.warning("AADE server error (HTTP %s) for AFM %s", response.status_code, afm)
            raise UserError(
                _("AADE service is temporarily unavailable. Please try again later.")
            )
        if response.status_code != 200:
            _logger.warning("AADE unexpected HTTP %s for AFM %s", response.status_code, afm)
            raise UserError(_("Unexpected response from AADE. Please contact support."))

        return self._l10n_gr_afm_parse_response(response.content)

    # --- Button action ---

    def action_l10n_gr_afm_fetch(self):
        """Fetch business data from AADE and open the preview wizard."""
        self.ensure_one()

        # Verify user has sales or accounting access before consuming AADE quota
        if not (
            self.env.user.has_group('sales_team.group_sale_salesman')
            or self.env.user.has_group('account.group_account_invoice')
        ):
            raise UserError(_("You do not have permission to perform AADE lookups."))

        # Validate VAT
        afm = self._l10n_gr_afm_extract_vat(self.vat)

        # Validate credentials (use sudo for password field access)
        company = self.env.company.sudo()
        username = company.l10n_gr_afm_aade_username
        password = company.l10n_gr_afm_aade_password
        if not username or not password:
            raise UserError(
                _("Please configure AADE credentials in Accounting Settings.")
            )

        # Call AADE
        result = self._l10n_gr_afm_call_aade(afm, username, password)

        # Audit trail via chatter (only if mail module is installed)
        if hasattr(self, 'message_post'):
            self.message_post(
                body=_("AADE AFM lookup performed."),
                subtype_xmlid='mail.mt_note',
            )

        # Create wizard with fetched data
        wizard = self.env['l10n_gr_afm.lookup.wizard'].create({
            'partner_id': self.id,
            **result,
        })

        return {
            'type': 'ir.actions.act_window',
            'name': _('AADE Lookup Result'),
            'res_model': 'l10n_gr_afm.lookup.wizard',
            'res_id': wizard.id,
            'view_mode': 'form',
            'target': 'new',
        }
