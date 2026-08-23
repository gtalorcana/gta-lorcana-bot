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

Docs are grouped into three folders, one per subagent profile in `.claude/agents/` — see
[CLAUDE.md](CLAUDE.md#subagent-profiles).

**[`docs/league-logic/`](docs/league-logic/)** — what the numbers should be

- [Results Reporting Pipeline & RPH Watcher](docs/league-logic/results-pipeline.md)
- [League Rarity Roles & Player Registry](docs/league-logic/roles.md)
- [Season Rollover](docs/league-logic/season-rollover.md)
- [Store Classification & Overrides](docs/league-logic/store-classification.md)
- [Google Sheets Layout](docs/league-logic/google-sheets.md)
- [Design Notes](docs/league-logic/design-notes.md)

**[`docs/discord-surface/`](docs/discord-surface/)** — what members and mods interact with

- [Commands & Scheduled Tasks](docs/discord-surface/commands.md)
- [Design Notes & Crash-Loop Prevention](docs/discord-surface/design-notes.md)

**[`docs/bot-infra/`](docs/bot-infra/)** — how it runs and deploys

- [Deployment & Local Development](docs/bot-infra/deployment.md)
- [Design Notes, Memory & constants.py](docs/bot-infra/design-notes.md)
