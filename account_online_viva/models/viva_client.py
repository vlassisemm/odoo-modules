# Copyright 2026 Vlassis Emmanouil
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
import logging
import time

import requests as http_requests  # Odoo namespace clash: MUST alias

_logger = logging.getLogger(__name__)

# Safety backstop: abort a transaction search that exceeds this many pages, to
# avoid an unbounded loop on a misbehaving response. Aborting (rather than
# returning a truncated list) keeps the sync watermark from advancing past
# transactions that were never fetched.
MAX_PAGES = 1000

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

# Data Services scope: unlocks every /dataservices/* endpoint (verified live
# against production on 2026-07-07: token 200, accounttransactions v2 200).
DATA_SCOPE = 'urn:viva:payments:biservices:datafileapi'
# Wallet API scope (core_api audience).
# TODO (credentials): the current production credential is biservices-only —
# minting this scope returns 400 invalid_scope, and biservices tokens are
# rejected by the wallet endpoints with 401 "audience invalid" (verified
# 2026-07-07). Wallet discovery therefore needs a second, core_api credential
# with Wallet API access enabled by Viva; until then list_wallets() fails with
# VivaApiError and journals must be set up manually (or wallet ids taken from
# the walletId field of the transactions themselves).
WALLET_SCOPE = 'urn:viva:payments:core:api:merchants:wallets'

# Seconds subtracted from expires_in before a cached token counts as stale.
TOKEN_EXPIRY_MARGIN = 60


def pick(mapping, *keys, default=None):
    """Return the value of the first key present in ``mapping``.

    Viva responses vary in key casing across endpoints; a present-but-null
    key still wins (callers chain ``pick(...) or fallback`` when needed).
    """
    for key in keys:
        if key in mapping:
            return mapping[key]
    return default


class VivaApiError(Exception):
    """Raised for any failure talking to the Viva API."""


class VivaClient:
    def __init__(self, client_id, client_secret, environment='demo', timeout=60):
        self.client_id = client_id
        self.client_secret = client_secret
        self.environment = environment if environment in HOSTS else 'demo'
        self.timeout = timeout
        self._tokens = {}  # scope -> (access_token, staleness deadline)

    @property
    def _accounts_host(self):
        return HOSTS[self.environment]['accounts']

    @property
    def _api_host(self):
        return HOSTS[self.environment]['api']

    def _token(self, scope=DATA_SCOPE):
        cached = self._tokens.get(scope)
        if cached and time.monotonic() < cached[1]:
            return cached[0]
        url = '%s/connect/token' % self._accounts_host
        try:
            resp = http_requests.post(
                url,
                auth=(self.client_id, self.client_secret),
                data={
                    'grant_type': 'client_credentials',
                    'scope': scope,
                },
                timeout=self.timeout,
            )
            resp.raise_for_status()
            data = resp.json()
            token = data['access_token']
            expires_in = int(data.get('expires_in') or 3600)
        except Exception as exc:
            raise VivaApiError('OAuth token request failed: %s' % exc) from exc
        deadline = time.monotonic() + max(expires_in - TOKEN_EXPIRY_MARGIN, 60)
        self._tokens[scope] = (token, deadline)
        return token

    def _headers(self, scope=DATA_SCOPE):
        return {'Authorization': 'Bearer %s' % self._token(scope)}

    def list_wallets(self):
        # TODO (credentials): unverifiable against production until Viva
        # enables Wallet API access on a core_api credential — see WALLET_SCOPE.
        url = '%s/merchants/v1/wallets' % self._api_host
        headers = self._headers(WALLET_SCOPE)
        try:
            resp = http_requests.get(url, headers=headers, timeout=self.timeout)
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            raise VivaApiError('Wallet discovery failed: %s' % exc) from exc
        rows = data if isinstance(data, list) else pick(
            data, 'wallets', 'Wallets', default=[])
        wallets = []
        for w in rows:
            wallet_id = pick(w, 'walletId', 'WalletId')
            wallets.append({
                'wallet_id': str(wallet_id) if wallet_id else False,
                'iban': pick(w, 'iban', 'Iban', 'IBAN'),
                'currency_code': pick(w, 'currencyCode', 'CurrencyCode'),
                'balance': pick(w, 'available', 'Available',
                                'availableBalance', 'AvailableBalance'),
                'name': pick(w, 'friendlyName', 'FriendlyName', 'name') or '',
            })
        return wallets

    def search_transactions(self, wallet_id, date_from, date_to, page_size=500):
        url = '%s/dataservices/v2/accounttransactions/Search' % self._api_host
        wallet_id_payload = int(wallet_id) if str(wallet_id).isdigit() else wallet_id
        body = {
            'WalletId': wallet_id_payload,
            'DateFrom': date_from.strftime('%Y-%m-%d'),
            'DateTo': date_to.strftime('%Y-%m-%d'),
        }
        page_size = min(max(int(page_size), 1), 500)
        headers = self._headers()  # a token failure propagates unwrapped
        transactions = []
        page = 1
        while True:
            params = {
                'PageSize': page_size,
                'Page': page,
                'OrderBy': 'Ascending',
            }
            try:
                resp = http_requests.post(
                    url,
                    headers=headers,
                    params=params,
                    json=body,
                    timeout=self.timeout,
                )
                resp.raise_for_status()
                # "No (more) data" is signalled by 204 No Content (empty
                # body), so only parse JSON on a 200. totalPages is DEPRECATED
                # in the Viva spec and must not drive pagination.
                data = resp.json() if resp.status_code != 204 else None
            except Exception as exc:
                raise VivaApiError('Transaction search failed: %s' % exc) from exc

            if data is None:
                break
            rows = data if isinstance(data, list) else pick(
                data, 'data', 'Data', 'transactions', 'Transactions', 'Items',
                default=None) or []
            if not rows:
                break
            transactions.extend(rows)
            if len(rows) < page_size:
                break
            if page >= MAX_PAGES:
                raise VivaApiError(
                    'Viva transaction search exceeded %s pages for wallet %s; '
                    'aborting to avoid a partial import.' % (MAX_PAGES, wallet_id))
            page += 1
        return transactions
