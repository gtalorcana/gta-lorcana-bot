"""
Run this once to force-sync slash commands to your Discord server instantly.
Usage: python discord_sync_commands.py
Requires: pip install discord.py python-dotenv

Must be run from the project root (same directory as bot.py).
DISCORD_GUILD_ID must be set in .env — right-click your server in Discord > Copy Server ID.
"""

import os
import discord
from dotenv import load_dotenv

load_dotenv()

DISCORD_GUILD_ID = os.getenv("DISCORD_GUILD_ID")

if not DISCORD_GUILD_ID:
    raise ValueError("DISCORD_GUILD_ID not set in .env — right-click your server in Discord > Copy Server ID")

from bot import bot, DISCORD_BOT_TOKEN

@bot.event
async def on_ready():
    guild = discord.Object(id=int(DISCORD_GUILD_ID))
    try:
        synced = await bot.tree.sync(guild=guild)
        print(f"✓ Synced {len(synced)} command(s) to guild {DISCORD_GUILD_ID}")
        for cmd in synced:
            print(f"  /{cmd.name}")
    except Exception as e:
        print(f"✗ Sync failed: {e}")
    await bot.close()

bot.run(DISCORD_BOT_TOKEN)