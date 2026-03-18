"""
Backfill player linking for existing standings data.

Reads all standings rows from the sheet, finds Playhub IDs not yet in
"Playhub <-> Discord IDs", fuzzy-matches them against Discord members,
and posts suggestions to the mod channel — identical to the per-event
linking flow that runs automatically going forward.

Run once after initial deploy to catch historical data:
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
    COMMON_ROLE_ID,
)
from roles import (
    get_unlinked_players,
    fuzzy_match_member,
    add_player_mapping,
    FUZZY_HIGH_CONFIDENCE,
    FUZZY_LOW_CONFIDENCE,
)

intents = discord.Intents.default()
intents.members = True
client = discord.Client(intents=intents)

# Keyed by message_id — same structure as bot.py _pending_link_suggestions
_pending: dict[int, dict] = {}


@client.event
async def on_ready():
    print(f"Connected as {client.user}")

    guild = client.guilds[0]
    mod_ch = guild.get_channel(MOD_CHANNEL_ID)
    if not mod_ch:
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

    members = [m for m in guild.members if not m.bot]
    print(f"  Matching against {len(members)} Discord members...")

    for playhub_id, display_name in new_players:
        best_member, score = fuzzy_match_member(display_name, members)

        if score >= FUZZY_HIGH_CONFIDENCE:
            embed = discord.Embed(
                title="🔗 Suggested Player Link",
                description=(
                    f"**Playhub:** {display_name} (ID: `{playhub_id}`)\n"
                    f"**Discord:** {best_member.mention} (`{best_member.display_name}`)\n"
                    f"**Confidence:** {score:.0%}\n\n"
                    f"React ✅ to confirm or ❌ to skip."
                ),
                colour=discord.Colour.yellow()
            )
            msg = await mod_ch.send(embed=embed)
            await msg.add_reaction("✅")
            await msg.add_reaction("❌")
            _pending[msg.id] = {
                'playhub_id':   playhub_id,
                'display_name': display_name,
                'discord_id':   best_member.id,
                'discord_name': best_member.display_name,
            }
            print(f"  [HIGH] {display_name} → {best_member.display_name} ({score:.0%})")

        elif score >= FUZZY_LOW_CONFIDENCE and best_member:
            embed = discord.Embed(
                title="🔗 Low-Confidence Match",
                description=(
                    f"**Playhub:** {display_name} (ID: `{playhub_id}`)\n"
                    f"**Closest Discord match:** {best_member.mention} "
                    f"(`{best_member.display_name}`) — {score:.0%}\n\n"
                    f"Use `/link @member {playhub_id}` to confirm manually."
                ),
                colour=discord.Colour.orange()
            )
            await mod_ch.send(embed=embed)
            print(f"  [LOW ] {display_name} → {best_member.display_name} ({score:.0%})")

        else:
            embed = discord.Embed(
                title="❓ Unmatched Player",
                description=(
                    f"**Playhub:** {display_name} (ID: `{playhub_id}`)\n"
                    f"No confident Discord match found.\n\n"
                    f"Use `/link @member {playhub_id}` to link manually."
                ),
                colour=discord.Colour.red()
            )
            await mod_ch.send(embed=embed)
            print(f"  [NONE] {display_name} (best: {best_member.display_name if best_member else 'n/a'} {score:.0%})")

    print(f"\nDone — {len(new_players)} suggestion(s) posted to mod channel.")
    print("Waiting for reactions (Ctrl+C to exit once you're done confirming)...")


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
        print(f"  Linked: {suggestion['display_name']} -> {suggestion['discord_name']}")
        new_embed = discord.Embed(
            title="✅ Linked",
            description=(
                f"**{suggestion['display_name']}** (Playhub `{suggestion['playhub_id']}`)"
                f" → <@{suggestion['discord_id']}>"
            ),
            colour=discord.Colour.green()
        )
    else:
        print(f"  Skipped: {suggestion['display_name']}")
        new_embed = discord.Embed(
            title="❌ Skipped — use /link to resolve",
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

    if not _pending:
        print("\nAll suggestions resolved — closing.")
        await client.close()


client.run(DISCORD_BOT_TOKEN)
