"""
Backfill rarity roles from historical season leaderboards.

Reads leaderboard data from the archive spreadsheet (S1–S10) and the current
season leaderboard, determines the highest role each player has earned across
all seasons, then fuzzy-matches player names to Discord members one at a time.

React to confirm or skip each match — roles are assigned immediately on confirm.
Safe to stop and restart: already-assigned members are skipped on resume.

Role thresholds:
  Rare      — leaderboard rank 1–32
  Uncommon  — 10+ events played (and not already Rare)

Run:
    python scripts/backfill_historical_roles.py

To include additional seasons in the archive, update ARCHIVE_SEASONS below.
"""

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
    ARCHIVE_SPREADSHEET_ID,
    RARE_ROLE_ID,
    UNCOMMON_ROLE_ID,
    RARE_RANK_THRESHOLD,
)
from roles import (
    RARITY_ROLE_IDS,
    UNCOMMON_EVENT_THRESHOLD,
    fuzzy_match_member,
    FUZZY_HIGH_CONFIDENCE,
    FUZZY_LOW_CONFIDENCE,
)

# Seasons available in the archive spreadsheet
ARCHIVE_SEASONS = ["S5", "S6", "S7", "S8", "S9", "S10"]

intents = discord.Intents.default()
intents.members = True
client = discord.Client(intents=intents)

# Module-level state
_queue: list[tuple] = []            # (player_name, earned_role_id, seasons) not yet posted
_members: list = []                 # non-bot guild members
_matched_discord_ids: set[int] = set()  # already assigned — excluded from future matching
_mod_ch = None
_pending: dict[int, dict] = {}      # at most 1 entry at a time


def _load_leaderboard(spreadsheet_id: str, range_name: str, season: str) -> list[dict]:
    """Load a leaderboard sheet and return list of {player_name, rank, events_played}."""
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
            results.append({'player_name': player_name, 'rank': rank, 'events_played': events_played, 'season': season})
        return results
    except Exception as e:
        print(f"  [WARN] Could not load {season} leaderboard: {e}")
        return []


def _build_queue() -> list[tuple]:
    """
    Read all season leaderboards, aggregate per player, return sorted queue.
    Each entry: (player_name, earned_role_id, seasons_str)
    Rare > Uncommon — only the highest earned role is included.
    """
    # player_name -> {'best_role': role_id, 'seasons': []}
    player_best: dict[str, dict] = {}

    def _update(player_name: str, role_id: int, season: str):
        key = player_name.lower()
        entry = player_best.setdefault(player_name, {'best_role_idx': -1, 'seasons': [], 'display_name': player_name})
        idx = RARITY_ROLE_IDS.index(role_id)
        if idx > entry['best_role_idx']:
            entry['best_role_idx'] = idx
        if season not in entry['seasons']:
            entry['seasons'].append(season)

    # Archive seasons only — current season (S11) is handled by /sync-roles
    for season in ARCHIVE_SEASONS:
        range_name = f"{season} Leaderboard!A2:D"
        print(f"  Loading {season} leaderboard (archive)...")
        rows = _load_leaderboard(ARCHIVE_SPREADSHEET_ID, range_name, season)
        for r in rows:
            if r['rank'] <= RARE_RANK_THRESHOLD:
                _update(r['player_name'], RARE_ROLE_ID, r['season'])
            elif r['events_played'] >= UNCOMMON_EVENT_THRESHOLD:
                _update(r['player_name'], UNCOMMON_ROLE_ID, r['season'])

    # Build sorted queue: Rare first, then Uncommon
    queue = []
    for display_name, entry in player_best.items():
        if entry['best_role_idx'] < 0:
            continue
        role_id = RARITY_ROLE_IDS[entry['best_role_idx']]
        seasons_str = ", ".join(sorted(entry['seasons']))
        queue.append((display_name, role_id, seasons_str))

    queue.sort(key=lambda x: RARITY_ROLE_IDS.index(x[1]), reverse=True)
    return queue


