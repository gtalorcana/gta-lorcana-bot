# Season Rollover

## Steps

1. Update `SEASON_START_DATE`, `SEASON_END_DATE`, `CURRENT_SEASON`, `SET_CHAMPS_START_DATE`, and `SET_CHAMPS_END_DATE` in `constants.py`
2. Create new `S## Standings - User Reported`, `S## Events - User Reported`, and `S## Leaderboard` tabs in the League Sheet
3. Create a new `S## Set Champs` tab in the Set Champs sheet
4. Deploy: `fly deploy`
5. At season end, run `/sync-roles` to assign Uncommon/Rare based on final standings

---

## TODO (not yet implemented)

- Move `CURRENT_SEASON`, `SEASON_START_DATE`, `SEASON_END_DATE` from `constants.py` to the **Bot State sheet** so season changes don't require a redeploy
- Add a `/season-rollover` command that updates those Bot State keys and reloads derived sheet name constants in-memory
  - Note: sheet name constants (`STANDINGS_SHEET_NAME` etc.) are built at module import time from `CURRENT_SEASON` — they'll need to be lazily resolved or rebuilt after a rollover
- Store the message ID of the league-rules Discord post in Bot State so `/season-rollover` can edit it in-place from `discord/league-rules.md`
