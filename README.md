# GTA Lorcana Bot

Discord bot for the Greater Toronto Area Lorcana community.

- Processes tournament results submitted by organizers and syncs standings to Google Sheets
- Auto-syncs `#announcements` posts to the community website via Cloudflare Worker
- Classifies Ontario stores by how regularly they run events and posts a weekly `#where-to-play` digest
- DMs subscribers when a spot opens at a full RPH event
- Assigns league rarity roles (Common → Uncommon → Rare → Super Rare → Legendary) based on participation and standings

---

## Project Structure

```
bot.py                                      # Main bot — events, slash commands, scheduled tasks
results.py                                  # Results reporting — processes RPH URLs, writes league standings
stores.py                                   # Store classification — RPH analysis, where-to-play logic, set champs refresh
roles.py                                    # League rarity role management — player registry, fuzzy matching, role sync
clients.py                                  # Shared API singletons (GoogleSheetsApi, RphApi) — instantiated once to avoid OOM
constants.py                                # All config — IDs, channel names, env var defaults
util/
  google_sheets_api_utils.py               # Google Sheets API wrapper (singleton)
  rph_api_utils.py                         # RPH API wrapper with pagination + retry
scripts/
  sync_commands.py                         # Fly.io release_command — syncs slash commands to guild on every deploy
  rph_get_set_championship_events.py       # Manual run — inspect/write Set Champs events from RPH
  test_debug_sheet.py                      # Local dev — runs analyse_stores() against a test spreadsheet
```

---

## Documentation

- [Commands & Scheduled Tasks](docs/commands.md)
- [League Rarity Roles & Player Registry](docs/roles.md)
- [Results Reporting Pipeline & RPH Watcher](docs/results-pipeline.md)
- [Store Classification & Overrides](docs/store-classification.md)
- [Google Sheets Layout](docs/google-sheets.md)
- [Deployment & Local Development](docs/deployment.md)
- [Season Rollover](docs/season-rollover.md)
- [Architecture & Design Notes](docs/architecture.md)
