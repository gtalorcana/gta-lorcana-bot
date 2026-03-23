"""
Shopify API client for ETB discount whitelist automation.

Steps 5 (already-whitelisted check) and 6 (apply whitelist) are stubbed
until Kris confirms the exact whitelist mechanism used in Shopify.

See specs/SHOPIFY_DISCOUNT_SPEC.md for full context.
"""

import requests

_API_VERSION = "2024-01"


class ShopifyApi:
    def __init__(self, token: str, domain: str):
        self.session = requests.Session()
        self.session.headers.update({'X-Shopify-Access-Token': token})
        self.base_url = f"https://{domain}/admin/api/{_API_VERSION}"

    def lookup_customer_by_email(self, email: str) -> dict | None:
        """
        Look up a Shopify customer by email address.
        Returns the customer dict if found, or None if no account exists.
        """
        resp = self.session.get(
            f"{self.base_url}/customers/search.json",
            params={'query': f'email:{email}'},
            timeout=10,
        )
        resp.raise_for_status()
        customers = resp.json().get('customers', [])
        return customers[0] if customers else None

    def is_whitelisted(self, customer: dict) -> bool:
        """
        Check if the customer is already whitelisted for the ETB GTA Lorcana discount.

        STUB — implement once Kris confirms the whitelist mechanism:
          e.g. tag-based: return 'gta-lorcana' in (customer.get('tags') or '')
          e.g. segment-based: check segment membership
        """
        # TODO: implement once Kris confirms whitelist mechanism
        return False

    def whitelist_customer(self, customer: dict) -> None:
        """
        Apply the ETB GTA Lorcana discount whitelist to a Shopify customer.

        STUB — implement once Kris confirms the whitelist mechanism:
          e.g. PUT /customers/{id} to add tag 'gta-lorcana'
        Raises on failure so the caller can handle and notify Ryan.
        """
        # TODO: implement once Kris confirms whitelist mechanism
        customer_id = customer.get('id', 'unknown')
        print(f"STUB: would whitelist Shopify customer ID {customer_id} here")
