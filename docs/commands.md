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
| `/link @member playhub_id` | Admin | Manually link a Discord member to a Playhub ID |
| `/sync-roles` | Admin | Compute and apply Uncommon/Rare upgrades from current standings |
| `/invitational-roles` | Admin | Preview and assign Legendary/Super Rare from an invitational event |
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
| `set_champs_daily` | Daily at 7 AM ET, 2 weeks before `SET_CHAMPS_START_DATE` through `SET_CHAMPS_END_DATE` | Refreshes the Set Champs tab in the League sheet from RPH |
| `rph_watcher` | Every 15 min | Checks watched events for open spots and DMs subscribers |
| `keepalive` | Every 30 min | Heartbeat log |
