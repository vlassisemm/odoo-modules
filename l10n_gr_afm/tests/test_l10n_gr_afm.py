# Copyright 2026 Vlassis Emmanouil
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
from unittest.mock import MagicMock, patch

from odoo.exceptions import UserError
from odoo.tests import tagged, TransactionCase

# Test fixtures: Unicode strings encoded to UTF-8 for correct Greek character handling
SAMPLE_RESPONSE_OK = """<?xml version="1.0" encoding="UTF-8"?>
<env:Envelope xmlns:env="http://www.w3.org/2003/05/soap-envelope"
              xmlns:xsd="http://www.w3.org/2001/XMLSchema"
              xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
   <env:Header/>
   <env:Body>
      <srvc:rgWsPublic2AfmMethodResponse
          xmlns="http://rgwspublic2/RgWsPublic2"
          xmlns:srvc="http://rgwspublic2/RgWsPublic2Service">
         <srvc:result>
            <rg_ws_public2_result_rtType>
               <call_seq_id>123456</call_seq_id>
               <error_rec>
                  <error_code xsi:nil="true"/>
                  <error_descr xsi:nil="true"/>
               </error_rec>
               <afm_called_by_rec>
                  <token_username>testuser</token_username>
                  <token_afm>123456789</token_afm>
                  <token_afm_fullname>Test User</token_afm_fullname>
                  <afm_called_by>123456789</afm_called_by>
                  <afm_called_by_fullname>Test User</afm_called_by_fullname>
                  <as_on_date>2026-03-17</as_on_date>
               </afm_called_by_rec>
               <basic_rec>
                  <afm>090165560</afm>
                  <doy>1104</doy>
                  <doy_descr>\u0394\u0384 \u0391\u0398\u0397\u039d\u03a9\u039d</doy_descr>
                  <i_ni_flag_descr>\u039c\u0397 \u03a6\u03a0</i_ni_flag_descr>
                  <deactivation_flag>1</deactivation_flag>
                  <deactivation_flag_descr>\u0395\u039d\u0395\u03a1\u0393\u039f\u03a3 \u0391\u03a6\u039c</deactivation_flag_descr>
                  <firm_flag_descr>\u0395\u03a0\u0399\u03a4\u0397\u0394\u0395\u03a5\u039c\u0391\u03a4\u0399\u0391\u03a3</firm_flag_descr>
                  <onomasia>TEST COMPANY IKE</onomasia>
                  <commer_title xsi:nil="true"/>
                  <legal_status_descr>\u0399\u039a\u0395</legal_status_descr>
                  <postal_address>\u039a \u03a3\u0395\u03a1\u0392\u0399\u0391\u03a3</postal_address>
                  <postal_address_no>10</postal_address_no>
                  <postal_zip_code>10110</postal_zip_code>
                  <postal_area_description>\u0391\u0398\u0397\u039d\u0391</postal_area_description>
                  <regist_date>1993-02-08</regist_date>
                  <stop_date xsi:nil="true"/>
                  <normal_vat_system_flag>N</normal_vat_system_flag>
               </basic_rec>
               <firm_act_tab>
                  <item>
                     <firm_act_code>84111000</firm_act_code>
                     <firm_act_descr>\u0393\u0395\u039d\u0399\u039a\u0395\u03a3 \u0394\u0397\u039c\u039f\u03a3\u0399\u0395\u03a3 \u03a5\u03a0\u0397\u03a1\u0395\u03a3\u0399\u0395\u03a3</firm_act_descr>
                     <firm_act_kind>1</firm_act_kind>
                     <firm_act_kind_descr>\u039a\u03a5\u03a1\u0399\u0391</firm_act_kind_descr>
                  </item>
               </firm_act_tab>
            </rg_ws_public2_result_rtType>
         </srvc:result>
      </srvc:rgWsPublic2AfmMethodResponse>
   </env:Body>
</env:Envelope>""".encode('utf-8')

