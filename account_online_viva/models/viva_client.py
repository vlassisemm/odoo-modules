# Copyright 2026 Vlassis Emmanouil
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
import logging

import requests as http_requests  # Odoo namespace clash: MUST alias

_logger = logging.getLogger(__name__)

# NOTE (verify against sandbox): hosts and the v1 path segment.
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


class VivaApiError(Exception):
    """Raised for any failure talking to the Viva API."""


class VivaClient:
    def __init__(self, client_id, client_secret, environment='demo', timeout=60):
        self.client_id = client_id
        self.client_secret = client_secret
        self.environment = environment if environment in HOSTS else 'demo'
        self.timeout = timeout
        self._access_token = None

    @property
    def _accounts_host(self):
        return HOSTS[self.environment]['accounts']

    @property
    def _api_host(self):
        return HOSTS[self.environment]['api']

    def _token(self):
        if self._access_token:
            return self._access_token
        url = '%s/connect/token' % self._accounts_host
        try:
            resp = http_requests.post(
                url,
                auth=(self.client_id, self.client_secret),
                data={'grant_type': 'client_credentials'},
                timeout=self.timeout,
            )
            resp.raise_for_status()
            self._access_token = resp.json()['access_token']
        except Exception as exc:
            raise VivaApiError('OAuth token request failed: %s' % exc) from exc
        return self._access_token

    def _headers(self):
        return {'Authorization': 'Bearer %s' % self._token()}

    def list_wallets(self):
        # NOTE (verify): /walletaccounts/v1/wallets shape.
        url = '%s/walletaccounts/v1/wallets' % self._api_host
        try:
            resp = http_requests.get(url, headers=self._headers(), timeout=self.timeout)
            resp.raise_for_status()
            data = resp.json()
        except VivaApiError:
            raise
        except Exception as exc:
            raise VivaApiError('Wallet discovery failed: %s' % exc) from exc
        rows = data if isinstance(data, list) else data.get('wallets', data.get('Wallets', []))
        return [{
            'wallet_id': w.get('walletId') or w.get('WalletId'),
            'iban': w.get('iban') or w.get('Iban') or w.get('IBAN'),
            'currency_code': w.get('currencyCode', w.get('CurrencyCode')),
            'balance': w.get('availableBalance', w.get('AvailableBalance')),
            'name': w.get('friendlyName') or w.get('FriendlyName') or w.get('name') or '',
        } for w in rows]

    def search_transactions(self, wallet_id, date_from, date_to, page=0, page_size=100):
        # NOTE (verify): path v1 vs v2; pagination keys.
        url = '%s/dataservices/v1/accounttransactions/Search' % self._api_host
        body = {
            'WalletId': wallet_id,
            'DateFrom': date_from.strftime('%Y-%m-%d'),
            'DateTo': date_to.strftime('%Y-%m-%d'),
            'Page': page,
            'MaxResults': page_size,
        }
        try:
            resp = http_requests.post(
                url, headers=self._headers(), json=body, timeout=self.timeout)
            resp.raise_for_status()
            data = resp.json()
        except VivaApiError:
            raise
        except Exception as exc:
            raise VivaApiError('Transaction search failed: %s' % exc) from exc
        if isinstance(data, list):
            return data
        return data.get('transactions') or data.get('Transactions') or data.get('Items') or []
