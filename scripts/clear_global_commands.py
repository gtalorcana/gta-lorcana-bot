"""
ONE-TIME SCRIPT — run this once to clear any existing global Discord commands
before switching to guild-scoped commands via the automated sync.

After running this, all future command syncs happen automatically on fly deploy
via scripts/sync_commands.py as a release_command.

Usage:
    python scripts/clear_global_commands.py
"""

import asyncio
import os
import discord
from dotenv import load_dotenv

load_dotenv()

DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")

if not DISCORD_BOT_TOKEN:
    raise ValueError("DISCORD_BOT_TOKEN not set")

from bot import bot, tree

async def clear_globals():
    async with bot:
        await bot.login(DISCORD_BOT_TOKEN)
        tree.clear_commands(guild=None)
        await tree.sync()
        print("✓ Global commands cleared")

asyncio.run(clear_globals())