"""
Remove Legendary, Super Rare, Rare, and Uncommon rarity roles from every
non-bot guild member.

Run:
    python scripts/clear_roles.py
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
    UNCOMMON_ROLE_ID,
    RARE_ROLE_ID,
    SUPER_RARE_ROLE_ID,
    LEGENDARY_ROLE_ID,
)
from roles import RARITY_ROLE_NAMES

RARITY_ROLES_TO_CLEAR = {UNCOMMON_ROLE_ID, RARE_ROLE_ID, SUPER_RARE_ROLE_ID, LEGENDARY_ROLE_ID}

intents = discord.Intents.default()
intents.members = True
client = discord.Client(intents=intents)


@client.event
async def on_ready():
    print(f"Connected as {client.user}")
    guild = client.guilds[0]
    members = [m for m in guild.members if not m.bot]
    print(f"  {len(members)} non-bot members found")
    print("  Clearing rarity roles...")

    cleared = 0
    for member in members:
        roles_to_remove = [r for r in member.roles if r.id in RARITY_ROLES_TO_CLEAR]
        if not roles_to_remove:
            continue
        role_names = [RARITY_ROLE_NAMES.get(r.id, r.name) for r in roles_to_remove]
        try:
            await member.remove_roles(*roles_to_remove, reason="clear_roles script")
            print(f"  Cleared {', '.join(role_names)} from {member.display_name}")
            cleared += 1
        except discord.HTTPException as e:
            print(f"  [WARN] Could not clear roles for {member.display_name}: {e}")

    print(f"\nDone — cleared roles from {cleared} members.")
    await client.close()


client.run(DISCORD_BOT_TOKEN)
