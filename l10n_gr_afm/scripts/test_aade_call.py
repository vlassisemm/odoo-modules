# Copyright 2026 Vlassis Emmanouil
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
#!/usr/bin/env python3
"""Standalone test script for AADE RgWsPublic2 SOAP call.

Usage:
    export AADE_USERNAME=your_user
    export AADE_PASSWORD=your_pass
    python scripts/test_aade_call.py --afm 090165560

Or with prompts:
    python scripts/test_aade_call.py --afm 090165560

Requires: requests, lxml (pip install requests lxml)
"""
import argparse
import getpass
import os
import sys

import requests
from lxml import etree

AADE_ENDPOINT = 'https://www1.gsis.gr/wsaade/RgWsPublic2/RgWsPublic2'
AADE_TIMEOUT = 30

NS_SOAP = 'http://www.w3.org/2003/05/soap-envelope'
NS_WSSE = 'http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd'
NS_SVC = 'http://rgwspublic2/RgWsPublic2Service'
NS_TYPES = 'http://rgwspublic2/RgWsPublic2'
NS_XSI = 'http://www.w3.org/2001/XMLSchema-instance'

RESPONSE_NS = {'ns': NS_TYPES}


def build_soap_envelope(username, password, afm):
    """Build the SOAP 1.2 envelope using lxml (safe from XML injection)."""
    envelope = etree.Element(f'{{{NS_SOAP}}}Envelope', nsmap={
        'env': NS_SOAP,
        'ns1': NS_WSSE,
        'ns2': NS_SVC,
        'ns3': NS_TYPES,
    })

    # Header with WS-Security
    header = etree.SubElement(envelope, f'{{{NS_SOAP}}}Header')
    security = etree.SubElement(header, f'{{{NS_WSSE}}}Security')
    token = etree.SubElement(security, f'{{{NS_WSSE}}}UsernameToken')
    user_el = etree.SubElement(token, f'{{{NS_WSSE}}}Username')
    user_el.text = username
    pass_el = etree.SubElement(token, f'{{{NS_WSSE}}}Password')
    pass_el.text = password

    # Body
    body = etree.SubElement(envelope, f'{{{NS_SOAP}}}Body')
    method = etree.SubElement(body, f'{{{NS_SVC}}}rgWsPublic2AfmMethod')
    input_rec = etree.SubElement(method, f'{{{NS_SVC}}}INPUT_REC')
    called_by = etree.SubElement(input_rec, f'{{{NS_TYPES}}}afm_called_by')
    called_by.text = None
    called_for = etree.SubElement(input_rec, f'{{{NS_TYPES}}}afm_called_for')
    called_for.text = afm

    return etree.tostring(envelope, xml_declaration=True, encoding='UTF-8')


def get_text(element, path):
    """Get text from an XML element, returning None for xsi:nil values."""
    el = element.find(path, RESPONSE_NS)
    if el is None:
        return None
    if el.get(f'{{{NS_XSI}}}nil') == 'true':
        return None
    return el.text


def parse_response(content):
    """Parse AADE response and return a dict of fields."""
    tree = etree.fromstring(content)
    result = tree.find('.//ns:rg_ws_public2_result_rtType', RESPONSE_NS)
    if result is None:
        return {'error': 'No result element found in response'}

    error_code = get_text(result, 'ns:error_rec/ns:error_code')
    if error_code:
        return {
            'error_code': error_code,
            'error_descr': get_text(result, 'ns:error_rec/ns:error_descr'),
        }

    street = get_text(result, 'ns:basic_rec/ns:postal_address') or ''
    street_no = get_text(result, 'ns:basic_rec/ns:postal_address_no') or ''

    # Primary activity
    kad_code = kad_descr = None
    for item in result.findall('ns:firm_act_tab/ns:item', RESPONSE_NS):
        if get_text(item, 'ns:firm_act_kind') == '1':
            kad_code = get_text(item, 'ns:firm_act_code')
            kad_descr = get_text(item, 'ns:firm_act_descr')
            break

    return {
        'name': get_text(result, 'ns:basic_rec/ns:onomasia'),
        'street': f"{street} {street_no}".strip(),
        'zip': get_text(result, 'ns:basic_rec/ns:postal_zip_code'),
        'city': get_text(result, 'ns:basic_rec/ns:postal_area_description'),
        'doy': get_text(result, 'ns:basic_rec/ns:doy_descr'),
        'legal_status': get_text(result, 'ns:basic_rec/ns:legal_status_descr'),
        'deactivation_flag': get_text(result, 'ns:basic_rec/ns:deactivation_flag'),
        'deactivation_descr': get_text(result, 'ns:basic_rec/ns:deactivation_flag_descr'),
        'entity_type': get_text(result, 'ns:basic_rec/ns:i_ni_flag_descr'),
        'business_status': get_text(result, 'ns:basic_rec/ns:firm_flag_descr'),
        'regist_date': get_text(result, 'ns:basic_rec/ns:regist_date'),
        'kad_code': kad_code,
        'kad_descr': kad_descr,
    }


def main():
    parser = argparse.ArgumentParser(description='Test AADE RgWsPublic2 SOAP call')
    parser.add_argument('--username', help='AADE username (or set AADE_USERNAME env var)')
    parser.add_argument('--password', help='AADE password (or set AADE_PASSWORD env var, or will prompt)')
    parser.add_argument('--afm', required=True, help='AFM (9 digits) to look up')
    parser.add_argument('--debug', action='store_true', help='Print raw request/response XML (credentials redacted)')
    args = parser.parse_args()

    username = args.username or os.environ.get('AADE_USERNAME')
    password = args.password or os.environ.get('AADE_PASSWORD')
    if not username:
        username = input('AADE Username: ')
    if not password:
        password = getpass.getpass('AADE Password: ')

    envelope = build_soap_envelope(username, password, args.afm)

    if args.debug:
        print("=== REQUEST (credentials redacted) ===")
        debug_tree = etree.fromstring(envelope)
        # Redact credentials in debug output
        for el in debug_tree.iter(f'{{{NS_WSSE}}}Username', f'{{{NS_WSSE}}}Password'):
            el.text = '***REDACTED***'
        print(etree.tostring(debug_tree, pretty_print=True).decode())

    try:
        response = requests.post(
            AADE_ENDPOINT,
            data=envelope,
            headers={'Content-Type': 'application/soap+xml; charset=utf-8'},
            timeout=AADE_TIMEOUT,
        )
    except requests.exceptions.RequestException as e:
        print(f"Connection error: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"HTTP Status: {response.status_code}")

    if args.debug:
        print("=== RESPONSE (caller identity redacted) ===")
        try:
            resp_tree = etree.fromstring(response.content)
            # Redact caller identity fields
            for tag in ('token_username', 'token_afm', 'token_afm_fullname',
                        'afm_called_by', 'afm_called_by_fullname'):
                for el in resp_tree.iter(f'{{{NS_TYPES}}}{tag}'):
                    el.text = '***REDACTED***'
            print(etree.tostring(resp_tree, pretty_print=True).decode())
        except etree.XMLSyntaxError:
            print(response.text)

    if response.status_code != 200:
        print(f"Error: HTTP {response.status_code}", file=sys.stderr)
        sys.exit(1)

    result = parse_response(response.content)
    print("\n=== PARSED RESULT ===")
    for key, value in result.items():
        print(f"  {key}: {value}")


if __name__ == '__main__':
    main()
