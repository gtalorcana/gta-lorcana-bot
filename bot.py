"""
GTA Lorcana — Discord Bot
=========================
Features:
  - Auto-syncs #announcements to the website via Cloudflare Worker
  - /schedule        — shows upcoming events
  - /results         — posts tournament results & syncs to site
  - /decklist        — members submit decklists to a dedicated channel
  - /rank            — self-assign a player role (Casual / Competitive / Judge)
  - /welcome         — manually welcome a member (admins only)
  - /help            — list all commands
  - on_member_join   — auto-greets new members automatically

Requirements:
  pip install discord.py aiohttp python-dotenv

Environment variables (.env or Wispbyte):
  DISCORD_BOT_TOKEN
  WORKER_URL
  WORKER_SECRET
  ANNOUNCEMENTS_CHANNEL      (default: announcements)
  RESULTS_REPORTING_CHANNEL  (default: results-reporting)
  RESULTS_CHANNEL            (default: results)
  DECKLISTS_CHANNEL          (default: decklists)
  WELCOME_CHANNEL            (default: general)
"""

import asyncio
import os
import re

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands, tasks
from dotenv import load_dotenv
from datetime import datetime, timezone

from rph_util import append_play_hub_url, get_standings

load_dotenv()

# ── Config ────────────────────────────────────────────────────
DISCORD_BOT_TOKEN     = os.getenv("DISCORD_BOT_TOKEN")
WORKER_URL            = os.getenv("WORKER_URL")
WORKER_SECRET         = os.getenv("WORKER_SECRET")
ANNOUNCEMENTS_CHANNEL = os.getenv("ANNOUNCEMENTS_CHANNEL", "announcements")
RESULTS_REPORTING_CHANNEL = os.getenv("RESULTS_REPORTING_CHANNEL", "results-reporting")
RESULTS_CHANNEL           = os.getenv("RESULTS_CHANNEL", "results")
DECKLISTS_CHANNEL     = os.getenv("DECKLISTS_CHANNEL", "decklists")
WELCOME_CHANNEL       = os.getenv("WELCOME_CHANNEL", "general")
EVENTS_URL_RE         = os.getenv("EVENTS_URL_RE", r'https://tcg.ravensburgerplay.com/events/[0-9]*')

# Roles members can self-assign via /rank
SELF_ASSIGN_ROLES = ["Casual", "Competitive", "Judge"]


# ── Bot setup ─────────────────────────────────────────────────
intents = discord.Intents.default()
intents.message_content = True  # read message text
intents.members = True          # on_member_join event

bot = commands.Bot(
    command_prefix="!",
    intents=intents,
)
tree = bot.tree


# ═══════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════

