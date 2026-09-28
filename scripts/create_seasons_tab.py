"""
Create the Seasons tab in the Bot Database sheet and seed it from Bot State.

One-off migration. Safe to re-run: it skips creation if the tab exists and refuses to
write over rows that are already there, so it can never clobber hand-edited dates.

The S13 row is seeded from the live Bot State keys rather than from any doc, because
CLAUDE.md and the commit history disagreed about when S13 ended and the sheet is the
only authority. The S14 row is seeded from the prerelease keys written when that
digest was first posted; its season end and Set Champs dates stay blank until
announced, which loads cleanly by design.

Set WRITE = True to apply. Afterwards:
  - format C2:H as yyyy-mm-dd (Format → Number → Custom date) so dates read back ISO
  - run scripts/print_season_calendar.py to check what the bot sees

Usage:
    python scripts/create_seasons_tab.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from googleapiclient.errors import HttpError

import stores
from clients import gs
from constants import BOT_DATABASE_SPREADSHEET_ID, SEASONS_SHEET_NAME

WRITE = False

HEADER = ['Season', 'Set Name', 'Prerelease Start', 'Prerelease End',
          'Season Start', 'Season End', 'Set Champs Start', 'Set Champs End']

# Set names are not in Bot State — S13's is the set its Set Champs ran for.
SET_NAMES = {'S13': 'Attack of the Vine!'}


def _rows_from_bot_state(state: dict) -> list[list]:
    """Seed rows for the current season and the one owning the open prerelease."""
    current = state.get('season', '')
    rows = [[
        current,
        SET_NAMES.get(current, ''),
        '', '',                                  # S13's prerelease is long past, and unrecorded
        state.get('season_start_date', ''),
        state.get('season_end_date', ''),
        state.get('set_champs_start_date', ''),
        state.get('set_champs_end_date', ''),
    ]]

    # The prerelease keys describe the *incoming* season, which is why they are being
    # retired: they had nowhere to record which season they belonged to.
    pre_start = state.get('prerelease_start_date', '')
    pre_end   = state.get('prerelease_end_date', '')
    if pre_start and current.startswith('S'):
        incoming = f"S{int(current[1:]) + 1}"
        rows.append([
            incoming,
            state.get('prerelease_set_name', ''),
            pre_start, pre_end,
            pre_start,      # a set's season starts on its prerelease weekend
            '', '', '',     # season end and Set Champs dates: announced later
        ])
    return rows


if __name__ == '__main__':
    state = stores.load_bot_state(strict=True)
    rows  = _rows_from_bot_state(state)

    print(f"\nSeeding '{SEASONS_SHEET_NAME}' from Bot State (season={state.get('season')}):\n")
    print(f"  {' | '.join(h[:16] for h in HEADER)}")
    for row in rows:
        print(f"  {' | '.join((c or '—') for c in row)}")

    if not WRITE:
        print(f"\n  ⚠ WRITE = False — set it to True to apply.")
        sys.exit(0)

    try:
        gs.add_sheet(BOT_DATABASE_SPREADSHEET_ID, SEASONS_SHEET_NAME)
        print(f"\n  ✓ Created tab '{SEASONS_SHEET_NAME}'")
    except HttpError as e:
        if stores._is_already_exists(e):
            print(f"\n  Tab '{SEASONS_SHEET_NAME}' already exists")
        else:
            raise

    existing = gs.get_values(BOT_DATABASE_SPREADSHEET_ID, f"{SEASONS_SHEET_NAME}!A2:H").get('values', [])
    if any(any(str(c).strip() for c in r) for r in existing):
        print(f"  ⚠ Rows already present below the header — refusing to overwrite them.")
        print(f"    Edit the tab by hand, then run scripts/print_season_calendar.py.")
        sys.exit(1)

    gs.update_values(BOT_DATABASE_SPREADSHEET_ID, f"{SEASONS_SHEET_NAME}!A1:H1", "RAW", [HEADER])
    gs.update_values(BOT_DATABASE_SPREADSHEET_ID, f"{SEASONS_SHEET_NAME}!A2:H{len(rows) + 1}", "RAW", rows)
    print(f"  ✓ Wrote the header and {len(rows)} row(s)")
    print(f"\n  Next: format C2:H as yyyy-mm-dd, then run scripts/print_season_calendar.py")
