# Commands

## Slash Commands

| Command | Who | Description |
|---------|-----|-------------|
| `/help` | Everyone | List all commands |
| `/etb-discount` | Everyone | Opens the ETB discount form — same as the button on the `#etb-discount` post |
| `/recheck` | Admin | Reprocess any unhandled threads in `#results-reporting` |
| `/link @member <id\|name>` | Admin | Link a Discord member to a Playhub player. Keyed on Playhub ID; a display name is accepted but must resolve to exactly one player, otherwise it is refused |
| `/assign-roles-from-registry` | Admin | Assign every Discord rarity role the registry records. Additive only, idempotent, safe to re-run |
| `/etb-post` | Admin | Post, or edit in place, the `#etb-discount` how-to (text from `discord/etb-discount.md`) with its apply button |
| `/season-close` | Admin | Post or refresh the season-close checklist now. Recording roles, rollover and archiving are its buttons — there are no slash commands for them |
| `/tidy-registry` | Admin | Remove blank rows from the Player Registry, refresh display names from this season's sheets, and sort by rarity tier, newest season first |
| `/where-to-play` | Admin | Manually trigger a `#where-to-play` refresh |
| `/seasons [reload]` | Admin | Show the season calendar as the bot reads it, what each digest resolves to, and any problems with the Seasons tab. Read-only |
| `/set-champs` | Admin | Manually refresh and post the Set Champs digest |
| `/ccqs` | Admin | Manually refresh and post the CCQ digest in `#ccqs`. No season involved — a rolling six-month lookahead |
| `/prereleases` | Admin | Manually refresh and post the prerelease digest. The window comes from the **Seasons** tab — it is the incoming season's, not the current one's |

> To restrict commands to specific roles, use Discord's server settings: **Server Settings → Integrations → GTA Lorcana Bot** — no code changes needed.

Admin commands are also accessible to any Discord user ID listed in `ADMIN_USER_IDS` in `constants.py`.

---

## Scheduled Tasks

| Task | When | What it does |
|------|------|-------------|
| `where_to_play_weekly` | Sundays at 11 PM ET | Edits (or posts) the `#where-to-play` messages |
| `event_digests_daily` | Every minute; acts at `DIGEST_HOUR_ET` (7 AM ET), one digest per minute in `_DIGESTS` order | Refreshes each digest that is active today. One per minute so two RPH window fetches are never in memory at once |
| ↳ `:00` Set Champs | Season start → Set Champs end, current season | Refreshes the `S## Set Champs` tab and the digest in `#gta-set-championships` |
| ↳ `:05` Prerelease | While any season's prerelease window has not ended (normally the incoming season) | Refreshes the digest in `#gta-prereleases`. No sheet |
| ↳ `:10` CCQ | Always — rolling six-month lookahead, no season | Refreshes the digest in `#ccqs`. No sheet. The widest fetch the bot runs: 600 km, both countries, ~55 API pages |
| `keepalive` | Every 30 min | Heartbeat log |
