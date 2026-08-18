# GTA Lorcana Bot — Dev Log

---

## 2026-03-18 — League Role System (initial implementation)

### Overview
Built the rarity role system: Common → Uncommon → Rare → Super Rare → Legendary.
Roles are earned based on competitive results stored in the standings sheet and
player identity data linked via a new Playhub ↔ Discord ID mapping sheet.

### Design decisions
- **Role triggers**: No automatic role assignment after each results import.
  `/sync-roles` is manual, intended to run at season end. Exception: Common is
  always assigned immediately on `on_member_join`.
- **Linking flow runs per-event**: After every `process_event_data` success,
  the bot checks for new Playhub IDs not yet in player_mapping and posts
  fuzzy-match suggestions to the mod channel. This ensures the mapping is
  populated throughout the season so `/sync-roles` at season end has no surprises.
- **No auto-downgrade**: `/sync-roles` only upgrades roles, never removes a
  higher role in favour of a lower one.
- **Enchanted/Promo**: Never touched by the bot under any circumstance.
- **Legendary/Super Rare**: Only assigned via `/assign-roles-from-invitational`,
  which posts a confirmation embed to the mod channel before applying.

### New files
- `roles.py` — player_mapping sheet CRUD, fuzzy matching, role calculation,
  `compute_role_assignments()`

### Modified files
- `constants.py`
  - `STANDINGS_RANGE_NAME` extended from `A3:F` → `A3:G` (new playhub_id col)
  - Added `MOD_CHANNEL_ID`, `COMMON/UNCOMMON/RARE/SUPER_RARE/LEGENDARY_ROLE_ID`
  - Added `PLAYER_MAPPING_SHEET_NAME`, `PLAYER_MAPPING_RANGE_NAME`
- `results.py`
  - `standing_rows` now includes `playhub_id` (col G) from `standing['player']['id']`
  - `process_event_data` returns `standing_rows` (previously returned None)
- `bot.py`
  - `_run_process_event_data` and `process_results_reporting_thread` propagate
    `standing_rows` return value up to `run_results_reporting_pipeline`
  - After results success, calls `_post_linking_suggestions()` as a background task
  - New events: `on_member_join` (Common role), `on_raw_reaction_add` (confirmations)
  - New commands: `/link`, `/sync-roles`, `/bootstrap-common`,
    `/assign-roles-from-invitational`
  - New in-memory state: `_pending_link_suggestions`, `_pending_invitational_assignments`

### New Google Sheet tab required
Create **"Playhub <-> Discord IDs"** in `STORE_SPREADSHEET_ID` with header row:
`discord_id | playhub_id | display_name | linked_at | linked_by`

### New env vars required (Fly.io secrets)
```
MOD_CHANNEL_ID
COMMON_ROLE_ID
UNCOMMON_ROLE_ID
RARE_ROLE_ID
SUPER_RARE_ROLE_ID
LEGENDARY_ROLE_ID
```

### Role thresholds
| Role | Condition |
|---|---|
| Common | Any linked member (auto on join) |
| Uncommon | 10+ distinct events attended |
| Rare | Rank 1–32 on season leaderboard |
| Super Rare | Top 8 at a designated invitational |
| Legendary | Rank 1 at a designated invitational |

### Fuzzy matching thresholds
- ≥ 75% similarity → auto-suggest with ✅/❌ reaction prompt in mod channel
- 50–74% → surface for manual `/link`, no reaction prompt
- < 50% → "unmatched player" notice, requires `/link`

---

## 2026-07-06 — Leaderboard carries Player ID; /sync-roles matches by ID

### Overview
`/sync-roles` now matches leaderboard earners to the Player Registry by **Playhub ID**
instead of display name. RPH display names change over time, but the Discord↔Playhub ID
link is permanent — name matching silently missed renamed players.

### Changes
- **`stores.py`** (`create_season_sheets`): insert a `Player ID` column at `Results!A`
  (name shifts to B; ID mirrors the name spill via `ARRAYFORMULA(VLOOKUP)` from Standings D→G),
  and shift the per-row formulas right one column (C2/M2/N2/O2/P2). The `Leaderboard` now
  seeds a header row (`A1:E1`) and a mask-based spill at `B2`
  (`FILTER(SORT(FILTER(Results!A2:P,…),13,15,16),{1,1,…,1,1,0,0})`) →
  layout `A=Rank, B=Player ID, C=Name, D=Points, E=Events`. Anchoring at B2 (row 1 = header)
  keeps it aligned with `/sync-roles` reading from row 2. Column A auto-sequences the rank
  (`ARRAYFORMULA(IF(LEN(C2:C),SEQUENCE(ROWS(C2:C)),""))`) off the Name spill — no manual fill.
