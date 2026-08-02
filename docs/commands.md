# Commands

## Slash Commands

| Command | Who | Description |
|---------|-----|-------------|
| `/schedule` | Everyone | Upcoming events from the website |
| `/watch-rph-event` | Everyone | Subscribe to DM alerts when a spot opens at a full RPH event |
| `/unwatch-rph-event` | Everyone | Unsubscribe from a watched event |
| `/list-watches` | Everyone | Show all currently watched events and subscriber counts |
| `/help` | Everyone | List all commands |
| `/recheck` | Admin | Reprocess any unhandled threads in `#results-reporting` |
| `/link @member <id\|name>` | Admin | Link a Discord member to a Playhub player. Keyed on Playhub ID; a display name is accepted but must resolve to exactly one player, otherwise it is refused |
| `/record-rare-and-uncommon` | Admin | Record Rare/Uncommon earned this season into the registry (columns I/J). Records only — does not grant Discord roles |
| `/record-legendary-and-super-rare [season]` | Admin | Record Legendary/Super Rare from an invitational into the registry (columns G/H). Records only. Pass a season to backfill an older event |
| `/assign-roles-from-registry` | Admin | Assign every Discord rarity role the registry records. Additive only, idempotent, safe to re-run |
| `/tidy-registry` | Admin | Remove blank rows from the Player Registry and sort it by rarity tier, newest season first |
| `/wheretoplay` | Admin | Manually trigger a `#where-to-play` refresh |
| `/season-rollover` | Admin | Create new season tabs in League sheet, update Bot State, reload config in memory |
| `/archive-season` | Admin | Copy a completed season's tabs from the League sheet to the Archive spreadsheet |

> To restrict commands to specific roles, use Discord's server settings: **Server Settings → Integrations → GTA Lorcana Bot** — no code changes needed.

Admin commands are also accessible to any Discord user ID listed in `ADMIN_USER_IDS` in `constants.py`.

---

## Scheduled Tasks

| Task | When | What it does |
|------|------|-------------|
| `where_to_play_weekly` | Sundays at 11 PM ET | Edits (or posts) the `#where-to-play` messages |
| `set_champs_daily` | Daily at 7 AM ET, from `SEASON_START_DATE` through `SET_CHAMPS_END_DATE` | Refreshes the Set Champs tab in the League sheet from RPH |
| `rph_watcher` | Every 15 min | Checks watched events for open spots and DMs subscribers |
| `keepalive` | Every 30 min | Heartbeat log |
