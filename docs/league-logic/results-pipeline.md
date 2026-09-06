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

---

## Set Championships

The `set_champs_daily` task calls `refresh_set_champs()` in `stores.py` every morning during the set champs window. It fetches all Ontario Lorcana events in the `SET_CHAMPS` date range (including upcoming and in-progress), filters to Set Championships via `_is_set_champs_event()`, and overwrites the Set Champs sheet.

`_is_set_champs_event()` matches the keyword `"Set Champ"` (case-insensitive) against the event's
**category** *or* its **name** — a union, not a preference. The category is the name of the RPH
event configuration template the event was built from, which is what the RPH event page labels
"Category" (e.g. `"Attack of the Vine! Set Championship"`). Events carry only the template UUID in
`event_configuration_template`; `RphApi.get_event_category()` resolves it through the
`event-configuration-templates` endpoint, fetched once and cached per instance.

Both halves of the union are load-bearing, and each alone loses real events:

- **Name alone** misses stores that mistitle a properly-templated event — one S13 store called
  theirs "Attack Of the Vine **Store** Championship".
- **Category alone** misses stores that build a genuine Set Championship from a generic template —
  three S10 Whispers in the Well Set Championships ran on `Weekly Play (Constructed)`.

The category is also only available for the *current* set. RPH's template endpoint lists just the
current set's templates (12 active, 6 inactive at time of writing); retired sets' Set Championship
templates are not exposed at all, so **every event outside the current set resolves to no category
and is carried entirely by the name match**. `refresh_set_champs` logs how many rows matched on name
only — during the current set's window that count should be at or near zero, and a sudden jump means
RPH has rotated the template list.

The keyword is deliberately the loose `"Set Champ"` rather than `"Set Championship"`: the template
name is prefixed with the set name, which changes every set, and the trailing wording drifts between
"Set Championship" and "Set Champs". Never match the set name itself.

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