SAMPLE_RESPONSE_ERROR = """<?xml version="1.0" encoding="UTF-8"?>
<env:Envelope xmlns:env="http://www.w3.org/2003/05/soap-envelope"
              xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
   <env:Header/>
   <env:Body>
      <srvc:rgWsPublic2AfmMethodResponse
          xmlns="http://rgwspublic2/RgWsPublic2"
          xmlns:srvc="http://rgwspublic2/RgWsPublic2Service">
         <srvc:result>
            <rg_ws_public2_result_rtType>
               <call_seq_id>0</call_seq_id>
               <error_rec>
                  <error_code>RG_WS_PUBLIC_AFM_CALLED_BY_NOT_FOUND</error_code>
                  <error_descr>AFM not found</error_descr>
               </error_rec>
               <afm_called_by_rec/>
               <basic_rec/>
               <firm_act_tab/>
            </rg_ws_public2_result_rtType>
         </srvc:result>
      </srvc:rgWsPublic2AfmMethodResponse>
   </env:Body>
</env:Envelope>""".encode('utf-8')

SAMPLE_RESPONSE_INACTIVE = """<?xml version="1.0" encoding="UTF-8"?>
<env:Envelope xmlns:env="http://www.w3.org/2003/05/soap-envelope"
              xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
   <env:Header/>
   <env:Body>
      <srvc:rgWsPublic2AfmMethodResponse
          xmlns="http://rgwspublic2/RgWsPublic2"
          xmlns:srvc="http://rgwspublic2/RgWsPublic2Service">
         <srvc:result>
            <rg_ws_public2_result_rtType>
               <call_seq_id>123457</call_seq_id>
               <error_rec>
                  <error_code xsi:nil="true"/>
                  <error_descr xsi:nil="true"/>
               </error_rec>
               <afm_called_by_rec/>
               <basic_rec>
                  <afm>999999999</afm>
                  <doy>1101</doy>
                  <doy_descr>TEST DOY</doy_descr>
                  <i_ni_flag_descr>FP</i_ni_flag_descr>
                  <deactivation_flag>2</deactivation_flag>
                  <deactivation_flag_descr>INACTIVE</deactivation_flag_descr>
                  <firm_flag_descr>NON BUSINESS</firm_flag_descr>
                  <onomasia>INACTIVE COMPANY</onomasia>
                  <commer_title xsi:nil="true"/>
                  <legal_status_descr>OE</legal_status_descr>
                  <postal_address>TEST STREET</postal_address>
                  <postal_address_no xsi:nil="true"/>
                  <postal_zip_code>11111</postal_zip_code>
                  <postal_area_description>CITY</postal_area_description>
                  <regist_date>2000-01-01</regist_date>
                  <stop_date>2020-06-30</stop_date>
                  <normal_vat_system_flag>N</normal_vat_system_flag>
               </basic_rec>
               <firm_act_tab>
                  <item>
                     <firm_act_code>47111000</firm_act_code>
                     <firm_act_descr>RETAIL</firm_act_descr>
                     <firm_act_kind>1</firm_act_kind>
                     <firm_act_kind_descr>PRIMARY</firm_act_kind_descr>
                  </item>
               </firm_act_tab>
            </rg_ws_public2_result_rtType>
         </srvc:result>
      </srvc:rgWsPublic2AfmMethodResponse>
   </env:Body>
</env:Envelope>""".encode('utf-8')


