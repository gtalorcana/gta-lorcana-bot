"""
League role management.

Handles:
  - Player Registry sheet CRUD ("Player Registry" in STORE_SPREADSHEET_ID)
  - Fuzzy matching of Playhub display names against Discord members
  - Role calculation: Uncommon (10+ events), Rare (top-32 leaderboard),
    Super Rare / Legendary (invitational)

Registry columns (A–J, data starts row 2):
  A: Playhub Name  B: Legendary  C: Super Rare  D: Rare  E: Uncommon
  F: Discord ID    G: Discord Display Name  H: Playhub ID
  I: Linked At     J: Link Method

Standing row columns (index): date(0) | store(1) | rank(2) | display_name(3) |
                                record(4) | match_points(5) | playhub_id(6)
"""

from difflib import SequenceMatcher
from datetime import datetime, timezone

from clients import gs as _gs
from constants import (
    STORE_SPREADSHEET_ID,
    PLAYER_REGISTRY_RANGE_NAME,
    PLAYER_REGISTRY_SHEET_NAME,
    COMMON_ROLE_ID,
    UNCOMMON_ROLE_ID,
    RARE_ROLE_ID,
    SUPER_RARE_ROLE_ID,
    LEGENDARY_ROLE_ID,
)

# Ordered lowest → highest; index is used for comparison and upgrade checks.
RARITY_ROLE_IDS = [
    COMMON_ROLE_ID,
    UNCOMMON_ROLE_ID,
    RARE_ROLE_ID,
    SUPER_RARE_ROLE_ID,
    LEGENDARY_ROLE_ID,
]

# Human-readable names for rarity roles (excluding Common)
RARITY_ROLE_NAMES = {
    UNCOMMON_ROLE_ID:   "Uncommon",
    RARE_ROLE_ID:       "Rare",
    SUPER_RARE_ROLE_ID: "Super Rare",
    LEGENDARY_ROLE_ID:  "Legendary",
}

FUZZY_HIGH_CONFIDENCE = 0.75   # auto-suggest with ✅/❌ reaction prompt
FUZZY_LOW_CONFIDENCE  = 0.50   # surface for manual /link, no reaction prompt

UNCOMMON_EVENT_THRESHOLD = 10  # distinct events to earn Uncommon
RARE_RANK_THRESHOLD      = 32  # leaderboard rank to earn Rare

# Maps role_id → column index (0-based) in registry row (A=0 … J=9)
# B=Legendary(1), C=Super Rare(2), D=Rare(3), E=Uncommon(4)
_ROLE_COL = {
    LEGENDARY_ROLE_ID:  1,
    SUPER_RARE_ROLE_ID: 2,
    RARE_ROLE_ID:       3,
    UNCOMMON_ROLE_ID:   4,
}
_ROLE_COL_LETTER = {
    LEGENDARY_ROLE_ID:  'B',
    SUPER_RARE_ROLE_ID: 'C',
    RARE_ROLE_ID:       'D',
    UNCOMMON_ROLE_ID:   'E',
}


# ── Helpers ────────────────────────────────────────────────────────────────────

def _row_to_dict(row: list) -> dict:
    """Convert a raw registry row (list) to a typed dict. Pads short rows."""
    padded = list(row) + [''] * (10 - len(row))
    return {
        'playhub_name':   padded[0].strip(),
        'legendary':      padded[1].strip(),
        'super_rare':     padded[2].strip(),
        'rare':           padded[3].strip(),
        'uncommon':       padded[4].strip(),
        'discord_id':     int(padded[5]) if padded[5].strip().isdigit() else None,
        'discord_name':   padded[6].strip(),
        'playhub_id':     padded[7].strip(),
        'linked_at':      padded[8].strip(),
        'link_method':    padded[9].strip(),
    }


