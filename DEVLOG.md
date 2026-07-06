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
  rollover — previously they were created blank and the operator added titles by hand.
- **Docs**: `google-sheets.md` (new League Sheet Tabs section), `roles.md`.

### Rollout note
New column layout only applies to tabs created by `/season-rollover` going forward (S13+).
Deploy this before running `/season-rollover S13`; do not run `/sync-roles` against an
old-layout leaderboard after deploying.