@tagged('post_install', '-at_install', 'l10n_gr_afm')
class TestL10nGrAfmExtractVat(TransactionCase):
    """Test VAT-to-AFM extraction logic."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Partner = cls.env['res.partner']

    def test_el_prefix(self):
        self.assertEqual(self.Partner._l10n_gr_afm_extract_vat('EL090165560'), '090165560')

    def test_lowercase_el_prefix(self):
        self.assertEqual(self.Partner._l10n_gr_afm_extract_vat('el090165560'), '090165560')

    def test_bare_digits(self):
        self.assertEqual(self.Partner._l10n_gr_afm_extract_vat('090165560'), '090165560')

    def test_empty_vat_raises(self):
        with self.assertRaises(UserError):
            self.Partner._l10n_gr_afm_extract_vat('')

    def test_none_vat_raises(self):
        with self.assertRaises(UserError):
            self.Partner._l10n_gr_afm_extract_vat(None)

    def test_non_greek_vat_raises(self):
        with self.assertRaises(UserError):
            self.Partner._l10n_gr_afm_extract_vat('DE123456789')

    def test_wrong_length_raises(self):
        with self.assertRaises(UserError):
            self.Partner._l10n_gr_afm_extract_vat('EL12345')


@tagged('post_install', '-at_install', 'l10n_gr_afm')
class TestL10nGrAfmParseResponse(TransactionCase):
    """Test AADE XML response parsing."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Partner = cls.env['res.partner']

    def test_parse_success_response(self):
        result = self.Partner._l10n_gr_afm_parse_response(SAMPLE_RESPONSE_OK)
        self.assertEqual(result['afm_name'], 'TEST COMPANY IKE')
        self.assertIn('\u03a3\u0395\u03a1\u0392\u0399\u0391\u03a3', result['afm_street'])
        self.assertIn('10', result['afm_street'])
        self.assertEqual(result['afm_zip'], '10110')
        self.assertEqual(result['afm_kad_code'], '84111000')
        self.assertFalse(result['afm_is_inactive'])

    def test_parse_error_response(self):
        with self.assertRaises(UserError):
            self.Partner._l10n_gr_afm_parse_response(SAMPLE_RESPONSE_ERROR)

    def test_parse_inactive_afm(self):
        result = self.Partner._l10n_gr_afm_parse_response(SAMPLE_RESPONSE_INACTIVE)
        self.assertTrue(result['afm_is_inactive'])
        self.assertEqual(result['afm_name'], 'INACTIVE COMPANY')

    def test_parse_street_no_number(self):
        """When postal_address_no is nil, street should be address only."""
        result = self.Partner._l10n_gr_afm_parse_response(SAMPLE_RESPONSE_INACTIVE)
        self.assertEqual(result['afm_street'], 'TEST STREET')

    def test_parse_invalid_xml(self):
        with self.assertRaises(UserError):
            self.Partner._l10n_gr_afm_parse_response(b'not xml')


