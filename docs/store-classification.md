# Store Classification

Every Sunday, `analyse_stores()` in `stores.py`:

1. Fetches all Ontario Lorcana events for the current season from RPH
2. Groups events by `(store_id, day_of_week, floored_hour, format)` — events within the same clock hour are merged to handle organizers adjusting start times slightly week to week
3. Classifies each group based on streak
4. Applies manual overrides from the `Overrides` sheet tab
5. Saves results to `Store Classifications` (post-override) and `Store Debug` (pre-override, raw RPH data)

---

## Classification Rules

| Status | Criteria |
|--------|----------|
| Regular | Consecutive streak ≥ 2 weeks |
| Semi-Regular | Ran at least once in the last 2 weeks AND at least twice total |
| *(unlisted)* | Everything else |

**Reference date:** Streaks are always evaluated against the last *completed* week, not the current in-progress one. This prevents a store from being demoted mid-week just because this week's event hasn't happened yet.

**Display time:** The most common raw start time across all events in the group. A `~` prefix is added when times vary (e.g. `~6:30 PM`). On a tie, the earliest time is shown — better to arrive early.

**City parsing:** City is extracted from the RPH `full_address` field by anchoring on the 2-letter province code (e.g. `ON`, `QC`) and taking the token immediately before it. Handles edge cases like missing commas before postal codes, lowercase city names, and multi-word cities (e.g. `Old Toronto`, `Greater Sudbury`).

---

## Overrides

The `Overrides` tab in the Bot Database sheet is manually maintained. The bot reads it on every `analyse_stores()` run and never writes to it.

**Sheet columns:**
```
store_id | store_name | day | time | format | override_status | override_day | override_time | reason
```

| `override_status` | Behaviour |
|------------------|-----------|
| `Regular` | Force to Regular; optionally replace `day` and/or `time` |
| `Semi-Regular` | Force to Semi-Regular; optionally replace `day` and/or `time` |
| `Exclude` | Remove the entry entirely |
| `Add` | Inject a brand new entry — `override_day` and `override_time` required |

Match-based overrides (`Regular`, `Semi-Regular`, `Exclude`) match on exact `(store_id, day, time, format)`. These must match what's in the `Store Classifications` tab exactly.

`Add` overrides don't need a match — the `day` and `time` columns can be left blank. City is automatically looked up from the RPH event data if the store appears anywhere in the season.

**Examples:**

Fix a store showing the wrong day in RPH data:
```
1776 | Game 3 TCG & Hobby | Wednesday | 7:00 PM | Core Constructed | Regular | Tuesday | 6:30 PM | Bad RPH data
```

Manually add a store missing from RPH entirely:
```
1467 | Enter The Battlefield Newmarket |  |  | Core Constructed | Add | Wednesday | 7:00 PM | Missing from RPH
```
