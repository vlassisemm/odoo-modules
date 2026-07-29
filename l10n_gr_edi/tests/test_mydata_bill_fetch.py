# Part of Odoo. See LICENSE file for full copyright and licensing details.
from unittest.mock import patch

import requests

from odoo import Command, _
from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.addons.l10n_gr_edi.models.res_company_fetch import FETCH_DEFAULT_TIMEOUT
from odoo.exceptions import UserError
from odoo.tests import tagged


def _requested_doc(body):
    """Wrap invoice/cancellation XML in a RequestedDoc envelope, return bytes.

    requestedInvoicesDoc.xsd declares a single targetNamespace
    (http://www.aade.gr/myDATA/invoice/v1.0) with elementFormDefault="qualified",
    so RequestedDoc and every descendant live in the invoice namespace. The `inv:`
    prefix is bound to that same namespace here so the fixtures stay readable.
    """
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<RequestedDoc xmlns="http://www.aade.gr/myDATA/invoice/v1.0" '
        'xmlns:inv="http://www.aade.gr/myDATA/invoice/v1.0">'
        f'{body}</RequestedDoc>'
    ).encode('utf-8')


def _cancellation_xml(invoice_mark, cancellation_mark, cancellation_date='2026-07-10'):
    return (
        '<cancelledInvoice>'
        f'<invoiceMark>{invoice_mark}</invoiceMark>'
        f'<cancellationMark>{cancellation_mark}</cancellationMark>'
        f'<cancellationDate>{cancellation_date}</cancellationDate>'
        '</cancelledInvoice>'
    )


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
        # cancelledInvoicesDoc is a CONTAINER of <cancelledInvoice> records
        # (requestedInvoicesDoc.xsd); two records here pin that distinction.
        content = _requested_doc(
            _invoice_xml()
            + '<cancelledInvoicesDoc>'
            + _cancellation_xml('400001234567800', '400009999999998')
            + _cancellation_xml('400001234567801', '400009999999999', '2026-07-11')
            + '</cancelledInvoicesDoc>'
            + '<continuationToken>'
            '<nextPartitionKey>PK1</nextPartitionKey><nextRowKey>RK1</nextRowKey>'
            '</continuationToken>'
        )
        result = self.company._l10n_gr_edi_parse_requested_docs(content)
        self.assertEqual(result['cancellations'], [
            {
                'invoice_mark': '400001234567800',
                'cancellation_mark': '400009999999998',
                'cancellation_date': '2026-07-10',
            },
            {
                'invoice_mark': '400001234567801',
                'cancellation_mark': '400009999999999',
                'cancellation_date': '2026-07-11',
            },
        ])
        self.assertEqual(result['continuation'],
                         {'nextPartitionKey': 'PK1', 'nextRowKey': 'RK1'})
        # the highest cancellation mark is larger than the invoice mark
        self.assertEqual(result['max_mark'], 400009999999999)

    def test_parse_flat_cancellations_block_yields_no_records(self):
        # A malformed payload carrying the fields directly under
        # <cancelledInvoicesDoc> has no <cancelledInvoice> record, so it must
        # parse to nothing rather than to one all-None pseudo-cancellation.
        content = _requested_doc(
            '<cancelledInvoicesDoc>'
            '<invoiceMark>400001234567800</invoiceMark>'
            '<cancellationMark>400009999999999</cancellationMark>'
            '<cancellationDate>2026-07-10</cancellationDate>'
            '</cancelledInvoicesDoc>'
        )
        result = self.company._l10n_gr_edi_parse_requested_docs(content)
        self.assertEqual(result['cancellations'], [])

    def test_parse_classification_and_payment_event_marks(self):
        content = _requested_doc(
            '<expenseClassificationsDoc><expensesInvoiceClassification>'
            '<classificationMark>400009999999998</classificationMark>'
            '</expensesInvoiceClassification></expenseClassificationsDoc>'
            '<paymentMethodsDoc><paymentMethod>'
            '<paymentMethodMark>400009999999999</paymentMethodMark>'
            '</paymentMethod></paymentMethodsDoc>'
        )
        result = self.company._l10n_gr_edi_parse_requested_docs(content)
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

    def test_active_partner_beats_older_archived_one(self):
        archived = self.env['res.partner'].create({
            'name': 'Archived Vendor', 'vat': 'EL123456789', 'is_company': True,
            'active': False,
        })
        active = self.env['res.partner'].create({
            'name': 'Active Vendor', 'vat': 'EL123456789', 'is_company': True,
        })
        self.assertLess(archived.id, active.id)  # id order alone would pick the archived one
        partner, _report = self.company._l10n_gr_edi_resolve_partner(self._issuer())
        self.assertEqual(partner, active)

    def test_create_minimal_greek_partner_tagged(self):
        partner, report = self.company._l10n_gr_edi_resolve_partner(self._issuer())
        self.assertEqual(partner.vat, 'EL123456789')
        self.assertIn('123456789', partner.name)
        self.assertTrue(partner.is_company)
        self.assertEqual(partner.supplier_rank, 1)
        tag = self.env.ref('l10n_gr_edi.res_partner_category_mydata_auto')
        self.assertIn(tag, partner.category_id)

    def test_create_partner_survives_missing_tag_record(self):
        # The tag ships as module data; a database where it was removed must
        # still get its vendor bills, minus the tag.
        self.env.ref('l10n_gr_edi.res_partner_category_mydata_auto').unlink()
        partner, report = self.company._l10n_gr_edi_resolve_partner(self._issuer())
        self.assertEqual(partner.vat, 'EL123456789')
        self.assertFalse(partner.category_id)
        self.assertTrue(any('tag' in line.lower() for line in report))

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

    def test_enrichment_error_log_omits_exception_details(self):
        Partner = self.env['res.partner']
        if not hasattr(Partner, '_l10n_gr_afm_call_aade'):
            self.skipTest('l10n_gr_afm not installed in this registry')
        self.company.sudo().write({
            'l10n_gr_afm_aade_username': 'u',
            'l10n_gr_afm_aade_password': 'p',
        })
        sensitive_error = 'caller VAT 123456789 rejected'
        with patch.object(
            Partner.__class__,
            '_l10n_gr_afm_call_aade',
            side_effect=UserError(sensitive_error),
        ), self.assertLogs(
            'odoo.addons.l10n_gr_edi.models.res_company_fetch',
            level='WARNING',
        ) as captured:
            self.company._l10n_gr_edi_resolve_partner(self._issuer())
        logs = '\n'.join(captured.output)
        self.assertNotIn(sensitive_error, logs)
        self.assertIn('company_id=', logs)
        self.assertIn('partner_id=', logs)
        self.assertIn('error_type=UserError', logs)


