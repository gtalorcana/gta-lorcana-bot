import time

import requests

from constants import RPH_EVENTS_URL, RPH_GAME_STORES_URL, RPH_STANDINGS_URL, RPH_MATCHES_URL, RPH_USERS_URL

_MAX_RETRIES = 3
_RETRY_DELAY = 2  # seconds between retries


def _get_with_retry(session, url, params=None):
    """
    GET a URL with up to _MAX_RETRIES attempts.
    Raises RuntimeError if all attempts fail.
    """
    last_error = None
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            resp = session.get(url, params=params, timeout=10)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            last_error = e
            if attempt < _MAX_RETRIES:
                print(f"  ⚠ RPH API attempt {attempt}/{_MAX_RETRIES} failed: {e} — retrying in {_RETRY_DELAY}s...")
                time.sleep(_RETRY_DELAY)
    raise RuntimeError(f"RPH API failed after {_MAX_RETRIES} attempts: {last_error}")


class RphApi:
    def __init__(self):
        self.session = requests.Session()

    def get_game_stores(self, extra_params=None):
        results = []
        for page_results in self.fetch_game_stores(extra_params=extra_params):
            for game_store in page_results:
                # filter on Ontario, Canada stores
                if (game_store['store']['country'] == "CA" and
                        game_store['store']['administrative_area_level_1_short'] == "ON"):
                    results.append(game_store)
        return results

    def fetch_game_stores(self, extra_params=None):
        params = {
            'latitude': 43.653226,
            'longitude': -79.3831843,
            'num_miles': 250,
            'game_id': '1',
            'page': 1,
            'page_size': 50,
        }
        if extra_params:
            params.update(extra_params)
            # Remove any keys explicitly set to None
            params = {k: v for k, v in params.items() if v is not None}

        current_page = _get_with_retry(self.session, RPH_GAME_STORES_URL, params)
        yield current_page['results']

        while current_page['next']:
            params['page'] = current_page['next']
            current_page = _get_with_retry(self.session, RPH_GAME_STORES_URL, params)
            yield current_page['results']

    def get_events(self, start_date_after, start_date_before, extra_params=None):
        results = []
        for page_results in self.fetch_events(start_date_after, start_date_before, extra_params=extra_params):
            for event in page_results:
                # filter on Ontario, Canada stores and events with more than 0 people
                if (event['store']['country'] == "CA" and
                        event['starting_player_count'] > 0):
                    results.append(event)
        return results

    def fetch_events(self, start_date_after, start_date_before, extra_params=None):
        params = {
            'start_date_after': start_date_after,
            'start_date_before': start_date_before,
            'display_status': 'past',
            'game_slug': 'disney-lorcana',
            'latitude': 43.653226,
            'longitude': -79.3831843,
            'num_miles': 250,
            'page': 1,
            'page_size': 50,
            'gameplay_format_ids': ["2b6e184a-72d7-4ae5-a5f1-f16d79646c39", "4f43d777-beeb-4e1e-a04c-c1f2b3c5258a"],
        }
        if extra_params:
            params.update(extra_params)
            # Remove any keys explicitly set to None
            params = {k: v for k, v in params.items() if v is not None}

        current_page = _get_with_retry(self.session, RPH_EVENTS_URL, params)
        yield current_page['results']

        while current_page['next']:
            params['page'] = current_page['next']
            current_page = _get_with_retry(self.session, RPH_EVENTS_URL, params)
            yield current_page['results']

    def get_event_by_id(self, event_id, extra_params=None):
        results = []
        for page_results in self.fetch_event_by_id(event_id, extra_params=extra_params):
            for event in page_results:
                # filter on Ontario, Canada stores and events with more than 0 people
                if (event['store']['country'] == "CA" and
                        event['starting_player_count'] > 0):
                    results.append(event)
        return results

    def fetch_event_by_id(self, event_id, extra_params=None):
        params = {'id': event_id}
        if extra_params:
            params.update(extra_params)
            # Remove any keys explicitly set to None
            params = {k: v for k, v in params.items() if v is not None}

        current_page = _get_with_retry(self.session, RPH_EVENTS_URL, params)
        yield current_page['results']

    def get_standings_from_tournament_round_id(self, round_id):
        url = RPH_STANDINGS_URL.format(round_id=round_id)
        data = _get_with_retry(self.session, url)
        return data['standings']

    def get_matches_from_tournament_round_id(self, round_id):
        url = RPH_MATCHES_URL.format(round_id=round_id)
        data = _get_with_retry(self.session, url)
        return data['matches']

    def lookup_user_by_username(self, username: str) -> dict | None:
        """
        Search for an RPH user by display name.
        Returns the first matching user dict (with at least 'id' and 'username'), or None if not found.
        NOTE: RPH username search may be case-sensitive — pass the username exactly as entered.
        """
        data = _get_with_retry(self.session, RPH_USERS_URL, params={'username': username})
        results = data.get('results', [])
        return results[0] if results else None

    def get_user_event_history(self, rph_id: str) -> list:
        """
        Fetch all event history entries for an RPH user.
        Returns a flat list of event dicts. Each entry is expected to have:
          store.id, start_datetime, registration_status
        NOTE: field names are based on the spec — verify against actual API response.
        """
        url = RPH_USERS_URL + str(rph_id) + '/event-history/'
        results = []
        params = {'page': 1, 'page_size': 50}
        while True:
            data = _get_with_retry(self.session, url, params)
            results.extend(data.get('results', []))
            if not data.get('next'):
                break
            params['page'] += 1
        return results