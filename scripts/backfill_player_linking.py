"""
Backfill player linking for existing standings data.

Reads all standings rows from the sheet, finds Playhub IDs not yet in
"Playhub <-> Discord IDs", fuzzy-matches them against Discord members,
and posts suggestions to the mod channel one at a time.

React ✅ or ❌ to each message — the next suggestion posts automatically.
Safe to stop and restart: confirmed players are saved to the sheet
immediately, so the script resumes from the first unresolved player.

Run:
    python scripts/backfill_player_linking.py
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
    LEAGUE_SPREADSHEET_ID,
    STANDINGS_RANGE_NAME,
    MOD_CHANNEL_ID,
)
from roles import (
    get_unlinked_players,
    get_player_mapping,
    fuzzy_match_member,
    add_player_mapping,
    FUZZY_HIGH_CONFIDENCE,
    FUZZY_LOW_CONFIDENCE,
)

intents = discord.Intents.default()
intents.members = True
client = discord.Client(intents=intents)

# Module-level state
_queue: list[tuple] = []            # (playhub_id, display_name) not yet posted
_members: list = []                 # cached non-bot guild members
_matched_discord_ids: set[int] = {} # discord IDs already linked — excluded from fuzzy matching
_mod_ch = None                      # cached mod channel
_pending: dict[int, dict] = {}      # at most 1 entry at a time


async def _post_next():
    """Post the next suggestion from the queue, or close if done."""
    # Skip LOW/NONE players automatically — post them but don't wait for a reaction
    while _queue:
        playhub_id, display_name = _queue.pop(0)
        available_members = [m for m in _members if m.id not in _matched_discord_ids]
        best_member, score = fuzzy_match_member(display_name, available_members)

        if score >= FUZZY_HIGH_CONFIDENCE or (score >= FUZZY_LOW_CONFIDENCE and best_member):
            remaining = len(_queue)
            high = score >= FUZZY_HIGH_CONFIDENCE
            embed = discord.Embed(
                title="Suggested Player Link" if high else "Low-Confidence Match",
                description=(
                    f"**Playhub:** {display_name} (ID: `{playhub_id}`)\n"
                    f"**Discord:** {best_member.mention} (`{best_member.display_name}`)\n"
                    f"**Confidence:** {score:.0%}\n\n"
                    f"React to confirm or skip.  ({remaining} remaining after this)"
                ),
                colour=discord.Colour.yellow() if high else discord.Colour.orange()
            )
            msg = await _mod_ch.send(embed=embed)
            await msg.add_reaction("✅")
            await msg.add_reaction("❌")
            _pending[msg.id] = {
                'playhub_id':   playhub_id,
                'display_name': display_name,
                'discord_id':   best_member.id,
                'discord_name': best_member.display_name,
            }
            level = "HIGH" if high else "LOW "
            print(f"  [{level}] {display_name} -> {best_member.display_name} ({score:.0%})  ({remaining} left)")
            return  # wait for reaction before continuing

        else:
            add_player_mapping(0, playhub_id, display_name, 'skipped')
            print(f"  [NONE] {display_name} (best: {best_member.display_name if best_member else 'n/a'} {score:.0%}) — written to sheet as skipped")
            # no reaction needed — fall through to next

    # Queue exhausted and no pending reaction
    if not _pending:
        print("\nAll suggestions resolved — closing.")
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

    print("Reading standings sheet...")
    data = _gs.get_values(LEAGUE_SPREADSHEET_ID, STANDINGS_RANGE_NAME)
    standing_rows = data.get('values', [])
    print(f"  {len(standing_rows)} standing rows retrieved")

    new_players = get_unlinked_players(standing_rows)
    print(f"  {len(new_players)} unlinked Playhub IDs found")

    if not new_players:
        print("Nothing to do — all players already linked.")
        await client.close()
        return

    _members[:] = [m for m in guild.members if not m.bot]
    _matched_discord_ids = {
        m['discord_id'] for m in get_player_mapping()
        if m['discord_id']  # excludes skipped rows (discord_id=0)
    }
    _queue[:] = new_players
    print(f"  Matching against {len(_members)} Discord members ({len(_matched_discord_ids)} already linked, excluded)...")
    print(f"  Posting one at a time — react to each to advance.\n")

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
        add_player_mapping(
            suggestion['discord_id'],
            suggestion['playhub_id'],
            suggestion['display_name'],
            'fuzzy-confirmed',
        )
        _matched_discord_ids.add(suggestion['discord_id'])
        print(f"  Linked: {suggestion['display_name']} -> {suggestion['discord_name']}")
        new_embed = discord.Embed(
            title="Linked",
            description=(
                f"**{suggestion['display_name']}** (Playhub `{suggestion['playhub_id']}`)"
                f" -> <@{suggestion['discord_id']}>"
            ),
            colour=discord.Colour.green()
        )
    else:
        add_player_mapping(0, suggestion['playhub_id'], suggestion['display_name'], 'skipped')
        print(f"  Skipped: {suggestion['display_name']}")
        new_embed = discord.Embed(
            title="Skipped - use /link to resolve",
            description=(
                f"**{suggestion['display_name']}** (Playhub `{suggestion['playhub_id']}`)"
            ),
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