class TestMyDataFetchLines(TestMyDataFetchCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.tax_p24 = cls._find_or_create_purchase_tax(24.0)
        cls.withholding_20 = cls._find_or_create_purchase_tax(-20.0)
        for tax in (cls.tax_p24, cls.withholding_20):
            cls.env['account.tax'].search([
                ('company_id', '=', cls.company.id),
                ('type_tax_use', '=', 'purchase'),
                ('amount_type', '=', 'percent'),
                ('amount', '=', tax.amount),
                ('id', '!=', tax.id),
            ]).active = False

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

    def test_ambiguous_vat_tax_falls_back_with_source_metadata(self):
        self.env['account.tax'].create({
            'name': 'Second purchase 24%',
            'amount': 24.0,
            'amount_type': 'percent',
            'type_tax_use': 'purchase',
            'company_id': self.company.id,
        })
        commands, report = self.company._l10n_gr_edi_prepare_line_vals(self._inv([
            self._line(line_number=7, vat_exemption_category=2),
        ]))
        vals = self._created_vals(commands)
        self.assertEqual(len(vals), 2)
        source = next(vals for vals in vals if vals['price_unit'] == 1000.0)
        adjustment = next(vals for vals in vals if vals['price_unit'] == 240.0)
        self.assertEqual(source['tax_ids'], [Command.set([])])
        self.assertEqual(source.get('l10n_gr_edi_source_line_number'), 7)
        self.assertEqual(source.get('l10n_gr_edi_tax_exemption_category'), '2')
        self.assertTrue(adjustment.get('l10n_gr_edi_is_fetch_adjustment'))
        self.assertTrue(report['warnings'])

    def test_vat_category_9_and_10_no_crash(self):
        tax_p3 = self._find_or_create_purchase_tax(3.0)
        self.env['account.tax'].search([
            ('company_id', '=', self.company.id),
            ('type_tax_use', '=', 'purchase'),
            ('amount_type', '=', 'percent'),
            ('amount', '=', 3.0),
            ('id', '!=', tax_p3.id),
        ]).active = False
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
        self.assertTrue(fallback.get('l10n_gr_edi_is_fetch_adjustment'))
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
        self.assertTrue(fallback.get('l10n_gr_edi_is_fetch_adjustment'))
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
        self.assertTrue(fallback[0].get('l10n_gr_edi_is_fetch_adjustment'))
        self.assertTrue(report['warnings'])

    def test_other_taxes_percent_colliding_with_vat_rate_falls_back(self):
        # OTHER_TAXES_PERCENT[3] is 4.0, which is also a myDATA VAT rate
        # (categories 6 and 10). Matching it would hang a purchase VAT tax off
        # an other-taxes charge and corrupt the VAT return.
        tax_p4 = self._find_or_create_purchase_tax(4.0)
        commands, report = self.company._l10n_gr_edi_prepare_line_vals(self._inv([
            self._line(other_taxes_amount=40.0, other_taxes_percent_category=3),
        ]))
        vals = self._created_vals(commands)
        for line_vals in vals:
            self.assertNotIn(tax_p4.id, line_vals['tax_ids'][0][2])
        fallback = [v for v in vals if 'ther taxes' in v['name']]
        self.assertEqual(len(fallback), 1)
        self.assertEqual(fallback[0]['price_unit'], 40.0)
        self.assertTrue(fallback[0].get('l10n_gr_edi_is_fetch_adjustment'))
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
        self.assertTrue(vals[1].get('l10n_gr_edi_is_fetch_adjustment'))
        self.assertTrue(vals[2].get('l10n_gr_edi_is_fetch_adjustment'))
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
        self.assertEqual(vals.get('l10n_gr_edi_correlation_id'), original.id)

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
        self.assertEqual(vals.get('invoice_currency_rate'), 1.08)

    def test_foreign_currency_missing_exchange_rate_warns(self):
        self.env['res.currency'].search([('name', '=', 'USD')]).active = True
        vals, report = self.company._l10n_gr_edi_prepare_bill_vals(
            self._inv(header_extra='<inv:currency>USD</inv:currency>'),
            self.partner)
        self.assertNotIn('invoice_currency_rate', vals)
        self.assertTrue(any('exchange rate' in warning.lower()
                            for warning in report['warnings']))

    def test_unknown_currency_warns(self):
        vals, report = self.company._l10n_gr_edi_prepare_bill_vals(
            self._inv(header_extra='<inv:currency>XXX</inv:currency>'), self.partner)
        self.assertNotIn('currency_id', vals)
        self.assertTrue(any('XXX' in w for w in report['warnings']))

    def test_eur_document_sets_currency_for_non_eur_company(self):
        usd = self.env['res.currency'].search([('name', '=', 'USD')])
        usd.active = True
        usd_company = self.env['res.company'].create({
            'name': 'USD Company',
            'currency_id': usd.id,
            'country_id': self.env.ref('base.gr').id,
        })
        eur = self.env['res.currency'].search([('name', '=', 'EUR')])
        vals, report = usd_company._l10n_gr_edi_prepare_bill_vals(
            self._inv(header_extra='<inv:currency>EUR</inv:currency>'), self.partner)
        self.assertEqual(vals['currency_id'], eur.id)
        self.assertNotIn('invoice_currency_rate', vals)
        self.assertTrue(any('company currency is not EUR' in warning
                            for warning in report['warnings']))

    def test_company_currency_document_does_not_set_foreign_currency(self):
        usd = self.env['res.currency'].search([('name', '=', 'USD')])
        usd.active = True
        usd_company = self.env['res.company'].create({
            'name': 'USD Company',
            'currency_id': usd.id,
            'country_id': self.env.ref('base.gr').id,
        })
        vals, report = usd_company._l10n_gr_edi_prepare_bill_vals(
            self._inv(header_extra='<inv:currency>USD</inv:currency>'), self.partner)
        self.assertNotIn('currency_id', vals)
        self.assertFalse(any('company currency is not EUR' in warning
                             for warning in report['warnings']))

    def test_fetched_line_preserves_source_vat_exemption(self):
        lines_xml = (
            '<inv:invoiceDetails><inv:lineNumber>7</inv:lineNumber>'
            '<inv:netValue>100.00</inv:netValue>'
            '<inv:vatCategory>7</inv:vatCategory><inv:vatAmount>0.00</inv:vatAmount>'
            '<inv:vatExemptionCategory>2</inv:vatExemptionCategory>'
            '</inv:invoiceDetails>'
        )
        vals, _report = self.company._l10n_gr_edi_prepare_bill_vals(
            self._inv(lines_xml=lines_xml), self.partner)
        move = self.env['account.move'].create(vals)
        source_line = move.invoice_line_ids.filtered(
            lambda line: line.l10n_gr_edi_source_line_number == 7)
        source_line._compute_l10n_gr_edi_tax_exemption_category()
        self.assertEqual(source_line.l10n_gr_edi_tax_exemption_category, '2')


class TestMyDataFetchClassification(TestMyDataFetchCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner = cls.env['res.partner'].create({
            'name': 'Vendor X', 'vat': 'EL123456789', 'is_company': True,
        })

    def _make_fetched_bill_with_adjustment(self):
        return self.env['account.move'].create({
            'move_type': 'in_invoice',
            'partner_id': self.partner.id,
            'invoice_date': '2026-07-01',
            'company_id': self.company.id,
            'l10n_gr_edi_is_fetched': True,
            'l10n_gr_edi_inv_type': '2.1',
            'invoice_line_ids': [
                Command.create({
                    'name': 'VAT adjustment',
                    'quantity': 1,
                    'price_unit': 24.0,
                    'tax_ids': [Command.set([])],
                    'l10n_gr_edi_is_fetch_adjustment': True,
                }),
                Command.create({
                    'name': 'Source line',
                    'quantity': 1,
                    'price_unit': 100.0,
                    'tax_ids': [Command.set([])],
                    'l10n_gr_edi_source_line_number': 7,
                }),
            ],
        })

    def test_expense_classification_excludes_adjustment_and_uses_source_number(self):
        move = self._make_fetched_bill_with_adjustment()
        xml_vals = move._l10n_gr_edi_get_expense_classification_xml_vals()
        details = xml_vals['invoice_values_list'][0]['details']
        self.assertEqual(len(details), 1)
        self.assertEqual(details[0]['line_number'], 7)

    def test_fetched_lines_skip_outgoing_tax_structure_validation(self):
        move = self._make_fetched_bill_with_adjustment()
        errors = move._l10n_gr_edi_get_pre_error_dict()
        tax_error_keys = [
            key for key in errors
            if any(token in key for token in (
                'multi_tax', 'missing_tax', 'missing_tax_exempt', 'invalid_tax_amount',
            ))
        ]
        self.assertEqual(tax_error_keys, [])


class TestMyDataFetchNote(TestMyDataFetchCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        tax = cls._find_or_create_purchase_tax(24.0)
        cls.env['account.tax'].search([
            ('company_id', '=', cls.company.id),
            ('type_tax_use', '=', 'purchase'),
            ('amount_type', '=', 'percent'),
            ('amount', '=', 24.0),
            ('id', '!=', tax.id),
        ]).active = False
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

    def test_note_is_concise_and_contains_source_summary(self):
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
        self.assertIn('A/101', html)              # source reference
        self.assertIn('2026-07-01', html)         # source date
        self.assertIn('1240.00', html)            # declared gross total
        self.assertIn('1', html)                  # source line count
        self.assertNotIn('Vendor X', html)        # routine partner narration
        self.assertNotIn('Cash', html)            # payment detail noise
        self.assertNotIn('Mapping', html)         # routine mapping narration
        self.assertNotIn('Warning', html)         # no warnings section when clean

    def test_note_contains_only_actionable_mapping_warnings(self):
        move, inv, report = self._build_move()
        report['lines'] = ['Line 1 mapped successfully.']
        report['warnings'] = ['Line 1 requires tax review.']
        html = str(self.company._l10n_gr_edi_build_fetch_note(
            inv, ['Partner matched by VAT: Vendor X'], report))
        self.assertIn('requires tax review', html)
        self.assertNotIn('mapped successfully', html)
        self.assertNotIn('Partner matched', html)

    def test_note_shows_invoice_type_label(self):
        move, inv, report = self._build_move()
        html = str(self.company._l10n_gr_edi_build_fetch_note(inv, [], report))
        self.assertIn('2.1', html)
        self.assertIn('Service Rendered Invoice', html)

    def test_note_unknown_invoice_type_falls_back_to_code(self):
        move, inv, report = self._build_move()
        inv['header']['invoice_type'] = '9.9'
        html = str(self.company._l10n_gr_edi_build_fetch_note(inv, [], report))
        self.assertIn('9.9', html)

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

    def test_manually_modified_draft_gets_activity_not_cancelled(self):
        move = self._make_fetched_bill('400001234567006')
        move.is_manually_modified = True
        self.company._l10n_gr_edi_process_cancellations(
            self._cancellation('400001234567006'))
        self.assertEqual(move.state, 'draft')
        self.assertTrue(move.activity_ids)

    def test_posted_bill_gets_activity_not_cancelled(self):
        move = self._make_fetched_bill('400001234567002')
        move.action_post()
        self.company._l10n_gr_edi_process_cancellations(
            self._cancellation('400001234567002'))
        self.assertEqual(move.state, 'posted')
        self.assertTrue(move.activity_ids)
        self.assertTrue(any('400009999999999' in (m.body or '')
                            for m in move.message_ids))

    def test_posted_bill_cancellation_is_idempotent(self):
        move = self._make_fetched_bill('400001234567005')
        move.action_post()
        self.company._l10n_gr_edi_process_cancellations(
            self._cancellation('400001234567005'))
        self.company._l10n_gr_edi_process_cancellations(
            self._cancellation('400001234567005'))
        notes = [m for m in move.message_ids if '400009999999999' in (m.body or '')]
        self.assertEqual(len(notes), 1)
        self.assertEqual(len(move.activity_ids), 1)

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

    def test_cancellation_without_mark_touches_nothing(self):
        # A cancellation entry with no invoice MARK must fail closed. Searching
        # for it would normalise to `l10n_gr_edi_mark = False` and match an
        # arbitrary markless bill, posting a bogus cancellation note on it.
        markless = self.env['account.move'].create({
            'move_type': 'in_invoice', 'partner_id': self.partner.id,
            'invoice_date': '2026-07-01', 'company_id': self.company.id,
            'invoice_line_ids': [Command.create({
                'name': 'line', 'quantity': 1, 'price_unit': 100.0,
            })],
        })
        self.assertFalse(markless.l10n_gr_edi_mark)
        message_count = len(markless.message_ids)
        self.company._l10n_gr_edi_process_cancellations([{
            'invoice_mark': None,
            'cancellation_mark': '400009999999999',
            'cancellation_date': '2026-07-10',
        }])
        self.assertEqual(markless.state, 'draft')
        self.assertEqual(len(markless.message_ids), message_count)
        self.assertFalse(markless.activity_ids)

    def test_flat_cancellations_payload_leaves_markless_bill_untouched(self):
        # End-to-end guard: a malformed (flat) cancellations block parsed and
        # then processed must not disturb an unrelated markless draft bill.
        markless = self.env['account.move'].create({
            'move_type': 'in_invoice', 'partner_id': self.partner.id,
            'invoice_date': '2026-07-01', 'company_id': self.company.id,
            'invoice_line_ids': [Command.create({
                'name': 'line', 'quantity': 1, 'price_unit': 100.0,
            })],
        })
        message_count = len(markless.message_ids)
        parsed = self.company._l10n_gr_edi_parse_requested_docs(_requested_doc(
            '<cancelledInvoicesDoc>'
            '<invoiceMark>400001234567800</invoiceMark>'
            '<cancellationMark>400009999999999</cancellationMark>'
            '<cancellationDate>2026-07-10</cancellationDate>'
            '</cancelledInvoicesDoc>'
        ))
        self.company._l10n_gr_edi_process_cancellations(parsed['cancellations'])
        self.assertEqual(markless.state, 'draft')
        self.assertEqual(len(markless.message_ids), message_count)
        self.assertFalse(markless.activity_ids)

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

    def test_fetch_watermark_field_is_system_only(self):
        field = self.env['res.company']._fields['l10n_gr_edi_fetch_mark']
        self.assertEqual(field.groups, 'base.group_system')

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
        self.assertFalse(self.company.l10n_gr_edi_fetch_mark)

    def test_cron_error_log_is_redacted_and_uses_operational_ids(self):
        sensitive_vat = '987654321'
        sensitive_name = 'Sensitive Supplier SA'
        bad = _invoice_xml(
            mark='400001234567890',
            vat=sensitive_vat,
            issuer_extra=f'<inv:name>{sensitive_name}</inv:name>',
            issue_date='not-a-date',
        )
        logger_name = 'odoo.addons.l10n_gr_edi.models.res_company_fetch'
        with self.assertLogs(logger_name, level='INFO') as captured:
            self._run_cron_with_pages([_requested_doc(bad)])
        output = '\n'.join(captured.output)
        self.assertNotIn(sensitive_vat, output)
        self.assertNotIn(sensitive_name, output)
        self.assertNotIn("'issuer':", output)
        self.assertIn(str(self.company.id), output)
        self.assertIn('400001234567890', output)
        self.assertTrue(any(record.levelname == 'ERROR'
                            for record in captured.records))

    def test_cron_processes_cancellations(self):
        self._run_cron_with_pages([_requested_doc(_invoice_xml())])
        move = self.env['account.move'].search([
            ('l10n_gr_edi_mark', '=', '400001234567890'),
            ('company_id', '=', self.company.id),
        ])
        self.company.l10n_gr_edi_fetch_mark = False
        self._run_cron_with_pages([_requested_doc(
            '<cancelledInvoicesDoc>'
            + _cancellation_xml('400001234567890', '400009999999999')
            + '</cancelledInvoicesDoc>')])
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

    def test_cron_page_cap_does_not_process_or_advance_watermark(self):
        page = _requested_doc(
            _invoice_xml()
            + '<continuationToken><nextPartitionKey>PK</nextPartitionKey>'
              '<nextRowKey>RK</nextRowKey></continuationToken>')
        with patch(
            'odoo.addons.l10n_gr_edi.models.res_company_fetch.FETCH_MAX_PAGES', 1
        ):
            self._run_cron_with_pages([page])
        move = self.env['account.move'].search([
            ('l10n_gr_edi_mark', '=', '400001234567890'),
            ('company_id', '=', self.company.id),
        ])
        self.assertFalse(move)
        self.assertFalse(self.company.l10n_gr_edi_fetch_mark)

    def test_cron_cancellation_failure_does_not_lose_bill_or_escape(self):
        # A broken cancellation pass keeps already-created bills but must not
        # checkpoint past the unprocessed cancellation batch.
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
        self.assertFalse(self.company.l10n_gr_edi_fetch_mark)

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

    def test_cron_unexpected_fetch_error_isolated_with_traceback(self):
        Company = self.env['res.company']
        logger_name = 'odoo.addons.l10n_gr_edi.models.res_company_fetch'
        with (
            patch.object(
                Company.__class__, '_l10n_gr_edi_fetch_docs',
                side_effect=RuntimeError('unexpected fetch failure'),
            ),
            self.assertLogs(logger_name, level='ERROR') as captured,
        ):
            caught_error = None
            try:
                self.env['res.company']._cron_l10n_gr_edi_fetch_invoices()
            except RuntimeError as error:
                caught_error = error
        self.assertIsNone(caught_error)
        output = '\n'.join(captured.output)
        self.assertIn(str(self.company.id), output)
        self.assertIn('RuntimeError', output)
        self.assertTrue(any(record.exc_info for record in captured.records))

    def test_cron_malformed_timeout_param_falls_back_to_default(self):
        self.env['ir.config_parameter'].sudo().set_param(
            'l10n_gr_edi.fetch_timeout', 'not-a-number')
        calls = self._run_cron_with_pages([_requested_doc('')])
        self.assertEqual(calls[0]['timeout'], FETCH_DEFAULT_TIMEOUT)
