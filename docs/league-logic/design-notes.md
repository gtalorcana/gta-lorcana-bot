# League Logic — Design Notes

## Key Design Decisions

- `_sheet_lock` serializes all sheet writes — never bypass it
- Roles never auto-downgrade — every path that grants them is additive only
- Registry role columns (G–J) hold the earliest season earned: blank takes the new value, populated is replaced only by an earlier season
- Recording and assigning are separate; the Player Registry is the single source of truth and Discord is downstream of it. See [roles.md](roles.md)

---

## Season Config (`season.py`)

Season values (dates, sheet names, range names) live in `season.py` as mutable module globals.
`season.init(bot_state)` is called at startup and by `/season-rollover` to rebuild all derived values.

All consumers must use `import season; season.X` — not `from season import X` — to get the live
call-time value rather than a frozen import-time copy.

Fallback values in `constants.py` are used when Bot State keys are absent.
