# GTA Lorcana Bot — Session Context

See README.md for project structure and links to all docs in docs/.

---

## Current Season

- Season: S11
- Start: 2026-02-13
- End: 2026-04-24
- Set Champs: 2026-04-04 → 2026-04-24

---

## Key Design Notes

- `ADMIN_USER_IDS` is a list (not set) — supports indexing for pings and `in` checks
- `_sheet_lock` serializes all sheet writes — never bypass it
- Bot State sheet is key-value; all runtime state (message IDs, watches, recheck guards) lives there
- Roles never auto-downgrade; role columns in Player Registry only written if blank (preserve earliest season earned)
