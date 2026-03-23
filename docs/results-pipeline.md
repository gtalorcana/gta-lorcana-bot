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

The `set_champs_daily` task calls `refresh_set_champs()` in `stores.py` every morning during the set champs window. It fetches all Ontario Lorcana events in the `SET_CHAMPS` date range (including upcoming and in-progress), filters to events whose name contains `"Set Champ"` (case-insensitive — matches "Set Championship", "Set Champs", etc.), and overwrites the Set Champs sheet.

**Set Champs sheet columns (A2:H):**
```
Date | Time (Toronto) | Store Name | Full Address | Player Cap | Format | Event Name | RPH Link
```

The task starts **2 weeks before** `SET_CHAMPS_START_DATE` so the sheet is populated ahead of time as stores register their events on RPH.

**Manual run** (inspect output before writing):
```bash
python scripts/rph_get_set_championship_events.py
```

Set `WRITE_TO_SHEET = True` in the script once the output looks correct. Set `NAME_FILTER = None` to see all events in the window and verify the filter keyword.

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
