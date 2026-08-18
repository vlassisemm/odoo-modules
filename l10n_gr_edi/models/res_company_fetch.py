# Part of Odoo. See LICENSE file for full copyright and licensing details.
import logging

from datetime import timedelta

import requests as http_requests
from lxml import etree
from markupsafe import Markup, escape

from odoo import _, api, fields, models, Command
from odoo.tools import float_compare
from odoo.addons.l10n_gr_edi.models.preferred_classification import (
    CLASSIFICATION_CATEGORY_SELECTION,
    INVOICE_TYPES_HAVE_EXPENSE,
    INVOICE_TYPES_SELECTION,
    TAX_EXEMPTION_CATEGORY_SELECTION,
)

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
# Percentages a positive charge must never be matched on: a purchase tax at
# such a rate is a VAT tax, and hanging it off a fees/stamp-duty/other-taxes
# charge would corrupt the VAT return.
VAT_PERCENTS = frozenset(VAT_CATEGORY_PERCENT.values())
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

# Document-level goods/services signal: sales-of-goods invoice types map to
# the domestic "G" purchase VAT taxes of the Greek chart template, service
# types to "S". Types without a reliable signal (3.x acquisition titles,
# 5.x credit notes, 8.x specials, ...) resolve to nothing and keep the
# explicit fallback line.
INVOICE_TYPE_VAT_SUFFIX = {
    '1.1': 'G', '1.2': 'G', '1.3': 'G', '1.4': 'G', '1.5': 'G', '1.6': 'G',
    '11.1': 'G', '11.3': 'G',
    '2.1': 'S', '2.2': 'S', '2.3': 'S', '2.4': 'S',
    '11.2': 'S', '11.4': 'S',
}
# Logistics documents (no fiscal value): no vendor bill is created for them.
# Future: delivery notes (9.3) could feed inventory receipts instead.
NON_FISCAL_INVOICE_TYPES = frozenset({'9.3'})