def _dict_to_row(d: dict) -> list:
    """Convert a registry dict back to a 10-element list for the sheet."""
    return [
        d.get('playhub_name', ''),
        d.get('legendary', ''),
        d.get('super_rare', ''),
        d.get('rare', ''),
        d.get('uncommon', ''),
        str(d['discord_id']) if d.get('discord_id') else '',
        d.get('discord_name', ''),
        d.get('playhub_id', ''),
        d.get('linked_at', ''),
        d.get('link_method', ''),
    ]


def _role_col_for(role_id: int) -> str | None:
    """Return the registry dict key for a role_id, or None."""
    return {
        LEGENDARY_ROLE_ID:  'legendary',
        SUPER_RARE_ROLE_ID: 'super_rare',
        RARE_ROLE_ID:       'rare',
        UNCOMMON_ROLE_ID:   'uncommon',
    }.get(role_id)


# ── Player Registry ────────────────────────────────────────────────────────────

def get_player_registry() -> list[dict]:
    """Return all non-empty rows from Player Registry as list of dicts."""
    data = _gs.get_values(STORE_SPREADSHEET_ID, PLAYER_REGISTRY_RANGE_NAME)
    result = []
    for row in data.get('values', []):
        if not row or not row[0].strip():
            continue
        result.append(_row_to_dict(row))
    return result


def upsert_player_roles(playhub_name: str, role_seasons: dict[int, str], playhub_id: str = None):
    """
    Create or update role columns B–E for a player in the registry.

    Only writes blank cells (preserves earliest season already recorded).
    Lookup priority: playhub_id (if provided) → playhub_name (case-insensitive).
    If no row exists, appends a new one.

    role_seasons: {role_id: season_str}
    """
    if not role_seasons:
        return

    data = _gs.get_values(STORE_SPREADSHEET_ID, PLAYER_REGISTRY_RANGE_NAME)
    rows = data.get('values', [])

    row_idx = None

    # Try to find by playhub_id first
    if playhub_id:
        for i, row in enumerate(rows):
            padded = list(row) + [''] * (10 - len(row))
            if padded[7].strip() == str(playhub_id):
                row_idx = i
                break

    # Fall back to name match — exact first, then case-insensitive
    if row_idx is None:
        for i, row in enumerate(rows):
            if row and row[0].strip() == playhub_name:
                row_idx = i
                break
    if row_idx is None:
        for i, row in enumerate(rows):
            if row and row[0].strip().lower() == playhub_name.lower():
                row_idx = i
                break

    if row_idx is None:
        # No existing row — build new one
        new_row = [playhub_name, '', '', '', '', '', '', playhub_id or '', '', '']
        for role_id, season in role_seasons.items():
            col = _ROLE_COL.get(role_id)
            if col is not None:
                new_row[col] = season
        _gs.append_values(
            STORE_SPREADSHEET_ID,
            PLAYER_REGISTRY_RANGE_NAME,
            'USER_ENTERED',
            [new_row],
        )
        return

    # Existing row — pad to 10 cols and update only blank cells
    existing = list(rows[row_idx]) + [''] * (10 - len(rows[row_idx]))
    sheet_row = row_idx + 2  # +1 for 0-index, +1 for header row

    for role_id, season in role_seasons.items():
        col = _ROLE_COL.get(role_id)
        col_letter = _ROLE_COL_LETTER.get(role_id)
        if col is None or col_letter is None:
            continue
        if existing[col]:
            continue  # already recorded — preserve oldest
        existing[col] = season
        _gs.update_values(
            STORE_SPREADSHEET_ID,
            f"{PLAYER_REGISTRY_SHEET_NAME}!{col_letter}{sheet_row}",
            'USER_ENTERED',
            [[season]],
        )

    # Also update playhub_id column (H) if provided and currently blank
    if playhub_id and not existing[7].strip():
        _gs.update_values(
            STORE_SPREADSHEET_ID,
            f"{PLAYER_REGISTRY_SHEET_NAME}!H{sheet_row}",
            'USER_ENTERED',
            [[str(playhub_id)]],
        )


