# Season Rollover

## Season-close checklist (automatic)

The day after the current season's `season_end` (from the Seasons tab), the bot posts a checklist
to the mod channel — and immediately on startup if that day has already passed. It is one message,
edited in place, refreshed on every button click and daily at `DIGEST_HOUR_ET`:20.

| Step | Button | Ticked when |
|---|---|---|
| 1. Rare / Uncommon | **Record & assign roles** — previews the final leaderboard's earners first | marker `rare_uncommon`, or registry I/J carry the season — *and* every linked member holds those roles |
| 2. Invitational | **Submit invitational URL** (opens a box → the usual ✅/❌ preview; ✅ also grants roles) or **No invitational** | marker `invitational`, or registry G/H carry the season — and the roles are on Discord |
| 3. Rollover | **Roll over to S##** — appears only once step 1 is recorded and the next row is complete; re-validated at click time | marker `rolled_over`, or the pointer is past the season |
| 4. Archive | **Archive S##** — after the rollover | marker `archived`, or the Archive sheet has `S## Leaderboard` |

The next season's dates come **only** from the Seasons tab; the checklist links it and has a
**Reload** button. When all four are done the message becomes a one-line `🏁 S## closed` summary
and is never touched again. Steps can finish out of order — the invitational usually lands after
the rollover, so the checklist stays open into the next season.

Every step checks what it *produced*, so the slash commands below tick it too. State lives in one
Bot State key per season, `season_close:S##` (JSON: `msg_id`, the step markers, `closed`).
Buttons are admin-only (`ADMIN_USER_IDS`) and route by custom_id, so they survive restarts.

**Why the markers exist:** registry role columns keep the *earliest* season. If every S13 earner
already held their role from S12, recording S13 changes no cell, and the registry alone cannot
tell "recorded, nothing new" from "never recorded". The record steps (button or slash) write the
marker either way, and `/season-rollover`'s guard accepts it — so that case no longer needs `force`.

## End-of-Season Checklist (manual)

1. Fill the new season's row in the **Seasons** tab — season start/end and Set Champs
   start/end. Check it with `/seasons`
2. Run `/record-rare-and-uncommon` to record final Uncommon/Rare from the leaderboard,
   then `/assign-roles-from-registry` to assign the Discord roles
3. Run `/archive-season S##` to copy the season's tabs to the Archive spreadsheet
4. Verify the archive looks correct
5. Run `/season-rollover S##` — it reads the dates from that row
6. Manually delete the old season's tabs from the League sheet when ready

> **Recording must come before rollover.** `/record-rare-and-uncommon` reads the leaderboard
> for `CURRENT_SEASON` and stamps `CURRENT_SEASON`. Run after rollover, it reads the *new*
> season's empty leaderboard, finds nobody, and reports success — the finished season is
> silently never recorded.
>
> `/season-rollover` enforces this: it refuses if no registry row carries the outgoing
> season in columns I/J. Pass `force: true` to override, e.g. for a season that genuinely
> had no earners.

## `/season-rollover` Command

```
/season-rollover new_season [start_date] [end_date] [set_champs_start] [set_champs_end] [force]
```

Example:
```
/season-rollover S14
```

What it does:
- Re-reads the Seasons tab, so a row edited moments earlier is picked up without a restart
- Resolves the new season's dates from its row, and **refuses, naming the empty cells**, if any
  of the four are missing. A future season normally has its Set Champs dates blank until they
  are announced — filling them in is the point of this step
- Creates five tabs: `S14 Standings`, `S14 Events`, `S14 Leaderboard`, `S14 Results` (in the League spreadsheet), and `S14 Set Champs` (in the Bot Database spreadsheet)
- Writes column headers into the `Results` tab
- Writes **only** `season` to Bot State — the dates live in the Seasons tab
- Calls `season.init()` in memory — no redeploy needed
- Warns if the outgoing season's Set Champs window is still open, because the Set Champs digest
  and its sheet follow `CURRENT_SEASON` and will stop refreshing the outgoing tab

Tabs that already exist are silently skipped (safe to re-run if something fails partway).

### Date overrides

The four date arguments override the tab row for the case where it is wrong and there is no time
to fix it. They apply **in memory only** — the bot never writes to the Seasons tab — so a restart,
or any reload including `/seasons`, reverts to the row. `/seasons` flags the current season as
`override` while one is in effect. Edit the row to make a change stick.

### Retired Bot State keys

Rollover no longer writes `season_start_date`, `season_end_date`, `set_champs_start_date` or
`set_champs_end_date`. They are left in place on purpose: they are the fallback for a season with
no row in the tab, and erasing them in the same write that flips the pointer would leave neither
source of truth. Delete them by hand once the tab has proven itself.

## `/archive-season` Command

```
/archive-season season_name
```

Example:
```
/archive-season S11
```

What it does:
- Reads all four season tabs from the League spreadsheet (`Standings`, `Events`, `Leaderboard`, `Results`)
- Creates matching tabs in the Archive spreadsheet and writes the data
- If a tab already exists in the archive, data is overwritten (safe to re-run)
- League sheet tabs are left intact — delete them manually when ready

## How Season Config Works

Season values are stored in the **Bot State sheet** and loaded at startup via `season.init(bot_state)`.
All derived values (sheet names, range names, RPH datetime strings) live in `season.py` as mutable module globals.

Consumers must use `import season; season.X` — not `from season import X` — so they get the live value
at call time rather than a frozen import-time copy.

Fallback values in `constants.py` are used when Bot State keys are absent (local dev, cold start).

### Bot State keys

| Key | Example value |
|-----|---------------|
| `season` | `S12` |
| `season_start_date` | `2026-05-01` |
| `season_end_date` | `2026-07-10` |
| `set_champs_start_date` | `2026-06-21` |
| `set_champs_end_date` | `2026-07-10` |

> When entering dates manually into the Bot State sheet, prefix with a single apostrophe (`'2026-05-01`)
> to prevent Google Sheets from converting the value to a date serial number.
