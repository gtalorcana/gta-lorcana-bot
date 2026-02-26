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

Environment variables (.env or Railway):
  DISCORD_BOT_TOKEN
  WORKER_URL
  WORKER_SECRET
  ANNOUNCEMENTS_CHANNEL   (default: announcements)
  RESULTS_CHANNEL         (default: results)
  DECKLISTS_CHANNEL       (default: decklists)
  WELCOME_CHANNEL         (default: general)
"""

import os
import aiohttp
import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv
from datetime import datetime

load_dotenv()

# ── Config ────────────────────────────────────────────────────
DISCORD_BOT_TOKEN     = os.getenv("DISCORD_BOT_TOKEN")
WORKER_URL            = os.getenv("WORKER_URL")
WORKER_SECRET         = os.getenv("WORKER_SECRET")
ANNOUNCEMENTS_CHANNEL = os.getenv("ANNOUNCEMENTS_CHANNEL", "announcements")
RESULTS_CHANNEL       = os.getenv("RESULTS_CHANNEL",       "results")
DECKLISTS_CHANNEL     = os.getenv("DECKLISTS_CHANNEL",     "decklists")
WELCOME_CHANNEL       = os.getenv("WELCOME_CHANNEL",       "general")

# Roles members can self-assign via /rank
SELF_ASSIGN_ROLES = ["Casual", "Competitive", "Judge"]


# ── Bot setup ─────────────────────────────────────────────────
intents = discord.Intents.default()
intents.message_content = True  # read message text
intents.members = True          # on_member_join event

bot = commands.Bot(command_prefix="!", intents=intents)
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

@bot.event
async def on_ready():
    await tree.sync()
    print(f"✦ GTA Lorcana Bot online as {bot.user}")
    print(f"  Slash commands synced")
    print(f"  Watching #{ANNOUNCEMENTS_CHANNEL} for website sync")


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


@bot.event
async def on_member_join(member: discord.Member):
    """Auto-greet new members when they join the server."""
    channel = get_channel(member.guild, WELCOME_CHANNEL)
    if not channel:
        return

    embed = make_embed(
        title=f"✦ Welcome, {member.display_name}!",
        description=(
            f"A new Illumineer has arrived in the GTA Lorcana community! 🌟\n\n"
            f"**Getting started:**\n"
            f"• Introduce yourself and tell us your favourite ink!\n"
            f"• Use `/rank` to pick your player role\n"
            f"• Check `#announcements` for upcoming events\n"
            f"• Visit our website for the full schedule\n\n"
            f"*The Great Illuminary shines brighter with you here.* ✨"
        )
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    await channel.send(embed=embed)


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
    await interaction.response.send_message(embed=embed, ephemeral=True)


# ═══════════════════════════════════════════════════════════════
# RUN
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    missing = [v for v in ["DISCORD_BOT_TOKEN", "WORKER_URL", "WORKER_SECRET"] if not os.getenv(v)]
    if missing:
        raise ValueError(f"Missing environment variables: {', '.join(missing)}")
    bot.run(DISCORD_BOT_TOKEN)
