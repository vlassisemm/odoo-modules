# Part of Odoo. See LICENSE file for full copyright and licensing details.
from unittest.mock import patch

import requests

from odoo import Command, _
from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.addons.l10n_gr_edi.models.res_company_fetch import FETCH_DEFAULT_TIMEOUT
from odoo.tests import tagged


def _requested_doc(body):
    """Wrap invoice/cancellation XML in a RequestedDoc envelope, return bytes."""
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<RequestedDoc xmlns="http://www.aade.gr/myDATA/requestedDoc/v1.0" '
        'xmlns:inv="http://www.aade.gr/myDATA/invoice/v1.0">'
        f'{body}</RequestedDoc>'
    ).encode('utf-8')


def _invoice_xml(
        mark='400001234567890',
        vat='123456789',
        country='GR',
        issuer_extra='',
        series='A',
        aa='101',
        issue_date='2026-07-01',
        inv_type='2.1',
        header_extra='',
        lines_xml=None,
        payments_xml='',
        taxes_totals_xml='',
        summary_xml=None,
        invoice_extra='',
):
    if lines_xml is None:
        lines_xml = (
            '<inv:invoiceDetails><inv:lineNumber>1</inv:lineNumber>'
            '<inv:netValue>1000.00</inv:netValue><inv:vatCategory>1</inv:vatCategory>'
            '<inv:vatAmount>240.00</inv:vatAmount></inv:invoiceDetails>'
        )
    if summary_xml is None:
        summary_xml = (
            '<inv:invoiceSummary><inv:totalNetValue>1000.00</inv:totalNetValue>'
            '<inv:totalVatAmount>240.00</inv:totalVatAmount>'
            '<inv:totalWithheldAmount>0.00</inv:totalWithheldAmount>'
            '<inv:totalFeesAmount>0.00</inv:totalFeesAmount>'
            '<inv:totalStampDutyamount>0.00</inv:totalStampDutyamount>'
            '<inv:totalOtherTaxesAmount>0.00</inv:totalOtherTaxesAmount>'
            '<inv:totalDeductionsAmount>0.00</inv:totalDeductionsAmount>'
            '<inv:totalGrossValue>1240.00</inv:totalGrossValue></inv:invoiceSummary>'
        )
    return (
        '<invoicesDoc><inv:invoice>'
        f'<inv:mark>{mark}</inv:mark>{invoice_extra}'
        f'<inv:issuer><inv:vatNumber>{vat}</inv:vatNumber>'
        f'<inv:country>{country}</inv:country><inv:branch>0</inv:branch>'
        f'{issuer_extra}</inv:issuer>'
        f'<inv:invoiceHeader><inv:series>{series}</inv:series><inv:aa>{aa}</inv:aa>'
        f'<inv:issueDate>{issue_date}</inv:issueDate>'
        f'<inv:invoiceType>{inv_type}</inv:invoiceType>{header_extra}</inv:invoiceHeader>'
        f'{payments_xml}{lines_xml}{taxes_totals_xml}{summary_xml}'
        '</inv:invoice></invoicesDoc>'
    )


FULL_LINE_XML = (
    '<inv:invoiceDetails><inv:lineNumber>1</inv:lineNumber>'
    '<inv:quantity>2.0</inv:quantity>'
    '<inv:netValue>1000.00</inv:netValue><inv:vatCategory>1</inv:vatCategory>'
    '<inv:vatAmount>240.00</inv:vatAmount>'
    '<inv:withheldAmount>200.00</inv:withheldAmount>'
    '<inv:withheldPercentCategory>3</inv:withheldPercentCategory>'
    '<inv:stampDutyAmount>12.00</inv:stampDutyAmount>'
    '<inv:stampDutyPercentCategory>1</inv:stampDutyPercentCategory>'
    '<inv:lineComments>Consulting July</inv:lineComments>'
    '<inv:itemDescr>Consulting services</inv:itemDescr>'
    '<inv:itemCode>SRV-01</inv:itemCode>'
    '</inv:invoiceDetails>'
)


@tagged('post_install_l10n', 'post_install', '-at_install')
class TestMyDataFetchCommon(AccountTestInvoicingCommon):

    @classmethod
    @AccountTestInvoicingCommon.setup_country('gr')
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.company.write({
            'vat': '047747270',
            'l10n_gr_edi_test_env': True,
            'l10n_gr_edi_aade_id': 'testid',
            'l10n_gr_edi_aade_key': 'testkey',
        })

    @classmethod
    def _find_or_create_purchase_tax(cls, amount):
        tax = cls.env['account.tax'].search([
            ('company_id', '=', cls.company.id),
            ('type_tax_use', '=', 'purchase'),
            ('amount_type', '=', 'percent'),
            ('amount', '=', amount),
        ], limit=1)
        return tax or cls.env['account.tax'].create({
            'name': f'Purchase {amount}%',
            'amount': amount,
            'amount_type': 'percent',
            'type_tax_use': 'purchase',
            'company_id': cls.company.id,
        })


