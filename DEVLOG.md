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
