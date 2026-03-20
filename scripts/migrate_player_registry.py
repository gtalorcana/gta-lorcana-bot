"""
One-time migration: populate the Player Registry sheet from the old
"Playhub <-> Discord IDs" and historical season leaderboards.

Steps:
  1. Read old "Playhub <-> Discord IDs" tab
  2. Clear Player Registry
  3. Read S5–S10 archive leaderboards
  4. Read Invitational Results
  5. Build per-player role_seasons
  6. Merge old link info
  7. Write Player Registry rows
  8. (Optional) --clear-discord-roles: strip Legendary/SR/Rare/Uncommon from all members
  9. (Optional) --assign-discord-roles: assign earned Discord roles for all linked players

Run:
    python scripts/migrate_player_registry.py
    python scripts/migrate_player_registry.py --assign-discord-roles
    python scripts/migrate_player_registry.py --clear-discord-roles --assign-discord-roles
"""

import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from dotenv import load_dotenv
load_dotenv()

import discord
from clients import gs as _gs
from constants import (
    DISCORD_BOT_TOKEN,
    BOT_DATABASE_SPREADSHEET_ID,
    ARCHIVE_SPREADSHEET_ID,
    RARE_ROLE_ID,
    UNCOMMON_ROLE_ID,
    SUPER_RARE_ROLE_ID,
    LEGENDARY_ROLE_ID,
    PLAYER_REGISTRY_RANGE_NAME,
    PLAYER_REGISTRY_SHEET_NAME,
    INVITATIONAL_RESULTS_RANGE_NAME,
    MOD_CHANNEL_ID,
)
from roles import (
    RARITY_ROLE_IDS,
    RARITY_ROLE_NAMES,
    UNCOMMON_EVENT_THRESHOLD,
    RARE_RANK_THRESHOLD,
)

# Seasons to pull from archive
ARCHIVE_SEASONS = ["S5", "S6", "S7", "S8", "S9", "S10"]

# Old mapping sheet columns: discord_id(0) | playhub_id(1) | display_name(2) | linked_at(3) | linked_by(4)
OLD_MAPPING_SHEET_NAME = "Playhub <-> Discord IDs"
OLD_MAPPING_RANGE_NAME = OLD_MAPPING_SHEET_NAME + "!A2:E"

# Parse args before bot startup
parser = argparse.ArgumentParser(description="Migrate to Player Registry sheet")
parser.add_argument("--rebuild-registry", action="store_true",
                    help="Clear and rebuild the Player Registry from archive + old mapping (DESTRUCTIVE)")
parser.add_argument("--clear-discord-roles", action="store_true",
                    help="Remove Legendary/SR/Rare/Uncommon from all guild members (requires --rebuild-registry)")
parser.add_argument("--assign-discord-roles", action="store_true",
                    help="Assign Discord roles based on current registry for all linked players (safe, never clears sheet)")
args = parser.parse_args()


intents = discord.Intents.default()
intents.members = True
client = discord.Client(intents=intents)


def _season_num(s: str) -> int:
    try:
        return int(s.strip().lstrip('Ss'))
    except ValueError:
        return 0


def _load_old_mapping() -> dict:
    """Return {display_name_lower: {discord_id, playhub_id, display_name, linked_at, linked_by}}"""
    print("  Loading old Playhub <-> Discord IDs mapping...")
    try:
        data = _gs.get_values(BOT_DATABASE_SPREADSHEET_ID, OLD_MAPPING_RANGE_NAME)
        rows = data.get('values', [])
    except Exception as e:
        print(f"  [WARN] Could not read old mapping: {e}")
        return {}

    result = {}
    for row in rows:
        padded = list(row) + [''] * (5 - len(row))
        discord_id_str = padded[0].strip()
        if not discord_id_str.isdigit():
            continue
        display_name = padded[2].strip()
        if not display_name:
            continue
        result[display_name.lower()] = {
            'discord_id':  int(discord_id_str),
            'playhub_id':  padded[1].strip(),
            'display_name': display_name,
            'linked_at':   padded[3].strip(),
            'linked_by':   padded[4].strip(),
        }
    print(f"  {len(result)} old mapping entries loaded")
    return result