class TestMyDataFetchParse(TestMyDataFetchCommon):

    def test_parse_full_invoice(self):
        content = _requested_doc(_invoice_xml(
            lines_xml=FULL_LINE_XML,
            header_extra='<inv:currency>EUR</inv:currency>',
            payments_xml=(
                '<inv:paymentMethods><inv:paymentMethodDetails>'
                '<inv:type>5</inv:type><inv:amount>1052.00</inv:amount>'
                '<inv:paymentMethodInfo>Net 30</inv:paymentMethodInfo>'
                '</inv:paymentMethodDetails></inv:paymentMethods>'
            ),
        ))
        result = self.company._l10n_gr_edi_parse_requested_docs(content)
        self.assertEqual(len(result['invoices']), 1)
        inv = result['invoices'][0]
        self.assertEqual(inv['mark'], '400001234567890')
        self.assertEqual(inv['issuer']['vat'], '123456789')
        self.assertEqual(inv['issuer']['country'], 'GR')
        self.assertEqual(inv['header']['series'], 'A')
        self.assertEqual(inv['header']['aa'], '101')
        self.assertEqual(inv['header']['invoice_type'], '2.1')
        self.assertEqual(inv['header']['currency'], 'EUR')
        line = inv['lines'][0]
        self.assertEqual(line['net_value'], 1000.0)
        self.assertEqual(line['vat_category'], 1)
        self.assertEqual(line['vat_amount'], 240.0)
        self.assertEqual(line['quantity'], 2.0)
        self.assertEqual(line['item_descr'], 'Consulting services')
        self.assertEqual(line['line_comments'], 'Consulting July')
        self.assertEqual(line['item_code'], 'SRV-01')
        self.assertEqual(line['withheld_amount'], 200.0)
        self.assertEqual(line['withheld_percent_category'], 3)
        self.assertEqual(line['stamp_duty_amount'], 12.0)
        self.assertEqual(inv['payment_methods'], [{'type': 5, 'amount': 1052.0, 'info': 'Net 30'}])
        self.assertEqual(inv['summary']['total_net_value'], 1000.0)
        self.assertEqual(inv['summary']['total_gross_value'], 1240.0)
        self.assertEqual(result['max_mark'], 400001234567890)
        self.assertIsNone(result['continuation'])
        self.assertEqual(result['cancellations'], [])

    def test_parse_cancellations_and_continuation(self):
        content = _requested_doc(
            _invoice_xml()
            + '<cancelledInvoicesDoc>'
            '<invoiceMark>400001234567800</invoiceMark>'
            '<cancellationMark>400009999999999</cancellationMark>'
            '<cancellationDate>2026-07-10</cancellationDate>'
            '</cancelledInvoicesDoc>'
            + '<continuationToken>'
            '<nextPartitionKey>PK1</nextPartitionKey><nextRowKey>RK1</nextRowKey>'
            '</continuationToken>'
        )
        result = self.company._l10n_gr_edi_parse_requested_docs(content)
        self.assertEqual(result['cancellations'], [{
            'invoice_mark': '400001234567800',
            'cancellation_mark': '400009999999999',
            'cancellation_date': '2026-07-10',
        }])
        self.assertEqual(result['continuation'],
                         {'nextPartitionKey': 'PK1', 'nextRowKey': 'RK1'})
        # cancellation mark is larger than the invoice mark
        self.assertEqual(result['max_mark'], 400009999999999)

    def test_parse_foreign_issuer_and_credit_header(self):
        content = _requested_doc(_invoice_xml(
            vat='DE811907980', country='DE',
            issuer_extra=(
                '<inv:name>ACME GmbH</inv:name>'
                '<inv:address><inv:street>Hauptstr.</inv:street><inv:number>5</inv:number>'
                '<inv:postalCode>10115</inv:postalCode><inv:city>Berlin</inv:city></inv:address>'
            ),
            inv_type='5.1',
            header_extra='<inv:correlatedInvoices>400001111111111</inv:correlatedInvoices>',
        ))
        inv = self.company._l10n_gr_edi_parse_requested_docs(content)['invoices'][0]
        self.assertEqual(inv['issuer']['name'], 'ACME GmbH')
        self.assertEqual(inv['issuer']['street'], 'Hauptstr.')
        self.assertEqual(inv['issuer']['city'], 'Berlin')
        self.assertEqual(inv['header']['correlated_marks'], ['400001111111111'])


