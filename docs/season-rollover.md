# Season Rollover

## End-of-Season Checklist

1. Run `/sync-roles` to assign final Uncommon/Rare based on standings
2. Run `/archive-season S##` to copy the season's tabs to the Archive spreadsheet
3. Verify the archive looks correct
4. Run `/season-rollover S## YYYY-MM-DD YYYY-MM-DD YYYY-MM-DD YYYY-MM-DD` with the new season's dates
5. Manually delete the old season's tabs from the League sheet when ready

## `/season-rollover` Command

```
/season-rollover new_season start_date end_date set_champs_start set_champs_end
```

Example:
```
/season-rollover S12 2026-05-01 2026-07-10 2026-06-21 2026-07-10
```

What it does:
- Creates four tabs in the League spreadsheet: `S12 Standings - User Reported`, `S12 Events - User Reported`, `S12 Leaderboard`, `S12 Set Champs`
- Updates the season keys in the Bot State sheet
- Calls `season.init()` in memory — no redeploy needed

Tabs that already exist are silently skipped (safe to re-run if something fails partway).

## `/archive-season` Command

```
/archive-season season_name
```

Example:
```
/archive-season S11
```

What it does:
- Reads all four season tabs from the League spreadsheet
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