- **`season.py`**: `LEADERBOARD_RANGE_NAME` `A2:D`→`A2:E`, `RESULTS_RANGE_NAME` `A2:O`→`A2:P`.
- **`bot.py`** (`sync_roles`): read new column positions, key earners by ID (name fallback),
  pass `playhub_id` into `batch_upsert_player_roles` so registry writes match by ID too.
  Added a final pass that calls `_merge_duplicate_rows` for each touched Discord ID the
  registry snapshot shows more than once — collapsing pre-existing duplicate rows in the same
  run (scoped to matched rows, so it's not a full read per player). Reported in the summary.
- **`stores.py`** also now seeds header rows (row 1) for the `Standings` and `Events` tabs on
  rollover — previously they were created blank and the operator added titles by hand. The four
  League tabs are created left-to-right as Leaderboard/Results/Standings/Events (via a new
  `index` arg on `add_sheet`), and rollover ensures a persistent `Ban List` tab exists.
- The Leaderboard **ban filter matches by Playhub ID** (Results col A vs `Ban List!A2:A`), not
  display name — stable key, consistent with the sync-roles change.
- **Docs**: `google-sheets.md` (new League Sheet Tabs section), `roles.md`.

### Rollout note
New column layout only applies to tabs created by `/season-rollover` going forward (S13+).
Deploy this before running `/season-rollover S13`; do not run `/sync-roles` against an
old-layout leaderboard after deploying.

---

## 2026-08-02 — Registry integrity; record/assign split

### Overview
Traced a misfiled registry row back through several long-standing bugs, then separated
recording a role from assigning it. The Player Registry is now the single source of truth
and Discord is strictly downstream of it.

### Root cause: Sheets `values.append`
A confirmed link landed in `I240:R240` instead of `A240:J240`. `values.append` does not write
to the range's first column — it does table detection and writes to the first column of
whatever "table" it finds. `/sync-roles` had been creating rows holding data only in A and I
(name + Rare season), and that B–H gap let append anchor on the right-hand block.

This also explained the duplicate rows, which had looked like a separate bug in
`_merge_duplicate_rows`. Proof: Harding33 held rows 52 and 240 with the same Discord ID. The
shifted append put row 240's Discord ID in column K, outside the A:J window the merge scans,
so it saw one match and early-returned. **The merge logic was correct all along.**
Fixed by writing to an explicitly computed `A{n}:J{n}` row (`2de7184`).

### Commands renamed and split
`/sync-roles` read the leaderboard, wrote the registry *and* granted Discord roles, and only
ever touched that season's earners — so a role lost later was never repaired. Now:

| | |
|---|---|
| `/record-rare-and-uncommon` | leaderboard → registry I/J. Records only |
| `/record-legendary-and-super-rare [season]` | invitational → registry G/H. Records only |
| `/assign-roles-from-registry` | registry → Discord. Additive, idempotent |

`/link` and the ✅ fuzzy-confirm still assign immediately, but all three paths now share
`_assign_recorded_roles` — the only `add_roles` site for rarity roles. First run of the assign
command found 4 members missing roles that nothing would otherwise have fixed.

### Other changes
- **`/record-legendary-and-super-rare`** now writes G/H at all — the old command granted
  Discord roles and recorded nothing, so those columns had been hand-maintained and stopped
  after S10. Backfilled S11 and S12; Legendary is complete S5→S12.
- **Earliest-wins** (`_should_write_season`): a populated role cell is replaced by a
  genuinely earlier season, compared numerically so `S9` beats `S10`. Both record commands
  use it, so backfilling an old season out of order is safe.
- **`/etb-discount`** resolved attendance by display name against Standings — a renamed
  player was under-counted, two players sharing a name had counts merged. Now resolves to a
  Playhub ID first (registry link wins over the typed name), counts by ID, refuses on
  ambiguity, and refuses if the ID belongs to another Discord account.
- **`/link`** accepted a name and, on no match, created a row with no Playhub ID — 14 of the
  registry's 23 ID-less linked rows arrived that way. Now resolves to an ID before writing
  and refuses names that match nothing or match several.
- **Renames propagate**: `link_player` and `batch_upsert_player_roles` rewrite column A when
  a row is matched by Playhub ID and the name differs. `docs/roles.md` had claimed this for
  a long time, but the only implementation lived in `upsert_player_roles`, which nothing calls.
- **`/tidy-registry`** (new): drops blank rows, refreshes stale names from the current
  season's sheets, sorts by rarity tier then newest season, unroled players last.
- **`/season-rollover`** refuses if no registry row carries the outgoing season in I/J.
  Running it before recording made `/record-rare-and-uncommon` read the new season's empty
  leaderboard and report success, silently losing the finished season. `force: true` overrides.
- **`/archive-season`** hardcoded column spans that had drifted: Leaderboard copied `A1:D`
  after it gained "Events Attended" at E, Results `A1:O` after it gained a column at P. Both
  were silently dropped, permanently, since the League tabs are deleted by hand afterwards.
  Spans now derive from the live season ranges.
- **Stale comments** in `constants.py`, `roles.py`, `stores.py` and `scripts/test_debug_sheet.py`
  described a column order and a `STORE_SPREADSHEET_ID` constant that no longer exist.

### Data cleanup
Registry 231 → 222 rows. 5 duplicate rows merged to 3; 18 ID-less rows resolved against the
S12/S13 sheets (9 filled, 9 were rename-duplicates and were cleared). A read-only audit of
Discord roles vs the registry found zero drift in either direction.

45 rows still hold roles with no Playhub ID. These are pre-S12 players — the `Player ID`
column only reached the Leaderboard at S12, so no sheet records their ID.

### Deliberately not done
- Bundling the season-end steps into one command. Rollover is the only non-idempotent step;
  bundling would make the whole sequence unretryable.
- `rph_api.lookup_user_by_username` to resolve the remaining 45. It is dead code, never run
  live, its docstring says "display name" while the query param is `username`, and it returns
  `results[0]` without checking how many matched. Writing a wrong ID is worse than a blank one.

---

## 2026-08-05 — /etb-discount no longer self-serves a registry link

### Overview
`/etb-discount` was a third writer of Playhub ID → Discord ID bindings, and the only one
with no human in the loop. It now proposes rather than binds: an unlinked caller's claim
goes to the mod channel as a ✅/❌ prompt, and nothing is granted until a mod ticks it.

### The hole
An unlinked caller typed a display name; if it resolved to a single Playhub ID in the
current standings and nobody had claimed that ID yet, the command called `link_player`
directly (old `bot.py:1694`). RPH display names are public, so the name was the entire
credential.

The discount was the smaller prize. Because roles flow registry → Discord via
`/assign-roles-from-registry`, a successful claim also handed over every rarity role
recorded against that row. The existing ownership check only refused IDs already bound to
another Discord account, so every earner row with a blank `discord_id` — including the 45
pre-S12 ID-less rows — was exposed.

### Changes
- **`bot.py`**
  - `_apply_etb_approval` — the granting half (Shopify whitelist, approvals row, registry
    link), extracted so the instant path and the mod-confirmed path share one
    implementation. `_etb_code_message` shares the DM text the same way.
  - `_post_etb_approval_request` — posts the identity check, including how closely the
    caller's own Discord name resembles the name they claim (via `fuzzy_match_member`
    against the single caller). A signal for the mod, not a decision.
  - `etb_discount` — steps 1–5 still run for everyone and still only read, so a bad email
    or a typo'd name is caught before anyone waits on a mod. The fork is after step 5:
    linked callers proceed, unlinked ones get the prompt. A repeat call while a request is
    pending is refused rather than posting a duplicate.
  - `on_raw_reaction_add` — new branch grants on ✅ (DMs the code; tells the mod channel if
    their DMs are closed) and DMs a decline pointing at `/link` on ❌.
  - The step-5 already-whitelisted recovery only writes the approvals row for a linked
    caller now. The whitelist was found by the caller's own email so confirming it is
    truthful either way, but the row records an RPH name that is still just a claim.
  - New in-memory state: `_pending_etb_approvals`.
