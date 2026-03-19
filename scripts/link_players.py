"""
Interactive fuzzy-match linking for players in the Player Registry who have
role data but no Discord ID.

Reads directly from the Player Registry sheet (no need to re-read leaderboards).
For each unlinked player, fuzzy-matches against Discord members and prompts for
confirmation in the mod channel.

React ✅ to confirm — calls link_player(), assigns Discord roles.
React ❌ to skip.

Safe to stop and restart: skips members who are already fully linked.

Run:
    python scripts/link_players.py
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from dotenv import load_dotenv
load_dotenv()

import discord
from constants import (
    DISCORD_BOT_TOKEN,
    MOD_CHANNEL_ID,
    UNCOMMON_ROLE_ID,
    RARE_ROLE_ID,
    SUPER_RARE_ROLE_ID,
    LEGENDARY_ROLE_ID,
)
from roles import (
    get_player_registry,
    link_player,
    fuzzy_match_member,
    RARITY_ROLE_IDS,
    RARITY_ROLE_NAMES,
    FUZZY_HIGH_CONFIDENCE,
    FUZZY_LOW_CONFIDENCE,
)

intents = discord.Intents.default()
intents.members = True
client = discord.Client(intents=intents)

# Module-level state
_queue: list[dict] = []         # registry rows with roles but no discord_id
_members: list = []             # non-bot guild members
_matched_discord_ids: set[int] = set()
_mod_ch = None
_pending: dict[int, dict] = {}  # at most 1 entry at a time

_ROLE_KEYS = [
    (LEGENDARY_ROLE_ID,  'legendary'),
    (SUPER_RARE_ROLE_ID, 'super_rare'),
    (RARE_ROLE_ID,       'rare'),
    (UNCOMMON_ROLE_ID,   'uncommon'),
]


def _has_roles(row: dict) -> bool:
    return any(row.get(key) for _, key in _ROLE_KEYS)


async def _post_next():
    """Post the next match suggestion from the queue."""
    while _queue:
        row = _queue.pop(0)
        player_name = row['playhub_name']

        # Build role summary
        role_ids = {role_id for role_id, key in _ROLE_KEYS if row.get(key)}
        role_names_str = " + ".join(
            RARITY_ROLE_NAMES.get(r, "?")
            for r in sorted(role_ids, key=lambda r: RARITY_ROLE_IDS.index(r), reverse=True)
        )
        seasons_str = ", ".join(
            f"{RARITY_ROLE_NAMES.get(role_id, '?')}:{row[key]}"
            for role_id, key in _ROLE_KEYS if row.get(key)
        )

        # Check if best match already has all roles (skip if so)
        best_all, score_all = fuzzy_match_member(player_name, _members)
        if best_all and score_all >= FUZZY_LOW_CONFIDENCE:
            already_has = {r.id for r in best_all.roles}
            if not (role_ids - already_has):
                _matched_discord_ids.add(best_all.id)
                print(f"  [SKIP] {player_name} -> {best_all.display_name} already has all earned roles")
                continue

        available = [m for m in _members if m.id not in _matched_discord_ids]
        best_member, score = fuzzy_match_member(player_name, available)

        if best_member and score >= FUZZY_LOW_CONFIDENCE:
            already_has = {r.id for r in best_member.roles}
            if not (role_ids - already_has):
                _matched_discord_ids.add(best_member.id)
                print(f"  [SKIP] {player_name} -> {best_member.display_name} already has all earned roles")
                continue

        remaining = len(_queue)

        if best_member and score >= FUZZY_LOW_CONFIDENCE:
            high = score >= FUZZY_HIGH_CONFIDENCE
            low_hint = f"\n\nIf the match is wrong, ❌ then use:\n`/link @member \"{player_name}\"`" if not high else ""
            embed = discord.Embed(
                title=f"{'Suggested' if high else 'Low-Confidence'} Match — {role_names_str}",
                description=(
                    f"**RPH name:** {player_name}\n"
                    f"**Discord:** {best_member.mention} (`{best_member.display_name}`)\n"
                    f"**Confidence:** {score:.0%}\n"
                    f"**Roles:** {role_names_str}\n"
                    f"**Seasons:** {seasons_str}\n\n"
                    f"React to confirm or skip. ({remaining} remaining after this)"
                    f"{low_hint}"
                ),
                colour=discord.Colour.yellow() if high else discord.Colour.orange()
            )
            msg = await _mod_ch.send(embed=embed)
            await msg.add_reaction("✅")
            await msg.add_reaction("❌")
            _pending[msg.id] = {
                'registry_row':  row,
                'discord_id':    best_member.id,
                'discord_name':  best_member.display_name,
                'role_ids':      role_ids,
                'role_names_str': role_names_str,
            }
            print(f"  [{'HIGH' if high else 'LOW '}] {player_name} -> {best_member.display_name} ({score:.0%}) — {role_names_str}  ({remaining} left)")
            return

        else:
            embed = discord.Embed(
                title=f"No Match — {role_names_str}",
                description=(
                    f"**RPH name:** {player_name}\n"
                    f"**Roles:** {role_names_str}\n"
                    f"**Seasons:** {seasons_str}\n"
                    f"**Best guess:** {best_member.display_name if best_member else 'n/a'} ({score:.0%})\n\n"
                    f"If you know who this is:\n`/link @member \"{player_name}\"`"
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

    guild = client.guilds[0]
    _mod_ch = guild.get_channel(MOD_CHANNEL_ID)
    if not _mod_ch:
        print(f"ERROR: Mod channel not found (MOD_CHANNEL_ID={MOD_CHANNEL_ID})")
        await client.close()
        return

    _members[:] = [m for m in guild.members if not m.bot]
    _matched_discord_ids = set()
    print(f"  {len(_members)} members loaded")

    print("  Loading Player Registry...")
    registry = get_player_registry()
    # Only rows that have role data but no discord_id
    unlinked = [r for r in registry if _has_roles(r) and not r['discord_id']]
    print(f"  {len(unlinked)} unlinked players with role data")

    # Sort: highest role first
    def _max_role_idx(row):
        ids = [role_id for role_id, key in _ROLE_KEYS if row.get(key)]
        return max((RARITY_ROLE_IDS.index(r) for r in ids), default=0)

    unlinked.sort(key=_max_role_idx, reverse=True)
    _queue[:] = unlinked
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
        row         = suggestion['registry_row']
        discord_id  = suggestion['discord_id']
        member      = guild.get_member(discord_id)

        # Call link_player to fill Discord columns
        try:
            role_seasons = link_player(
                discord_id,
                suggestion['discord_name'],
                'fuzzy-confirmed',
                playhub_id=row.get('playhub_id') or None,
                playhub_name=row.get('playhub_name'),
            )
        except Exception as e:
            print(f"  [ERROR] link_player failed for {row['playhub_name']}: {e}")
            role_seasons = {}

        # Assign Discord roles
        if member and role_seasons:
            current = {r.id for r in member.roles}
            for role_id in role_seasons:
                if role_id not in current:
                    discord_role = guild.get_role(role_id)
                    if discord_role:
                        try:
                            await member.add_roles(discord_role, reason="link_players-script")
                        except discord.HTTPException as e:
                            print(f"  [WARN] Could not assign {role_id} to {member.display_name}: {e}")

        _matched_discord_ids.add(discord_id)
        print(f"  Linked {row['playhub_name']} -> {suggestion['discord_name']} ({suggestion['role_names_str']})")

        new_embed = discord.Embed(
            title=f"Linked — {suggestion['role_names_str']}",
            description=f"**{row['playhub_name']}** → <@{discord_id}>",
            colour=discord.Colour.green()
        )
    else:
        print(f"  Skipped: {suggestion['registry_row']['playhub_name']}")
        new_embed = discord.Embed(
            title="Skipped",
            description=f"**{suggestion['registry_row']['playhub_name']}**",
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