async def call_worker(payload: dict) -> bool:
    """Forward a payload to the Cloudflare Worker. Returns True on success."""
    headers = {
        "Content-Type":    "application/json",
        "X-Worker-Secret": WORKER_SECRET,
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(WORKER_URL, json=payload, headers=headers) as resp:
                if resp.status == 200:
                    print(f"  ✓ Worker synced OK")
                    return True
                body = await resp.text()
                print(f"  ✗ Worker {resp.status}: {body}")
                return False
    except Exception as e:
        print(f"  ✗ Worker error: {e}")
        return False


def make_embed(
    title: str,
    description: str,
    colour: discord.Colour = discord.Colour.gold()
) -> discord.Embed:
    """Create a consistently branded embed."""
    embed = discord.Embed(title=title, description=description, colour=colour)
    embed.set_footer(text="GTA Lorcana ✦ Greater Toronto Area")
    return embed


def get_channel(guild: discord.Guild, name: str):
    """Find a text channel by name."""
    return discord.utils.get(guild.text_channels, name=name)


# ═══════════════════════════════════════════════════════════════
# EVENTS
# ═══════════════════════════════════════════════════════════════

@tasks.loop(minutes=5)
async def keepalive():
    """Periodic heartbeat to keep the Discord gateway connection alive on Wispbyte."""
    print(f"  ♥ Heartbeat — bot alive, watching #{ANNOUNCEMENTS_CHANNEL} and #{RESULTS_REPORTING_CHANNEL}")


@bot.event
async def on_ready():
    print(f"✦ GTA Lorcana Bot online as {bot.user}")
    print(f"  Watching #{ANNOUNCEMENTS_CHANNEL} for website sync")
    print(f"  Watching #{RESULTS_REPORTING_CHANNEL} for website sync")
    # Start heartbeat first — before any potentially blocking calls
    if not keepalive.is_running():
        keepalive.start()
        print(f"  ♻ Keepalive task started")
    # Sync slash commands in background — non-blocking
    try:
        await tree.sync()
        print(f"  Slash commands synced")
    except Exception as e:
        print(f"  ⚠ Slash command sync failed (non-fatal): {e}")


@bot.event
async def on_message(message: discord.Message):
    """Auto-sync any message posted in #announcements to the website."""
    if message.author.bot:
        return
    if message.channel.name != ANNOUNCEMENTS_CHANNEL:
        await bot.process_commands(message)
        return
    if not message.content and not message.embeds:
        return

    print(f"  → Announcement from {message.author.display_name}: {message.content[:60]}...")

    payload = {
        "id":           str(message.id),
        "content":      message.content,
        "timestamp":    message.created_at.isoformat(),
        "channel_name": message.channel.name,
        "author": {
            "username": message.author.display_name,
            "bot":      False,
        },
        "embeds": [
            {"title": e.title or "", "description": e.description or ""}
            for e in message.embeds
        ],
    }
    await call_worker(payload)
    await bot.process_commands(message)


# @bot.event
# async def on_member_join(member: discord.Member):
#     """Auto-greet new members when they join the server."""
#     channel = get_channel(member.guild, WELCOME_CHANNEL)
#     if not channel:
#         return
#
#     embed = make_embed(
#         title=f"✦ Welcome, {member.display_name}!",
#         description=(
#             f"A new Illumineer has arrived in the GTA Lorcana community! 🌟\n\n"
#             f"**Getting started:**\n"
#             f"• Introduce yourself and tell us your favourite ink!\n"
#             f"• Use `/rank` to pick your player role\n"
#             f"• Check `#announcements` for upcoming events\n"
#             f"• Visit our website for the full schedule\n\n"
#             f"*The Great Illuminary shines brighter with you here.* ✨"
#         )
#     )
#     embed.set_thumbnail(url=member.display_avatar.url)
#     await channel.send(embed=embed)




# ═══════════════════════════════════════════════════════════════
# RESULTS REPORTING — Thread Detection
# ═══════════════════════════════════════════════════════════════

# Tracks thread IDs that have already been processed.
# Never cleared — so any duplicate on_thread_create for the same thread is
# always blocked, regardless of which event arrives first.
_seen_threads: set[int] = set()


async def process_results_reporting_thread(thread: discord.Thread) -> bool:
    """
    Fetch the thread starter message and run your results processing function.
    Returns True on success, False on validation/HTTP error.
    Replace the body of this function with your actual processing logic.
    """
    try:
        # Fetch the starter message (first message in the thread)
        starter = await thread.fetch_message(thread.id)
        text = starter.content

        print(f"  → Processing results thread: '{thread.name}' — {len(text)} chars")

        if not re.fullmatch(EVENTS_URL_RE, text.strip()):
            raise ValueError(f"Thread content does not match expected URL format.\nExpected: {EVENTS_URL_RE}\nGot: {text.strip()[:100]}")

        # ── YOUR FUNCTION GOES HERE ──────────────────────────
        loop = asyncio.get_running_loop()
        result1 = await loop.run_in_executor(None, append_play_hub_url, text)
        result2 = await loop.run_in_executor(None, lambda: get_standings())
        # ────────────────────────────────────────────────────

        return True

    except ValueError as e:
        raise  # Re-raise validation errors so caller can handle them
    except Exception as e:
        raise  # Re-raise HTTP/other errors so caller can handle them


async def run_results_reporting_pipeline(thread: discord.Thread, starter_msg: discord.Message, is_retry: bool = False):
    """Shared processing logic for both on_thread_create and on_message_edit."""
    retry_prefix = "Still could not" if is_retry else "Could not"
    retry_suffix = "again " if is_retry else ""

    # Transient status messages sent during this run.
    # All deleted in finally — only the final success/error message is kept.
    transient_msgs: list[discord.Message] = []

    # Clear any previous result reactions, then add the running indicator.
    # Each reaction gets its own try/except — if one doesn't exist, the others still run.
    try:
        await starter_msg.remove_reaction("✅", thread.guild.me)
    except Exception:
        pass
    try:
        await starter_msg.remove_reaction("❌", thread.guild.me)
    except Exception:
        pass
    try:
        await starter_msg.add_reaction("⏳")
    except Exception:
        pass

    # Single status message covers both first run and retries.
    try:
        status_msg = await thread.send(
            embed=make_embed(
                title="🔄 Retrying..." if is_retry else "🔄 Processing...",
                description="Reprocessing your results now..." if is_retry else "Your results are being uploaded...",
                colour=discord.Colour.blurple()
            )
        )
        transient_msgs.append(status_msg)
    except Exception:
        pass

    try:
        await process_results_reporting_thread(thread)

        # Success — permanent message kept in thread
        await thread.send(
            embed=make_embed(
                title="✅ Results Processed",
                description="Your results have been successfully processed!",
                colour=discord.Colour.green()
            )
        )
        try:
            await starter_msg.add_reaction("✅")
        except Exception:
            pass
        print(f"  ✓ Results processed OK: '{thread.name}'")

    except ValueError as e:
        # Validation error — permanent message kept so the user knows what to fix
        await thread.send(
            embed=make_embed(
                title="⚠️ Validation Error",
                description=f"{retry_prefix} process your results:\n```{e}```\nPlease edit your message {retry_suffix}to fix the issue — I'll retry automatically.",
                colour=discord.Colour.yellow()
            )
        )
        try:
            await starter_msg.add_reaction("❌")
        except Exception:
            pass
        print(f"  ⚠ Validation error in '{thread.name}': {e}")

    except Exception as e:
        # Unexpected error — permanent message kept so the user knows to retry
        await thread.send(
            embed=make_embed(
                title="❌ Processing Error",
                description=f"An error occurred while processing your results:\n```{e}```\nPlease edit your message {retry_suffix}to retry — I'll pick it up automatically.",
                colour=discord.Colour.red()
            )
        )
        try:
            await starter_msg.add_reaction("❌")
        except Exception:
            pass
        print(f"  ✗ Error processing '{thread.name}': {e}")

    finally:
        # Remove the ⏳ reaction
        try:
            await starter_msg.remove_reaction("⏳", thread.guild.me)
        except Exception:
            pass

        # Delete all transient status messages (Processing..., Retrying..., etc.)
        # Success/error messages are NOT in this list and are intentionally kept.
        for msg in transient_msgs:
            try:
                await msg.delete()
            except Exception:
                pass


@bot.event
async def on_thread_create(thread: discord.Thread):
    """Detect new threads in #results-reporting and process them."""
    if not thread.parent or thread.parent.name != RESULTS_REPORTING_CHANNEL:
        return

    if thread.id in _seen_threads:
        print(f"  ↩ [on_thread_create] Duplicate ignored for '{thread.name}'")
        return
    _seen_threads.add(thread.id)

    print(f"  🧵 [on_thread_create] New results thread: '{thread.name}'")

    await thread.join()
    await asyncio.sleep(1)  # wait for Discord to register the starter message

    try:
        starter_msg = await thread.fetch_message(thread.id)
    except Exception as e:
        print(f"  ✗ Could not fetch starter message: {e}")
        return

    await run_results_reporting_pipeline(thread, starter_msg, is_retry=False)


@bot.event
async def on_message_edit(before: discord.Message, after: discord.Message):
    """Re-process a results thread if the user edits the starter message after an error."""
    if not isinstance(after.channel, discord.Thread):
        return
    if not after.channel.parent or after.channel.parent.name != RESULTS_REPORTING_CHANNEL:
        return
    # Only re-process the thread starter message (message ID == thread ID)
    if after.id != after.channel.id:
        return
    if after.author.bot:
        return
    if before.content == after.content:
        return  # URL embed preview or reaction update — not a real user edit
    # _seen_threads intentionally not checked here — user edits should always retry

    print(f"  ✏️  [on_message_edit] Results thread edited: '{after.channel.name}' — retrying...")

    await run_results_reporting_pipeline(after.channel, after, is_retry=True)


# ═══════════════════════════════════════════════════════════════
# SLASH COMMANDS
# ═══════════════════════════════════════════════════════════════

# ── /schedule ─────────────────────────────────────────────────
@tree.command(name="schedule", description="Show upcoming GTA Lorcana events")
async def schedule(interaction: discord.Interaction):
    """
    Shows upcoming events. Edit the EVENTS list below to keep it current.
    Future enhancement: fetch from events.json in your GitHub repo,
    using the same pattern as announcements.json.
    """
    EVENTS = [
        {"date": "Sat Mar 8",  "name": "Monthly Championship", "type": "🏆 Tournament", "location": "TBA"},
        {"date": "Wed Mar 12", "name": "Weekly Casual Night",   "type": "🎴 Casual",     "location": "TBA"},
        {"date": "Sat Mar 22", "name": "Booster Draft Night",   "type": "✨ Draft",      "location": "TBA"},
    ]

    lines = "\n\n".join(
        f"**{e['date']}** — {e['name']}\n{e['type']} · {e['location']}"
        for e in EVENTS
    )

    embed = make_embed(
        title="📅 Upcoming Events",
        description=lines or "No events scheduled yet — check back soon!"
    )
    embed.add_field(
        name="Full details & RSVPs",
        value="Check `#announcements` or visit the GTA Lorcana website.",
        inline=False
    )
    await interaction.response.send_message(embed=embed)


# ── /results ──────────────────────────────────────────────────
@tree.command(name="results", description="Post tournament results (organizers only)")
@app_commands.describe(
    event_name = "Tournament name (e.g. March Championship)",
    winner     = "1st place player name",
    second     = "2nd place player name",
    third      = "3rd place player name",
    notes      = "Extra notes, e.g. decklist link (optional)",
)
async def results(
    interaction: discord.Interaction,
    event_name: str,
    winner: str,
    second: str,
    third: str,
    notes: str = ""
):
    # Restrict to members with event management permission
    if not interaction.user.guild_permissions.manage_events:
        await interaction.response.send_message(
            "⚠️ Only event organizers can post results.", ephemeral=True
        )
        return

    date_str = datetime.now().strftime("%B %d, %Y")

    embed = make_embed(
        title=f"🏆 {event_name} — Results",
        description=(
            f"🥇 **1st** — {winner}\n"
            f"🥈 **2nd** — {second}\n"
            f"🥉 **3rd** — {third}\n"
            + (f"\n📝 {notes}" if notes else "")
        )
    )
    embed.set_footer(text=f"GTA Lorcana ✦ {date_str}")

    # Post to #results channel
    results_ch = get_channel(interaction.guild, RESULTS_CHANNEL)
    if results_ch:
        await results_ch.send(embed=embed)

    # Sync a summary to the website via Worker
    content = f"**{event_name} Results** — 🥇 {winner} · 🥈 {second} · 🥉 {third}"
    if notes:
        content += f" | {notes}"

    payload = {
        "id":           str(int(datetime.now().timestamp())),
        "content":      content,
        "timestamp":    datetime.now().isoformat(),
        "channel_name": "announcements",
        "author":       {"username": "GTA Lorcana", "bot": False},
        "embeds":       [],
        "icon":         "🏆",
    }
    synced = await call_worker(payload)

    status = "✅ Results posted"
    if results_ch:
        status += f" to `#{RESULTS_CHANNEL}`"
    status += " and synced to website! 🌐" if synced else " (website sync failed — check Worker logs)."

    await interaction.response.send_message(status, ephemeral=True)


# ── /decklist ─────────────────────────────────────────────────
@tree.command(name="decklist", description="Submit your Lorcana decklist to the community")
@app_commands.describe(
    deck_name   = "Your deck's name",
    ink_colours = "Ink colours used (e.g. Amber/Sapphire)",
    decklist    = "Paste your card list or a dreamborn.ink / moxfield link",
    notes       = "Strategy or description (optional)",
)
async def decklist(
    interaction: discord.Interaction,
    deck_name: str,
    ink_colours: str,
    decklist: str,
    notes: str = ""
):
    embed = make_embed(
        title=f"🎴 {deck_name}",
        description=(
            f"**Submitted by:** {interaction.user.display_name}\n"
            f"**Inks:** {ink_colours}\n\n"
            f"**Decklist:**\n```\n{decklist[:800]}\n```"
            + (f"\n**Notes:** {notes}" if notes else "")
        ),
        colour=discord.Colour.purple()
    )

    decklists_ch = get_channel(interaction.guild, DECKLISTS_CHANNEL)
    if decklists_ch:
        await decklists_ch.send(embed=embed)
        await interaction.response.send_message(
            f"✅ Decklist posted to `#{DECKLISTS_CHANNEL}`! Thanks for sharing 🎴",
            ephemeral=True
        )
    else:
        await interaction.response.send_message(
            f"⚠️ Couldn't find `#{DECKLISTS_CHANNEL}` — ask an admin to create it!",
            ephemeral=True
        )


# ── /rank ─────────────────────────────────────────────────────
@tree.command(name="rank", description="Self-assign your player role")
@app_commands.describe(role="Choose the role that best describes you")
@app_commands.choices(role=[
    app_commands.Choice(name="Casual — I play for fun",       value="Casual"),
    app_commands.Choice(name="Competitive — I play to win",   value="Competitive"),
    app_commands.Choice(name="Judge — I know the rules well", value="Judge"),
])
async def rank(interaction: discord.Interaction, role: app_commands.Choice[str]):
    guild  = interaction.guild
    member = interaction.user

    # Remove any existing self-assign roles
    for role_name in SELF_ASSIGN_ROLES:
        existing = discord.utils.get(guild.roles, name=role_name)
        if existing and existing in member.roles:
            await member.remove_roles(existing)

    # Find or create the target role
    target_role = discord.utils.get(guild.roles, name=role.value)
    if not target_role:
        role_colours = {
            "Casual":      discord.Colour.green(),
            "Competitive": discord.Colour.red(),
            "Judge":       discord.Colour.gold(),
        }
        target_role = await guild.create_role(
            name=role.value,
            colour=role_colours.get(role.value, discord.Colour.default()),
            reason="GTA Lorcana self-assign"
        )

    await member.add_roles(target_role)
    await interaction.response.send_message(
        f"✦ You've been assigned the **{role.value}** role! Welcome to your rank, Illumineer. ✨",
        ephemeral=True
    )


# ── /welcome (manual) ─────────────────────────────────────────
@tree.command(name="welcome", description="Manually welcome a member (admins only)")
@app_commands.describe(member="The member to welcome")
async def welcome(interaction: discord.Interaction, member: discord.Member):
    if not interaction.user.guild_permissions.manage_guild:
        await interaction.response.send_message("⚠️ Admins only.", ephemeral=True)
        return

    channel = get_channel(interaction.guild, WELCOME_CHANNEL)
    if not channel:
        await interaction.response.send_message(
            f"⚠️ Couldn't find `#{WELCOME_CHANNEL}` channel.", ephemeral=True
        )
        return

    embed = make_embed(
        title=f"✦ Welcome, {member.display_name}!",
        description=(
            f"Please give a warm GTA Lorcana welcome to {member.mention}! 🌟\n\n"
            f"Use `/rank` to pick your player role, and check `#announcements` for upcoming events."
        )
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    await channel.send(embed=embed)
    await interaction.response.send_message(
        f"✅ Welcomed {member.display_name} in `#{WELCOME_CHANNEL}`!", ephemeral=True
    )


# ── /recheck ──────────────────────────────────────────────────
@tree.command(name="recheck", description=f"Reprocess any unhandled threads in #{RESULTS_REPORTING_CHANNEL} (admins only)")
@app_commands.describe(after="Only recheck threads created on or after this date (YYYY-MM-DD). Leave blank to check all.")
async def recheck(interaction: discord.Interaction, after: str = ""):
    """
    Scans all active threads in the results-reporting forum channel.
    Any thread that does not already have a ✅ or ❌ reaction from the bot
    is considered missed and will be reprocessed.
    """
    if not interaction.user.guild_permissions.manage_guild:
        await interaction.response.send_message("⚠️ Admins only.", ephemeral=True)
        return

    # Parse optional date filter
    after_date = None
    if after:
        try:
            after_date = datetime.strptime(after, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            await interaction.response.send_message(
                "⚠️ Invalid date format. Use YYYY-MM-DD (e.g. `2025-01-15`).", ephemeral=True
            )
            return

    # Find the forum channel
    forum = discord.utils.get(interaction.guild.forums, name=RESULTS_REPORTING_CHANNEL)
    if not forum:
        await interaction.response.send_message(
            f"⚠️ Could not find forum channel `#{RESULTS_REPORTING_CHANNEL}`.", ephemeral=True
        )
        return

    await interaction.response.defer(ephemeral=True)

    # Collect all active (non-archived) threads
    threads = forum.threads  # already-cached active threads
    # Also fetch any active threads not yet in cache
    async for thread in forum.archived_threads(limit=None):
        if thread not in threads:
            threads = list(threads) + [thread]

    # Apply date filter if provided
    if after_date:
        threads = [t for t in threads if t.created_at and t.created_at >= after_date]

    if not threads:
        date_note = f" after {after}" if after_date else ""
        await interaction.followup.send(f"No threads found{date_note}.", ephemeral=True)
        return

    missed = []
    for thread in threads:
        try:
            starter_msg = await thread.fetch_message(thread.id)
        except Exception:
            continue

        bot_reactions = {
            r.emoji for r in starter_msg.reactions
            if r.me  # reactions added by this bot
        }
        already_handled = "✅" in bot_reactions or "❌" in bot_reactions

        if not already_handled:
            missed.append((thread, starter_msg))

    if not missed:
        await interaction.followup.send(
            embed=make_embed(
                title="✅ All caught up!",
                description=f"All {len(threads)} thread(s) in `#{RESULTS_REPORTING_CHANNEL}` have already been processed.",
                colour=discord.Colour.green()
            ),
            ephemeral=True
        )
        return

    await interaction.followup.send(
        embed=make_embed(
            title="🔄 Rechecking missed threads...",
            description=f"Found {len(missed)} unprocessed thread(s) out of {len(threads)} total. Processing now...",
            colour=discord.Colour.blurple()
        ),
        ephemeral=True
    )

    for thread, starter_msg in missed:
        print(f"  🔄 Rechecking missed thread: '{thread.name}'")
        await thread.join()
        await run_results_reporting_pipeline(thread, starter_msg, is_retry=False)

    await interaction.followup.send(
        embed=make_embed(
            title="✦ Recheck Complete",
            description=f"Finished processing {len(missed)} missed thread(s).",
            colour=discord.Colour.gold()
        ),
        ephemeral=True
    )


# ── /help ─────────────────────────────────────────────────────
@tree.command(name="help", description="Show all GTA Lorcana bot commands")
async def help_command(interaction: discord.Interaction):
    embed = make_embed(
        title="✦ GTA Lorcana Bot — Commands",
        description="Here's everything I can do:"
    )
    embed.add_field(name="/schedule",         value="Show upcoming events",                                  inline=False)
    embed.add_field(name="/results",          value="Post tournament results *(organizers only)*",            inline=False)
    embed.add_field(name="/decklist",         value="Submit your decklist to the community",                  inline=False)
    embed.add_field(name="/rank",             value="Self-assign Casual / Competitive / Judge role",          inline=False)
    embed.add_field(name="/welcome @member",  value="Manually welcome a member *(admins only)*",              inline=False)
    embed.add_field(name="🔁 Auto-sync",      value=f"Posts in `#{ANNOUNCEMENTS_CHANNEL}` appear on the website automatically", inline=False)
    embed.add_field(name="🧵 Results Threads", value=f"New threads in `#{RESULTS_REPORTING_CHANNEL}` are processed automatically. Edit to retry on error.", inline=False)
    embed.add_field(name="/recheck",          value=f"Reprocess any missed threads in `#{RESULTS_REPORTING_CHANNEL}` *(admins only)*",     inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)


# ═══════════════════════════════════════════════════════════════
# RUN
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    missing = [v for v in ["DISCORD_BOT_TOKEN", "WORKER_URL", "WORKER_SECRET"] if not os.getenv(v)]
    if missing:
        raise ValueError(f"Missing environment variables: {', '.join(missing)}")
    bot.run(DISCORD_BOT_TOKEN)