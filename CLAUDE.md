# GTA Lorcana Bot — Session Context

See README.md for project structure and links to all docs in docs/.

---

## Seasons

The league calendar lives in the **Seasons tab** of the Bot Database sheet, one row per set's
cycle (prerelease → season → Set Champs). It is hand-maintained; the bot only reads it. Bot State
holds just the `season` pointer. Run `/seasons` for the live view, or
`python scripts/print_season_calendar.py`.

Do not restate the current season's dates here — they go stale. As of S13 the pointer was S13
(Jul 17 → Sep 18 2026, Set Champs Sep 4 → 27) with S14 (Hyperia City) seeded ahead of it.

**Two seasons are live at once, by design.** A set's prerelease is listed by stores weeks before
that season starts, and the previous season's Set Champs can still be running when it does. So:

| Consumer | Follows |
|---|---|
| Results eligibility, Standings/Results/Leaderboard, roles, every sheet name | `CURRENT_SEASON` — always |
| Set Champs digest | The **current** season's window (`season.set_champs_window()`), because the sheet it writes is named for `CURRENT_SEASON` |
| Prerelease digest | Whichever row's prerelease window has not ended (`season.active_prerelease()`) — normally the **incoming** season |

---

## White-Label Extraction (future, not now)

When ready to extract a generic league engine from this GTA-specific bot,
the GTA-specific code is concentrated in these places:

**`util/rph_api_utils.py`** — hardcoded GTA/Lorcana params that would become Bot State config:
- `latitude: 43.653226` / `longitude: -79.3831843` — Toronto coordinates
- `num_miles: 250` — search radius
- `country == "CA"` / `administrative_area_level_1_short == "ON"` — Canada/Ontario filters
- `game_id: '1'` / `game_slug: 'disney-lorcana'` — Lorcana-specific
- `gameplay_format_ids: [...]` — Constructed + Booster Draft format UUIDs

**`stores.py`** — `_SET_CHAMPS_KEYWORD = "Set Champ"` keyword for RPH event category/name matching

**`constants.py`** — `WHERE_TO_PLAY_MIN_CONSECUTIVE_WEEKS`, `WHERE_TO_PLAY_POST_DAY/HOUR_ET`

**GTA-only features** (strip out entirely for white-label):
- `/etb-discount` command + `util/shopify_api_utils.py` — ETB discount integration
- `SHOPIFY_TOKEN`, `SHOPIFY_STORE_DOMAIN` constants
- `specs/SHOPIFY_DISCOUNT_SPEC.md`

Everything else (season rollover, results pipeline, store classification,
player registry, rarity roles, RPH watcher) is already generic league logic.

---

## Subagent Profiles

Three scoped subagents live in `.claude/agents/`. Delegate to them rather than working the whole
codebase from one context:

| Agent | Docs | Owns |
|---|---|---|
| `league-logic` | `docs/league-logic/` | Results pipeline, sheet formulas, rarity roles & Player Registry, season rollover, store classification |
| `discord-surface` | `docs/discord-surface/` | Slash commands, embeds and copy, reaction flows, DMs, scheduled posts |
| `bot-infra` | `docs/bot-infra/` | Fly.io, Dockerfile, secrets, `constants.py`, API wrappers, memory footprint |

`docs/` mirrors this split one folder per agent. Each folder has a `design-notes.md` for
cross-cutting notes that don't belong to a single topic file.

Each restates the invariants for its own area and names what to hand back. Keep them in sync when
a design note below changes.

---

## TODO

- **Update league-rules Discord post on season rollover**: `discord/league-rules.md` is manually
  updated each season but still needs to be pushed to Discord. Plan: store the message ID in Bot
  State, add a `/update-league-post` command that reads the file and edits the message in-place.
  Message is a plain Discord message (not an embed). Do this after confirming message ID.

---

## Key Design Notes

- `ADMIN_USER_IDS` is a list (not set) — supports indexing for pings and `in` checks
- `_sheet_lock` serializes all sheet writes — never bypass it
- Digests (Set Champs, prereleases) are one table: `_DIGESTS` in `bot.py`, refreshed by a single
  `event_digests_daily` loop, one per minute from `DIGEST_HOUR_ET`. Adding one is a row, not a task.
  The stagger is what keeps two RPH window fetches out of memory at once on a 256MB machine
