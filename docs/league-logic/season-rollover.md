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

Steps 1 and 2 show each player's progression — highest rarity before → after, e.g.
`Common → Rare (+Uncommon)` — split into **Moving up**, **Already hold theirs** (nothing new to
stamp) and **Not on Discord** (unlinked or left the server; their roles land on `/link`). After
recording, "moved up" is read back from cells stamped with the season, which under
earliest-season-wins are exactly the roles first earned in it. The invitational ✅/❌ preview
shows the same before → after per finisher.

The next season's dates come **only** from the Seasons tab; the checklist links it and has a
**Reload** button. When all four are done the message becomes a one-line `🏁 S## closed` summary
and is never touched again. Steps can finish out of order — the invitational usually lands after
the rollover, so the checklist stays open into the next season.

Every step checks what it *produced* — registry stamps, the season pointer, the Archive sheet —
or a marker the step writes. State lives in one Bot State key per season, `season_close:S##`
(JSON: `msg_id`, the step markers, `closed`). Buttons are admin-only (`ADMIN_USER_IDS`) and route
by custom_id, so they survive restarts. `/season-close` posts or refreshes the checklist on demand.

The checklist is the only way to record, roll over and archive — the `/record-*`,
`/season-rollover` and `/archive-season` commands were removed in Oct 2026.

**Why the markers exist:** registry role columns keep the *earliest* season. If every S13 earner
already held their role from S12, recording S13 changes no cell, and the registry alone cannot
tell "recorded, nothing new" from "never recorded". The record step writes the marker either
way, and the rollover guard accepts it.

## Before the season ends

Fill the next season's row in the **Seasons** tab — season start/end and Set Champs start/end —
and check it with `/seasons`. The checklist links the tab and has a **Reload** button.

> **Recording must come before rollover**, or the finished season is easy never to record. The
> Roll over button only appears once step 1 is recorded, and is re-checked at click time.

## Roll over (step 3)

- Re-reads the Seasons tab, so a row edited moments earlier is picked up without a restart
- Refuses, naming the empty cells, if any of the next season's four dates are missing
- Creates five tabs: `S14 Standings`, `S14 Events`, `S14 Leaderboard`, `S14 Results` (League
  spreadsheet) and `S14 Set Champs` (Bot Database spreadsheet), with the Results headers and
  formulas seeded. Tabs that already exist are skipped, so it is safe to re-run
- Writes **only** `season` to Bot State — the dates live in the Seasons tab — and calls
  `season.init()` in memory, so no redeploy is needed
- Warns if the outgoing season's Set Champs window is still open: the Set Champs digest and its
  sheet follow `CURRENT_SEASON` and stop refreshing the outgoing tab

## Archive (step 4)

- Copies the season's four League tabs (`Standings`, `Events`, `Leaderboard`, `Results`) to the
  Archive spreadsheet, overwriting a tab that already exists there (safe to re-run)
- Leaves the League sheet tabs intact — delete them by hand once you've checked the archive

## How Season Config Works

The season **pointer** (`season` in Bot State) says which season is current; its dates come from
that season's row in the **Seasons** tab. Both are loaded at startup via `season.init(bot_state, calendar)`,
and rollover changes only the pointer. All derived values (sheet names, range names, RPH datetime
strings) live in `season.py` as mutable module globals.

Consumers must use `import season; season.X` — not `from season import X` — so they get the live value
at call time rather than a frozen import-time copy.

A current season with no row in the tab has blank windows — there is no fallback. The flat
`season_start_date` / `set_champs_*_date` / `prerelease_*` Bot State keys that preceded the tab were
removed after the S14 rollover.

If `season` itself is absent from Bot State (local dev, cold start), `constants.CURRENT_SEASON` is used.