class TestMyDataFetchPartner(TestMyDataFetchCommon):

    def _issuer(self, **overrides):
        issuer = {'vat': '123456789', 'country': 'GR', 'branch': 0, 'name': None,
                  'street': None, 'number': None, 'postal_code': None, 'city': None}
        issuer.update(overrides)
        return issuer

    def test_match_existing_partner_el_prefix(self):
        existing = self.env['res.partner'].create({
            'name': 'Known Vendor', 'vat': 'EL123456789', 'is_company': True,
        })
        partner, report = self.company._l10n_gr_edi_resolve_partner(self._issuer())
        self.assertEqual(partner, existing)
        self.assertTrue(any('matched' in line.lower() for line in report))

    def test_match_existing_partner_bare_vat(self):
        existing = self.env['res.partner'].create({
            'name': 'Bare Vendor', 'vat': '123456789', 'is_company': True,
        })
        partner, _report = self.company._l10n_gr_edi_resolve_partner(self._issuer())
        self.assertEqual(partner, existing)

    def test_match_archived_partner(self):
        existing = self.env['res.partner'].create({
            'name': 'Archived Vendor', 'vat': 'EL123456789', 'active': False,
        })
        partner, _report = self.company._l10n_gr_edi_resolve_partner(self._issuer())
        self.assertEqual(partner, existing)

    def test_create_minimal_greek_partner_tagged(self):
        partner, report = self.company._l10n_gr_edi_resolve_partner(self._issuer())
        self.assertEqual(partner.vat, 'EL123456789')
        self.assertIn('123456789', partner.name)
        self.assertTrue(partner.is_company)
        self.assertEqual(partner.supplier_rank, 1)
        tag = self.env.ref('l10n_gr_edi.res_partner_category_mydata_auto')
        self.assertIn(tag, partner.category_id)

    def test_create_foreign_partner_with_address(self):
        issuer = self._issuer(
            vat='DE811907980', country='DE', name='ACME GmbH',
            street='Hauptstr.', number='5', postal_code='10115', city='Berlin')
        partner, _report = self.company._l10n_gr_edi_resolve_partner(issuer)
        self.assertEqual(partner.name, 'ACME GmbH')
        self.assertEqual(partner.vat, 'DE811907980')
        self.assertEqual(partner.street, 'Hauptstr. 5')
        self.assertEqual(partner.zip, '10115')
        self.assertEqual(partner.city, 'Berlin')
        self.assertEqual(partner.country_id.code, 'DE')

    def test_enrichment_applied_when_afm_installed(self):
        Partner = self.env['res.partner']
        if not hasattr(Partner, '_l10n_gr_afm_call_aade'):
            self.skipTest('l10n_gr_afm not installed in this registry')
        self.company.sudo().write({
            'l10n_gr_afm_aade_username': 'u', 'l10n_gr_afm_aade_password': 'p',
        })
        with patch.object(
            Partner.__class__, '_l10n_gr_afm_call_aade',
            return_value={'afm_name': 'REAL NAME SA', 'afm_street': 'Stadiou 1',
                          'afm_zip': '10559', 'afm_city': 'Athens'},
        ):
            partner, report = self.company._l10n_gr_edi_resolve_partner(self._issuer())
        self.assertEqual(partner.name, 'REAL NAME SA')
        self.assertEqual(partner.street, 'Stadiou 1')
        self.assertTrue(any('enriched' in line.lower() for line in report))

    def test_enrichment_skipped_without_credentials(self):
        # Works whether or not l10n_gr_afm is installed: without credentials
        # (or without the module) the partner must stay minimal, creation must succeed.
        partner, report = self.company._l10n_gr_edi_resolve_partner(self._issuer())
        self.assertIn('123456789', partner.name)
        self.assertTrue(any('skipped' in line.lower() or 'not installed' in line.lower()
                            for line in report))