def _load_leaderboard(spreadsheet_id: str, range_name: str, season: str) -> list[dict]:
    """Load a leaderboard range and return list of {player_name, rank, events_played, season}."""
    try:
        data = _gs.get_values(spreadsheet_id, range_name)
        rows = data.get('values', [])
        results = []
        for row in rows:
            if len(row) < 2:
                continue
            try:
                rank = int(row[0])
            except ValueError:
                continue
            player_name = row[1].strip()
            try:
                events_played = int(row[3]) if len(row) > 3 and row[3] else 0
            except ValueError:
                events_played = 0
            results.append({
                'player_name':   player_name,
                'rank':          rank,
                'events_played': events_played,
                'season':        season,
            })
        return results
    except Exception as e:
        print(f"  [WARN] Could not load {season} leaderboard: {e}")
        return []


def _build_player_data(old_mapping: dict) -> list[list]:
    """
    Read archive leaderboards + invitational results.
    Returns list of 10-element rows ready for the Player Registry sheet.
    """
    # player_name -> {'role_seasons': {role_id: earliest_season}, 'playhub_id': str|None, 'link': dict|None}
    player_data: dict[str, dict] = {}

    def _update(player_name: str, role_id: int, season: str):
        entry = player_data.setdefault(player_name, {
            'role_seasons': {},
            'playhub_id': None,
        })
        existing = entry['role_seasons'].get(role_id)
        if existing is None or _season_num(season) < _season_num(existing):
            entry['role_seasons'][role_id] = season

    # Archive leaderboards
    for season in ARCHIVE_SEASONS:
        range_name = f"{season} Leaderboard!A2:D"
        print(f"  Loading {season} leaderboard...")
        rows = _load_leaderboard(ARCHIVE_SPREADSHEET_ID, range_name, season)
        for r in rows:
            if r['rank'] <= RARE_RANK_THRESHOLD:
                _update(r['player_name'], RARE_ROLE_ID, r['season'])
            if r['events_played'] >= UNCOMMON_EVENT_THRESHOLD:
                _update(r['player_name'], UNCOMMON_ROLE_ID, r['season'])

    # Invitational results
    print("  Loading invitational results...")
    try:
        inv_data = _gs.get_values(BOT_DATABASE_SPREADSHEET_ID, INVITATIONAL_RESULTS_RANGE_NAME)
        for row in inv_data.get('values', []):
            if len(row) < 3:
                continue
            season      = row[0].strip()
            player_name = row[1].strip()
            try:
                finish = int(row[2])
            except ValueError:
                continue
            _update(player_name, SUPER_RARE_ROLE_ID, season)
            if finish == 1:
                _update(player_name, LEGENDARY_ROLE_ID, season)
        print("  Invitational results loaded")
    except Exception as e:
        print(f"  [WARN] Could not load invitational results: {e}")

    # Build registry rows
    registry_rows = []
    for player_name, data in player_data.items():
        role_seasons = data['role_seasons']
        link = old_mapping.get(player_name.lower())

        row = [
            player_name,
            role_seasons.get(LEGENDARY_ROLE_ID, ''),
            role_seasons.get(SUPER_RARE_ROLE_ID, ''),
            role_seasons.get(RARE_ROLE_ID, ''),
            role_seasons.get(UNCOMMON_ROLE_ID, ''),
            str(link['discord_id']) if link else '',
            link['display_name'] if link else '',
            link['playhub_id'] if link else '',
            link['linked_at'] if link else '',
            link['linked_by'] if link else '',
        ]
        registry_rows.append(row)

    print(f"  {len(registry_rows)} player rows built")
    return registry_rows


