# Part of Odoo. See LICENSE file for full copyright and licensing details.
from unittest.mock import patch

from odoo import Command
from odoo.addons.account.tests.common import AccountTestInvoicingCommon
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