def link_player(
    discord_id: int,
    discord_display_name: str,
    link_method: str,
    playhub_id: str = None,
    playhub_name: str = None,
) -> dict:
    """
    Fill Discord columns (F–J) for a registry row.

    Lookup priority: playhub_id → playhub_name → append new row.
    Returns {role_id: season} for all non-blank role columns in the matched row.
    Calls _merge_duplicate_rows after writing.
    """
    now = datetime.now(timezone.utc).isoformat()

    data = _gs.get_values(STORE_SPREADSHEET_ID, PLAYER_REGISTRY_RANGE_NAME)
    rows = data.get('values', [])

    row_idx = None

    if playhub_id:
        for i, row in enumerate(rows):
            padded = list(row) + [''] * (10 - len(row))
            if padded[7].strip() == str(playhub_id):
                row_idx = i
                break

    if row_idx is None and playhub_name:
        # Exact match first, then case-insensitive fallback
        for i, row in enumerate(rows):
            if row and row[0].strip() == playhub_name:
                row_idx = i
                break
    if row_idx is None and playhub_name:
        for i, row in enumerate(rows):
            if row and row[0].strip().lower() == playhub_name.lower():
                row_idx = i
                break

    if row_idx is None:
        # No existing row — create one
        new_row = [
            playhub_name or '',
            '', '', '', '',
            str(discord_id),
            discord_display_name,
            str(playhub_id) if playhub_id else '',
            now,
            link_method,
        ]
        _gs.append_values(
            STORE_SPREADSHEET_ID,
            PLAYER_REGISTRY_RANGE_NAME,
            'USER_ENTERED',
            [new_row],
        )
        _merge_duplicate_rows(discord_id)
        return {}

    # Update Discord columns in the existing row
    sheet_row = row_idx + 2
    existing = list(rows[row_idx]) + [''] * (10 - len(rows[row_idx]))

    updates = [
        ('F', str(discord_id)),
        ('G', discord_display_name),
        ('I', now),
        ('J', link_method),
    ]
    if playhub_id and not existing[7].strip():
        updates.append(('H', str(playhub_id)))

    for col_letter, value in updates:
        _gs.update_values(
            STORE_SPREADSHEET_ID,
            f"{PLAYER_REGISTRY_SHEET_NAME}!{col_letter}{sheet_row}",
            'USER_ENTERED',
            [[value]],
        )

    # Read back the row to extract role data
    updated_row = existing[:]
    updated_row[5] = str(discord_id)
    updated_row[6] = discord_display_name
    updated_row[8] = now
    updated_row[9] = link_method
    if playhub_id and not existing[7].strip():
        updated_row[7] = str(playhub_id)

    row_dict = _row_to_dict(updated_row)
    role_seasons = {}
    for role_id, key in [
        (LEGENDARY_ROLE_ID,  'legendary'),
        (SUPER_RARE_ROLE_ID, 'super_rare'),
        (RARE_ROLE_ID,       'rare'),
        (UNCOMMON_ROLE_ID,   'uncommon'),
    ]:
        if row_dict[key]:
            role_seasons[role_id] = row_dict[key]

    _merge_duplicate_rows(discord_id)
    return role_seasons