- **`specs/SHOPIFY_DISCOUNT_SPEC.md`** — new Step 5b documenting the gate; Step 7 now
  mentions the registry link it always performed.

### Known limitation
`_pending_etb_approvals` is in-memory, like `_pending_link_suggestions` and
`_pending_invitational_assignments`. A restart drops pending prompts and a later ✅ does
nothing at all — no write, no reply — while the embed still looks live. Every Fly deploy
does this. Persisting all three to Bot State is the next piece of work; the caller can
re-run `/etb-discount` in the meantime.

---

## 2026-08-18 — Standings W/L/D columns, ID-keyed Results, best-10 scoring

### Overview
`S13 Standings` column E held a single `W-L-D` string. It is now three integer
columns, and the Results tab that reads it was rebuilt to key on Playhub ID
instead of display name. Three separate scoring bugs fell out of the rewrite.

### The record column was being destroyed on write
Standings rows are written with `valueInputOption="USER_ENTERED"`, which parses
values exactly as if typed. `"2-1-0"` is a valid date, so Sheets stored the serial
`36527` and the record was gone. Only records with a `0` in the W or L slot
(`"3-0-0"`) survived, because month 0 and day 0 are invalid. 246 of 346 S13 rows
were affected, and S12's archive is 81% lost.

Column E fed no formula and no code path, so nothing scored wrong — but the data
was unrecoverable from the cell alone. Three integer columns cannot be coerced,
which removes the failure mode rather than working around it.

