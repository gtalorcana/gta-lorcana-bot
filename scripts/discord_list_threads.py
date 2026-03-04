"""
List threads in #results-reporting within a date range.

Usage:
    python scripts/discord_list_threads.py 2026-02-13
    python scripts/discord_list_threads.py 2026-02-13 2026-04-24
"""

import asyncio
import sys
from datetime import datetime, timezone

import discord
import os

from constants import RESULTS_REPORTING_CHANNEL

DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")


def parse_date(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc)


async def main(start_date: datetime, end_date: datetime):
    intents = discord.Intents.default()
    intents.message_content = True
    intents.members = True

    client = discord.Client(intents=intents)

    @client.event
    async def on_ready():
        try:
            for guild in client.guilds:
                forum = discord.utils.get(guild.forums, name=RESULTS_REPORTING_CHANNEL)
                if not forum:
                    print(f"Could not find forum channel #{RESULTS_REPORTING_CHANNEL} in {guild.name}")
                    continue

                # Collect active + archived threads
                threads = list(forum.threads)
                async for thread in forum.archived_threads(limit=None):
                    if thread not in threads:
                        threads.append(thread)

                # Filter by date range
                filtered = [
                    t for t in threads
                    if t.created_at
                    and t.created_at >= start_date
                    and t.created_at <= end_date
                ]

                # Sort chronologically
                filtered.sort(key=lambda t: t.created_at)

                print(f"\n{'─' * 60}")
                print(f"  #{RESULTS_REPORTING_CHANNEL} threads")
                print(f"  From: {start_date.date()}  To: {end_date.date()}")
                print(f"  Found: {len(filtered)} thread(s)")
                print(f"{'─' * 60}")
                for t in filtered:
                    print(f"  {t.created_at.strftime('%Y-%m-%d')}  {t.id}  {t.name}")
                print(f"{'─' * 60}\n")
        finally:
            await client.close()

    await client.start(DISCORD_BOT_TOKEN)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python scripts/discord_list_threads.py <start_date> [end_date]")
        print("       Dates in YYYY-MM-DD format")
        sys.exit(1)

    try:
        start = parse_date(sys.argv[1])
    except ValueError:
        print(f"Invalid start date: {sys.argv[1]} — use YYYY-MM-DD")
        sys.exit(1)

    if len(sys.argv) >= 3:
        try:
            end = parse_date(sys.argv[2]).replace(hour=23, minute=59, second=59)
        except ValueError:
            print(f"Invalid end date: {sys.argv[2]} — use YYYY-MM-DD")
            sys.exit(1)
    else:
        end = datetime.now(timezone.utc)

    asyncio.run(main(start, end))