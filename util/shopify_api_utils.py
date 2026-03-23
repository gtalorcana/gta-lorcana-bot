"""
Shopify API client for ETB discount whitelist automation.

Authentication uses the client credentials OAuth flow:
  - SHOPIFY_CLIENT_ID in constants.py (non-secret, identifies the app)
  - SHOPIFY_CLIENT_SECRET as a Fly.io secret (never logged or exposed)

The access token is cached in memory and refreshed after 23 hours or on 401.
Call ShopifyApi.prefetch_token() at bot startup to warm the cache.

Whitelist mechanism: price rule customer prerequisite list.
  The ETBGTALORCANA discount code is backed by a price rule with
  customer_selection="prerequisite". Approved customers are added to
  the price rule's prerequisite_customer_ids list.

See specs/SHOPIFY_DISCOUNT_SPEC.md for full context.
"""

import requests
from datetime import datetime, timezone, timedelta

_API_VERSION    = "2024-01"
_TOKEN_LIFETIME = timedelta(hours=23)

# Module-level token cache — shared across all ShopifyApi instances in this process.
_cached_token:     str | None      = None
_token_expires_at: datetime | None = None


class ShopifyApi:
    def __init__(self, client_id: str, client_secret: str, domain: str):
        self.client_id     = client_id
        self.client_secret = client_secret
        self.domain        = domain
        self.base_url      = f"https://{domain}/admin/api/{_API_VERSION}"
        self.session       = requests.Session()

    # ── Token management ─────────────────────────────────────────────────────

    def _fetch_token(self) -> str:
        """Exchange client credentials for a Shopify access token."""
        resp = requests.post(
            f"https://{self.domain}/admin/oauth/access_token",
            json={
                'client_id':     self.client_id,
                'client_secret': self.client_secret,
                'grant_type':    'client_credentials',
            },
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()['access_token']

    def _get_token(self) -> str:
        """Return a valid access token, refreshing if expired or missing."""
        global _cached_token, _token_expires_at
        now = datetime.now(timezone.utc)
        if _cached_token and _token_expires_at and now < _token_expires_at:
            return _cached_token
        _cached_token     = self._fetch_token()
        _token_expires_at = now + _TOKEN_LIFETIME
        return _cached_token

    def prefetch_token(self) -> None:
        """Warm the token cache. Call at bot startup."""
        self._get_token()

    # ── HTTP helper ──────────────────────────────────────────────────────────

    def _request(self, method: str, path: str, **kwargs) -> dict:
        """
        Make an authenticated Shopify API request.
        Automatically retries once on 401 with a fresh token.
        """
        global _cached_token
        for attempt in range(2):
            token = self._get_token()
            resp  = self.session.request(
                method,
                f"{self.base_url}/{path}",
                headers={'X-Shopify-Access-Token': token},
                timeout=10,
                **kwargs,
            )
            if resp.status_code == 401 and attempt == 0:
                _cached_token = None  # force refresh on next _get_token() call
                continue
            resp.raise_for_status()
            return resp.json()
        raise RuntimeError("Shopify auth failed after token refresh")

    # ── API calls ────────────────────────────────────────────────────────────

    def lookup_customer_by_email(self, email: str) -> dict | None:
        """
        Look up a Shopify customer by email address.
        Returns the customer dict if found, or None if no account exists.
        """
        data      = self._request('GET', 'customers/search.json', params={'query': f'email:{email}'})
        customers = data.get('customers', [])
        return customers[0] if customers else None

    def get_price_rule_id(self, discount_code: str) -> int:
        """
        Resolve a discount code string to its backing price rule ID.
        Raises if the code doesn't exist in Shopify.
        """
        data = self._request('GET', 'discount_codes/lookup.json', params={'code': discount_code})
        return data['discount_code']['price_rule_id']

    def is_whitelisted(self, price_rule_id: int, customer_id: int) -> bool:
        """
        Check if a customer is already in the price rule's prerequisite_customer_ids.
        Returns False if the price rule uses open customer selection (shouldn't happen).
        """
        data = self._request('GET', f'price_rules/{price_rule_id}.json')
        rule = data['price_rule']
        if rule.get('customer_selection') != 'prerequisite':
            return False
        return customer_id in (rule.get('prerequisite_customer_ids') or [])

    def add_to_whitelist(self, price_rule_id: int, customer_id: int) -> None:
        """
        Add a customer to the price rule's prerequisite_customer_ids.
        Reads current list, appends the customer, writes back atomically.
        No-ops silently if already present. Raises on API failure.
        """
        data = self._request('GET', f'price_rules/{price_rule_id}.json')
        rule = data['price_rule']
        ids  = list(rule.get('prerequisite_customer_ids') or [])
        if customer_id in ids:
            return
        ids.append(customer_id)
        self._request(
            'PUT',
            f'price_rules/{price_rule_id}.json',
            json={'price_rule': {'id': price_rule_id, 'prerequisite_customer_ids': ids}},
        )
