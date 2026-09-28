"""
Print the CCQs the bot would post, with a breakdown of which signal matched each one.

Read-only — nothing is written to a sheet or to Discord. Use it to check the match
before trusting the digest, and to catch the two failure modes that are otherwise
silent:

  - RPH rotates the CCQ template UUID. The template arm then matches nothing and the
    digest quietly degrades to name-only. Watch the "template" count go to zero.
  - A false positive on the name. "ccq" is a three-letter token and this fetch spans
    373 miles and two countries, so read every matched event name.

Knobs below: SHOW_ALL dumps every event in the window; ARMS restricts matching to one
signal at a time; KEEP_FORMAT_FILTER re-applies the league's Constructed/Draft filter
to see how much of the fetch it would save.

Usage:
    python scripts/rph_get_ccq_events.py
"""

import os
import sys

# Allow running from project root or scripts/
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import date, datetime, timedelta

import season
import stores
from clients import rph_api

# 'both' | 'template' | 'name' — restrict which signal may match
ARMS = 'both'
# True to dump every event in the window instead of just CCQs
SHOW_ALL = False
# True to keep the league's gameplay_format_ids filter (Constructed + Draft)
KEEP_FORMAT_FILTER = False


def _matches(event: dict) -> bool:
    by_template = event.get('event_configuration_template') in stores._CCQ_TEMPLATE_IDS
    by_name     = bool(stores._CCQ_NAME_RE.search(event.get('name') or ''))
    if ARMS == 'template':
        return by_template
    if ARMS == 'name':
        return by_name
    return by_template or by_name


if __name__ == '__main__':
    today    = date.today()
    end      = today + timedelta(days=stores._CCQ_LOOKAHEAD_DAYS)
    extra    = {
        'display_status':   None,
        'display_statuses': ['upcoming', 'inProgress'],
        'num_miles':        stores._CCQ_RADIUS_MILES,
    }
    if not KEEP_FORMAT_FILTER:
        extra['gameplay_format_ids'] = None

    print(f"\nFetching CCQs")
    print(f"  Window:  {today} → {end}  ({stores._CCQ_LOOKAHEAD_DAYS} days, upcoming only)")
    print(f"  Radius:  {stores._CCQ_RADIUS_MILES} miles (~{round(stores._CCQ_RADIUS_MILES * 1.609)} km), all countries")
    print(f"  Arms:    {ARMS}   SHOW_ALL={SHOW_ALL}   KEEP_FORMAT_FILTER={KEEP_FORMAT_FILTER}")
    print(f"  Template(s): {', '.join(stores._CCQ_TEMPLATE_IDS)}\n")

    scanned, pages_est, matched = 0, 0, []
    for event in rph_api.iter_events(
        start_date_after=season.rph_day_start(today.isoformat()),
        start_date_before=season.rph_day_end(end.isoformat()),
        extra_params=extra,
        require_started=False,
        countries=None,
    ):
        scanned += 1
        if SHOW_ALL or _matches(event):
            matched.append(event)

    print(f"  ✓ {scanned} event(s) scanned, {len(matched)} matched "
          f"(~{scanned // 50 + 1} API pages)\n")

    both = tmpl_only = name_only = 0
    for e in matched:
        by_template = e.get('event_configuration_template') in stores._CCQ_TEMPLATE_IDS
        by_name     = bool(stores._CCQ_NAME_RE.search(e.get('name') or ''))
        if by_template and by_name:
            both += 1
        elif by_template:
            tmpl_only += 1
        else:
            name_only += 1
    print(f"  Matched by both arms:      {both}")
    print(f"  Template UUID only:        {tmpl_only}")
    print(f"  Event name only:           {name_only}")
    if matched and both + tmpl_only == 0:
        print(f"  ⚠ NOTHING matched the template UUID — RPH may have rotated it.")
    print()

    rows = stores._event_digest_rows(matched, with_region=True)
    print(f"  {'#':<4} {'Date':<12} {'Time':<9} {'Cap':<5} {'Format':<20} {'Store':<34} City")
    print(f"  {'-'*4} {'-'*12} {'-'*9} {'-'*5} {'-'*20} {'-'*34} {'-'*24}")
    for i, row in enumerate(rows, 1):
        print(f"  {i:<4} {row[0]:<12} {row[1]:<9} {str(row[5]):<5} {row[6][:20]:<20} "
              f"{row[3][:34]:<34} {row[4]}")

    print(f"\n  Event names (read these for false positives):")
    for row in rows:
        print(f"    {row[0]}  {row[7]}")

    dates = sorted({row[0] for row in rows})
    print(f"\n  {len(rows)} row(s) across {len(dates)} distinct date(s) — one digest message per date.")