Recovered by replaying all 1,331 `W-L-D` combinations through `USER_ENTERED` to
build a serial → record table, then narrowing with `Points == W*3 + D`. 100
serials are ambiguous (`2-1-0` and `0-1-2` both give `36527`); the points column
resolved every one.

### Three bugs in the Results formulas
- **"Top 10" took the first 10, not the best 10.** `SORTN(FILTER(A:G,…),10,0,15,FALSE)`
  passed sort column 15 for a 7-column array. An out-of-range sort column silently
  disables the sort, so the cap kept whichever 10 rows came first. Dustymac scored
  58 instead of 64. `sort_column` indexes the *filtered* array, not the sheet.
- **Grouping by display name merged and split players.** Sheets' `FILTER` and
  `COUNTIF` are case-insensitive, so `HABIBI` and `Habibi` — two different people —
  each collected both players' events and both showed 10 points instead of 3 and 7.
  A mid-season rename split one player across two rows.
- **Column P was `#REF!` for anyone with two events on one date.** The inner
  `FILTER` returned two rows into one cell. Now `MIN`, which also picks the better
  rank of the two.

### RPH reports records as of the final round
When `_is_all_draw_round` fires, the bot takes standings from the previous round.
But RPH's per-round endpoint returns `match_points` for that round and `record` as
of the event's *final* round — verified: the round-3 payload's records are
byte-identical to round 4's for all 9 players. So the dropped round stayed in the
record while its points did not, and `W*3 + D` disagreed with Points for the whole
event.

`_rewind_records()` rewinds each record by one round using the same `match_points`
delta `_is_all_draw_round` already computes: +3 removes a win or bye, +1 a draw,
0 a loss. Reconstructs all 9 Derpy Cards rows exactly, including guan87, whose +3
was a bye — rewinding a *win* gives `0-2-1` = 1 point, matching a rank-9 finish
that made no sense before.

### Changes
- **`season.py`** — `STANDINGS_RANGE_NAME` `A3:G` → `A3:I`
- **`results.py`**
  - `_parse_record()` splits `"W-L-D"` into ints; unparseable input yields `[0,0,0]`
    and a warning rather than aborting an event import
  - `_rewind_records()` as above, called where the all-draw path previously did
    `standings = prev_standings`
  - `standing_rows` emits 9 columns; standings clear range `:G`/`*7` → `:I`/`*9`
    (the Events clear at 7 columns is unrelated and unchanged)
- **`stores.py`**
  - Playhub ID index `6` → `8` in `get_current_display_names` and
    `lookup_player_standings`
  - `create_season_sheets` seeds `Win | Loss | Draw` headers and the *current*
    Results formulas — it was still emitting every bug listed above, so the next
    rollover would have reintroduced all of them. Per-row formulas now seed to
    `RESULTS_SEED_ROWS` instead of being dragged by hand; the manual fill had
    stopped one row short, leaving the last player with no Points
- **`constants.py`** — `RESULTS_SEED_ROWS`
- **`util/google_sheets_api_utils.py`** — `set_column_date_format()`, since Results
  column O is a `MAX()` over dates and renders as a bare serial otherwise