def _merge_duplicate_rows(discord_id: int):
    """
    If multiple rows share the same Discord ID, union their role seasons
    (keeping the earliest per role) and blank out duplicates.

    The "best" row is determined by: has playhub_id + most roles filled.
    All other rows with the same discord_id are cleared of their Discord columns.
    """
    data = _gs.get_values(STORE_SPREADSHEET_ID, PLAYER_REGISTRY_RANGE_NAME)
    rows = data.get('values', [])

    matching = []
    for i, row in enumerate(rows):
        padded = list(row) + [''] * (10 - len(row))
        if padded[5].strip().isdigit() and int(padded[5].strip()) == discord_id:
            matching.append((i, padded))

    if len(matching) <= 1:
        return  # nothing to merge

    # Score each row: has playhub_id → +10, each non-blank role col → +1
    def _score(padded):
        s = 10 if padded[7].strip() else 0
        for col in range(1, 5):
            if padded[col].strip():
                s += 1
        return s

    matching.sort(key=lambda x: _score(x[1]), reverse=True)
    best_idx, best_row = matching[0]

    # Union role seasons into the best row — preserve earliest (lowest sort order)
    best_row = list(best_row)
    for _, dup_row in matching[1:]:
        for col in range(1, 5):
            if dup_row[col].strip():
                if not best_row[col].strip() or _season_num(dup_row[col]) < _season_num(best_row[col]):
                    best_row[col] = dup_row[col].strip()

    # Write best row back
    best_sheet_row = best_idx + 2
    _gs.update_values(
        STORE_SPREADSHEET_ID,
        f"{PLAYER_REGISTRY_SHEET_NAME}!A{best_sheet_row}:J{best_sheet_row}",
        'USER_ENTERED',
        [best_row],
    )

    # Blank the entire duplicate row (A–J) so it no longer appears in the registry
    for dup_idx, _ in matching[1:]:
        dup_sheet_row = dup_idx + 2
        _gs.update_values(
            STORE_SPREADSHEET_ID,
            f"{PLAYER_REGISTRY_SHEET_NAME}!A{dup_sheet_row}:J{dup_sheet_row}",
            'USER_ENTERED',
            [[''] * 10],
        )


def get_linked_playhub_ids() -> set[str]:
    """Return set of playhub_ids where discord_id is set."""
    registry = get_player_registry()
    return {r['playhub_id'] for r in registry if r['playhub_id'] and r['discord_id']}


def get_unlinked_players(standing_rows: list[list]) -> list[tuple[str, str]]:
    """
    From a full standings row list (playhub_id at index 6, display_name at index 3),
    return (playhub_id, display_name) for players not yet linked in the registry.

    Checks by both playhub_id and display_name. Deduplicates by playhub_id.
    """
    registry = get_player_registry()
    linked_ids   = {r['playhub_id'] for r in registry if r['playhub_id'] and r['discord_id']}
    linked_names = {r['playhub_name'].lower() for r in registry if r['discord_id']}

    seen   = set()
    result = []
    for row in standing_rows:
        if len(row) < 7:
            continue
        playhub_id   = str(row[6])
        display_name = row[3]
        if playhub_id and playhub_id not in linked_ids and display_name.lower() not in linked_names:
            if playhub_id not in seen:
                seen.add(playhub_id)
                result.append((playhub_id, display_name))
    return result


# ── Fuzzy Matching ─────────────────────────────────────────────────────────────

def _season_num(s: str) -> int:
    """Extract numeric value from a season string for correct ordering, e.g. 'S10' -> 10."""
    try:
        return int(s.strip().lstrip('Ss'))
    except ValueError:
        return 0


def _similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def fuzzy_match_member(name: str, members) -> tuple:
    """
    Match a Playhub display name against a list of discord.Member objects.
    Checks display_name and global_name for each member.
    Returns (best_member, score) where score is 0.0–1.0.
    """
    best_member = None
    best_score  = 0.0
    for member in members:
        candidates = [member.display_name]
        if getattr(member, 'global_name', None):
            candidates.append(member.global_name)
        for candidate in candidates:
            score = _similarity(name, candidate)
            if score > best_score:
                best_score  = score
                best_member = member
    return best_member, best_score


# ── Role Calculation ───────────────────────────────────────────────────────────

def compute_earned_roles(rank: int, events_played: int) -> set[int]:
    """
    Return the set of role_ids earned based on rank and events_played.
    Roles are additive — both Rare and Uncommon can be earned simultaneously.

    Rare      — rank ≤ RARE_RANK_THRESHOLD (32)
    Uncommon  — events_played ≥ UNCOMMON_EVENT_THRESHOLD (10)
    """
    earned = set()
    if rank <= RARE_RANK_THRESHOLD:
        earned.add(RARE_ROLE_ID)
    if events_played >= UNCOMMON_EVENT_THRESHOLD:
        earned.add(UNCOMMON_ROLE_ID)
    return earned
