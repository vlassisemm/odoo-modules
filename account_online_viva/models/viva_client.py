# Copyright 2026 Vlassis Emmanouil
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
import logging

import requests as http_requests  # Odoo namespace clash: MUST alias

_logger = logging.getLogger(__name__)

# Safety backstop: stop paginating transaction search after this many pages even
# if the API keeps reporting more, to avoid an unbounded loop on a misbehaving
# response.
MAX_PAGES = 1000

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
                data={
                    'grant_type': 'client_credentials',
                    # Data-services (transaction search) scope. The cached token is
                    # reused for wallet discovery too; verify both endpoints accept it.
                    'scope': 'urn:viva:payments:biservices:publicapi',
                },
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
        wallets = []
        for w in rows:
            wallet_id = w.get('walletId') or w.get('WalletId')
            wallets.append({
                'wallet_id': str(wallet_id) if wallet_id else False,
                'iban': w.get('iban') or w.get('Iban') or w.get('IBAN'),
                'currency_code': w.get('currencyCode', w.get('CurrencyCode')),
                'balance': (
                    w.get('available')
                    if 'available' in w
                    else w.get('Available', w.get('availableBalance', w.get('AvailableBalance')))
                ),
                'name': w.get('friendlyName') or w.get('FriendlyName') or w.get('name') or '',
            })
        return wallets

    def search_transactions(self, wallet_id, date_from, date_to, page=1, page_size=500):
        url = '%s/dataservices/v1/accounttransactions/Search' % self._api_host
        wallet_id_payload = int(wallet_id) if str(wallet_id).isdigit() else wallet_id
        body = {
            'WalletId': wallet_id_payload,
            'DateFrom': date_from.strftime('%Y-%m-%d'),
            'DateTo': date_to.strftime('%Y-%m-%d'),
        }
        page_size = min(max(int(page_size), 1), 500)
        page = max(int(page), 1)
        transactions = []
        pages_fetched = 0
        while True:
            params = {
                'PageSize': page_size,
                'Page': page,
                'OrderBy': 'Ascending',
            }
            try:
                resp = http_requests.post(
                    url,
                    headers=self._headers(),
                    params=params,
                    json=body,
                    timeout=self.timeout,
                )
                resp.raise_for_status()
                data = resp.json()
            except VivaApiError:
                raise
            except Exception as exc:
                raise VivaApiError('Transaction search failed: %s' % exc) from exc

            pages_fetched += 1

            if isinstance(data, list):
                transactions.extend(data)
                break

            rows = (
                data.get('data')
                or data.get('Data')
                or data.get('transactions')
                or data.get('Transactions')
                or data.get('Items')
                or []
            )
            transactions.extend(rows)

            total_pages = data.get('totalPages') or data.get('TotalPages')
            if not total_pages or page >= int(total_pages):
                break
            if pages_fetched >= MAX_PAGES:
                _logger.warning(
                    'Viva transaction search reached the %s-page safety cap for '
                    'wallet %s; remaining pages were not fetched.',
                    MAX_PAGES, wallet_id)
                break
            page += 1
        return transactions