### Sheet changes (S13, applied directly)
- Standings reshaped to `Date | Store | Rank | Players | Win | Loss | Draw | Points |
  Playhub User ID`; 344 of 346 records decoded, 2 filled in by hand
- Results A–P rebuilt: ID-keyed, best-10, sorted by display name
- Removed the unplayed 2026-08-15 "Lorcana League" (RPH 859663) — 13 players, one
  round generated, no results ever entered. It scored 0 so the leaderboard was
  unaffected, but it inflated Events Attended, and `/etb-discount` gates on that
  count (`_ETB_DISCOUNT_MIN_EVENTS = 3`), so four players qualified on an event
  nobody played
- `W*3 + D == Points` now holds on every row and is worth keeping as an invariant

### Known
- S12's archive is still 81% corrupted and S9 has 11 bad rows. Archives are
  write-only and read by nothing, so this is cosmetic; the decode method above
  would recover them if wanted.
- Two Playhub IDs share the name "Taxfreud" (15930, 61566) — genuinely two
  accounts. Deliberately left unmerged; there is no alias mechanism and none is
  planned. `lookup_player_standings("Taxfreud")` therefore refuses as ambiguous
  by design, so that caller cannot self-serve `/etb-discount` by name.

### Follow-up — rollover dry-run (same day)

Ran `create_season_sheets` against a throwaway season and replayed S13's real
Standings into it. Two bugs only a live run would have caught:

- **The seeder read Points from the wrong column.** `C2` and `O2` were still
  pointing at `F` — Points *before* the split, but **Loss** after it. The live S13
  sheet was unaffected because inserting columns made Sheets rewrite its own
  references; a freshly seeded S14 would have scored every player off their loss
  count. Fixed to `H`, and the column mapping the formulas depend on is now
  spelled out in a comment beside them.
- **`create_season_sheets` crashed on every rollover after the first.** The
  "tab already exists" guards tested `'ALREADY_EXISTS' in str(e)`, but addSheet
  returns a plain 400 whose message reads `A sheet with the name "X" already
  exists.` — there is no such token, so all six guards re-raised instead of
  skipping. The Ban List always exists after season one, so rollover was
  guaranteed to fail there, part-way through creating tabs. Now `_is_already_exists()`,
  matching on message text; it also covers `archive_season_data`, which had the
  same dead guard.

Dry-run result after both fixes: the seeded formulas reproduce S13 **exactly** —
126 players, 0 error cells, 0 row-by-row differences across all 16 Results
columns, and an identical Leaderboard. Headers, the column-O date format, and the
seed depth to `RESULTS_SEED_ROWS` all verified, and the test tabs cleaned up.

---

## 2026-08-18 — ETB Approvals records the Playhub ID

### Overview
`ETB Approvals` recorded only a Discord ID and an RPH display name, so the row
could not be tied back to a player once they renamed. It now carries the Playhub
ID as column B, matching the identity rule used everywhere else.

Layout is now `Discord ID | Playhub ID | RPH Username | Email | Approved At |
Events Count` (`A2:F`). Playhub ID sits at B rather than appended at F so the two
identity keys are adjacent, matching the field order `_pending_etb_approvals`
already used.

### Changes
- **`constants.py`** — `ETB_APPROVALS_RANGE_NAME` `A2:E` → `A2:F`, column comment
- **`stores.py`** — `get_etb_approval` returns `playhub_id` and its indices shift;
  `append_etb_approval` takes it as the second argument
- **`bot.py`** — both `append_etb_approval` call sites pass it. The value was
  already in scope at each: `_apply_etb_approval` takes `playhub_id` as a
  parameter, and the already-whitelisted recovery path is guarded by `known_id`

### Sheet
Column B inserted and backfilled from the Player Registry by Discord ID — 8 of 9
rows resolved. The unresolved one is the `gtalorcana` admin account (a March test
row claiming `ryanfan`); the name resolves to Playhub 37381, but that ID is
already bound to a different Discord account in the registry, so filling it in
would assert a two-to-one binding the registry does not allow. Left blank —
`get_etb_approval` documents that pre-existing rows may have a blank ID.

### Verified
`append_values` is used on this tab, and the note in CLAUDE.md records that the
same API misfiled rows on the Player Registry by choosing its own anchor column
via table detection. Tested with a sentinel row at the new 6-column width: it
landed in the correct columns and round-tripped through `get_etb_approval`
intact. Sentinel removed.
