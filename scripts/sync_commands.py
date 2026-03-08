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

# Ensure /app is on the path so `bot` can be imported when running from scripts/
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

# Signal bot to sync commands and exit instead of running normally
os.environ["SYNC_COMMANDS_ONLY"] = "1"

from dotenv import load_dotenv
load_dotenv()

from bot import bot, DISCORD_BOT_TOKEN
bot.run(DISCORD_BOT_TOKEN)