async def _post_next():
    """Post the next match suggestion, skipping members who already have the role."""
    while _queue:
        player_name, role_id, seasons_str = _queue.pop(0)
        role_name = {RARE_ROLE_ID: "Rare", UNCOMMON_ROLE_ID: "Uncommon"}.get(role_id, "Unknown")

        available = [m for m in _members if m.id not in _matched_discord_ids]
        best_member, score = fuzzy_match_member(player_name, available)

        # Skip if this member already has this role or higher
        if best_member and score >= FUZZY_LOW_CONFIDENCE:
            rarity_id_set = set(RARITY_ROLE_IDS)
            current_max_idx = max(
                (RARITY_ROLE_IDS.index(r.id) for r in best_member.roles if r.id in rarity_id_set),
                default=-1
            )
            if current_max_idx >= RARITY_ROLE_IDS.index(role_id):
                print(f"  [SKIP] {player_name} -> {best_member.display_name} already has {role_name} or higher")
                continue

        remaining = len(_queue)

        if score >= FUZZY_HIGH_CONFIDENCE or score >= FUZZY_LOW_CONFIDENCE:
            high = score >= FUZZY_HIGH_CONFIDENCE
            embed = discord.Embed(
                title=f"{'Suggested' if high else 'Low-Confidence'} Match — {role_name}",
                description=(
                    f"**RPH name:** {player_name}\n"
                    f"**Discord:** {best_member.mention} (`{best_member.display_name}`)\n"
                    f"**Confidence:** {score:.0%}\n"
                    f"**Seasons:** {seasons_str}\n\n"
                    f"React to confirm or skip. ({remaining} remaining after this)"
                ),
                colour=discord.Colour.yellow() if high else discord.Colour.orange()
            )
            msg = await _mod_ch.send(embed=embed)
            await msg.add_reaction("✅")
            await msg.add_reaction("❌")
            _pending[msg.id] = {
                'player_name': player_name,
                'discord_id':  best_member.id,
                'discord_name': best_member.display_name,
                'role_id':     role_id,
                'role_name':   role_name,
            }
            print(f"  [{'HIGH' if high else 'LOW '}] {player_name} -> {best_member.display_name} ({score:.0%}) — {role_name}  ({remaining} left)")
            return

        else:
            embed = discord.Embed(
                title=f"No Match — {role_name}",
                description=(
                    f"**RPH name:** {player_name}\n"
                    f"**Seasons:** {seasons_str}\n"
                    f"**Best guess:** {best_member.display_name if best_member else 'n/a'} ({score:.0%})\n\n"
                    f"Assign manually in Discord roles, then re-run to skip."
                ),
                colour=discord.Colour.red()
            )
            await _mod_ch.send(embed=embed)
            print(f"  [NONE] {player_name} — no confident match (best: {best_member.display_name if best_member else 'n/a'} {score:.0%})")

    if not _pending:
        print("\nAll done — closing.")
        await client.close()


@client.event
async def on_ready():
    global _members, _mod_ch, _matched_discord_ids

    print(f"Connected as {client.user}")

    from constants import MOD_CHANNEL_ID
    guild = client.guilds[0]
    _mod_ch = guild.get_channel(MOD_CHANNEL_ID)
    if not _mod_ch:
        print(f"ERROR: Mod channel not found (MOD_CHANNEL_ID={MOD_CHANNEL_ID})")
        await client.close()
        return

    _members[:] = [m for m in guild.members if not m.bot]
    rarity_id_set = set(RARITY_ROLE_IDS)

    # Seed already-matched IDs: members who already have Rare or higher
    _matched_discord_ids = {
        m.id for m in _members
        if any(r.id in rarity_id_set and RARITY_ROLE_IDS.index(r.id) >= RARITY_ROLE_IDS.index(UNCOMMON_ROLE_ID)
               for r in m.roles)
    }
    print(f"  {len(_members)} members loaded, {len(_matched_discord_ids)} already have Uncommon or higher")

    print("\nLoading leaderboard data from all seasons...")
    queue = _build_queue()
    print(f"  {len(queue)} players to process\n")

    _queue[:] = queue
    await _post_next()


@client.event
async def on_raw_reaction_add(payload: discord.RawReactionActionEvent):
    if payload.user_id == client.user.id:
        return
    if payload.message_id not in _pending:
        return

    emoji = str(payload.emoji)
    if emoji not in ("✅", "❌"):
        return

    suggestion = _pending.pop(payload.message_id)
    guild  = client.get_guild(payload.guild_id)
    mod_ch = guild.get_channel(payload.channel_id)

    if emoji == "✅":
        member = guild.get_member(suggestion['discord_id'])
        role   = guild.get_role(suggestion['role_id'])
        if member and role:
            try:
                await member.add_roles(role, reason="historical-backfill")
                _matched_discord_ids.add(suggestion['discord_id'])
                print(f"  Assigned {suggestion['role_name']} to {suggestion['discord_name']}")
            except discord.HTTPException as e:
                print(f"  Failed to assign role: {e}")

        new_embed = discord.Embed(
            title=f"Assigned — {suggestion['role_name']}",
            description=f"**{suggestion['player_name']}** -> <@{suggestion['discord_id']}>",
            colour=discord.Colour.green()
        )
    else:
        print(f"  Skipped: {suggestion['player_name']}")
        new_embed = discord.Embed(
            title="Skipped",
            description=f"**{suggestion['player_name']}**",
            colour=discord.Colour.greyple()
        )

    try:
        msg = await mod_ch.fetch_message(payload.message_id)
        await msg.edit(embed=new_embed)
        await msg.clear_reactions()
    except Exception:
        pass

    await _post_next()


client.run(DISCORD_BOT_TOKEN)