@tagged('post_install', '-at_install', 'l10n_gr_afm')
class TestL10nGrAfmFetchAction(TransactionCase):
    """Test the full fetch flow with mocked HTTP calls."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.company.sudo().write({
            'l10n_gr_afm_aade_username': 'test_user',
            'l10n_gr_afm_aade_password': 'test_pass',
            'country_id': cls.env.ref('base.gr').id,
        })
        cls.partner = cls.env['res.partner'].create({
            'name': 'Test Partner',
            'vat': 'EL090165560',
            'country_id': cls.env.ref('base.gr').id,
        })

    @patch('odoo.addons.l10n_gr_afm.models.res_partner.http_requests.post')
    def test_fetch_opens_wizard(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = SAMPLE_RESPONSE_OK
        mock_post.return_value = mock_response

        action = self.partner.action_l10n_gr_afm_fetch()
        self.assertEqual(action['res_model'], 'l10n_gr_afm.lookup.wizard')
        wizard = self.env['l10n_gr_afm.lookup.wizard'].browse(action['res_id'])
        self.assertEqual(wizard.afm_name, 'TEST COMPANY IKE')

    @patch('odoo.addons.l10n_gr_afm.models.res_partner.http_requests.post')
    def test_fetch_inactive_sets_flag(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = SAMPLE_RESPONSE_INACTIVE
        mock_post.return_value = mock_response

        action = self.partner.action_l10n_gr_afm_fetch()
        wizard = self.env['l10n_gr_afm.lookup.wizard'].browse(action['res_id'])
        self.assertTrue(wizard.afm_is_inactive)

    def test_fetch_no_vat_raises(self):
        partner = self.env['res.partner'].create({
            'name': 'No VAT',
            'country_id': self.env.ref('base.gr').id,
        })
        with self.assertRaises(UserError):
            partner.action_l10n_gr_afm_fetch()

    def test_fetch_no_credentials_raises(self):
        self.company.sudo().write({
            'l10n_gr_afm_aade_username': False,
            'l10n_gr_afm_aade_password': False,
        })
        self.addCleanup(
            self.company.sudo().write,
            {'l10n_gr_afm_aade_username': 'test_user',
             'l10n_gr_afm_aade_password': 'test_pass'},
        )
        with self.assertRaises(UserError):
            self.partner.action_l10n_gr_afm_fetch()

    @patch('odoo.addons.l10n_gr_afm.models.res_partner.http_requests.post')
    def test_fetch_http_401_raises(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_post.return_value = mock_response

        with self.assertRaises(UserError):
            self.partner.action_l10n_gr_afm_fetch()

    @patch('odoo.addons.l10n_gr_afm.models.res_partner.http_requests.post')
    def test_fetch_request_exception_raises(self, mock_post):
        import requests
        mock_post.side_effect = requests.exceptions.ConnectionError("timeout")

        with self.assertRaises(UserError):
            self.partner.action_l10n_gr_afm_fetch()


@tagged('post_install', '-at_install', 'l10n_gr_afm')
class TestL10nGrAfmWizardApply(TransactionCase):
    """Test wizard apply writes correct data to partner."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner = cls.env['res.partner'].create({
            'name': 'Original Name',
            'country_id': cls.env.ref('base.gr').id,
        })

    def test_apply_updates_partner(self):
        wizard = self.env['l10n_gr_afm.lookup.wizard'].create({
            'partner_id': self.partner.id,
            'afm_name': 'NEW COMPANY NAME',
            'afm_street': 'NEW STREET 5',
            'afm_zip': '12345',
            'afm_city': 'NEW CITY',
            'afm_doy': 'TEST DOY',
            'afm_kad_code': '47111000',
            'afm_kad_descr': 'RETAIL',
        })
        wizard.action_apply()

        self.assertEqual(self.partner.name, 'NEW COMPANY NAME')
        self.assertEqual(self.partner.street, 'NEW STREET 5')
        self.assertEqual(self.partner.zip, '12345')
        self.assertEqual(self.partner.city, 'NEW CITY')
        self.assertEqual(self.partner.l10n_gr_afm_doy, 'TEST DOY')
        self.assertEqual(self.partner.l10n_gr_afm_kad_code, '47111000')
        self.assertEqual(self.partner.l10n_gr_afm_kad_descr, 'RETAIL')


