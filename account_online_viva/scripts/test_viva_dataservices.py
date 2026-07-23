#!/usr/bin/env python3
# Copyright 2026 Vlassis Emmanouil
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
"""Standalone smoke test for Viva Data Services access (no Odoo needed).

Goal: confirm a demo account has Data Services enabled, that our OAuth token +
scope are accepted, and capture a real Sale Transactions File Request response.

CREDENTIALS — never paste secrets into chat. Provide them via env vars:

    export VIVA_CLIENT_ID=...           # OAuth2 client id (Smart Checkout)
    export VIVA_CLIENT_SECRET=...       # OAuth2 client secret
    export VIVA_ENV=demo                # demo | production  (default: demo)
    python account_online_viva/scripts/test_viva_dataservices.py --date 2026-06-17

Or keep them in a gitignored file and source it first:

    set -a; source /path/to/your-secrets.env; set +a
    python account_online_viva/scripts/test_viva_dataservices.py --date 2026-06-17

Requires: requests  (pip install requests)

This script prints status codes and response bodies but NEVER prints the secret
or the full access token.
"""
import argparse
import json
import os
import sys
import uuid

import requests

HOSTS = {
    'demo': {
        'accounts': 'https://demo-accounts.vivapayments.com',
        'api': 'https://demo-api.vivapayments.com',
    },
    'production': {
        'accounts': 'https://accounts.vivapayments.com',
        'api': 'https://api.vivapayments.com',
    },
}

# Confirmed scopes (Viva OpenAPI spec + live verification 2026-07-07): every
# /dataservices/* endpoint (acquiring exports, Account Transactions, MT940,
# webhooks) uses datafileapi; the Wallet API uses core:api:merchants:wallets.
# Override with --scope.
DEFAULT_SCOPE = 'urn:viva:payments:biservices:datafileapi'

# Confirmed File Request (export) endpoint for the Sale Transactions report.
# Download is then GET {api}/dataservices/v1/FileRequests/File/{requestId}/{fileId}.
DEFAULT_REPORT_PATH = '/dataservices/v1/transactions/exports'

TIMEOUT = 60


def get_token(accounts_host, client_id, client_secret, scope):
    url = '%s/connect/token' % accounts_host
    data = {'grant_type': 'client_credentials'}
    if scope:
        data['scope'] = scope
    print('[token] scope requested: %s' % (scope or '(none)'))
    resp = requests.post(
        url,
        auth=(client_id, client_secret),
        data=data,
        timeout=TIMEOUT,
    )
    print('[token] POST %s -> HTTP %s' % (url, resp.status_code))
    if resp.status_code != 200:
        print('[token] body: %s' % resp.text[:1000])
        resp.raise_for_status()
    data = resp.json()
    tok = data.get('access_token', '')
    print('[token] OK: access_token received (len=%s), token_type=%s, expires_in=%s'
          % (len(tok), data.get('token_type'), data.get('expires_in')))
    return tok


def request_report(api_host, report_path, token, date, http_method, use_query,
                   basic=None):
    url = '%s%s' % (api_host, report_path)
    headers = {'Content-Type': 'application/json'}
    if token:
        headers['Authorization'] = 'Bearer %s' % token
    params = {'date': date} if use_query else None
    if 'exports' in report_path.lower():
        # Sale Transactions File Request export body (confirmed shape).
        body = {'Id': str(uuid.uuid4()), 'Date': date, 'FileType': 'csv'}
    elif use_query:
        body = None
    else:
        body = {'date': date}
    print('\n[report] %s %s  (date=%s, %s, auth=%s)'
          % (http_method, url, date, 'query' if use_query else 'json body',
             'basic' if basic else 'bearer'))
    resp = requests.request(
        http_method, url, headers=headers, params=params, json=body,
        auth=basic, timeout=TIMEOUT)
    print('[report] -> HTTP %s' % resp.status_code)
    ct = resp.headers.get('Content-Type', '')
    print('[report] Content-Type: %s' % ct)
    try:
        print('[report] body: %s' % json.dumps(resp.json(), indent=2)[:2000])
    except ValueError:
        print('[report] body (text): %s' % resp.text[:2000])
    return resp


def main():
    p = argparse.ArgumentParser(description='Viva Data Services smoke test')
    p.add_argument('--date', help='Report date, YYYY-MM-DD '
                                  '(required unless --token-only)')
    p.add_argument('--env', default=os.environ.get('VIVA_ENV', 'demo'),
                   choices=['demo', 'production'])
    p.add_argument('--scope', default=DEFAULT_SCOPE)
    p.add_argument('--no-scope', action='store_true',
                   help='Omit the scope param entirely from the token request')
    p.add_argument('--basic', action='store_true',
                   help='Skip OAuth; call the endpoint with HTTP Basic auth '
                        '(client_id:client_secret) directly')
    p.add_argument('--report-path', default=DEFAULT_REPORT_PATH)
    p.add_argument('--method', default='POST', choices=['POST', 'GET'])
    p.add_argument('--query', action='store_true',
                   help='Send the date as a query param instead of JSON body')
    p.add_argument('--token-only', action='store_true',
                   help='Only test the OAuth token (skip the file request)')
    args = p.parse_args()
    if not args.token_only and not args.date:
        p.error('--date is required unless --token-only is set')

    client_id = os.environ.get('VIVA_CLIENT_ID')
    client_secret = os.environ.get('VIVA_CLIENT_SECRET')
    if not client_id or not client_secret:
        sys.exit('ERROR: set VIVA_CLIENT_ID and VIVA_CLIENT_SECRET env vars '
                 '(do not pass secrets on the command line).')

    hosts = HOSTS[args.env]
    print('=== Viva Data Services smoke test (env=%s) ===' % args.env)

    if args.basic:
        print('[auth] HTTP Basic (no OAuth token)')
        request_report(hosts['api'], args.report_path, None,
                       args.date, args.method, args.query,
                       basic=(client_id, client_secret))
        return

    scope = None if args.no_scope else args.scope
    token = get_token(hosts['accounts'], client_id, client_secret, scope)

    if args.token_only:
        print('\nToken OK. Re-run without --token-only to try the file request.')
        return

    request_report(hosts['api'], args.report_path, token,
                   args.date, args.method, args.query)
    print('\nDone. Share this output (it contains no secrets) so we can pin the '
          'exact endpoint/params and confirm Data Services access.')


if __name__ == '__main__':
    main()