- A digest's message IDs live in Bot State (`<key>_msg_ids`, header first) so the daily refresh edits
  in place. If the in-memory list is empty, `_post_event_digest` re-reads the key before posting and
  refuses to post when that read fails — assuming "never posted" duplicates the whole digest
- Bot State sheet is key-value; all runtime state (message IDs, watches, recheck guards) lives there
- **Never let a Bot State read fail quietly on a path that writes it back.** `set_bot_state_key`
  reads the whole tab and rewrites it, and `save_bot_state` clears the range first, so `{}` from a
  failed read wipes every key. `load_bot_state(strict=True)` is mandatory before any write, and
  `save_bot_state` refuses an empty dict. Background tasks must catch the resulting exception —
  an exception escaping a `tasks.loop` body kills that loop until the next restart
- Roles never auto-downgrade — every path that grants them is additive only
- Registry role columns (G–J) hold the **earliest** season earned: a blank cell takes the new
  value, a populated one is replaced only by an earlier season (numeric compare, so S9 < S10)
- Recording and assigning are separate. `/record-rare-and-uncommon` and
  `/record-legendary-and-super-rare` write the registry; `/assign-roles-from-registry` reads it
  back and makes Discord match. The registry is the single source of truth
- Playhub ID is the identity key everywhere; display names are a fallback for pre-S12 rows only.
  A row matched by ID has its name refreshed if RPH has renamed the player
- Never use the Sheets `values.append` API on the Player Registry — it picks its own anchor
  column by table detection and has misfiled rows. Write to an explicit `A{n}:J{n}` range
- Standings columns are `A Date | B Store | C Rank | D Players | E Win | F Loss | G Draw |
  H Points | I Playhub User ID` (`A3:I`, data starts at row 3 — row 2 is reserved for manual
  adjustments the results pipeline must not overwrite)
- **`Points == W*3 + D` on every Standings row** — a real invariant, worth checking after any
  import. Win/Loss/Draw are separate integer columns *because* a combined `"W-L-D"` string is
  destroyed on write: `USER_ENTERED` parses `"2-1-0"` as a date. Never write a record as one
  string, and never assume a value with dashes survives a sheet write intact
- RPH's per-round `/standings` returns `match_points` for that round but `record` as of the
  event's **final** round. Any path that scores a non-final round must rewind the record too —
  see `_rewind_records()`. The dropped round's result comes from the `match_points` delta
- Results/Leaderboard formulas group by Playhub ID, never display name. Sheets' `FILTER` and
  `COUNTIF` are case-insensitive, so name grouping merged `HABIBI` with `Habibi` (two people,
  both credited both records) and split anyone who renamed mid-season
- `SORTN`'s `sort_column` indexes the *filtered array*, not the source sheet. An out-of-range
  index silently disables the sort instead of erroring — that is how "best 10 results" ran as
  "first 10" for a whole season
- `create_season_sheets` seeds the Results/Leaderboard formulas for a new season. Any formula
  fix applied to the live sheet must be mirrored there, or rollover reintroduces the bug.
  Editing a live formula by *inserting columns* is especially deceptive: Sheets silently
  rewrites its own references, so the sheet looks right while the seeder still holds the old
  column letters. Dry-run rollover into a throwaway season and diff it against the live one
- Set Champs detection (`stores.is_set_champs_event`) matches `"Set Champ"` against the
  event's phase text **or** its store-authored name. RPH exposes no category field — only
  an `event_configuration_template` UUID, whose name lookup RPH broke mid-window and which
  is a different UUID every set. Both arms are load-bearing and neither is safe alone;
  never date-scope the name arm, as a previous set's championships run early in a new
  season, outside the Set Champs window
- Sheets' `addSheet` returns a bare 400 with no machine-readable reason — the only signal is
  the message text `A sheet with the name "X" already exists.` Match on that (see
  `_is_already_exists`), never on an `ALREADY_EXISTS` token; the API does not send one
