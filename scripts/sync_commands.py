"""
Syncs slash commands to the configured Discord guild.

Run manually after adding/removing commands:
    python scripts/sync_commands.py

Also runs automatically as a Fly.io release_command on every deploy.

To make the bot global (i.e. work in all servers):
    1. Remove the guild= parameter from bot.tree.sync() — use await bot.tree.sync() only
    2. Remove bot.tree.copy_global_to(guild=guild)
    3. Remove bot.tree.clear_commands(guild=guild)
    4. Note: global commands take up to 1 hour to propagate to all servers
    5. Run this script once after switching to clear the old guild commands:
           bot.tree.clear_commands(guild=guild)
           await bot.tree.sync(guild=guild)
"""

import asyncio
import os
import sys
import discord
from dotenv import load_dotenv

# Ensure /app is on the path so `bot` can be imported when running from scripts/
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

load_dotenv()

DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
DISCORD_GUILD_ID  = os.getenv("DISCORD_GUILD_ID")

if not DISCORD_BOT_TOKEN:
    raise ValueError("DISCORD_BOT_TOKEN not set")
if not DISCORD_GUILD_ID:
    raise ValueError("DISCORD_GUILD_ID not set — right-click your server in Discord > Copy Server ID")

# Import only the command tree from bot, not the full bot startup
from bot import bot, tree

async def sync():
    async with bot:
        await bot.login(DISCORD_BOT_TOKEN)

        guild = discord.Object(id=int(DISCORD_GUILD_ID))

        # Clear global commands
        tree.clear_commands(guild=None)
        await tree.sync()

        # Sync to guild
        tree.clear_commands(guild=guild)
        tree.copy_global_to(guild=guild)
        synced = await tree.sync(guild=guild)

        print(f"✓ Synced {len(synced)} command(s) to guild {DISCORD_GUILD_ID}:")
        for cmd in synced:
            print(f"  /{cmd.name}")

asyncio.run(sync())