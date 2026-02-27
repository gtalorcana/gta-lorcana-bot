"""
GTA Lorcana — Discord Bot
=========================
Features:
  - Auto-syncs #announcements to the website via Cloudflare Worker
  - /schedule        — shows upcoming events
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
  WELCOME_CHANNEL            (default: general)
"""

import os
import re

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands, tasks
from dotenv import load_dotenv

load_dotenv()

# ── Config ────────────────────────────────────────────────────
DISCORD_BOT_TOKEN     = os.getenv("DISCORD_BOT_TOKEN")
WORKER_URL            = os.getenv("WORKER_URL")
WORKER_SECRET         = os.getenv("WORKER_SECRET")
ANNOUNCEMENTS_CHANNEL = os.getenv("ANNOUNCEMENTS_CHANNEL", "announcements")
RESULTS_REPORTING_CHANNEL = os.getenv("RESULTS_REPORTING_CHANNEL", "results-reporting")
WELCOME_CHANNEL           = os.getenv("WELCOME_CHANNEL", "general")
EVENTS_URL_RE         = os.getenv("EVENTS_URL_RE", "https://tcg.ravensburgerplay.com/events/[0-9]*")

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

async def process_results_thread(thread: discord.Thread) -> bool:
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

        # ── YOUR FUNCTION GOES HERE ──────────────────────────
        # result = await your_results_function(text)
        # ────────────────────────────────────────────────────

        # Placeholder — replace with your actual function call
        if not re.fullmatch(EVENTS_URL_RE, text.strip()):
            raise ValueError(f"Thread content does not match expected URL format.\nExpected: {EVENTS_URL_RE}\nGot: {text.strip()[:100]}")

        # Simulate success for now
        return True

    except ValueError as e:
        raise  # Re-raise validation errors so caller can handle them
    except Exception as e:
        raise  # Re-raise HTTP/other errors so caller can handle them


@bot.event
async def on_thread_create(thread: discord.Thread):
    """Detect new threads in #results-reporting and process them."""
    # Only handle threads in the results-reporting channel
    if not thread.parent or thread.parent.name != RESULTS_REPORTING_CHANNEL:
        return

    print(f"  🧵 New results thread: '{thread.name}' in #{RESULTS_REPORTING_CHANNEL}")

    # Join the thread so the bot can send messages in it
    await thread.join()

    # Give Discord a moment to deliver the starter message
    import asyncio
    await asyncio.sleep(1)

    try:
        success = await process_results_thread(thread)
        try:
            starter_msg = await thread.fetch_message(thread.id)
            await starter_msg.add_reaction("✅")
        except Exception:
            pass  # Reaction is nice-to-have, not critical
        await thread.send(
            embed=make_embed(
                title="✅ Results Processed",
                description="Your results have been successfully processed!",
                colour=discord.Colour.green()
            )
        )
        print(f"  ✓ Results thread processed OK: '{thread.name}'")

    except ValueError as e:
        await thread.send(
            embed=make_embed(
                title="⚠️ Validation Error",
                description=(
                    f"Could not process your results:\n```{e}```\nPlease edit your message to fix the issue — I'll retry automatically."
                ),
                colour=discord.Colour.yellow()
            )
        )
        print(f"  ⚠ Validation error in '{thread.name}': {e}")

    except Exception as e:
        await thread.send(
            embed=make_embed(
                title="❌ Processing Error",
                description=(
                    f"An error occurred while processing your results:\n```{e}```\nPlease edit your message to retry — I'll pick it up automatically."
                ),
                colour=discord.Colour.red()
            )
        )
        print(f"  ✗ Error processing '{thread.name}': {e}")


@bot.event
async def on_message_edit(before: discord.Message, after: discord.Message):
    """Re-process a results thread if the user edits the starter message after an error."""
    # Only care about threads in results-reporting
    if not isinstance(after.channel, discord.Thread):
        return
    if not after.channel.parent or after.channel.parent.name != RESULTS_REPORTING_CHANNEL:
        return
    # Only re-process if this is the thread starter message (message ID == thread ID)
    if after.id != after.channel.id:
        return
    # Ignore bot edits
    if after.author.bot:
        return

    print(f"  ✏️  Results thread edited: '{after.channel.name}' — retrying...")

    await after.channel.send(
        embed=make_embed(
            title="🔄 Retrying...",
            description="Detected an edit — reprocessing your results now.",
            colour=discord.Colour.blurple()
        )
    )

    try:
        success = await process_results_thread(after.channel)
        await after.add_reaction("✅")
        await after.channel.send(
            embed=make_embed(
                title="✅ Results Processed",
                description="Your results have been successfully processed!",
                colour=discord.Colour.green()
            )
        )
        print(f"  ✓ Retry succeeded: '{after.channel.name}'")

    except ValueError as e:
        await after.channel.send(
            embed=make_embed(
                title="⚠️ Validation Error",
                description=(
                    f"Still could not process your results:\n```{e}```\nPlease edit your message again to retry."
                ),
                colour=discord.Colour.yellow()
            )
        )
        print(f"  ⚠ Retry validation error: {e}")

    except Exception as e:
        await after.channel.send(
            embed=make_embed(
                title="❌ Processing Error",
                description=(
                    f"Error on retry:\n```{e}```\nPlease edit your message again to retry."
                ),
                colour=discord.Colour.red()
            )
        )
        print(f"  ✗ Retry error: {e}")


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


# ── /help ─────────────────────────────────────────────────────
@tree.command(name="help", description="Show all GTA Lorcana bot commands")
async def help_command(interaction: discord.Interaction):
    embed = make_embed(
        title="✦ GTA Lorcana Bot — Commands",
        description="Here's everything I can do:"
    )
    embed.add_field(name="/schedule",         value="Show upcoming events",                                  inline=False)
    embed.add_field(name="/rank",             value="Self-assign Casual / Competitive / Judge role",          inline=False)
    embed.add_field(name="/welcome @member",  value="Manually welcome a member *(admins only)*",              inline=False)
    embed.add_field(name="🔁 Auto-sync",      value=f"Posts in `#{ANNOUNCEMENTS_CHANNEL}` appear on the website automatically", inline=False)
    embed.add_field(name="🧵 Results Threads", value=f"New threads in `#{RESULTS_REPORTING_CHANNEL}` are processed automatically. Edit to retry on error.", inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)


# ═══════════════════════════════════════════════════════════════
# RUN
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    missing = [v for v in ["DISCORD_BOT_TOKEN", "WORKER_URL", "WORKER_SECRET"] if not os.getenv(v)]
    if missing:
        raise ValueError(f"Missing environment variables: {', '.join(missing)}")
    bot.run(DISCORD_BOT_TOKEN)