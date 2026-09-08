# Results Reporting Pipeline

1. Organizer creates a thread in `#results-reporting` with an RPH event URL as the first message
2. Bot validates the URL format, fetches event data and standings from RPH
3. Writes standings rows first, then the event row — so if a crash occurs mid-write, the missing event row signals a safe retry rather than a false duplicate
4. On validation error: posts feedback and waits for the organizer to edit — `on_message_edit` re-triggers automatically
5. On API error: schedules auto-retries (up to `RPH_RETRY_ATTEMPTS`, spaced `RPH_RETRY_DELAY` seconds apart)
6. If all retries fail: pings all `ADMIN_USER_IDS` in the thread
7. Deleting a thread removes its event data from the sheet via `on_thread_delete`
8. After a successful write, the bot fuzzy-matches any new Playhub players and posts linking suggestions to the mod channel

**Duplicate vs retry detection:** same URL + same thread = retry (allowed, overwrites). Same URL + different thread = true duplicate (rejected).

**Submission eligibility.** A submitted event must pass all of these, in order:

1. Thread created on or before the season's final day (`bot.py`, `is_past_reporting_cutoff`)
2. Message body is exactly an RPH event URL (`EVENTS_URL_RE`)
3. URL not already reported by another thread (`results.py`)
4. `gameplay_format` is Core Constructed or Infinity Constructed
5. **Not a Set Championship** — `stores.is_set_champs_event()`
6. Event date inside the season window

Rules 5 and 6 are gated behind `_fetch_single_event(..., validate_eligibility=True)`, set only for
live submissions. The bulk re-fetch path (`_fetch_event_rows_and_standings`) deliberately leaves it
False: it re-reads rows already accepted into the sheet, and must not start rejecting history when
the eligibility rules change.

The Set Champs rule is separate from the date rule because **the season and Set Champs windows
overlap** — S13 ran the season to Sep 18 while Set Champs ran Sep 4-27. An in-window Set
Championship is otherwise a perfectly valid Core Constructed event, so nothing else rejects it. One
(event 843203) was accepted into S13 before this rule existed.

---

## Set Championships

The `set_champs_daily` task calls `refresh_set_champs()` in `stores.py` every morning during the set champs window. It fetches all Ontario Lorcana events in the `SET_CHAMPS` date range (including upcoming and in-progress), filters to Set Championships via `is_set_champs_event()`, and overwrites the Set Champs sheet.

`is_set_champs_event()` matches the keyword `"Set Champ"` (case-insensitive) against two
independent signals, as a **union**. Neither alone is complete:

- **Phase text** - the `phase_name` / `phase_description` RPH copies onto the event from the event
  configuration template it was built from ("Participate in the preliminary rounds for the Disney
  Lorcana Set Championships..."). It names no set, so it is set-agnostic, and it is stored on the
  event itself, so it keeps resolving for past sets. It misses stores that build a genuine Set
  Championship on a generic template - three S10 events and J&B's event 778116 all ran on
  `Weekly Play (Constructed)`, with an empty phase description. Only the name catches those.
- **Event name** - store-authored, so it drifts. It misses stores that mistitle a properly
  templated event: one S13 store called theirs "Attack Of the Vine **Store** Championship". The
  phase text catches those.

Measured on the S13 window: name alone 76, union 77. On S10 (a past set): name alone 74, union 77.

**Never narrow the name match to the Set Champs window.** A previous set's championships can run
early in a new league season, well outside it - event 778116 is a real Set Championship on
2026-07-18, seven weeks before the S13 window opened on 09-04. It sits on a generic template with
no phase text, so the name is the only signal that rejects it. Date-scoping that arm would let
previous-set championships straight into league results.

The two signals are independent, and a store has to get *both* wrong for an event to slip through.
Across S10, S13 and Winterspell that has not happened: every mis-templated event was still named
"Set Championship", and the one mis-named event was still correctly templated. That is an observed
base rate, not a guarantee - a store that picks a generic template *and* an ordinary name is
undetectable, and would have to be caught by review.

**Do not resolve the event's category through RPH's `event-configuration-templates` endpoint.**
That was tried and shipped, and it does not hold. RPH dropped the "Attack of the Vine! Set
Championship" template from the list two days into the S13 set champs window - 12 templates on
Sep 5, 11 on Sep 7 - *while the window was still running*, so the lookup silently returned nothing
for every event and the mistitled S13 event stopped being detected. Retired sets' templates are
never listed at all. The phase text carries the same information without the lookup, without an
extra API call, and without disappearing.

`refresh_set_champs` logs how many rows matched on the store's event name only (no Set Champs phase
text). Those are the fragile ones, riding entirely on a store's chosen title.

**Set Champs sheet columns (A2:I):**
```
Date | Time (Toronto) | Store ID | Store Name | City | Player Cap | Format | Event Name | RPH Link
```
Store ID is the RPH store identity key — the same value the Store Classification tab is keyed on,
so the two can be joined. Row 1 (the header) is hand-maintained; `create_season_sheets` adds the
tab bare and seeds no header.

The task starts on `SEASON_START_DATE` so the sheet is populated as soon as stores register their events on RPH (some post on day 1 of the season).

**Manual run** (inspect output before writing):
```bash
python scripts/rph_get_set_championship_events.py
```

Set `WRITE_TO_SHEET = True` in the script once the output looks correct. Set `SHOW_ALL = True` to dump every event in the window with its category and name, to sanity-check the keyword.

---

## RPH Event Watcher

Users can subscribe to DM alerts for any RPH event that is full or filling up.

**Commands:**
- `/watch-rph-event event_id:413990 end_date:2026-03-29` — subscribe; bot confirms current registration status immediately
- `/unwatch-rph-event event_id:413990` — unsubscribe (other subscribers unaffected)
- `/list-watches` — see all active watches on the server

Every 15 minutes, `rph_watcher` fetches each watched event from RPH and DMs all subscribers if `registered_user_count < capacity` and `queue_status == ACCEPTING_SIGNUPS`. Watches expire automatically after `end_date`.

**Finding the event ID:** it's the number at the end of the RPH event URL:
`https://tcg.ravensburgerplay.com/events/`**`413990`**

**Bot State:** each watch is stored as `rph_watch:<event_id>` → `{"name": "...", "end_date": "YYYY-MM-DD", "subscribers": [user_id, ...]}`.
