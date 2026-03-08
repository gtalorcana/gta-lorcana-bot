"""
Syncs slash commands to the configured Discord guild.

Runs automatically as a Fly.io release_command on every deploy.
Can also be run manually: python scripts/sync_commands.py

To make the bot global (i.e. work in all servers):
    1. Remove guild= from tree.sync() — use await tree.sync() only
    2. Remove tree.copy_global_to(guild=guild)
    3. Note: global commands take up to 1 hour to propagate to all servers
    4. Run this script once after switching to clear old guild commands:
           tree.clear_commands(guild=guild)
           await tree.sync(guild=guild)
"""

import os
import sys
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

import discord
from bot import bot, tree

@bot.event
async def on_ready():
    guild = discord.Object(id=int(DISCORD_GUILD_ID))
    try:
        # Clear global commands
        tree.clear_commands(guild=None)
        await tree.sync()

        # Sync to guild
        tree.copy_global_to(guild=guild)
        synced = await tree.sync(guild=guild)

        print(f"✓ Synced {len(synced)} command(s) to guild {DISCORD_GUILD_ID}:")
        for cmd in synced:
            print(f"  /{cmd.name}")
    except Exception as e:
        print(f"✗ Sync failed: {e}")
    finally:
        await bot.close()

bot.run(DISCORD_BOT_TOKEN)