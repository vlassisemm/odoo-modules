# Part of Odoo. See LICENSE file for full copyright and licensing details.
import logging

import requests as http_requests
from lxml import etree

from odoo import _, api, fields, models, Command

_logger = logging.getLogger(__name__)

FETCH_URL_PROD = 'https://mydatapi.aade.gr/myDATA/RequestDocs'
FETCH_URL_DEV = 'https://mydataapidev.aade.gr/RequestDocs'
FETCH_MAX_PAGES = 50
FETCH_DEFAULT_TIMEOUT = 30

# myDATA appendix 8.2 — VAT category code -> percentage.
# Code 8 ("entries without VAT", e.g. payroll) is deliberately absent: no tax applies.
VAT_CATEGORY_PERCENT = {
    1: 24.0, 2: 13.0, 3: 6.0, 4: 17.0, 5: 9.0, 6: 4.0, 7: 0.0, 9: 3.0, 10: 4.0,
}
# Appendix 8.4/8.6/8.7/8.5 — percent-based categories only; absent codes are
# amount-based and always fall back to an explicit line.
WITHHELD_PERCENT = {
    1: 15.0, 2: 20.0, 3: 20.0, 4: 3.0, 5: 1.0, 6: 4.0, 7: 8.0, 8: 4.0,
    9: 10.0, 10: 15.0, 12: 15.0, 13: 10.0, 18: 5.0,
}
STAMP_DUTY_PERCENT = {1: 1.2, 2: 2.4, 3: 3.6}
FEES_PERCENT = {
    1: 12.0, 2: 15.0, 3: 18.0, 4: 20.0, 5: 12.0, 6: 10.0, 7: 5.0, 9: 2.0,
    13: 10.0, 14: 10.0, 15: 0.0,
}
OTHER_TAXES_PERCENT = {
    3: 4.0, 4: 15.0, 5: 0.0, 11: 5.0, 12: 10.0, 13: 10.0, 14: 80.0, 15: 20.0,
}

CREDIT_INVOICE_TYPES = ('5.1', '5.2')


def _el_text(element, path):
    """findtext with None for missing/empty, namespace-agnostic ({*} paths)."""
    if element is None:
        return None
    value = element.findtext(path)
    return value if value not in (None, '') else None


def _el_float(element, path):
    value = _el_text(element, path)
    return float(value) if value is not None else None


def _el_int(element, path):
    value = _el_text(element, path)
    return int(value) if value is not None else None


