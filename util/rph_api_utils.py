import time

import requests

from constants import (RPH_EVENTS_URL, RPH_EVENT_URL, RPH_GAME_STORES_URL,
                       RPH_STANDINGS_URL, RPH_USERS_URL)

_MAX_RETRIES = 3
_RETRY_DELAY = 2  # seconds between retries


# Page sizes for the successive passes in fetch_events. 250 is the server's cap (it
# silently clamps anything larger); 173 is coprime with it, so the second pass puts
# its boundaries somewhere new and returns the rows the first pass dropped between
# pages. See fetch_events for the measurements.
_EVENT_PAGE_SIZES = (250, 173)


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

    def iter_events(self, start_date_after, start_date_before, extra_params=None,
                    require_started=True, countries=("CA",)):
        """
        Yield filtered RPH events one at a time, a page at a time — nothing is
        accumulated across pages.

        Callers that keep only the events they match (the digests) should use this
        rather than get_events: a wide fetch is thousands of event dicts, and holding
        them all at once is what the gc.collect() calls around these fetches exist to
        clean up. Peak retention here is one page plus the matches.

        require_started: if True (default), also drop events with starting_player_count == 0.
            Set to False when pulling upcoming events (e.g. a digest preview) — upcoming
            events haven't started, so their starting_player_count is always 0.

        countries: store country codes to keep, or None for every country. Defaults to
            Canada, which is what the league scores. The CCQ digest passes None because
            its 600km radius reaches New York and Michigan stores worth travelling to.
        """
        for page_results in self.fetch_events(start_date_after, start_date_before, extra_params=extra_params):
            for event in page_results:
                if countries and event['store']['country'] not in countries:
                    continue
                if require_started and event['starting_player_count'] <= 0:
                    continue
                yield event

    def get_events(self, start_date_after, start_date_before, extra_params=None,
                   require_started=True, countries=("CA",)):
        """Every event iter_events would yield, as a list."""
        return list(self.iter_events(start_date_after, start_date_before,
                                     extra_params=extra_params,
                                     require_started=require_started,
                                     countries=countries))

    def fetch_events(self, start_date_after, start_date_before, extra_params=None):
        """
        Yield pages of RPH events, de-duplicated, and re-walk the window once if the
        first walk came back short.

        RPH's pagination drops rows. A plain page-by-page read returns some events
        twice and *silently skips* others: measured 2026-10-02 over one prerelease
        week, the endpoint reported 351 events and a 50-per-page walk returned 351
        rows but only 344 distinct ones. One of the 7 it dropped was a Richmond Hill
        prerelease a store had just added, which is how this was found. Over the S13
        season window — what store classification and #where-to-play are built from —
        it dropped 3 of 1,211.

        The cause is page boundaries, not timing. Walking the same window twice at the
        same page_size returns byte-identical results, gap included, so it is not that
        events arrive mid-walk; the server's ordering simply is not a total order, and
        rows near a boundary fall between consecutive pages. Which rows are lost
        therefore depends only on where the boundaries land.

        So each pass uses a *different* page_size, and the second pass sees what the
        first could not. Measured on a 1,648-event window: 250-per-page returned 1,644
        and 173-per-page returned 1,643, but their union was all 1,648. The sizes are
        deliberately not multiples of one another, so no boundary recurs.

        A pass is only run if the previous one came up short of the `count` the
        endpoint reports, which is also the only signal that anything went missing.
        Bounded at two passes; a still-short result is logged rather than chased.

        Callers see each event at most once, so none of them need to de-duplicate.
        """
        params = {
            'start_date_after': start_date_after,
            'start_date_before': start_date_before,
            'display_status': 'past',
            'game_slug': 'disney-lorcana',
            'latitude': 43.653226,
            'longitude': -79.3831843,
            'num_miles': 250,
            'page': 1,
            'gameplay_format_ids': ["2b6e184a-72d7-4ae5-a5f1-f16d79646c39", "4f43d777-beeb-4e1e-a04c-c1f2b3c5258a"],
        }
        if extra_params:
            params.update(extra_params)
            # Remove any keys explicitly set to None
            params = {k: v for k, v in params.items() if v is not None}

        seen: set = set()
        expected = None

        for attempt, page_size in enumerate(_EVENT_PAGE_SIZES, start=1):
            params['page']      = 1
            params['page_size'] = page_size
            while True:
                current_page = _get_with_retry(self.session, RPH_EVENTS_URL, params)
                if expected is None:
                    expected = current_page.get('count')
                fresh = [e for e in current_page['results'] if e['id'] not in seen]
                seen.update(e['id'] for e in fresh)
                if fresh:
                    yield fresh
                if not current_page.get('next'):
                    break
                params['page'] = current_page['next']

            if expected is None or len(seen) >= expected:
                return
            if attempt < len(_EVENT_PAGE_SIZES):
                print(f"  ⚠ RPH returned {len(seen)} of {expected} events "
                      f"(dropped at page boundaries) — re-walking at a different page size")
            else:
                print(f"  ⚠ RPH still short after {attempt} passes: {len(seen)} of "
                      f"{expected} events. Some may be missing from this refresh.")

    def get_event_by_id(self, event_id):
        event = self.fetch_event_by_id(event_id)
        if not event or 'store' not in event:
            return None
        # filter on Ontario, Canada stores and events with more than 0 people
        if event['store']['country'] == "CA" and event['starting_player_count'] > 0:
            return event
        return None

    def fetch_event_by_id(self, event_id):
        return _get_with_retry(self.session, RPH_EVENT_URL.format(event_id=event_id))

    def get_standings_from_tournament_round_id(self, round_id):
        url = RPH_STANDINGS_URL.format(round_id=round_id)
        data = _get_with_retry(self.session, url)
        return data['standings']

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