@client.event
async def on_ready():
    print(f"Connected as {client.user}")
    guild = client.guilds[0]
    members = [m for m in guild.members if not m.bot]
    rarity_roles = {UNCOMMON_ROLE_ID, RARE_ROLE_ID, SUPER_RARE_ROLE_ID, LEGENDARY_ROLE_ID}

    # --assign-discord-roles alone: read registry as-is, assign roles, done.
    # Never touches the sheet data.
    if args.assign_discord_roles and not args.rebuild_registry:
        print("  --assign-discord-roles only: reading current Player Registry...")
        from roles import get_player_registry, RARITY_ROLE_NAMES as _ROLE_NAMES
        registry = get_player_registry()
        assigned_count = 0
        discord_id_to_member = {m.id: m for m in members}
        for entry in registry:
            if not entry['discord_id']:
                continue
            member = discord_id_to_member.get(entry['discord_id'])
            if not member:
                continue
            current_role_ids = {r.id for r in member.roles}
            role_col_map = [
                (LEGENDARY_ROLE_ID,  entry['legendary']),
                (SUPER_RARE_ROLE_ID, entry['super_rare']),
                (RARE_ROLE_ID,       entry['rare']),
                (UNCOMMON_ROLE_ID,   entry['uncommon']),
            ]
            roles_to_add = [guild.get_role(rid) for rid, season in role_col_map
                            if season and rid not in current_role_ids and guild.get_role(rid)]
            if roles_to_add:
                try:
                    await member.add_roles(*roles_to_add, reason="migrate_player_registry: assign")
                    print(f"    Assigned {', '.join(_ROLE_NAMES.get(r.id, r.name) for r in roles_to_add)} → {member.display_name}")
                    assigned_count += len(roles_to_add)
                except discord.HTTPException as e:
                    print(f"    [WARN] Could not assign roles for {member.display_name}: {e}")
        print(f"\nDone — assigned {assigned_count} role(s).")
        await client.close()
        return

    # Full rebuild (default, or --rebuild-registry)
    # Step 1: Load old mapping
    old_mapping = _load_old_mapping()

    # Step 2: Clear Player Registry
    print(f"  Clearing Player Registry...")
    try:
        _gs.clear_values(BOT_DATABASE_SPREADSHEET_ID, PLAYER_REGISTRY_RANGE_NAME)
        print("  Player Registry cleared")
    except Exception as e:
        print(f"  [ERROR] Could not clear Player Registry: {e}")
        await client.close()
        return

    # Steps 3–6: Build registry rows
    print("  Building player data...")
    registry_rows = _build_player_data(old_mapping)

    # Step 7: Write registry rows
    print(f"  Writing {len(registry_rows)} rows to Player Registry...")
    if registry_rows:
        try:
            _gs.update_values(
                BOT_DATABASE_SPREADSHEET_ID,
                PLAYER_REGISTRY_RANGE_NAME,
                'USER_ENTERED',
                registry_rows,
            )
            print("  Player Registry written successfully")
        except Exception as e:
            print(f"  [ERROR] Could not write Player Registry: {e}")
            await client.close()
            return

    # Step 8: --clear-discord-roles
    if args.clear_discord_roles:
        print(f"  --clear-discord-roles: removing rarity roles from {len(members)} members...")
        cleared = 0
        for member in members:
            roles_to_remove = [r for r in member.roles if r.id in rarity_roles]
            if roles_to_remove:
                try:
                    await member.remove_roles(*roles_to_remove, reason="migrate_player_registry: clear")
                    cleared += 1
                    print(f"    Cleared: {member.display_name}")
                except discord.HTTPException as e:
                    print(f"    [WARN] Could not clear roles for {member.display_name}: {e}")
        print(f"  Cleared rarity roles from {cleared} members")

    # Step 9: --assign-discord-roles
    if args.assign_discord_roles:
        print("  --assign-discord-roles: assigning earned roles for linked players...")
        assigned_count = 0
        discord_id_to_member = {m.id: m for m in members}

        for row in registry_rows:
            discord_id_str = row[5].strip()
            if not discord_id_str.isdigit():
                continue
            discord_id = int(discord_id_str)
            member = discord_id_to_member.get(discord_id)
            if not member:
                continue

            current_role_ids = {r.id for r in member.roles}
            # role columns: Legendary=1, SR=2, Rare=3, Uncommon=4
            role_col_map = [
                (LEGENDARY_ROLE_ID,  row[1]),
                (SUPER_RARE_ROLE_ID, row[2]),
                (RARE_ROLE_ID,       row[3]),
                (UNCOMMON_ROLE_ID,   row[4]),
            ]
            roles_to_add = []
            for role_id, season in role_col_map:
                if season and role_id not in current_role_ids:
                    discord_role = guild.get_role(role_id)
                    if discord_role:
                        roles_to_add.append(discord_role)

            if roles_to_add:
                try:
                    await member.add_roles(*roles_to_add, reason="migrate_player_registry: assign")
                    role_names = [RARITY_ROLE_NAMES.get(r.id, r.name) for r in roles_to_add]
                    print(f"    Assigned {', '.join(role_names)} → {member.display_name}")
                    assigned_count += len(roles_to_add)
                except discord.HTTPException as e:
                    print(f"    [WARN] Could not assign roles for {member.display_name}: {e}")

        print(f"  Assigned {assigned_count} role(s) across linked members")

    print("\nMigration complete.")
    await client.close()


client.run(DISCORD_BOT_TOKEN)
