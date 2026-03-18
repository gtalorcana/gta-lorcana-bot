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