# (amount_key, category_key, percent_table, label_key, sign) — sign -1 reduces the payable
LINE_CHARGES = (
    ('withheld_amount', 'withheld_percent_category', WITHHELD_PERCENT, 'withheld', -1),
    ('fees_amount', 'fees_percent_category', FEES_PERCENT, 'fees', 1),
    ('stamp_duty_amount', 'stamp_duty_percent_category', STAMP_DUTY_PERCENT, 'stamp_duty', 1),
    ('other_taxes_amount', 'other_taxes_percent_category', OTHER_TAXES_PERCENT, 'other_taxes', 1),
    ('deductions_amount', None, None, 'deductions', -1),
)
TAX_TOTALS_SPEC = {
    1: ('withheld', -1), 2: ('fees', 1), 3: ('other_taxes', 1),
    4: ('stamp_duty', 1), 5: ('deductions', -1),
}


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
        groups='base.group_system',
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
                'taric_no': _el_text(det, '{*}TaricNo'),
                'fuel_code': _el_text(det, '{*}fuelCode'),
                'line_comments': _el_text(det, '{*}lineComments'),
                'other_uom_quantity': _el_float(det, '{*}otherMeasurementUnitQuantity'),
                'other_uom_title': _el_text(det, '{*}otherMeasurementUnitTitle'),
                'income_classifications': [
                    {
                        'category': _el_text(cls, '{*}classificationCategory'),
                        'type': _el_text(cls, '{*}classificationType'),
                        'amount': _el_float(cls, '{*}amount'),
                    }
                    for cls in det.iterfind('{*}incomeClassification')
                ],
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
            # cancelledInvoicesDoc is a container of <cancelledInvoice> records
            for el in root.iterfind('.//{*}cancelledInvoicesDoc/{*}cancelledInvoice')
        ]
        token_el = root.find('.//{*}continuationToken')
        continuation = None
        if token_el is not None:
            continuation = {
                'nextPartitionKey': _el_text(token_el, '{*}nextPartitionKey'),
                'nextRowKey': _el_text(token_el, '{*}nextRowKey'),
            }
            if not any(continuation.values()):
                # An empty/childless <continuationToken/> is not a real
                # continuation - treat it the same as no token at all.
                continuation = None
        marks = (
            [int(inv['mark']) for inv in invoices if inv['mark']]
            + [int(c['cancellation_mark']) for c in cancellations if c['cancellation_mark']]
        )
        for tag in ('classificationMark', 'paymentMethodMark'):
            marks.extend(
                int(element.text)
                for element in root.iterfind(f'.//{{*}}{tag}')
                if element.text
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
            _logger.warning(
                'myDATA fetch: partner enrichment failed company_id=%s '
                'partner_id=%s error_type=%s',
                self.id, partner.id, type(error).__name__)
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
            partner = pool.sorted(
                key=lambda p: (not p.active, not p.is_company, p.id))[:1]
            report.append(_('Partner matched by VAT: %s', partner.display_name))
            return partner, report
        vals = {
            'name': issuer['name'] or _('Vendor %s (myDATA)', afm),
            'vat': vat_variants[0],
            'is_company': True,
            'supplier_rank': 1,
            'company_id': False,
        }
        # Ships as module data, but a database where it was removed must still
        # get its vendor bills - tag the partner only when the record is there.
        tag = self.env.ref('l10n_gr_edi.res_partner_category_mydata_auto',
                           raise_if_not_found=False)
        if tag:
            vals['category_id'] = [Command.link(tag.id)]
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
        if not tag:
            report.append(_('The "myDATA Auto-created" partner tag is missing from '
                            'this database — partner created without it.'))
        if is_greek:
            report.append(self._l10n_gr_edi_enrich_partner_from_afm(partner))
        return partner, report

    # ------------------------------------------------------------------
    # Line building
    # ------------------------------------------------------------------

    @api.model
    def _l10n_gr_edi_charge_label(self, key):
        return {
            'withheld': _('Withheld tax'),
            'fees': _('Fees'),
            'stamp_duty': _('Stamp duty'),
            'other_taxes': _('Other taxes'),
            'deductions': _('Deductions'),
        }[key]

    def _l10n_gr_edi_match_purchase_tax(self, percent):
        self.ensure_one()
        taxes = self.env['account.tax'].search([
            ('company_id', '=', self.id),
            ('type_tax_use', '=', 'purchase'),
            ('amount_type', '=', 'percent'),
            ('amount', '=', percent),
        ])
        return taxes if len(taxes) == 1 else self.env['account.tax']

    def _l10n_gr_edi_resolve_vat_tax(self, vat_pct, invoice_type, fiscal_position):
        """Resolve a payload VAT rate to a concrete purchase tax.

        Picks the Greek chart-template G/S tax for the document type and maps
        it through the fiscal position when one applies. Returns
        ``(tax, warning)``: an empty recordset when unresolvable, and a warning
        string only when the fiscal position mapping was refused because it
        changes the rate (applying it would break payload reconciliation).
        """
        self.ensure_one()
        empty = self.env['account.tax']
        suffix = INVOICE_TYPE_VAT_SUFFIX.get(invoice_type)
        if not suffix:
            return empty, None
        tax = self.env.ref(
            f'account.{self.id}_l10n_gr_tax_p{vat_pct:g}_{suffix}',
            raise_if_not_found=False)
        if not tax or not tax.active:
            return empty, None
        if fiscal_position and fiscal_position not in tax.fiscal_position_ids:
            # A tax that itself lists the position (e.g. the domestic taxes
            # under the Greek chart's auto-applied "Domestic" positions) is
            # already valid under it; only map taxes foreign to the position.
            # The position wins only when it expresses exactly one same-rate
            # replacement. A deliberate different-rate mapping conflicts with
            # the declared payload rate and flags for review; an empty or
            # ambiguous mapping (e.g. the Aegean position, whose tax list is
            # empty) expresses no preference, so the payload rate stands.
            mapped = fiscal_position.map_tax(tax)
            if (len(mapped) == 1 and mapped.amount_type == 'percent'
                    and float_compare(mapped.amount, vat_pct, precision_digits=2) == 0):
                tax = mapped
            elif len(mapped) == 1:
                return empty, _(
                    'Fiscal position "%(fpos)s" maps %(tax)s to a different '
                    'rate — the VAT amount was kept as a separate line to '
                    'preserve the declared totals.',
                    fpos=fiscal_position.name, tax=tax.name)
        return tax, None

    @api.model
    def _l10n_gr_edi_line_description(self, line):
        """Fold every descriptive field the issuer sent into the line label.
        myDATA only allows ``itemDescr`` on tax-free / delivery-note invoices and
        ``lineComments`` is optional, so ordinary bills usually carry neither —
        the issuer's income classification is then the most telling label left."""
        item_descr = line.get('item_descr')
        comments = line.get('line_comments')
        parts = [item_descr]
        if comments and comments != item_descr:
            parts.append(comments)
        descr = ' — '.join(part for part in parts if part)
        if not descr:
            if line.get('rec_type') == 2:
                descr = _('Fees')
            elif line.get('rec_type') == 3:
                descr = _('Other taxes')
            else:
                cls_labels = self._l10n_gr_edi_classification_labels(
                    line.get('income_classifications') or [])
                if cls_labels:
                    descr = _('%(cls)s — myDATA line %(n)s',
                              cls=', '.join(cls_labels), n=line['line_number'])
                else:
                    descr = _('myDATA line %s', line['line_number'])
        if line.get('item_code'):
            descr = f"[{line['item_code']}] {descr}"
        extras = []
        if line.get('taric_no'):
            extras.append(_('TARIC %s', line['taric_no']))
        if line.get('fuel_code'):
            extras.append(_('fuel code %s', line['fuel_code']))
        if line.get('other_uom_title'):
            other_qty = line.get('other_uom_quantity')
            extras.append(f"{other_qty:g} {line['other_uom_title']}"
                          if other_qty else line['other_uom_title'])
        if extras:
            descr = f"{descr} ({'; '.join(extras)})"
        return descr

    @api.model
    def _l10n_gr_edi_classification_labels(self, classifications):
        """Distinct human labels for the issuer's income classifications, e.g.
        ``1.3 - Provision of Services Income (E3_561_001)``."""
        category_labels = dict(CLASSIFICATION_CATEGORY_SELECTION)
        labels = []
        for cls in classifications:
            category, cls_type = cls.get('category'), cls.get('type')
            if not category and not cls_type:
                continue
            label = category_labels.get(category, category) or ''
            if cls_type:
                label = f'{label} ({cls_type})' if label else cls_type
            if label not in labels:
                labels.append(label)
        return labels

    def _l10n_gr_edi_prepare_line_vals(self, inv, fiscal_position=None):
        """Map payload lines to account.move.line create-commands.
        Invariant: every payload amount lands in a matched tax or an explicit
        fallback line, so the bill total always reconciles with the payload."""
        self.ensure_one()
        invoice_type = (inv.get('header') or {}).get('invoice_type')
        commands = []
        report = {'lines': [], 'warnings': []}
        for line in inv['lines']:
            number = line['line_number']
            sign = -1.0 if line['rec_type'] == 7 else 1.0
            descr = self._l10n_gr_edi_line_description(line)

            quantity = line['quantity'] or 1.0
            net_value = line['net_value']
            price_unit = round(net_value / quantity, 2)
            if quantity != 1.0 and float_compare(
                    price_unit * quantity, net_value, precision_digits=2) != 0:
                descr = _('%(descr)s (original quantity: %(qty)s)',
                          descr=descr, qty=quantity)
                quantity, price_unit = 1.0, net_value

            tax_ids = []
            vat_pct = VAT_CATEGORY_PERCENT.get(line['vat_category'])
            if line['vat_category'] == 8:
                pass  # entries without VAT
            elif vat_pct is None:
                report['warnings'].append(_(
                    'Line %(n)s: unknown VAT category %(cat)s — no VAT applied.',
                    n=number, cat=line['vat_category']))
            else:
                vat_tax, fpos_warning = self._l10n_gr_edi_resolve_vat_tax(
                    vat_pct, invoice_type, fiscal_position)
                if vat_tax:
                    tax_ids.append(vat_tax.id)
                elif line['vat_amount']:
                    if fpos_warning:
                        report['warnings'].append(fpos_warning)
                    commands.append(Command.create({
                        'name': _('VAT %(pct)s%% (no matching purchase tax) — line %(n)s',
                                  pct=vat_pct, n=number),
                        'quantity': 1.0,
                        'price_unit': sign * line['vat_amount'],
                        'tax_ids': [Command.set([])],
                        'l10n_gr_edi_is_fetch_adjustment': True,
                    }))
                    report['warnings'].append(_(
                        'Line %(n)s: VAT %(pct)s%% could not be mapped to a '
                        'purchase tax — VAT amount added as a separate line.',
                        n=number, pct=vat_pct))
                if line['vat_exemption_category']:
                    label = dict(TAX_EXEMPTION_CATEGORY_SELECTION).get(
                        str(line['vat_exemption_category']),
                        str(line['vat_exemption_category']))
                    report['warnings'].append(_(
                        'Line %(n)s: VAT exemption category — %(label)s.',
                        n=number, label=label))

            for amount_key, category_key, table, label_key, charge_sign in LINE_CHARGES:
                amount = line.get(amount_key)
                if not amount:
                    continue
                label = self._l10n_gr_edi_charge_label(label_key)
                category = line.get(category_key) if category_key else None
                pct = table.get(category) if (table and category) else None
                # Withheld/deductions are negative, so their rates can never be
                # confused with a VAT rate; a positive charge whose rate happens
                # to equal one (other taxes category 3 is 4%) must not be matched.
                collides = bool(pct) and charge_sign > 0 and pct in VAT_PERCENTS
                charge_tax = (self._l10n_gr_edi_match_purchase_tax(charge_sign * pct)
                              if pct and not collides else self.env['account.tax'])
                if charge_tax:
                    tax_ids.append(charge_tax.id)
                    report['lines'].append(_(
                        'Line %(n)s: %(label)s %(pct)s%% mapped to tax "%(tax)s".',
                        n=number, label=label, pct=pct, tax=charge_tax.name))
                else:
                    commands.append(Command.create({
                        'name': _('%(label)s (myDATA category %(cat)s) — line %(n)s',
                                  label=label, cat=category or '-', n=number),
                        'quantity': 1.0,
                        'price_unit': sign * charge_sign * amount,
                        'tax_ids': [Command.set([])],
                        'l10n_gr_edi_is_fetch_adjustment': True,
                    }))
                    if collides:
                        report['warnings'].append(_(
                            'Line %(n)s: %(label)s %(amount).2f added as a separate '
                            'line — its %(pct)s%% rate is also a VAT rate, so it was '
                            'not mapped to a tax.',
                            n=number, label=label, amount=amount, pct=pct))
                    else:
                        report['warnings'].append(_(
                            'Line %(n)s: %(label)s %(amount).2f added as a separate '
                            'line (no matching tax).',
                            n=number, label=label, amount=amount))

            commands.append(Command.create({
                'name': descr,
                'quantity': quantity,
                'price_unit': sign * price_unit,
                'tax_ids': [Command.set(tax_ids)],
                'l10n_gr_edi_source_line_number': number,
                'l10n_gr_edi_tax_exemption_category': (
                    str(line['vat_exemption_category'])
                    if line['vat_exemption_category'] else False
                ),
            }))

        # Document-level taxes_totals entries have no product/base line of their own to
        # attach a tax to, so matching against the CoA would double-count against
        # whatever tax already applies to the invoice lines — always an explicit line.
        for tax_total in inv['taxes_totals']:
            label_key, t_sign = TAX_TOTALS_SPEC.get(tax_total['tax_type'], ('other_taxes', 1))
            label = self._l10n_gr_edi_charge_label(label_key)
            commands.append(Command.create({
                'name': _('%(label)s (document level, myDATA category %(cat)s)',
                          label=label, cat=tax_total['tax_category'] or '-'),
                'quantity': 1.0,
                'price_unit': t_sign * tax_total['tax_amount'],
                'tax_ids': [Command.set([])],
                'l10n_gr_edi_is_fetch_adjustment': True,
            }))
            report['warnings'].append(_(
                'Document-level %(label)s %(amount).2f added as a separate line.',
                label=label, amount=tax_total['tax_amount']))
        return commands, report

    # ------------------------------------------------------------------
    # Bill header
    # ------------------------------------------------------------------

    def _l10n_gr_edi_prepare_bill_vals(self, inv, partner):
        self.ensure_one()
        header = inv['header']
        fiscal_position = self.env['account.fiscal.position'].sudo().with_company(
            self)._get_fiscal_position(partner)
        commands, report = self._l10n_gr_edi_prepare_line_vals(inv, fiscal_position)
        move_type = ('in_refund' if header['invoice_type'] in CREDIT_INVOICE_TYPES
                     else 'in_invoice')
        series = header['series']
        ref = (header['aa'] if not series or series == '0'
               else f"{series}/{header['aa']}")
        vals = {
            'move_type': move_type,
            'company_id': self.id,
            'partner_id': partner.id,
            'invoice_date': fields.Date.to_date(header['issue_date']),
            'date': fields.Date.to_date(header['issue_date']),
            'ref': ref,
            'invoice_line_ids': commands,
            'fiscal_position_id': fiscal_position.id,
            'l10n_gr_edi_is_fetched': True,
        }
        if header['invoice_type'] in INVOICE_TYPES_HAVE_EXPENSE:
            vals['l10n_gr_edi_inv_type'] = header['invoice_type']
        currency_code = header['currency']
        if currency_code and currency_code != self.currency_id.name:
            currency = self.env['res.currency'].search(
                [('name', '=', currency_code)], limit=1)
            if currency:
                vals['currency_id'] = currency.id
                exchange_rate = header['exchange_rate']
                if self.currency_id.name == 'EUR' and exchange_rate and exchange_rate > 0:
                    vals['invoice_currency_rate'] = exchange_rate
                elif self.currency_id.name != 'EUR':
                    report['warnings'].append(_(
                        'AADE exchange rate was not applied because the company '
                        'currency is not EUR. Review the bill rate.'))
                else:
                    report['warnings'].append(_(
                        'AADE did not provide a valid exchange rate. '
                        'Review the bill rate.'))
            else:
                report['warnings'].append(_(
                    'Unknown or inactive currency %(code)s — amounts recorded in '
                    'company currency, totals may be wrong.', code=currency_code))
        if move_type == 'in_refund' and header['correlated_marks']:
            original = self.env['account.move'].sudo().search([
                ('l10n_gr_edi_mark', 'in', header['correlated_marks']),
                ('company_id', '=', self.id),
            ], limit=1)
            if original:
                vals['reversed_entry_id'] = original.id
                vals['l10n_gr_edi_correlation_id'] = original.id
                report['lines'].append(_(
                    'Credit note linked to %(name)s (MARK %(mark)s).',
                    name=original.display_name, mark=original.l10n_gr_edi_mark))
            else:
                report['warnings'].append(_(
                    'Correlated invoice MARK(s) %(marks)s not found in Odoo — '
                    'credit note left unlinked.',
                    marks=', '.join(header['correlated_marks'])))
        return vals, report

    # ------------------------------------------------------------------
    # Summary checksum & chatter note
    # ------------------------------------------------------------------

    def _l10n_gr_edi_check_summary(self, move, inv):
        """Compare the created bill's total against the payload summary.
        Returns a list with one warning string on mismatch, else []."""
        summary = inv.get('summary')
        if not summary:
            return []
        expected = (
            summary['total_net_value'] + summary['total_vat_amount']
            + summary['total_fees_amount'] + summary['total_stamp_duty_amount']
            + summary['total_other_taxes_amount']
            - summary['total_withheld_amount'] - summary['total_deductions_amount']
        )
        if float_compare(move.amount_total, expected, precision_digits=2) != 0:
            return [_(
                'Total mismatch: Odoo bill total %(odoo).2f differs from the myDATA '
                'summary %(expected).2f (net %(net).2f, VAT %(vat).2f, '
                'withheld %(withheld).2f, fees %(fees).2f, stamp duty %(stamp).2f, '
                'other taxes %(other).2f, deductions %(deductions).2f, '
                'gross %(gross).2f). Please review the bill lines.',
                odoo=move.amount_total, expected=expected,
                net=summary['total_net_value'], vat=summary['total_vat_amount'],
                withheld=summary['total_withheld_amount'],
                fees=summary['total_fees_amount'],
                stamp=summary['total_stamp_duty_amount'],
                other=summary['total_other_taxes_amount'],
                deductions=summary['total_deductions_amount'],
                gross=summary['total_gross_value'],
            )]
        return []

    def _l10n_gr_edi_build_fetch_note(self, inv, _partner_report, report):
        header = inv['header']
        invoice_type = header['invoice_type']
        # The selection labels already carry the code ("2.1 - Service Rendered
        # Invoice"); fall back to the bare code for anything unrecognised.
        source_lines = [
            _('MARK: %s', inv['mark']),
            _('Invoice type: %s',
              dict(INVOICE_TYPES_SELECTION).get(invoice_type) or invoice_type),
            _('Reference: %(series)s/%(aa)s',
              series=header['series'] or '0', aa=header['aa'] or ''),
            _('Issue date: %s', header['issue_date']),
            _('Source lines: %s', len(inv['lines'])),
        ]
        if summary := inv.get('summary'):
            source_lines.append(_(
                'Declared totals: net %(net).2f, VAT %(vat).2f, gross %(gross).2f',
                net=summary['total_net_value'],
                vat=summary['total_vat_amount'],
                gross=summary['total_gross_value']))

        def section(title, items):
            body = Markup('<br/>').join(escape(item) for item in items)
            return Markup('<b>%s</b><br/>%s') % (title, body)

        parts = [section(_('Fetched from myDATA'), source_lines)]
        qr_code_url = inv['qr_code_url']
        if qr_code_url and qr_code_url.startswith(('http://', 'https://')):
            parts[0] += Markup('<br/><a href="%s">%s</a>') % (
                qr_code_url, _('View on myDATA'))
        if report['warnings']:
            parts.append(section(_('Warnings'), report['warnings']))
        return Markup('<br/><br/>').join(parts)

    # ------------------------------------------------------------------
    # Cancellations
    # ------------------------------------------------------------------

    def _l10n_gr_edi_process_cancellations(self, cancellations):
        """Handle issuer-side cancellations: cancel untouched drafts, flag the rest."""
        self.ensure_one()
        Move = self.env['account.move'].sudo()
        for cancellation in cancellations:
            if not cancellation['invoice_mark']:
                # Searching an empty MARK normalises to `= False` and would
                # match an arbitrary markless bill - fail closed instead.
                _logger.warning(
                    'myDATA fetch: cancellation without invoice MARK ignored '
                    'company_id=%s cancellation_mark=%s',
                    self.id, cancellation.get('cancellation_mark'))
                continue
            move = Move.search([
                ('l10n_gr_edi_mark', '=', cancellation['invoice_mark']),
                ('company_id', '=', self.id),
                ('move_type', 'in', ('in_invoice', 'in_refund')),
            ], limit=1)
            if not move:
                _logger.debug(
                    'myDATA fetch: cancellation skipped company_id=%s '
                    'invoice_mark=%s reason=unknown',
                    self.id, cancellation['invoice_mark'])
                continue
            if move.state == 'cancel':
                continue  # idempotent on refetch
            if (move.state == 'draft' and not move.posted_before
                    and not move.is_manually_modified
                    and move.l10n_gr_edi_state == 'bill_fetched'):
                move.button_cancel()
                move.message_post(
                    body=_('Invoice cancelled by the issuer on myDATA '
                           '(cancellation MARK %(cmark)s, date %(date)s). '
                           'This draft bill has been cancelled automatically.',
                           cmark=cancellation['cancellation_mark'],
                           date=cancellation['cancellation_date']),
                    subtype_xmlid='mail.mt_note')
            else:
                cancellation_mark = cancellation['cancellation_mark']
                if cancellation_mark and any(
                        cancellation_mark in (message.body or '')
                        for message in move.message_ids):
                    continue  # already flagged on an earlier run
                note = _('Invoice cancelled by the issuer on myDATA '
                         '(cancellation MARK %(cmark)s, date %(date)s). '
                         'Manual review/reversal required.',
                         cmark=cancellation['cancellation_mark'],
                         date=cancellation['cancellation_date'])
                move.message_post(body=note, subtype_xmlid='mail.mt_note')
                move.activity_schedule(
                    'mail.mail_activity_data_todo',
                    summary=_('myDATA: vendor invoice cancelled by issuer'),
                    note=note,
                    user_id=(move.invoice_user_id or move.create_uid).id,
                )

    # ------------------------------------------------------------------
    # HTTP fetch + orchestrator
    # ------------------------------------------------------------------

    def _l10n_gr_edi_fetch_docs(self, session):
        """Fetch all RequestDocs pages for this company. Returns merged dict:
        {'invoices': [...], 'cancellations': [...], 'max_mark': int}."""
        self.ensure_one()
        url = FETCH_URL_DEV if self.l10n_gr_edi_test_env else FETCH_URL_PROD
        try:
            timeout = int(self.env['ir.config_parameter'].sudo().get_param(
                'l10n_gr_edi.fetch_timeout', FETCH_DEFAULT_TIMEOUT))
        except (ValueError, TypeError):
            timeout = FETCH_DEFAULT_TIMEOUT
        headers = {
            'aade-user-id': self.l10n_gr_edi_aade_id,
            'ocp-apim-subscription-key': self.l10n_gr_edi_aade_key,
        }
        watermark = self.sudo().l10n_gr_edi_fetch_mark
        params = {'mark': watermark or 0}
        if not watermark:
            params['dateFrom'] = (
                fields.Datetime.now() - timedelta(days=90)).strftime('%d/%m/%Y')
            params['dateTo'] = fields.Datetime.now().strftime('%d/%m/%Y')
        result = {'invoices': [], 'cancellations': [], 'max_mark': 0}
        for _page in range(FETCH_MAX_PAGES):
            response = session.get(url, headers=headers, params=params, timeout=timeout)
            response.raise_for_status()
            parsed = self._l10n_gr_edi_parse_requested_docs(response.content)
            result['invoices'] += parsed['invoices']
            result['cancellations'] += parsed['cancellations']
            result['max_mark'] = max(result['max_mark'], parsed['max_mark'])
            if not parsed['continuation']:
                return result
            params = dict(params, **parsed['continuation'])
        raise ValueError(_(
            'myDATA RequestDocs page limit (%s) reached; the batch was not processed.',
            FETCH_MAX_PAGES))

    def _l10n_gr_edi_create_bill(self, inv):
        """Create one draft vendor bill (+ document + chatter note) from an
        invoice dict. Returns ``(move, warning_count)``; ``move`` is empty when
        the source invoice is skipped."""
        self.ensure_one()
        Move = self.env['account.move'].sudo()
        if inv['header']['invoice_type'] in NON_FISCAL_INVOICE_TYPES:
            _logger.debug(
                'myDATA fetch: invoice skipped company_id=%s mark=%s '
                'reason=non_fiscal_type invoice_type=%s',
                self.id, inv['mark'], inv['header']['invoice_type'])
            return Move.browse(), 0
        if inv['cancelled_by_mark']:
            _logger.debug(
                'myDATA fetch: invoice skipped company_id=%s mark=%s '
                'reason=pre_cancelled cancellation_mark=%s',
                self.id, inv['mark'], inv['cancelled_by_mark'])
            return Move.browse(), 0
        if Move.search_count([
            ('l10n_gr_edi_mark', '=', inv['mark']),
            ('company_id', '=', self.id),
        ], limit=1):
            _logger.debug(
                'myDATA fetch: invoice skipped company_id=%s mark=%s reason=duplicate',
                self.id, inv['mark'])
            return Move.browse(), 0
        partner, partner_report = self._l10n_gr_edi_resolve_partner(inv['issuer'])
        vals, report = self._l10n_gr_edi_prepare_bill_vals(inv, partner)
        move = Move.create(vals)
        self.env['l10n_gr_edi.document'].sudo().create({
            'state': 'bill_fetched',
            'move_id': move.id,
            'mydata_mark': inv['mark'],
        })
        report['warnings'] += self._l10n_gr_edi_check_summary(move, inv)
        move.message_post(
            body=self._l10n_gr_edi_build_fetch_note(inv, partner_report, report),
            subtype_xmlid='mail.mt_note')
        warning_count = len(report['warnings'])
        if warning_count:
            _logger.warning(
                'myDATA fetch: bill requires review company_id=%s mark=%s '
                'move_id=%s warnings=%s',
                self.id, inv['mark'], move.id, warning_count)
        return move, warning_count

    @api.model
    def _cron_l10n_gr_edi_fetch_invoices(self):
        """Receive issued myDATA invoices and create draft vendor bills."""
        gr_companies = self.env['res.company'].search([
            ('l10n_gr_edi_aade_id', '!=', False),
            ('l10n_gr_edi_aade_key', '!=', False),
        ])
        with http_requests.Session() as session:
            for company in gr_companies:
                try:
                    docs = company._l10n_gr_edi_fetch_docs(session)
                except (http_requests.RequestException, etree.XMLSyntaxError, ValueError) as error:
                    _logger.error(
                        'myDATA fetch: company run failed company_id=%s '
                        'error_type=%s error=%s',
                        company.id, type(error).__name__, error)
                    continue
                except Exception as error:  # noqa: BLE001 - isolate companies
                    _logger.exception(
                        'myDATA fetch: unexpected company fetch failure '
                        'company_id=%s error_type=%s',
                        company.id, type(error).__name__)
                    continue
                failed = False
                created_count = 0
                skipped_count = 0
                warning_count = 0
                for inv in docs['invoices']:
                    try:
                        with self.env.cr.savepoint():
                            move, bill_warning_count = (
                                company._l10n_gr_edi_create_bill(inv))
                        created_count += bool(move)
                        skipped_count += not move
                        warning_count += bill_warning_count
                    except Exception as error:  # noqa: BLE001 - isolate per-invoice failures
                        failed = True
                        _logger.exception(
                            'myDATA fetch: bill creation failed company_id=%s '
                            'mark=%s error_type=%s',
                            company.id, inv.get('mark'), type(error).__name__)
                try:
                    with self.env.cr.savepoint():
                        company._l10n_gr_edi_process_cancellations(docs['cancellations'])
                except Exception as error:  # noqa: BLE001 - isolate per-company failures
                    failed = True
                    _logger.exception(
                        'myDATA fetch: cancellation processing failed company_id=%s '
                        'error_type=%s',
                        company.id, type(error).__name__)
                company_sudo = company.sudo()
                old_mark = int(company_sudo.l10n_gr_edi_fetch_mark or 0)
                if (
                    not failed
                    and docs['max_mark'] > old_mark
                ):
                    company_sudo.l10n_gr_edi_fetch_mark = str(docs['max_mark'])
                _logger.info(
                    'myDATA fetch: company run complete company_id=%s fetched=%s '
                    'created=%s skipped=%s cancellations=%s failures=%s warnings=%s '
                    'watermark_from=%s watermark_to=%s',
                    company.id, len(docs['invoices']), created_count, skipped_count,
                    len(docs['cancellations']), int(failed), warning_count,
                    old_mark, int(company_sudo.l10n_gr_edi_fetch_mark or 0))