class TestMyDataFetchLines(TestMyDataFetchCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.tax_p24 = cls._find_or_create_purchase_tax(24.0)
        cls.withholding_20 = cls._find_or_create_purchase_tax(-20.0)

    def _line(self, **overrides):
        line = {
            'line_number': 1, 'rec_type': None, 'quantity': None, 'net_value': 1000.0,
            'vat_category': 1, 'vat_amount': 240.0, 'vat_exemption_category': None,
            'item_descr': None, 'item_code': None, 'line_comments': None,
            'withheld_amount': None, 'withheld_percent_category': None,
            'fees_amount': None, 'fees_percent_category': None,
            'stamp_duty_amount': None, 'stamp_duty_percent_category': None,
            'other_taxes_amount': None, 'other_taxes_percent_category': None,
            'deductions_amount': None,
        }
        line.update(overrides)
        return line

    def _inv(self, lines=None, taxes_totals=None):
        return {'lines': lines or [self._line()], 'taxes_totals': taxes_totals or []}

    def _created_vals(self, commands):
        return [cmd[2] for cmd in commands]

    def test_description_chain_and_item_code(self):
        commands, _report = self.company._l10n_gr_edi_prepare_line_vals(self._inv([
            self._line(item_descr='Widget', item_code='W-1'),
            self._line(line_number=2, line_comments='Comment only'),
            self._line(line_number=3),
        ]))
        names = [vals['name'] for vals in self._created_vals(commands)]
        self.assertEqual(names[0], '[W-1] Widget')
        self.assertEqual(names[1], 'Comment only')
        self.assertIn('3', names[2])  # generic "myDATA line 3" fallback

    def test_vat_tax_matched(self):
        commands, report = self.company._l10n_gr_edi_prepare_line_vals(self._inv())
        vals = self._created_vals(commands)[0]
        self.assertEqual(vals['tax_ids'], [Command.set([self.tax_p24.id])])
        self.assertEqual(vals['price_unit'], 1000.0)
        self.assertFalse(report['warnings'])

    def test_vat_category_9_and_10_no_crash(self):
        tax_p3 = self._find_or_create_purchase_tax(3.0)
        commands, _report = self.company._l10n_gr_edi_prepare_line_vals(self._inv([
            self._line(vat_category=9, vat_amount=30.0),
            self._line(line_number=2, vat_category=10, vat_amount=40.0),
        ]))
        vals = self._created_vals(commands)
        self.assertEqual(vals[0]['tax_ids'], [Command.set([tax_p3.id])])

    def test_vat_fallback_line_when_no_tax(self):
        # 17% (Aegean islands rate) purchase tax does not exist in the CoA
        self.env['account.tax'].search([
            ('company_id', '=', self.company.id), ('amount', '=', 17.0),
            ('type_tax_use', '=', 'purchase'),
        ]).unlink()
        commands, report = self.company._l10n_gr_edi_prepare_line_vals(self._inv([
            self._line(vat_category=4, vat_amount=170.0),
        ]))
        vals = self._created_vals(commands)
        self.assertEqual(len(vals), 2)
        fallback = [v for v in vals if 'VAT' in v['name']][0]
        self.assertEqual(fallback['price_unit'], 170.0)
        self.assertTrue(report['warnings'])

    def test_withholding_matched_to_negative_tax(self):
        commands, report = self.company._l10n_gr_edi_prepare_line_vals(self._inv([
            self._line(withheld_amount=200.0, withheld_percent_category=3),  # 20%
        ]))
        vals = self._created_vals(commands)
        self.assertEqual(len(vals), 1)
        self.assertEqual(vals[0]['tax_ids'],
                         [Command.set([self.tax_p24.id, self.withholding_20.id])])

    def test_withholding_fallback_line_when_amount_based(self):
        commands, report = self.company._l10n_gr_edi_prepare_line_vals(self._inv([
            self._line(withheld_amount=50.0, withheld_percent_category=17),  # amount-based
        ]))
        vals = self._created_vals(commands)
        self.assertEqual(len(vals), 2)
        fallback = [v for v in vals if v['price_unit'] < 0][0]
        self.assertEqual(fallback['price_unit'], -50.0)
        self.assertTrue(report['warnings'])

    def test_stamp_duty_fallback_positive(self):
        # no 1.2% purchase tax in CoA -> explicit positive line
        commands, report = self.company._l10n_gr_edi_prepare_line_vals(self._inv([
            self._line(stamp_duty_amount=12.0, stamp_duty_percent_category=1),
        ]))
        vals = self._created_vals(commands)
        fallback = [v for v in vals if 'tamp' in v['name']]
        self.assertEqual(len(fallback), 1)
        self.assertEqual(fallback[0]['price_unit'], 12.0)
        self.assertTrue(report['warnings'])

    def test_quantity_kept_when_division_exact(self):
        commands, _report = self.company._l10n_gr_edi_prepare_line_vals(self._inv([
            self._line(quantity=4.0, net_value=100.0),
        ]))
        vals = self._created_vals(commands)[0]
        self.assertEqual(vals['quantity'], 4.0)
        self.assertEqual(vals['price_unit'], 25.0)

    def test_quantity_collapsed_when_division_drifts(self):
        commands, _report = self.company._l10n_gr_edi_prepare_line_vals(self._inv([
            self._line(quantity=3.0, net_value=100.0),
        ]))
        vals = self._created_vals(commands)[0]
        self.assertEqual(vals['quantity'], 1.0)
        self.assertEqual(vals['price_unit'], 100.0)
        self.assertIn('3', vals['name'])

    def test_taxes_totals_document_level_lines(self):
        commands, report = self.company._l10n_gr_edi_prepare_line_vals(self._inv(
            taxes_totals=[
                {'tax_type': 1, 'tax_category': 3, 'underlying_value': 1000.0, 'tax_amount': 200.0},
                {'tax_type': 4, 'tax_category': 1, 'underlying_value': None, 'tax_amount': 12.0},
            ],
        ))
        vals = self._created_vals(commands)
        self.assertEqual(len(vals), 3)  # 1 product line + 2 doc-level charge lines
        self.assertEqual(vals[1]['price_unit'], -200.0)  # withheld: negative
        self.assertEqual(vals[2]['price_unit'], 12.0)    # stamp duty: positive
        self.assertEqual(len(report['warnings']), 2)

    def test_rec_type_7_negates(self):
        commands, _report = self.company._l10n_gr_edi_prepare_line_vals(self._inv([
            self._line(rec_type=7, net_value=100.0, vat_category=8, vat_amount=0.0),
        ]))
        vals = self._created_vals(commands)[0]
        self.assertEqual(vals['price_unit'], -100.0)

    def test_vat_category_8_no_tax(self):
        commands, report = self.company._l10n_gr_edi_prepare_line_vals(self._inv([
            self._line(vat_category=8, vat_amount=0.0),
        ]))
        vals = self._created_vals(commands)[0]
        self.assertEqual(vals['tax_ids'], [Command.set([])])
        self.assertFalse(report['warnings'])


class TestMyDataFetchBillVals(TestMyDataFetchCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner = cls.env['res.partner'].create({
            'name': 'Vendor X', 'vat': 'EL123456789', 'is_company': True,
        })

    def _inv(self, **kw):
        content = _requested_doc(_invoice_xml(**kw))
        return self.company._l10n_gr_edi_parse_requested_docs(content)['invoices'][0]

    def test_header_basics(self):
        vals, _report = self.company._l10n_gr_edi_prepare_bill_vals(self._inv(), self.partner)
        self.assertEqual(vals['move_type'], 'in_invoice')
        self.assertEqual(vals['ref'], 'A/101')
        self.assertEqual(str(vals['invoice_date']), '2026-07-01')
        self.assertEqual(vals['partner_id'], self.partner.id)
        self.assertEqual(vals['company_id'], self.company.id)
        self.assertTrue(vals['l10n_gr_edi_is_fetched'])
        self.assertEqual(vals['l10n_gr_edi_inv_type'], '2.1')

    def test_series_zero_collapses_ref(self):
        vals, _report = self.company._l10n_gr_edi_prepare_bill_vals(
            self._inv(series='0'), self.partner)
        self.assertEqual(vals['ref'], '101')

    def test_credit_invoice_becomes_refund_and_links_original(self):
        original = self.env['account.move'].create({
            'move_type': 'in_invoice', 'partner_id': self.partner.id,
            'invoice_date': '2026-06-01', 'company_id': self.company.id,
        })
        self.env['l10n_gr_edi.document'].create({
            'state': 'bill_fetched', 'move_id': original.id,
            'mydata_mark': '400001111111111',
        })
        vals, report = self.company._l10n_gr_edi_prepare_bill_vals(
            self._inv(inv_type='5.1',
                      header_extra='<inv:correlatedInvoices>400001111111111</inv:correlatedInvoices>'),
            self.partner)
        self.assertEqual(vals['move_type'], 'in_refund')
        self.assertEqual(vals['reversed_entry_id'], original.id)

    def test_orphan_correlated_mark_warns(self):
        vals, report = self.company._l10n_gr_edi_prepare_bill_vals(
            self._inv(inv_type='5.1',
                      header_extra='<inv:correlatedInvoices>400002222222222</inv:correlatedInvoices>'),
            self.partner)
        self.assertEqual(vals['move_type'], 'in_refund')
        self.assertNotIn('reversed_entry_id', vals)
        self.assertTrue(any('400002222222222' in w for w in report['warnings']))

    def test_foreign_currency_set(self):
        self.env['res.currency'].search([('name', '=', 'USD')]).active = True
        vals, _report = self.company._l10n_gr_edi_prepare_bill_vals(
            self._inv(header_extra='<inv:currency>USD</inv:currency>'
                                   '<inv:exchangeRate>1.08000</inv:exchangeRate>'),
            self.partner)
        usd = self.env['res.currency'].search([('name', '=', 'USD')])
        self.assertEqual(vals['currency_id'], usd.id)

    def test_unknown_currency_warns(self):
        vals, report = self.company._l10n_gr_edi_prepare_bill_vals(
            self._inv(header_extra='<inv:currency>XXX</inv:currency>'), self.partner)
        self.assertNotIn('currency_id', vals)
        self.assertTrue(any('XXX' in w for w in report['warnings']))


class TestMyDataFetchNote(TestMyDataFetchCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._find_or_create_purchase_tax(24.0)
        cls.partner = cls.env['res.partner'].create({
            'name': 'Vendor X', 'vat': 'EL123456789', 'is_company': True,
        })

    def _build_move(self, **kw):
        inv = self.company._l10n_gr_edi_parse_requested_docs(
            _requested_doc(_invoice_xml(**kw)))['invoices'][0]
        vals, report = self.company._l10n_gr_edi_prepare_bill_vals(inv, self.partner)
        move = self.env['account.move'].create(vals)
        return move, inv, report

    def test_summary_check_passes_on_consistent_totals(self):
        move, inv, _report = self._build_move()
        self.assertEqual(self.company._l10n_gr_edi_check_summary(move, inv), [])

    def test_summary_check_warns_on_mismatch(self):
        move, inv, _report = self._build_move()
        inv['summary']['total_gross_value'] = 9999.0
        inv['summary']['total_net_value'] = 9000.0
        warnings = self.company._l10n_gr_edi_check_summary(move, inv)
        self.assertEqual(len(warnings), 1)
        self.assertIn('9999.00', warnings[0])
        self.assertIn('9000.00', warnings[0])
        self.assertIn(f'{move.amount_total:.2f}', warnings[0])

    def test_note_contains_sections(self):
        move, inv, report = self._build_move(payments_xml=(
            '<inv:paymentMethods><inv:paymentMethodDetails>'
            '<inv:type>3</inv:type><inv:amount>1240.00</inv:amount>'
            '</inv:paymentMethodDetails></inv:paymentMethods>'
        ))
        note = self.company._l10n_gr_edi_build_fetch_note(
            inv, [_('Partner matched by VAT: Vendor X')], report)
        html = str(note)
        self.assertIn('400001234567890', html)   # MARK
        self.assertIn('2.1', html)               # invoice type
        self.assertIn('Vendor X', html)          # partner section
        self.assertIn('Cash', html)              # payment method label
        self.assertNotIn('Warning', html)        # no warnings section when clean

    def test_note_escapes_payload_html(self):
        move, inv, report = self._build_move()
        inv['header']['invoice_type'] = '<script>alert(1)</script>'
        note = self.company._l10n_gr_edi_build_fetch_note(inv, [], report)
        self.assertNotIn('<script>', str(note))

    def test_note_qr_link_rendered_for_http_url(self):
        move, inv, report = self._build_move(invoice_extra=(
            '<inv:qrCodeUrl>https://mydata.aade.gr/some/path</inv:qrCodeUrl>'
        ))
        note = self.company._l10n_gr_edi_build_fetch_note(inv, [], report)
        html = str(note)
        self.assertIn('<a href="https://mydata.aade.gr/some/path">', html)
        self.assertIn('View on myDATA', html)

    def test_note_qr_link_omitted_for_non_http_scheme(self):
        move, inv, report = self._build_move(invoice_extra=(
            '<inv:qrCodeUrl>javascript:alert(1)</inv:qrCodeUrl>'
        ))
        note = self.company._l10n_gr_edi_build_fetch_note(inv, [], report)
        html = str(note)
        self.assertNotIn('<a ', html)
        self.assertNotIn('javascript:alert(1)', html)


class TestMyDataFetchCancellations(TestMyDataFetchCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner = cls.env['res.partner'].create({
            'name': 'Vendor X', 'vat': 'EL123456789', 'is_company': True,
        })

    def _make_fetched_bill(self, mark):
        move = self.env['account.move'].create({
            'move_type': 'in_invoice', 'partner_id': self.partner.id,
            'invoice_date': '2026-07-01', 'company_id': self.company.id,
            'invoice_line_ids': [Command.create({
                'name': 'line', 'quantity': 1, 'price_unit': 100.0,
            })],
        })
        self.env['l10n_gr_edi.document'].create({
            'state': 'bill_fetched', 'move_id': move.id, 'mydata_mark': mark,
        })
        return move

    def _cancellation(self, mark):
        return [{'invoice_mark': mark, 'cancellation_mark': '400009999999999',
                 'cancellation_date': '2026-07-10'}]

    def test_draft_bill_is_cancelled(self):
        move = self._make_fetched_bill('400001234567001')
        self.company._l10n_gr_edi_process_cancellations(
            self._cancellation('400001234567001'))
        self.assertEqual(move.state, 'cancel')
        self.assertTrue(any('400009999999999' in (m.body or '')
                            for m in move.message_ids))

    def test_posted_bill_gets_activity_not_cancelled(self):
        move = self._make_fetched_bill('400001234567002')
        move.action_post()
        self.company._l10n_gr_edi_process_cancellations(
            self._cancellation('400001234567002'))
        self.assertEqual(move.state, 'posted')
        self.assertTrue(move.activity_ids)
        self.assertTrue(any('400009999999999' in (m.body or '')
                            for m in move.message_ids))

    def test_reset_to_draft_bill_gets_activity_not_cancelled(self):
        move = self._make_fetched_bill('400001234567004')
        move.action_post()
        move.button_draft()
        self.company._l10n_gr_edi_process_cancellations(
            self._cancellation('400001234567004'))
        self.assertEqual(move.state, 'draft')
        self.assertTrue(move.activity_ids)

    def test_unknown_mark_is_ignored(self):
        # must not raise
        self.company._l10n_gr_edi_process_cancellations(
            self._cancellation('400000000000000'))

    def test_already_cancelled_is_idempotent(self):
        move = self._make_fetched_bill('400001234567003')
        self.company._l10n_gr_edi_process_cancellations(
            self._cancellation('400001234567003'))
        message_count = len(move.message_ids)
        self.company._l10n_gr_edi_process_cancellations(
            self._cancellation('400001234567003'))
        self.assertEqual(len(move.message_ids), message_count)


class _FakeResponse:
    def __init__(self, content):
        self.content = content

    def raise_for_status(self):
        pass


class TestMyDataFetchFlow(TestMyDataFetchCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._find_or_create_purchase_tax(24.0)

    def _run_cron_with_pages(self, pages):
        """Run the cron with mocked HTTP GETs returning the given XML bodies."""
        responses = [_FakeResponse(p) for p in pages]
        calls = []

        def fake_get(url, **kwargs):
            calls.append(kwargs)
            return responses.pop(0)

        with patch(
            'odoo.addons.l10n_gr_edi.models.res_company_fetch.http_requests.Session'
        ) as session_cls:
            # Session is used as a context manager (`with Session() as session:`);
            # make the mock's `__enter__` return the same object so `.get` is reachable.
            session_cls.return_value.__enter__.return_value = session_cls.return_value
            session_cls.return_value.__exit__.return_value = False
            session_cls.return_value.get.side_effect = fake_get
            self.env['res.company']._cron_l10n_gr_edi_fetch_invoices()
        return calls

    def test_cron_creates_bill_with_document_note_and_watermark(self):
        self._run_cron_with_pages([_requested_doc(_invoice_xml())])
        move = self.env['account.move'].search([
            ('l10n_gr_edi_mark', '=', '400001234567890'),
            ('company_id', '=', self.company.id),
        ])
        self.assertEqual(len(move), 1)
        self.assertEqual(move.state, 'draft')
        self.assertEqual(move.move_type, 'in_invoice')
        self.assertEqual(move.ref, 'A/101')
        self.assertTrue(move.l10n_gr_edi_is_fetched)
        self.assertEqual(move.l10n_gr_edi_state, 'bill_fetched')
        self.assertTrue(any('myDATA' in (m.body or '') for m in move.message_ids))
        self.assertEqual(self.company.l10n_gr_edi_fetch_mark, '400001234567890')
        # partner auto-created and tagged
        tag = self.env.ref('l10n_gr_edi.res_partner_category_mydata_auto')
        self.assertIn(tag, move.partner_id.category_id)

    def test_cron_pagination_follows_continuation_token(self):
        page1 = _requested_doc(
            _invoice_xml(mark='400001234567890')
            + '<continuationToken><nextPartitionKey>PK</nextPartitionKey>'
              '<nextRowKey>RK</nextRowKey></continuationToken>')
        page2 = _requested_doc(_invoice_xml(mark='400001234567891', aa='102'))
        calls = self._run_cron_with_pages([page1, page2])
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[1]['params'].get('nextPartitionKey'), 'PK')
        self.assertEqual(calls[1]['params'].get('nextRowKey'), 'RK')
        bills = self.env['account.move'].search([
            ('l10n_gr_edi_is_fetched', '=', True),
            ('company_id', '=', self.company.id),
        ])
        self.assertEqual(len(bills), 2)
        self.assertEqual(self.company.l10n_gr_edi_fetch_mark, '400001234567891')

    def test_cron_uses_watermark_as_mark_param(self):
        self.company.l10n_gr_edi_fetch_mark = '400001234567890'
        calls = self._run_cron_with_pages([_requested_doc('')])
        self.assertEqual(calls[0]['params']['mark'], '400001234567890')
        self.assertNotIn('dateFrom', calls[0]['params'])

    def test_cron_first_run_uses_date_window(self):
        calls = self._run_cron_with_pages([_requested_doc('')])
        self.assertEqual(calls[0]['params']['mark'], 0)
        self.assertIn('dateFrom', calls[0]['params'])
        self.assertIn('dateTo', calls[0]['params'])

    def test_cron_dedup_by_mark(self):
        self._run_cron_with_pages([_requested_doc(_invoice_xml())])
        self.company.l10n_gr_edi_fetch_mark = False  # force refetch of same doc
        self._run_cron_with_pages([_requested_doc(_invoice_xml())])
        bills = self.env['account.move'].search([
            ('l10n_gr_edi_mark', '=', '400001234567890'),
            ('company_id', '=', self.company.id),
        ])
        self.assertEqual(len(bills), 1)

    def test_cron_skips_precancelled_invoice(self):
        self._run_cron_with_pages([_requested_doc(_invoice_xml(
            invoice_extra='<inv:cancelledByMark>400009999999999</inv:cancelledByMark>'))])
        bills = self.env['account.move'].search([
            ('l10n_gr_edi_mark', '=', '400001234567890'),
            ('company_id', '=', self.company.id),
        ])
        self.assertFalse(bills)
        # watermark still advances past the skipped doc
        self.assertEqual(self.company.l10n_gr_edi_fetch_mark, '400001234567890')

    def test_cron_isolates_bad_invoice(self):
        # first invoice has an unparseable issue date -> creation fails;
        # second invoice must still be created
        bad = _invoice_xml(mark='400001234567890', issue_date='not-a-date')
        good = _invoice_xml(mark='400001234567891', aa='102')
        self._run_cron_with_pages([_requested_doc(bad + good)])
        bills = self.env['account.move'].search([
            ('l10n_gr_edi_is_fetched', '=', True),
            ('company_id', '=', self.company.id),
        ])
        self.assertEqual(len(bills), 1)
        self.assertEqual(bills.l10n_gr_edi_mark, '400001234567891')
        self.assertEqual(self.company.l10n_gr_edi_fetch_mark, '400001234567891')

    def test_cron_processes_cancellations(self):
        self._run_cron_with_pages([_requested_doc(_invoice_xml())])
        move = self.env['account.move'].search([
            ('l10n_gr_edi_mark', '=', '400001234567890'),
            ('company_id', '=', self.company.id),
        ])
        self.company.l10n_gr_edi_fetch_mark = False
        self._run_cron_with_pages([_requested_doc(
            '<cancelledInvoicesDoc>'
            '<invoiceMark>400001234567890</invoiceMark>'
            '<cancellationMark>400009999999999</cancellationMark>'
            '<cancellationDate>2026-07-10</cancellationDate>'
            '</cancelledInvoicesDoc>')])
        self.assertEqual(move.state, 'cancel')

    def test_cron_empty_continuation_token_does_not_loop(self):
        # A <continuationToken/> element with no (or empty) child text must
        # not be treated as a real continuation - the loop must stop after
        # one page. Only one page is supplied here: if the guard is missing,
        # the fetch loop asks for a second page and the test errors out
        # trying to pop a response that doesn't exist.
        page = _requested_doc(_invoice_xml() + '<continuationToken/>')
        calls = self._run_cron_with_pages([page])
        self.assertEqual(len(calls), 1)
        move = self.env['account.move'].search([
            ('l10n_gr_edi_mark', '=', '400001234567890'),
            ('company_id', '=', self.company.id),
        ])
        self.assertEqual(len(move), 1)

    def test_cron_cancellation_failure_does_not_lose_bill_or_escape(self):
        # A broken cancellation pass for one company must not roll back that
        # company's already-created bills or its watermark advance, and must
        # not escape and abort the cron for other companies.
        Company = self.env['res.company']
        with patch.object(
            Company.__class__, '_l10n_gr_edi_process_cancellations',
            side_effect=Exception('boom'),
        ):
            self._run_cron_with_pages([_requested_doc(_invoice_xml())])
        move = self.env['account.move'].search([
            ('l10n_gr_edi_mark', '=', '400001234567890'),
            ('company_id', '=', self.company.id),
        ])
        self.assertEqual(len(move), 1)
        self.assertEqual(self.company.l10n_gr_edi_fetch_mark, '400001234567890')

    def test_cron_http_error_isolates_company_no_bills_created(self):
        class _FailingResponse:
            def raise_for_status(self):
                raise requests.RequestException('network down')

        with patch(
            'odoo.addons.l10n_gr_edi.models.res_company_fetch.http_requests.Session'
        ) as session_cls:
            session_cls.return_value.__enter__.return_value = session_cls.return_value
            session_cls.return_value.__exit__.return_value = False
            session_cls.return_value.get.return_value = _FailingResponse()
            # must not raise
            self.env['res.company']._cron_l10n_gr_edi_fetch_invoices()
        bills = self.env['account.move'].search([
            ('l10n_gr_edi_is_fetched', '=', True),
            ('company_id', '=', self.company.id),
        ])
        self.assertFalse(bills)

    def test_cron_malformed_timeout_param_falls_back_to_default(self):
        self.env['ir.config_parameter'].sudo().set_param(
            'l10n_gr_edi.fetch_timeout', 'not-a-number')
        calls = self._run_cron_with_pages([_requested_doc('')])
        self.assertEqual(calls[0]['timeout'], FETCH_DEFAULT_TIMEOUT)
