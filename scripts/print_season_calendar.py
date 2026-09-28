"""
Print the Seasons tab as the bot reads it, plus what each digest resolves to.

Read-only. Use it to check a hand-edited row before deploying, or after editing the
tab, without waiting for a 7 AM digest to silently post nothing.

Usage:
    python scripts/print_season_calendar.py
"""

import os
import sys

# Allow running from project root or scripts/
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import date

import season
import stores


def _fmt(row: dict, key_start: str, key_end: str) -> str:
    return f"{row.get(key_start) or '—':<12} → {row.get(key_end) or '—'}"


if __name__ == '__main__':
    state, calendar, problems = stores.load_season_config()
    season.init(state, calendar)

    print(f"\nCurrent season: {season.CURRENT_SEASON}   (source: {season.CALENDAR_SOURCE})")
    print(f"Calendar rows:  {len(calendar)}\n")

    for row in season.SEASONS:
        marker = '  ◀ current' if row['season'] == season.CURRENT_SEASON else ''
        print(f"  {row['season']:<5} {row.get('set_name') or '(no set name)':<24} "
              f"row {row.get('sheet_row')}{marker}")
        print(f"        prerelease  {_fmt(row, 'prerelease_start', 'prerelease_end')}")
        print(f"        season      {_fmt(row, 'season_start', 'season_end')}")
        print(f"        set champs  {_fmt(row, 'set_champs_start', 'set_champs_end')}")

    print("\nResolved digest windows:")
    sc = season.set_champs_window()
    print(f"  set champs  {season.CURRENT_SEASON}: "
          f"{season.SET_CHAMPS_START_DATE or '—'} → {season.SET_CHAMPS_END_DATE or '—'}"
          f"{'' if sc else '   (not configured)'}")
    pre = season.active_prerelease()
    if pre:
        print(f"  prerelease  {pre['season']} {pre.get('set_name') or ''}: "
              f"{pre['prerelease_start']} → {pre['prerelease_end']}")
    else:
        print(f"  prerelease  none open as of {date.today()}")

    if problems:
        print(f"\n⚠ {len(problems)} problem(s):")
        for p in problems:
            print(f"    • {p}")
    else:
        print("\n✓ No problems.")