@tagged('post_install', '-at_install', 'l10n_gr_afm')
class TestL10nGrAfmAccessControl(TransactionCase):
    """Test that non-authorized users cannot trigger AADE lookups."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.company.sudo().write({
            'l10n_gr_afm_aade_username': 'test_user',
            'l10n_gr_afm_aade_password': 'test_pass',
            'country_id': cls.env.ref('base.gr').id,
        })
        # Create a basic internal user without sales or accounting group
        cls.basic_user = cls.env['res.users'].create({
            'name': 'Basic User',
            'login': 'basic_user_afm_test',
            'groups_id': [(6, 0, [cls.env.ref('base.group_user').id])],
        })
        cls.partner = cls.env['res.partner'].create({
            'name': 'Test Partner',
            'vat': 'EL090165560',
            'country_id': cls.env.ref('base.gr').id,
        })

    @patch('odoo.addons.l10n_gr_afm.models.res_partner.http_requests.post')
    def test_basic_user_cannot_fetch(self, mock_post):
        """Non-authorized user should get UserError before AADE is called."""
        partner_as_basic = self.partner.with_user(self.basic_user)
        with self.assertRaises(UserError):
            partner_as_basic.action_l10n_gr_afm_fetch()
        # Verify AADE was never called
        mock_post.assert_not_called()


@tagged('post_install', '-at_install', 'l10n_gr_afm')
class TestL10nGrAfmMultiCompany(TransactionCase):
    """Test multi-company credential isolation and partner field sharing."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Company A: default company, with AADE credentials
        cls.company_a = cls.env.company
        cls.company_a.sudo().write({
            'l10n_gr_afm_aade_username': 'user_a',
            'l10n_gr_afm_aade_password': 'pass_a',
            'country_id': cls.env.ref('base.gr').id,
        })

        # Company B: no AADE credentials
        cls.company_b = cls.env['res.company'].create({
            'name': 'Company B',
            'country_id': cls.env.ref('base.gr').id,
        })

        # User assigned to Company B with sales group (passes permission check)
        cls.user_b = cls.env['res.users'].create({
            'name': 'User B',
            'login': 'user_b_multicompany_test',
            'company_id': cls.company_b.id,
            'company_ids': [(6, 0, [cls.company_b.id])],
            'groups_id': [(6, 0, [
                cls.env.ref('base.group_user').id,
                cls.env.ref('sales_team.group_sale_salesman').id,
            ])],
        })

        # Shared partner (no company_id, visible to all companies)
        cls.partner = cls.env['res.partner'].create({
            'name': 'Shared Partner',
            'vat': 'EL090165560',
            'country_id': cls.env.ref('base.gr').id,
        })

    @patch('odoo.addons.l10n_gr_afm.models.res_partner.http_requests.post')
    def test_credential_isolation_company_a_succeeds(self, mock_post):
        """Fetch from Company A (has credentials) succeeds and returns wizard."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = SAMPLE_RESPONSE_OK
        mock_post.return_value = mock_response

        action = self.partner.action_l10n_gr_afm_fetch()
        self.assertEqual(action['res_model'], 'l10n_gr_afm.lookup.wizard')
        wizard = self.env['l10n_gr_afm.lookup.wizard'].browse(action['res_id'])
        self.assertEqual(wizard.afm_name, 'TEST COMPANY IKE')

    @patch('odoo.addons.l10n_gr_afm.models.res_partner.http_requests.post')
    def test_credential_isolation_company_b_raises(self, mock_post):
        """Fetch from Company B (no credentials) raises UserError before calling AADE."""
        partner_b = self.partner.with_user(self.user_b).with_company(self.company_b)
        with self.assertRaisesRegex(UserError, 'credentials'):
            partner_b.action_l10n_gr_afm_fetch()
        mock_post.assert_not_called()

    def test_partner_fields_shared_across_companies(self):
        """AADE fields written by Company A are visible from Company B."""
        self.partner.write({
            'l10n_gr_afm_doy': '1104',
            'l10n_gr_afm_kad_descr': 'RETAIL TRADE',
        })
        partner_b = self.partner.with_user(self.user_b).with_company(self.company_b)
        self.assertEqual(partner_b.l10n_gr_afm_doy, '1104')
        self.assertEqual(partner_b.l10n_gr_afm_kad_descr, 'RETAIL TRADE')

    def test_can_fetch_no_company_id_on_partner(self):
        """can_fetch works when partner has no company_id (OCA partner_multi_company compat)."""
        self.assertFalse(self.partner.company_id)
        self.assertTrue(self.partner.l10n_gr_afm_can_fetch)

    @patch('odoo.addons.l10n_gr_afm.models.res_partner.http_requests.post')
    def test_company_b_uses_own_credentials(self, mock_post):
        """When Company B has credentials, its credentials appear in the SOAP envelope."""
        self.company_b.sudo().write({
            'l10n_gr_afm_aade_username': 'user_b',
            'l10n_gr_afm_aade_password': 'pass_b',
        })
        self.addCleanup(
            self.company_b.sudo().write,
            {'l10n_gr_afm_aade_username': False,
             'l10n_gr_afm_aade_password': False},
        )

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = SAMPLE_RESPONSE_OK
        mock_post.return_value = mock_response

        partner_b = self.partner.with_user(self.user_b).with_company(self.company_b)
        partner_b.action_l10n_gr_afm_fetch()

        # Verify the SOAP envelope contains Company B's credentials
        call_kwargs = mock_post.call_args
        soap_body = call_kwargs.kwargs.get('data') or call_kwargs[1].get('data')
        self.assertIn(b'user_b', soap_body)
        self.assertIn(b'pass_b', soap_body)
        self.assertNotIn(b'user_a', soap_body)
        self.assertNotIn(b'pass_a', soap_body)
