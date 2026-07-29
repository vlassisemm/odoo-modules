# Part of Odoo. See LICENSE file for full copyright and licensing details.
import logging

import requests as http_requests
from lxml import etree
from markupsafe import Markup, escape

from odoo import _, api, fields, models, Command
from odoo.tools import float_compare
from odoo.addons.l10n_gr_edi.models.preferred_classification import (
    INVOICE_TYPES_HAVE_EXPENSE,
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

PAYMENT_METHOD_LABELS = {
    1: 'Domestic business payment account',
    2: 'Foreign business payment account',
    3: 'Cash',
    4: 'Cheque',
    5: 'On credit',
    6: 'Web banking',
    7: 'POS / e-POS',
    8: 'IRIS instant payment',
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
        return self.env['account.tax'].search([
            ('company_id', '=', self.id),
            ('type_tax_use', '=', 'purchase'),
            ('amount_type', '=', 'percent'),
            ('amount', '=', percent),
        ], limit=1)

    def _l10n_gr_edi_prepare_line_vals(self, inv):
        """Map payload lines to account.move.line create-commands.
        Invariant: every payload amount lands in a matched tax or an explicit
        fallback line, so the bill total always reconciles with the payload."""
        self.ensure_one()
        commands = []
        report = {'lines': [], 'warnings': []}
        for line in inv['lines']:
            number = line['line_number']
            sign = -1.0 if line['rec_type'] == 7 else 1.0
            descr = line['item_descr'] or line['line_comments']
            if not descr:
                if line['rec_type'] == 2:
                    descr = _('Fees')
                elif line['rec_type'] == 3:
                    descr = _('Other taxes')
                else:
                    descr = _('myDATA line %s', number)
            if line['item_code']:
                descr = f"[{line['item_code']}] {descr}"

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
                vat_tax = self._l10n_gr_edi_match_purchase_tax(vat_pct)
                if vat_tax:
                    tax_ids.append(vat_tax.id)
                    report['lines'].append(_(
                        'Line %(n)s: VAT %(pct)s%% mapped to tax "%(tax)s".',
                        n=number, pct=vat_pct, tax=vat_tax.name))
                elif line['vat_amount']:
                    commands.append(Command.create({
                        'name': _('VAT %(pct)s%% (no matching purchase tax) — line %(n)s',
                                  pct=vat_pct, n=number),
                        'quantity': 1.0,
                        'price_unit': sign * line['vat_amount'],
                        'tax_ids': [Command.set([])],
                    }))
                    report['warnings'].append(_(
                        'Line %(n)s: no %(pct)s%% purchase tax found — '
                        'VAT amount added as a separate line.', n=number, pct=vat_pct))
                if line['vat_exemption_category']:
                    label = dict(TAX_EXEMPTION_CATEGORY_SELECTION).get(
                        str(line['vat_exemption_category']),
                        str(line['vat_exemption_category']))
                    report['lines'].append(_(
                        'Line %(n)s: VAT exemption category — %(label)s.',
                        n=number, label=label))

            for amount_key, category_key, table, label_key, charge_sign in LINE_CHARGES:
                amount = line.get(amount_key)
                if not amount:
                    continue
                label = self._l10n_gr_edi_charge_label(label_key)
                category = line.get(category_key) if category_key else None
                pct = table.get(category) if (table and category) else None
                charge_tax = (self._l10n_gr_edi_match_purchase_tax(charge_sign * pct)
                              if pct else self.env['account.tax'])
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
                    }))
                    report['warnings'].append(_(
                        'Line %(n)s: %(label)s %(amount).2f added as a separate line '
                        '(no matching tax).', n=number, label=label, amount=amount))

            commands.append(Command.create({
                'name': descr,
                'quantity': quantity,
                'price_unit': sign * price_unit,
                'tax_ids': [Command.set(tax_ids)],
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
        commands, report = self._l10n_gr_edi_prepare_line_vals(inv)
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
            'l10n_gr_edi_is_fetched': True,
        }
        if header['invoice_type'] in INVOICE_TYPES_HAVE_EXPENSE:
            vals['l10n_gr_edi_inv_type'] = header['invoice_type']
        currency_code = header['currency']
        if currency_code and currency_code != 'EUR':
            currency = self.env['res.currency'].search(
                [('name', '=', currency_code)], limit=1)
            if currency:
                vals['currency_id'] = currency.id
                if header['exchange_rate']:
                    report['lines'].append(_(
                        'Document currency %(code)s (exchange rate to EUR: %(rate)s).',
                        code=currency_code, rate=header['exchange_rate']))
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

    def _l10n_gr_edi_build_fetch_note(self, inv, partner_report, report):
        header = inv['header']
        source_lines = [
            _('MARK: %s', inv['mark']),
            _('Invoice type: %s', header['invoice_type']),
            _('Reference: %(series)s/%(aa)s',
              series=header['series'] or '0', aa=header['aa'] or ''),
        ]
        if inv['uid']:
            source_lines.append(_('UID: %s', inv['uid']))
        payment_lines = [
            _('%(label)s: %(amount).2f%(info)s',
              label=PAYMENT_METHOD_LABELS.get(pm['type'], pm['type']),
              amount=pm['amount'],
              info=f" ({pm['info']})" if pm['info'] else '')
            for pm in inv['payment_methods']
        ]

        def section(title, items):
            body = Markup('<br/>').join(escape(item) for item in items)
            return Markup('<b>%s</b><br/>%s') % (title, body)

        parts = [section(_('Fetched from myDATA'), source_lines)]
        if inv['qr_code_url']:
            parts[0] += Markup('<br/><a href="%s">%s</a>') % (
                inv['qr_code_url'], _('View on myDATA'))
        if partner_report:
            parts.append(section(_('Partner'), partner_report))
        if report['lines']:
            parts.append(section(_('Mapping'), report['lines']))
        if payment_lines:
            parts.append(section(_('Payment'), payment_lines))
        if report['warnings']:
            parts.append(section(_('Warnings'), report['warnings']))
        return Markup('<br/><br/>').join(parts)
