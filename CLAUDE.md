# GTA Lorcana Bot — Session Context

See README.md for project structure and links to all docs in docs/.

---

## Current Season

- Season: S11
- Start: 2026-02-13
- End: 2026-04-24
- Set Champs: 2026-04-04 → 2026-04-26

---

## White-Label Extraction (future, not now)

When ready to extract a generic league engine from this GTA-specific bot,
the GTA-specific code is concentrated in these places:

**`util/rph_api_utils.py`** — hardcoded GTA/Lorcana params that would become Bot State config:
- `latitude: 43.653226` / `longitude: -79.3831843` — Toronto coordinates
- `num_miles: 250` — search radius
- `country == "CA"` / `administrative_area_level_1_short == "ON"` — Canada/Ontario filters
- `game_id: '1'` / `game_slug: 'disney-lorcana'` — Lorcana-specific
- `gameplay_format_ids: [...]` — Constructed + Booster Draft format UUIDs

**`stores.py`** — `_SET_CHAMPS_NAME_FILTER = "Set Champ"` keyword for RPH event name matching

**`constants.py`** — `WHERE_TO_PLAY_MIN_CONSECUTIVE_WEEKS`, `WHERE_TO_PLAY_POST_DAY/HOUR_ET`

**GTA-only features** (strip out entirely for white-label):
- `/etb-discount` command + `util/shopify_api_utils.py` — ETB discount integration
- `SHOPIFY_TOKEN`, `SHOPIFY_STORE_DOMAIN` constants
- `specs/SHOPIFY_DISCOUNT_SPEC.md`

Everything else (season rollover, results pipeline, store classification,
player registry, rarity roles, RPH watcher) is already generic league logic.

---

## TODO

- **Update league-rules Discord post on season rollover**: `discord/league-rules.md` is manually
  updated each season but still needs to be pushed to Discord. Plan: store the message ID in Bot
  State, add a `/update-league-post` command that reads the file and edits the message in-place.
  Message is a plain Discord message (not an embed). Do this after confirming message ID.

---

## Key Design Notes

- `ADMIN_USER_IDS` is a list (not set) — supports indexing for pings and `in` checks
- `_sheet_lock` serializes all sheet writes — never bypass it
- Bot State sheet is key-value; all runtime state (message IDs, watches, recheck guards) lives there
- Roles never auto-downgrade; role columns in Player Registry only written if blank (preserve earliest season earned)