class ResCompany(models.Model):
    _inherit = 'res.company'

    l10n_gr_edi_fetch_mark = fields.Char(
        string='myDATA Fetch Watermark',
        copy=False,
        help='Highest myDATA MARK processed by the vendor-bill fetch cron. '
             'Clear it to re-fetch the last 90 days (already-imported bills are skipped).',
    )

    # ------------------------------------------------------------------
    # Parse layer: RequestDocs XML -> plain dicts
    # ------------------------------------------------------------------

    @api.model
    def _l10n_gr_edi_parse_invoice(self, inv_el):
        issuer_el = inv_el.find('{*}issuer')
        address_el = issuer_el.find('{*}address') if issuer_el is not None else None
        lines = []
        for det in inv_el.iterfind('{*}invoiceDetails'):
            lines.append({
                'line_number': _el_int(det, '{*}lineNumber') or len(lines) + 1,
                'rec_type': _el_int(det, '{*}recType'),
                'quantity': _el_float(det, '{*}quantity'),
                'net_value': _el_float(det, '{*}netValue') or 0.0,
                'vat_category': _el_int(det, '{*}vatCategory'),
                'vat_amount': _el_float(det, '{*}vatAmount') or 0.0,
                'vat_exemption_category': _el_int(det, '{*}vatExemptionCategory'),
                'item_descr': _el_text(det, '{*}itemDescr'),
                'item_code': _el_text(det, '{*}itemCode'),
                'line_comments': _el_text(det, '{*}lineComments'),
                'withheld_amount': _el_float(det, '{*}withheldAmount'),
                'withheld_percent_category': _el_int(det, '{*}withheldPercentCategory'),
                'fees_amount': _el_float(det, '{*}feesAmount'),
                'fees_percent_category': _el_int(det, '{*}feesPercentCategory'),
                'stamp_duty_amount': _el_float(det, '{*}stampDutyAmount'),
                'stamp_duty_percent_category': _el_int(det, '{*}stampDutyPercentCategory'),
                'other_taxes_amount': _el_float(det, '{*}otherTaxesAmount'),
                'other_taxes_percent_category': _el_int(det, '{*}otherTaxesPercentCategory'),
                'deductions_amount': _el_float(det, '{*}deductionsAmount'),
            })
        summary_el = inv_el.find('{*}invoiceSummary')
        summary = None
        if summary_el is not None:
            summary = {
                'total_net_value': _el_float(summary_el, '{*}totalNetValue') or 0.0,
                'total_vat_amount': _el_float(summary_el, '{*}totalVatAmount') or 0.0,
                'total_withheld_amount': _el_float(summary_el, '{*}totalWithheldAmount') or 0.0,
                'total_fees_amount': _el_float(summary_el, '{*}totalFeesAmount') or 0.0,
                # official schema spells it 'totalStampDutyamount'; accept both
                'total_stamp_duty_amount': (
                    _el_float(summary_el, '{*}totalStampDutyamount')
                    or _el_float(summary_el, '{*}totalStampDutyAmount') or 0.0),
                'total_other_taxes_amount': _el_float(summary_el, '{*}totalOtherTaxesAmount') or 0.0,
                'total_deductions_amount': _el_float(summary_el, '{*}totalDeductionsAmount') or 0.0,
                'total_gross_value': _el_float(summary_el, '{*}totalGrossValue') or 0.0,
            }
        return {
            'mark': _el_text(inv_el, '{*}mark'),
            'uid': _el_text(inv_el, '{*}uid'),
            'cancelled_by_mark': _el_text(inv_el, '{*}cancelledByMark'),
            'qr_code_url': _el_text(inv_el, '{*}qrCodeUrl'),
            'issuer': {
                'vat': _el_text(issuer_el, '{*}vatNumber'),
                'country': _el_text(issuer_el, '{*}country'),
                'branch': _el_int(issuer_el, '{*}branch') or 0,
                'name': _el_text(issuer_el, '{*}name'),
                'street': _el_text(address_el, '{*}street'),
                'number': _el_text(address_el, '{*}number'),
                'postal_code': _el_text(address_el, '{*}postalCode'),
                'city': _el_text(address_el, '{*}city'),
            },
            'header': {
                'series': _el_text(inv_el, '{*}invoiceHeader/{*}series'),
                'aa': _el_text(inv_el, '{*}invoiceHeader/{*}aa'),
                'issue_date': _el_text(inv_el, '{*}invoiceHeader/{*}issueDate'),
                'invoice_type': _el_text(inv_el, '{*}invoiceHeader/{*}invoiceType'),
                'currency': _el_text(inv_el, '{*}invoiceHeader/{*}currency'),
                'exchange_rate': _el_float(inv_el, '{*}invoiceHeader/{*}exchangeRate'),
                'correlated_marks': [
                    el.text for el in
                    inv_el.iterfind('{*}invoiceHeader/{*}correlatedInvoices')
                    if el.text
                ],
                'self_pricing': _el_text(inv_el, '{*}invoiceHeader/{*}selfPricing') == 'true',
            },
            'lines': lines,
            'taxes_totals': [
                {
                    'tax_type': _el_int(tax_el, '{*}taxType'),
                    'tax_category': _el_int(tax_el, '{*}taxCategory'),
                    'underlying_value': _el_float(tax_el, '{*}underlyingValue'),
                    'tax_amount': _el_float(tax_el, '{*}taxAmount') or 0.0,
                }
                for tax_el in inv_el.iterfind('{*}taxesTotals/{*}taxes')
            ],
            'payment_methods': [
                {
                    'type': _el_int(pm_el, '{*}type'),
                    'amount': _el_float(pm_el, '{*}amount') or 0.0,
                    'info': _el_text(pm_el, '{*}paymentMethodInfo'),
                }
                for pm_el in inv_el.iterfind('{*}paymentMethods/{*}paymentMethodDetails')
            ],
            'summary': summary,
        }

    @api.model
    def _l10n_gr_edi_parse_requested_docs(self, content):
        root = etree.fromstring(content)
        invoices = [
            self._l10n_gr_edi_parse_invoice(el)
            for el in root.iterfind('.//{*}invoicesDoc/{*}invoice')
        ]
        cancellations = [
            {
                'invoice_mark': _el_text(el, '{*}invoiceMark'),
                'cancellation_mark': _el_text(el, '{*}cancellationMark'),
                'cancellation_date': _el_text(el, '{*}cancellationDate'),
            }
            for el in root.iterfind('.//{*}cancelledInvoicesDoc')
        ]
        token_el = root.find('.//{*}continuationToken')
        continuation = None
        if token_el is not None:
            continuation = {
                'nextPartitionKey': _el_text(token_el, '{*}nextPartitionKey'),
                'nextRowKey': _el_text(token_el, '{*}nextRowKey'),
            }
        marks = (
            [int(inv['mark']) for inv in invoices if inv['mark']]
            + [int(c['cancellation_mark']) for c in cancellations if c['cancellation_mark']]
        )
        return {
            'invoices': invoices,
            'cancellations': cancellations,
            'continuation': continuation,
            'max_mark': max(marks, default=0),
        }

    # ------------------------------------------------------------------
    # Partner resolution
    # ------------------------------------------------------------------

    def _l10n_gr_edi_enrich_partner_from_afm(self, partner):
        """Best-effort enrichment of a Greek partner from the AADE registry
        via l10n_gr_afm (soft dependency). Returns one chatter report line."""
        self.ensure_one()
        Partner = self.env['res.partner']
        if not hasattr(Partner, '_l10n_gr_afm_call_aade'):
            return _('AADE registry enrichment skipped: l10n_gr_afm is not installed.')
        company_sudo = self.sudo()
        username = company_sudo.l10n_gr_afm_aade_username
        password = company_sudo.l10n_gr_afm_aade_password
        if not (username and password):
            return _('AADE registry enrichment skipped: no AADE registry credentials configured.')
        try:
            afm = Partner._l10n_gr_afm_extract_vat(partner.vat)
            data = Partner._l10n_gr_afm_call_aade(afm, username, password)
        except Exception as error:  # noqa: BLE001 - UserError/network; must never break the fetch
            _logger.warning("myDATA fetch: AADE enrichment failed for %s: %s", partner.vat, error)
            return _('AADE registry enrichment failed: %s', error)
        partner.write({
            field: value
            for field, value in {
                'name': data.get('afm_name'),
                'street': data.get('afm_street'),
                'zip': data.get('afm_zip'),
                'city': data.get('afm_city'),
            }.items()
            if value
        })
        return _('Partner enriched from the AADE registry.')

    def _l10n_gr_edi_resolve_partner(self, issuer):
        """Return (partner, report_lines) for a payload issuer dict.
        Matches by VAT first; otherwise creates a tagged minimal partner,
        enriched from the AADE registry for Greek issuers when possible."""
        self.ensure_one()
        report = []
        Partner = self.env['res.partner'].sudo().with_context(active_test=False)
        afm = issuer['vat']
        is_greek = (issuer['country'] or 'GR') == 'GR'
        vat_variants = [f'EL{afm}', afm] if is_greek else [afm]
        candidates = Partner.search([
            ('vat', 'in', vat_variants),
            '|', ('company_id', '=', False), ('company_id', '=', self.id),
        ])
        if candidates:
            exact_company = candidates.filtered(lambda p: p.company_id == self)
            pool = exact_company or candidates
            partner = pool.sorted(key=lambda p: (not p.is_company, p.id))[:1]
            report.append(_('Partner matched by VAT: %s', partner.display_name))
            return partner, report
        vals = {
            'name': issuer['name'] or _('Vendor %s (myDATA)', afm),
            'vat': vat_variants[0],
            'is_company': True,
            'supplier_rank': 1,
            'company_id': False,
            'category_id': [Command.link(
                self.env.ref('l10n_gr_edi.res_partner_category_mydata_auto').id)],
        }
        if issuer['country']:
            country = self.env['res.country'].search(
                [('code', '=', issuer['country'])], limit=1)
            if country:
                vals['country_id'] = country.id
        if issuer['street'] or issuer['number']:
            vals['street'] = ' '.join(filter(None, [issuer['street'], issuer['number']]))
        if issuer['postal_code']:
            vals['zip'] = issuer['postal_code']
        if issuer['city']:
            vals['city'] = issuer['city']
        if issuer['branch']:
            vals['l10n_gr_edi_branch_number'] = issuer['branch']
        partner = Partner.create(vals)
        report.append(_('Partner auto-created from myDATA: %s', partner.display_name))
        if is_greek:
            report.append(self._l10n_gr_edi_enrich_partner_from_afm(partner))
        return partner, report
