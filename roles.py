"""
League role management.

Handles:
  - player_mapping sheet CRUD ("Playhub <-> Discord IDs" in STORE_SPREADSHEET_ID)
  - Fuzzy matching of Playhub display names against Discord members
  - Role calculation: Common (join), Uncommon (10+ events), Rare (top-32 leaderboard)
  - /sync-roles bulk assignment logic

Standing row columns (index): date(0) | store(1) | rank(2) | display_name(3) |
                                record(4) | match_points(5) | playhub_id(6)
"""

from difflib import SequenceMatcher
from datetime import datetime, timezone

from clients import gs as _gs
from constants import (
    STORE_SPREADSHEET_ID,
    PLAYER_MAPPING_RANGE_NAME,
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

FUZZY_HIGH_CONFIDENCE = 0.75   # auto-suggest with ✅/❌ reaction prompt
FUZZY_LOW_CONFIDENCE  = 0.50   # surface for manual /link, no reaction prompt

UNCOMMON_EVENT_THRESHOLD = 10  # distinct events to earn Uncommon
RARE_RANK_THRESHOLD      = 32  # leaderboard rank to earn Rare


# ── Player Mapping Sheet ───────────────────────────────────────────────────────

def get_player_mapping() -> list[dict]:
    """Return all rows from 'Playhub <-> Discord IDs' as list of dicts."""
    data = _gs.get_values(STORE_SPREADSHEET_ID, PLAYER_MAPPING_RANGE_NAME)
    result = []
    for row in data.get('values', []):
        if len(row) < 2:
            continue
        result.append({
            'discord_id':   int(row[0]) if row[0].isdigit() else None,
            'playhub_id':   row[1],
            'display_name': row[2] if len(row) > 2 else '',
            'linked_at':    row[3] if len(row) > 3 else '',
            'linked_by':    row[4] if len(row) > 4 else '',
        })
    return result


def get_mapped_playhub_ids() -> set[str]:
    return {m['playhub_id'] for m in get_player_mapping() if m['playhub_id']}


def add_player_mapping(discord_id: int, playhub_id: str, display_name: str, linked_by: str):
    """Write a link row to the player mapping sheet.

    If a row with the same playhub_id already exists, overwrite it in place.
    Otherwise append a new row.
    """
    now = datetime.now(timezone.utc).isoformat()
    row = [str(discord_id), playhub_id, display_name, now, linked_by]

    data = _gs.get_values(STORE_SPREADSHEET_ID, PLAYER_MAPPING_RANGE_NAME)
    rows = data.get('values', [])
    for i, existing in enumerate(rows):
        if len(existing) > 1 and existing[1] == playhub_id:
            # Row i in the data = sheet row (i + 2) because the range starts at A2
            sheet_row = i + 2
            sheet_name = PLAYER_MAPPING_RANGE_NAME.split('!')[0]
            _gs.update_values(
                STORE_SPREADSHEET_ID,
                f"{sheet_name}!A{sheet_row}:E{sheet_row}",
                "USER_ENTERED",
                [row],
            )
            return

    _gs.append_values(
        STORE_SPREADSHEET_ID,
        PLAYER_MAPPING_RANGE_NAME,
        "USER_ENTERED",
        [row],
    )


# ── Fuzzy Matching ─────────────────────────────────────────────────────────────

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


def get_unlinked_players(standing_rows: list[list]) -> list[tuple[str, str]]:
    """
    From a full standings row list, return [(playhub_id, display_name)]
    for players not yet in player_mapping. Deduplicates by playhub_id.
    """
    known = get_mapped_playhub_ids()
    seen  = set()
    result = []
    for row in standing_rows:
        if len(row) < 7:
            continue
        playhub_id   = str(row[6])
        display_name = row[3]
        if playhub_id and playhub_id not in known and playhub_id not in seen:
            seen.add(playhub_id)
            result.append((playhub_id, display_name))
    return result


# ── Role Calculation ───────────────────────────────────────────────────────────

def compute_role_assignments(guild, standing_rows: list[list]) -> list[tuple]:
    """
    For each player in player_mapping who is a guild member and has standings data,
    compute whether they've earned a higher rarity role than they currently hold.

    Returns list of (member, roles_to_remove, new_role) — only where an upgrade
    is needed. Roles are never downgraded.
    """
    mapping = get_player_mapping()
    playhub_to_discord = {
        m['playhub_id']: m['discord_id']
        for m in mapping if m['playhub_id'] and m['discord_id']
    }

    # Group standing rows by discord_id
    discord_to_rows: dict[int, list] = {}
    for row in standing_rows:
        if len(row) < 7:
            continue
        pid        = str(row[6])
        discord_id = playhub_to_discord.get(pid)
        if discord_id:
            discord_to_rows.setdefault(discord_id, []).append(row)

    rarity_id_set = set(RARITY_ROLE_IDS)
    changes = []

    for discord_id, rows in discord_to_rows.items():
        member = guild.get_member(discord_id)
        if not member:
            continue

        distinct_events = len({(r[0], r[1]) for r in rows})
        try:
            best_rank = min(int(r[2]) for r in rows)
        except (ValueError, IndexError):
            best_rank = 9999

        if best_rank <= RARE_RANK_THRESHOLD:
            earned_id = RARE_ROLE_ID
        elif distinct_events >= UNCOMMON_EVENT_THRESHOLD:
            earned_id = UNCOMMON_ROLE_ID
        else:
            earned_id = COMMON_ROLE_ID

        earned_idx = RARITY_ROLE_IDS.index(earned_id)

        current_rarity_roles = [r for r in member.roles if r.id in rarity_id_set]
        current_max_idx = max(
            (RARITY_ROLE_IDS.index(r.id) for r in current_rarity_roles),
            default=-1
        )

        if earned_idx <= current_max_idx:
            continue  # no upgrade needed

        new_role        = guild.get_role(earned_id)
        roles_to_remove = [r for r in current_rarity_roles if RARITY_ROLE_IDS.index(r.id) < earned_idx]
        if new_role:
            changes.append((member, roles_to_remove, new_role))

    return changes
