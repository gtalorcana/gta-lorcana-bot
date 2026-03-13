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
  - /recheck         — reprocess missed results threads (admins only)
  - /watch-rph-event — subscribe to DM alerts when a spot opens at a full event
  - /unwatch-rph-event — unsubscribe from a watched event
  - /list-watches    — see all currently watched events
  - /help            — list all commands
  - on_member_join   — auto-greets new members (currently disabled)
  - whos_going_daily — posts #whos_going polls at 7AM ET for stores expected to run today
  - where_to_play_weekly — refreshes #where-to-play every Sunday evening

Requirements:
  pip install discord.py aiohttp python-dotenv requests
              google-api-python-client google-auth-httplib2 google-auth-oauthlib

Environment variables (required — set as Fly.io secrets):
  DISCORD_BOT_TOKEN
  WORKER_URL
  WORKER_SECRET
  GOOGLE_CREDENTIALS_JSON
  GOOGLE_TOKEN_JSON

Environment variables (optional — override via .env for local dev):
  ANNOUNCEMENTS_CHANNEL       default: announcements
  RESULTS_REPORTING_CHANNEL   default: results-reporting
  WHERE_TO_PLAY_CHANNEL       default: where-to-play
  WHOS_GOING_CHANNEL          default: whos_going
  CURRENT_SEASON              default: S11
  RPH_RETRY_ATTEMPTS          default: 2
  RPH_RETRY_DELAY             default: 300 (seconds)
  WHOS_GOING_POST_HOUR_ET     default: 7 (7AM ET)
  WHERE_TO_PLAY_POST_HOUR_ET  default: 23 (11PM ET)
"""

import asyncio
import gc
import json
import os
import re

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands, tasks
from datetime import datetime, timezone, date, timedelta

from results import process_event_data, remove_event_data
from stores import analyse_stores, get_expected_stores_for_date, load_bot_state, save_bot_state, refresh_set_champs, set_bot_state_key, delete_bot_state_key, fetch_event_status

from constants import (
    DISCORD_BOT_TOKEN,
    WORKER_URL,
    WORKER_SECRET,
    ANNOUNCEMENTS_CHANNEL,
    RESULTS_REPORTING_CHANNEL,
    EVENTS_URL_RE,
    RPH_RETRY_DELAY,
    RPH_RETRY_ATTEMPTS,
    ADMIN_USER_ID,
    UPCOMING_EVENTS_JSON_URL,
    RESULTS_CHANNEL,
    DECKLISTS_CHANNEL,
    WELCOME_CHANNEL,
    SELF_ASSIGN_ROLES,
    WHERE_TO_PLAY_CHANNEL,
    WHOS_GOING_CHANNEL,
    WHOS_GOING_POST_HOUR_ET,
    WHERE_TO_PLAY_POST_DAY,
    WHERE_TO_PLAY_POST_HOUR_ET,
    SET_CHAMPS_START_DATE,
    SET_CHAMPS_END_DATE,
    SET_CHAMPS_SPREADSHEET_ID,
    SET_CHAMPS_EVENTS_RANGE_NAME,
)

# ── Bot setup ─────────────────────────────────────────────────
intents = discord.Intents.default()
intents.message_content = True  # read message text
intents.members = True  # on_member_join event

class GtaLorcanaBot(commands.Bot):
    async def setup_hook(self):
        if os.getenv("SYNC_COMMANDS_ONLY") == "1":
            guild_id = os.getenv("DISCORD_GUILD_ID", "0")
            guild = discord.Object(id=int(guild_id))
            print(f"  SYNC_COMMANDS_ONLY mode — guild_id={guild_id}, commands registered={len(self.tree.get_commands())}")
            # Copy to guild FIRST, then clear globals
            self.tree.copy_global_to(guild=guild)
            synced = await self.tree.sync(guild=guild)
            print(f"✓ Synced {len(synced)} command(s) to guild {guild_id}:")
            for cmd in synced:
                print(f"  /{cmd.name}")
            # Clear global commands after guild sync
            self.tree.clear_commands(guild=None)
            await self.tree.sync()
            await self.close()

bot = GtaLorcanaBot(
    command_prefix="!",
    intents=intents,
)
tree = bot.tree

# Serializes all sheet writes — prevents concurrent threads from overwriting each other
_sheet_lock = asyncio.Lock()


# ═══════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════

from zoneinfo import ZoneInfo

_TZ_ET = ZoneInfo("America/Toronto")


def _now_et():
    """Current datetime in Eastern Time (DST-aware)."""
    return datetime.now(_TZ_ET)


async def post_to_worker(payload: dict) -> bool:
    """POST a payload to the Cloudflare Worker. Returns True on success."""
    headers = {
        "Content-Type": "application/json",
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


def _grouped_by_day(entries: list) -> str:
    """Format a list of event entries grouped by day with day headers."""
    if not entries:
        return "*None yet this season*"
    groups = {}
    for e in entries:
        groups.setdefault(e['day'], []).append(e)
    lines = []
    for day, day_entries in groups.items():
        lines.append(f"__{day}__")
        for e in day_entries:
            city = f" ({e['city']})" if e.get('city') else ''
            time = f" @ {e['time']}" if e.get('time') else ''
            lines.append(f"• **{e['store_name']}**{city}{time} · {e['format']}")
    return "\n".join(lines)


def _build_where_to_play_messages(store_analysis: dict, as_of: date) -> tuple[str, str, str]:
    """
    Build three #where-to-play messages from a store analysis result.
    Returns (regular_msg, semi_regular_msg, info_msg).
    """
    date_str = as_of.strftime('%B %d, %Y').replace(' 0', ' ')

    regular_msg = "\n".join([
        f"📍 **Where to Play — GTA Lorcana** — *Updated {date_str}*",
        "",
        "✅ Regular Events — *ran every week for 2+ weeks*",
        _grouped_by_day(store_analysis['regular']),
    ])

    semi_regular_msg = "\n".join([
        "\u200b",
        "🔄 Semi-Regular Events — *ran at least twice in the last 4 weeks*",
        _grouped_by_day(store_analysis.get('semi_regular', [])),
    ])

    info_msg = "\n".join([
        "\u200b",
        "🏪 Don't see your store?",
        "Ask them to run the same event (same day, same time) at least twice in the last 4 weeks and it'll appear here automatically!",
        "*If something looks off, DM <@904550642213875723> and we'll manually fix it.*",
        "",
        "ℹ️ How this works",
        "Ratings are based on historical RPH event data and update every Sunday.",
        "*~ before a time means the start time varies slightly week to week — e.g. ~7:00 PM could mean anywhere from 7:00–7:30 PM. Arrive a few minutes early to be safe.*",
    ])

    return regular_msg, semi_regular_msg, info_msg


# ═══════════════════════════════════════════════════════════════
# EVENTS
# ═══════════════════════════════════════════════════════════════

@tasks.loop(minutes=30)
async def keepalive():
    """Periodic heartbeat to confirm the bot is alive and connected."""
    print(f"  ♥ Heartbeat — bot alive, watching #{ANNOUNCEMENTS_CHANNEL} and #{RESULTS_REPORTING_CHANNEL}")


# ── Whos-Going & Where-to-Play tasks ────────────────────────────────

# Stores the message ID of the current #where-to-play post so we can edit it
# in-place each Sunday rather than posting a new one.
_where_to_play_msg_ids: list[int | None] = [None, None, None]  # regular, semi-regular, info


@tasks.loop(minutes=1)
async def whos_going_daily():
    """
    Posts one #Whos-Going poll per Regular store expected to run today.
    Fires once daily at WHOS_GOING_POST_HOUR_ET (ET).
    Skips if no stores are expected today.
    """
    now_et = _now_et()
    if now_et.hour != WHOS_GOING_POST_HOUR_ET or now_et.minute != 0:
        return

    print(f"  🗓 whos_going_daily: checking expected stores for {now_et.date()}...")
    await _post_whos_going_polls(now_et.date())


async def _post_whos_going_polls(target_date, interaction: discord.Interaction = None):
    """
    Core who's-going poll posting logic. Posts one poll per Regular store expected on target_date.
    If interaction is provided, sends ephemeral feedback to the caller.
    """
    loop = asyncio.get_running_loop()
    try:
        store_analysis  = await loop.run_in_executor(None, analyse_stores, target_date)
        expected_stores = await loop.run_in_executor(
            None, get_expected_stores_for_date, target_date, store_analysis
        )
        gc.collect()  # TODO: remove when upgraded to 1GB RAM — analyse_stores holds a full season of RPH events
    except Exception as e:
        msg = f"Failed to fetch store analysis: {e}"
        print(f"  ✗ _post_whos_going_polls: {msg}")
        if interaction:
            await interaction.followup.send(f"❌ {msg}", ephemeral=True)
        return

    if not expected_stores:
        msg = f"No stores expected on {target_date} — no polls posted."
        print(f"  ↩ _post_whos_going_polls: {msg}")
        if interaction:
            await interaction.followup.send(f"ℹ️ {msg}", ephemeral=True)
        return

    posted = 0
    for guild in bot.guilds:
        whos_going_ch = get_channel(guild, WHOS_GOING_CHANNEL)
        if not whos_going_ch:
            print(f"  ⚠ _post_whos_going_polls: #{WHOS_GOING_CHANNEL} not found in {guild.name}")
            continue

        for store in expected_stores:
            embed = make_embed(
                title=f"📅 Who's coming today?",
                description=(
                    f"**{store['store_name']}**\n"
                    f"📆 {target_date.strftime('%A, %B %d').replace(' 0', ' ')}\n"
                    f"🕐 Typically starts: {store['time']} (Toronto time)\n"
                    f"🎮 Format: {store['format']}\n\n"
                    f"React below to let the community know if you're attending!\n"
                    f"👍 Going · 👎 Not going · 🤔 Maybe"
                ),
                colour=discord.Colour.blurple()
            )
            try:
                msg = await whos_going_ch.send(embed=embed)
                await msg.add_reaction("👍")
                await msg.add_reaction("👎")
                await msg.add_reaction("🤔")
                print(f"  ✓ Who's-going poll posted for {store['store_name']}")
                posted += 1
            except Exception as e:
                print(f"  ✗ Failed to post who's-going poll for {store['store_name']}: {e}")

    if interaction:
        await interaction.followup.send(
            f"✅ Posted {posted} who's-going poll(s) for {target_date.strftime('%A, %B %d').replace(' 0', ' ')}.",
            ephemeral=True
        )


@tasks.loop(minutes=1)
async def where_to_play_weekly():
    """
    Posts or edits the #where-to-play messages every Sunday at WHERE_TO_PLAY_POST_HOUR_ET (ET).
    Re-runs store analysis so graduations and relegations are reflected automatically.
    Sends three messages: regular events, semi-regular events, and info/footer.
    """
    global _where_to_play_msg_ids

    now_et = _now_et()
    if now_et.weekday() != WHERE_TO_PLAY_POST_DAY or now_et.hour != WHERE_TO_PLAY_POST_HOUR_ET or now_et.minute != 0:
        return

    print(f"  🗺 where_to_play_weekly: refreshing #{WHERE_TO_PLAY_CHANNEL}...")

    loop = asyncio.get_running_loop()
    try:
        store_analysis = await loop.run_in_executor(None, analyse_stores, now_et.date())
    except Exception as e:
        print(f"  ✗ where_to_play_weekly: failed to fetch store analysis: {e}")
        return

    gc.collect()  # TODO: remove when upgraded to 1GB RAM — analyse_stores holds a full season of RPH events
    messages = _build_where_to_play_messages(store_analysis, now_et.date())

    for guild in bot.guilds:
        wtp_ch = get_channel(guild, WHERE_TO_PLAY_CHANNEL)
        if not wtp_ch:
            print(f"  ⚠ where_to_play_weekly: #{WHERE_TO_PLAY_CHANNEL} not found in {guild.name}")
            continue

        try:
            new_ids = []
            for i, content in enumerate(messages):
                msg_id = _where_to_play_msg_ids[i]
                if msg_id:
                    try:
                        existing = await wtp_ch.fetch_message(msg_id)
                        await existing.edit(content=content)
                        new_ids.append(msg_id)
                        continue
                    except discord.NotFound:
                        pass
                msg = await wtp_ch.send(content)
                new_ids.append(msg.id)
            _where_to_play_msg_ids = new_ids
            # Persist message IDs so edits survive restarts
            await loop.run_in_executor(None, save_bot_state, {
                'wtp_msg_0': str(new_ids[0]) if new_ids[0] else '',
                'wtp_msg_1': str(new_ids[1]) if new_ids[1] else '',
                'wtp_msg_2': str(new_ids[2]) if new_ids[2] else '',
            })
            print(f"  ✓ #{WHERE_TO_PLAY_CHANNEL} updated ({len(messages)} messages)")
        except Exception as e:
            print(f"  ✗ Failed to update #{WHERE_TO_PLAY_CHANNEL}: {e}")


# ── Set Championships daily refresh ─────────────────────────────────────────

_SET_CHAMPS_START = date.fromisoformat(SET_CHAMPS_START_DATE) - timedelta(weeks=2)
_SET_CHAMPS_END   = date.fromisoformat(SET_CHAMPS_END_DATE)


@tasks.loop(minutes=1)
async def set_champs_daily():
    """
    Refreshes the Set Champs sheet once daily at noon ET during the set champs window.
    No-ops outside of SET_CHAMPS_START_DATE to SET_CHAMPS_END_DATE.
    """
    now_et = _now_et()
    if now_et.hour != 7 or now_et.minute != 0:
        return
    if not (_SET_CHAMPS_START <= now_et.date() <= _SET_CHAMPS_END):
        return

    print(f"  🏆 set_champs_daily: refreshing Set Champs sheet for {now_et.date()}...")
    loop = asyncio.get_running_loop()
    try:
        count = await loop.run_in_executor(None, refresh_set_champs)
        gc.collect()  # TODO: remove when upgraded to 1GB RAM — refresh_set_champs fetches a date range of RPH events
        print(f"  ✓ Set Champs sheet refreshed ({count} event(s))")
    except Exception as e:
        print(f"  ✗ set_champs_daily failed: {e}")




# ═══════════════════════════════════════════════════════════════
# RPH EVENT WATCHER
# ═══════════════════════════════════════════════════════════════

_RPH_WATCH_KEY_PREFIX = "rph_watch:"


def _watch_key(event_id: int) -> str:
    return f"{_RPH_WATCH_KEY_PREFIX}{event_id}"


def _load_watches(state: dict) -> dict[str, dict]:
    """Return all active rph_watch entries from bot state as {key: data}."""
    return {
        k: json.loads(v)
        for k, v in state.items()
        if k.startswith(_RPH_WATCH_KEY_PREFIX)
    }


@tasks.loop(minutes=15)
async def rph_watcher():
    """
    Every 15 minutes: check each watched RPH event for open spots.
    DMs all subscribers if spots are available.
    Cleans up expired watches automatically.
    """
    loop = asyncio.get_running_loop()
    try:
        state = await loop.run_in_executor(None, load_bot_state)
    except Exception as e:
        print(f"  ⚠ rph_watcher: could not load bot state: {e}")
        return

    watches = _load_watches(state)
    if not watches:
        return

    today = _now_et().date().isoformat()

    for key, watch in watches.items():
        event_id  = int(key.removeprefix(_RPH_WATCH_KEY_PREFIX))
        end_date  = watch.get('end_date', '')
        name      = watch.get('name', f'Event {event_id}')
        subs      = watch.get('subscribers', [])

        # Auto-expire past end_date
        if end_date and today > end_date:
            print(f"  🗑 rph_watcher: {name} (id={event_id}) past end_date {end_date} — removing")
            await loop.run_in_executor(None, delete_bot_state_key, key)
            continue

        if not subs:
            await loop.run_in_executor(None, delete_bot_state_key, key)
            continue

        # Fetch live event status
        status = await loop.run_in_executor(None, fetch_event_status, event_id)
        if status is None:
            print(f"  ⚠ rph_watcher: could not fetch status for {name} (id={event_id})")
            continue

        available = status['available']
        print(f"  👁 rph_watcher: {name} — {status['registered']}/{status['capacity']} "
              f"({'OPEN' if available else 'FULL'})")

        if available:
            cap_str = f"{status['registered']}/{status['capacity']}" if status['capacity'] else str(status['registered'])
            dm_msg  = (
                f"🎟️ **Spot available at {name}!**\n"
                f"📅 {status['start_date']}\n"
                f"👥 Registered: {cap_str}\n"
                f"🔗 {status['url']}"
            )
            for uid in subs:
                try:
                    user = await bot.fetch_user(int(uid))
                    await user.send(dm_msg)
                except Exception as e:
                    print(f"  ⚠ rph_watcher: could not DM user {uid}: {e}")

        gc.collect()  # TODO: remove when upgraded to 1GB RAM — belt-and-suspenders after each RPH fetch


@tree.command(name="watch-rph-event", description="Get DMs when a spot opens at a full RPH event")
@app_commands.describe(
    event_id="RPH event ID (from the event URL)",
    end_date="Stop watching after this date (YYYY-MM-DD)",
)
async def watch_rph_event(
    interaction: discord.Interaction,
    event_id: int,
    end_date: str,
):
    await interaction.response.defer(ephemeral=True)
    loop = asyncio.get_running_loop()

    # Validate end_date format
    try:
        date.fromisoformat(end_date)
    except ValueError:
        await interaction.followup.send("❌ Invalid end_date — use YYYY-MM-DD format.", ephemeral=True)
        return

    # Load current state for this watch key
    try:
        state = await loop.run_in_executor(None, load_bot_state)
    except Exception as e:
        await interaction.followup.send(f"❌ Could not load bot state: {e}", ephemeral=True)
        return

    key       = _watch_key(event_id)
    uid       = str(interaction.user.id)
    watch     = json.loads(state[key]) if key in state else {}
    subs      = watch.get('subscribers', [])

    if uid in subs:
        await interaction.followup.send(
            f"ℹ️ You're already watching **{watch.get('name', f'Event {event_id}')}**.",
            ephemeral=True
        )
        return

    # Fetch event to validate the ID and get a name if not provided
    status = await loop.run_in_executor(None, fetch_event_status, event_id)
    if status is None:
        await interaction.followup.send(
            f"❌ Could not find RPH event `{event_id}`. Double-check the ID.",
            ephemeral=True
        )
        return

    event_name = status['name']
    subs.append(uid)
    watch = {
        'name':       event_name,
        'end_date':   end_date,
        'subscribers': subs,
    }

    try:
        await loop.run_in_executor(None, set_bot_state_key, key, json.dumps(watch))
    except Exception as e:
        await interaction.followup.send(f"❌ Could not save watch: {e}", ephemeral=True)
        return

    cap_str = (f"{status['registered']}/{status['capacity']}"
               if status['capacity'] else f"{status['registered']} registered")
    avail_str = "✅ Spots are open right now!" if status['available'] else f"🔴 Currently full ({cap_str})"

    await interaction.followup.send(
        f"✅ Watching **{event_name}** (id={event_id}) until {end_date}.\n"
        f"{avail_str}\n"
        f"I'll DM you every 15 min while spots are open.",
        ephemeral=True
    )


@tree.command(name="unwatch-rph-event", description="Stop watching an RPH event for open spots")
@app_commands.describe(event_id="RPH event ID to stop watching")
async def unwatch_rph_event(interaction: discord.Interaction, event_id: int):
    await interaction.response.defer(ephemeral=True)
    loop = asyncio.get_running_loop()

    try:
        state = await loop.run_in_executor(None, load_bot_state)
    except Exception as e:
        await interaction.followup.send(f"❌ Could not load bot state: {e}", ephemeral=True)
        return

    key = _watch_key(event_id)
    if key not in state:
        await interaction.followup.send(f"ℹ️ No active watch found for event `{event_id}`.", ephemeral=True)
        return

    watch = json.loads(state[key])
    uid   = str(interaction.user.id)
    subs  = watch.get('subscribers', [])

    if uid not in subs:
        await interaction.followup.send(
            f"ℹ️ You're not subscribed to **{watch.get('name', f'Event {event_id}')}**.",
            ephemeral=True
        )
        return

    subs.remove(uid)

    if subs:
        watch['subscribers'] = subs
        await loop.run_in_executor(None, set_bot_state_key, key, json.dumps(watch))
    else:
        # Last subscriber — remove the whole key
        await loop.run_in_executor(None, delete_bot_state_key, key)

    await interaction.followup.send(
        f"✅ Stopped watching **{watch.get('name', f'Event {event_id}')}**.",
        ephemeral=True
    )


@tree.command(name="list-watches", description="Show all currently watched RPH events")
async def list_watches(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    loop = asyncio.get_running_loop()

    try:
        state = await loop.run_in_executor(None, load_bot_state)
    except Exception as e:
        await interaction.followup.send(f"❌ Could not load bot state: {e}", ephemeral=True)
        return

    watches = _load_watches(state)
    uid     = str(interaction.user.id)

    if not watches:
        await interaction.followup.send("ℹ️ No events are currently being watched.", ephemeral=True)
        return

    lines = []
    for key, watch in watches.items():
        event_id   = key.removeprefix(_RPH_WATCH_KEY_PREFIX)
        subs       = watch.get('subscribers', [])
        you        = " *(you're subscribed)*" if uid in subs else ""
        sub_str    = f"{len(subs)} subscriber" + ("s" if len(subs) != 1 else "")
        lines.append(
            f"• **{watch.get('name', f'Event {event_id}')}** (id={event_id}) "
            f"— until {watch.get('end_date', '?')} "
            f"— {sub_str}{you}"
        )

    await interaction.followup.send(
        "👁️ **Active RPH event watches:**\n" + "\n".join(lines),
        ephemeral=True
    )


@bot.event
async def on_ready():
    global _where_to_play_msg_ids
    print(f"✦ GTA Lorcana Bot online as {bot.user}")
    print(f"  Watching #{ANNOUNCEMENTS_CHANNEL} for website sync")
    print(f"  Watching #{RESULTS_REPORTING_CHANNEL} for results processing")

    # Restore persisted where-to-play message IDs so edits work after restarts
    try:
        loop = asyncio.get_running_loop()
        state = await loop.run_in_executor(None, load_bot_state)
        ids = [
            int(state['wtp_msg_0']) if 'wtp_msg_0' in state else None,
            int(state['wtp_msg_1']) if 'wtp_msg_1' in state else None,
            int(state['wtp_msg_2']) if 'wtp_msg_2' in state else None,
        ]
        _where_to_play_msg_ids = ids
        print(f"  ✓ Restored where-to-play message IDs: {ids}")
    except Exception as e:
        print(f"  ⚠ Could not restore where-to-play message IDs: {e}")

    if not keepalive.is_running():
        keepalive.start()
        print(f"  ♻ Keepalive task started")
    if not whos_going_daily.is_running():
        whos_going_daily.start()
        print(f"  ♻ Whos-going daily task started (fires at {WHOS_GOING_POST_HOUR_ET}AM ET)")
    if not where_to_play_weekly.is_running():
        where_to_play_weekly.start()
        print(f"  ♻ Where-to-play weekly task started (fires Sundays at {WHERE_TO_PLAY_POST_HOUR_ET}:00 ET)")
    if not set_champs_daily.is_running():
        set_champs_daily.start()
        print(f"  ♻ Set Champs daily task started (fires 7AM ET, {SET_CHAMPS_START_DATE} → {SET_CHAMPS_END_DATE})")
    if not rph_watcher.is_running():
        rph_watcher.start()
        print(f"  ♻ RPH event watcher started (polls every 15 min)")

    # Auto-recheck any unprocessed results threads from the last 3 days.
    # Catches threads that were mid-flight when the bot last crashed or restarted.
    # startup=True enables crash-loop prevention — see _find_and_reprocess_missed_threads.
    after_date = datetime.now(timezone.utc) - timedelta(days=3)
    print(f"  🔄 Startup recheck: scanning threads since {after_date.date()}...")
    for guild in bot.guilds:
        try:
            missed, total = await _find_and_reprocess_missed_threads(guild, after_date, startup=True)
            if missed:
                print(f"  ✓ Startup recheck: reprocessed {missed} missed thread(s) out of {total} scanned")
            else:
                print(f"  ✓ Startup recheck: all {total} thread(s) already processed")
        except Exception as e:
            print(f"  ⚠ Startup recheck failed for {guild.name}: {e}")


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
        "id": str(message.id),
        "content": message.content,
        "timestamp": message.created_at.isoformat(),
        "channel_name": message.channel.name,
        "author": {
            "username": message.author.display_name,
            "bot": False,
        },
        "embeds": [
            {"title": e.title or "", "description": e.description or ""}
            for e in message.embeds
        ],
    }
    await post_to_worker(payload)
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


async def _run_process_event_data(thread: discord.Thread, rph_url: str) -> None:
    """
    Acquire the sheet lock and run process_event_data in a thread executor.
    Raises on any error — caller is responsible for handling.
    """
    async with _sheet_lock:
        if _sheet_lock._waiters:
            waiter_count = len(_sheet_lock._waiters)
            print(f"  ⏳ Sheet lock acquired for '{thread.name}' ({waiter_count} thread(s) were waiting)")
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, process_event_data, rph_url, thread.id)


async def process_results_reporting_thread(thread: discord.Thread) -> None:
    """
    Validate the thread starter message URL and run the results processing pipeline.
    Raises ValueError on bad URL, RuntimeError on API/sheet failure.
    """
    starter = await thread.fetch_message(thread.id)
    rph_url = starter.content.strip()

    print(f"  → Validating results thread: '{thread.name}'")

    if not re.fullmatch(EVENTS_URL_RE, rph_url):
        raise ValueError(
            f"Thread content does not match expected URL format.\n"
            f"Expected: {EVENTS_URL_RE}\n"
            f"Got: {rph_url[:100]}"
        )

    print(f"  → URL validated: {rph_url}")
    await _run_process_event_data(thread, rph_url)


async def run_results_reporting_pipeline(
        thread: discord.Thread,
        starter_msg: discord.Message,
        is_retry: bool = False,
        auto_retry: bool = False,
):
    """
    Shared processing logic for on_thread_create, on_message_edit, and auto-retries.

    - is_retry:   True when triggered by a user edit (changes wording slightly)
    - auto_retry: True when triggered by the bot's internal retry loop (suppresses
                  the initial status message since one already exists in the thread)

    Returns True if processing completed successfully, False otherwise.
    Used by the startup recheck to decide whether to clear the crash-loop guard.
    """
    # Transient status messages sent during this run — deleted in finally.
    # Success/error messages are NOT added here and are intentionally kept.
    transient_msgs: list[discord.Message] = []
    success = False

    # Clear any previous result reactions, then add the running indicator.
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

    if not auto_retry:
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

        # ── Success ───────────────────────────────────────────
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
        success = True

    except ValueError as e:
        # ── Validation error — user needs to fix their URL ────
        await thread.send(
            embed=make_embed(
                title="⚠️ Validation Error",
                description=(
                    f"{'Still could not' if is_retry else 'Could not'} process your results:\n"
                    f"```{e}```\n"
                    f"Please edit your message {'again ' if is_retry else ''}to fix the issue — I'll retry automatically."
                ),
                colour=discord.Colour.yellow()
            )
        )
        try:
            await starter_msg.add_reaction("❌")
        except Exception:
            pass
        print(f"  ⚠ Validation error in '{thread.name}': {e}")

    except Exception as e:
        # ── API / system error — schedule auto-retries ────────
        print(f"  ✗ Error processing '{thread.name}': {e}")
        await _schedule_auto_retry(thread, starter_msg, error=e)

    finally:
        try:
            await starter_msg.remove_reaction("⏳", thread.guild.me)
        except Exception:
            pass
        for msg in transient_msgs:
            try:
                await msg.delete()
            except Exception:
                pass

    return success


async def _schedule_auto_retry(
        thread: discord.Thread,
        starter_msg: discord.Message,
        error: Exception,
        attempt: int = 1,
):
    """
    Automatically retry process_event_data after a delay when RPH is flaky.
    Posts a countdown message, waits RPH_RETRY_DELAY seconds, then retries.
    Up to RPH_RETRY_ATTEMPTS total retries. If all fail, pings the admin.
    """
    if attempt > RPH_RETRY_ATTEMPTS:
        print(f"  ✗ All auto-retries failed for '{thread.name}' — pinging admin")
        await thread.send(
            embed=make_embed(
                title="❌ Processing Failed",
                description=(
                    f"All {RPH_RETRY_ATTEMPTS} automatic retries failed.\n"
                    f"Last error:\n```{error}```\n"
                    f"<@{ADMIN_USER_ID}> Manual intervention required."
                ),
                colour=discord.Colour.red()
            )
        )
        try:
            await starter_msg.add_reaction("❌")
        except Exception:
            pass
        return

    delay_minutes = RPH_RETRY_DELAY // 60
    print(f"  ⏳ Scheduling auto-retry {attempt}/{RPH_RETRY_ATTEMPTS} for '{thread.name}' in {delay_minutes} min...")

    try:
        await thread.send(
            embed=make_embed(
                title="⏳ Processing Delayed",
                description=(
                    f"An error occurred while processing your results:\n```{error}```\n"
                    f"I'll retry automatically in {delay_minutes} minutes. "
                    f"*(Attempt {attempt}/{RPH_RETRY_ATTEMPTS})*"
                ),
                colour=discord.Colour.orange()
            )
        )
    except Exception:
        pass

    await asyncio.sleep(RPH_RETRY_DELAY)

    print(f"  🔄 Auto-retry {attempt}/{RPH_RETRY_ATTEMPTS} for '{thread.name}'...")

    try:
        await _run_process_event_data(thread, starter_msg.content.strip())

        await thread.send(
            embed=make_embed(
                title="✅ Results Processed",
                description=f"Results successfully processed on retry {attempt}/{RPH_RETRY_ATTEMPTS}!",
                colour=discord.Colour.green()
            )
        )
        try:
            await starter_msg.add_reaction("✅")
            await starter_msg.remove_reaction("❌", thread.guild.me)
        except Exception:
            pass
        print(f"  ✓ Auto-retry {attempt} succeeded for '{thread.name}'")

    except Exception as retry_error:
        print(f"  ✗ Auto-retry {attempt} failed for '{thread.name}': {retry_error}")
        await _schedule_auto_retry(thread, starter_msg, error=retry_error, attempt=attempt + 1)


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
    """Re-process a results thread if the user edits the starter message after a validation error."""
    if not isinstance(after.channel, discord.Thread):
        return
    if not after.channel.parent or after.channel.parent.name != RESULTS_REPORTING_CHANNEL:
        return
    if after.id != after.channel.id:
        return
    if after.author.bot:
        return
    if before.content == after.content:
        return  # URL embed preview or reaction update — not a real user edit

    print(f"  ✏️  [on_message_edit] Results thread edited: '{after.channel.name}' — retrying...")

    await run_results_reporting_pipeline(after.channel, after, is_retry=True)


@bot.event
async def on_message_delete(message: discord.Message):
    """Sync announcement deletion to the website."""
    if message.author.bot:
        return
    if message.channel.name != ANNOUNCEMENTS_CHANNEL:
        return

    print(f"  🗑 Announcement deleted by {message.author.display_name}: {message.content[:60]}...")

    payload = {
        "id": str(message.id),
        "action": "delete",
        "channel_name": message.channel.name,
    }
    await post_to_worker(payload)


@bot.event
async def on_thread_delete(thread: discord.Thread):
    """Remove event data from the sheet when a results thread is deleted."""
    if not thread.parent or thread.parent.name != RESULTS_REPORTING_CHANNEL:
        return

    print(f"  🗑 Results thread deleted: '{thread.name}'")

    loop = asyncio.get_running_loop()
    try:
        await loop.run_in_executor(None, remove_event_data, thread.id)
        print(f"  ✓ Event data removed for thread '{thread.name}'")
    except ValueError as e:
        print(f"  ↩ No event data to remove for thread '{thread.name}': {e}")
    except Exception as e:
        print(f"  ✗ Failed to remove event data for thread '{thread.name}': {e}")


# ═══════════════════════════════════════════════════════════════
# SLASH COMMANDS
# ═══════════════════════════════════════════════════════════════

# ── /schedule ─────────────────────────────────────────────────
# Events are read from data/upcoming_events.json in the website repo.
# To add or update events, edit that file directly in GitHub.
# Future enhancement: /addevent bot command to write to upcoming_events.json via the Worker.

@tree.command(name="schedule", description="Show upcoming GTA Lorcana events")
async def schedule(interaction: discord.Interaction):
    await interaction.response.defer()

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(UPCOMING_EVENTS_JSON_URL) as resp:
                if resp.status != 200:
                    await interaction.followup.send(
                        embed=make_embed(
                            title="📅 Upcoming Events",
                            description="Could not load events right now — check `#announcements` for the latest.",
                            colour=discord.Colour.red()
                        )
                    )
                    return
                events = await resp.json(content_type=None)
    except Exception as e:
        print(f"  ✗ Failed to fetch upcoming_events.json: {e}")
        await interaction.followup.send(
            embed=make_embed(
                title="📅 Upcoming Events",
                description="Could not load events right now — check `#announcements` for the latest.",
                colour=discord.Colour.red()
            )
        )
        return

    today = datetime.now(timezone.utc).date()
    upcoming = [
        e for e in events
        if e.get("date") and datetime.strptime(e["date"], "%Y-%m-%d").date() >= today
    ]
    upcoming.sort(key=lambda e: e["date"])

    embed = make_embed(title="📅 Upcoming Events", description="")

    if not upcoming:
        embed.description = "No upcoming events — check back soon!"
    else:
        type_icons = {
            "Tournament": "🏆",
            "Casual": "🎴",
            "Draft": "✨",
        }
        for e in upcoming:
            icon = type_icons.get(e.get("type", ""), "📅")
            date = datetime.strptime(e["date"], "%Y-%m-%d").strftime("%a %b %d").replace(" 0", " ")
            name = e.get("name", "Unnamed Event")
            location = e.get("location", "TBA")
            url = e.get("url", "")

            value = f"{icon} {e.get('type', '')} · {location}"
            if url:
                value += f"\n[RSVP here]({url})"

            embed.add_field(name=f"**{date}** — {name}", value=value, inline=False)

    embed.add_field(
        name="Full details",
        value="Check `#announcements` or visit the GTA Lorcana website.",
        inline=False
    )
    await interaction.followup.send(embed=embed)


# ── /results ──────────────────────────────────────────────────
@tree.command(name="results", description="Post tournament results (organizers only)")
@app_commands.describe(
    event_name="Tournament name (e.g. March Championship)",
    winner="1st place player name",
    second="2nd place player name",
    third="3rd place player name",
    notes="Extra notes, e.g. decklist link (optional)",
)
async def results(
        interaction: discord.Interaction,
        event_name: str,
        winner: str,
        second: str,
        third: str,
        notes: str = ""
):
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

    results_ch = get_channel(interaction.guild, RESULTS_CHANNEL)
    if results_ch:
        await results_ch.send(embed=embed)

    content = f"**{event_name} Results** — 🥇 {winner} · 🥈 {second} · 🥉 {third}"
    if notes:
        content += f" | {notes}"

    payload = {
        "id": str(int(datetime.now().timestamp())),
        "content": content,
        "timestamp": datetime.now().isoformat(),
        "channel_name": "announcements",
        "author": {"username": "GTA Lorcana", "bot": False},
        "embeds": [],
        "icon": "🏆",
    }
    synced = await post_to_worker(payload)

    status = "✅ Results posted"
    if results_ch:
        status += f" to `#{RESULTS_CHANNEL}`"
    status += " and synced to website! 🌐" if synced else " (website sync failed — check Worker logs)."

    await interaction.response.send_message(status, ephemeral=True)


# ── /decklist ─────────────────────────────────────────────────
@tree.command(name="decklist", description="Submit your Lorcana decklist to the community")
@app_commands.describe(
    deck_name="Your deck's name",
    ink_colours="Ink colours used (e.g. Amber/Sapphire)",
    decklist="Paste your card list or a dreamborn.ink / moxfield link",
    notes="Strategy or description (optional)",
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
    app_commands.Choice(name="Casual — I play for fun", value="Casual"),
    app_commands.Choice(name="Competitive — I play to win", value="Competitive"),
    app_commands.Choice(name="Judge — I know the rules well", value="Judge"),
])
async def rank(interaction: discord.Interaction, role: app_commands.Choice[str]):
    guild = interaction.guild
    member = interaction.user

    for role_name in SELF_ASSIGN_ROLES:
        existing = discord.utils.get(guild.roles, name=role_name)
        if existing and existing in member.roles:
            await member.remove_roles(existing)

    target_role = discord.utils.get(guild.roles, name=role.value)
    if not target_role:
        role_colours = {
            "Casual": discord.Colour.green(),
            "Competitive": discord.Colour.red(),
            "Judge": discord.Colour.gold(),
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

async def _find_and_reprocess_missed_threads(
        guild: discord.Guild,
        after_date: datetime = None,
        startup: bool = False,
) -> tuple[int, int]:
    """
    Scan all threads in #results-reporting and reprocess any without a ✅ reaction.
    Returns (found, total) — number of missed threads and total threads scanned.

    startup=True enables crash-loop prevention:
      - Threads already attempted this boot (tracked in Bot State as
        'recheck:<thread_id>') are skipped, the bot adds ❌ and pings the admin
        instead of retrying indefinitely.
      - On success, the Bot State entry is cleared.
      - On failure, the entry is left so the next restart also skips it.

    # TODO: When white-labelling, replace Bot State sheet tracking with a proper
    # database (per-guild, per-thread retry counters). The sheet works for a
    # single server but won't handle concurrent multi-server writes safely.
    """
    forum = discord.utils.get(guild.forums, name=RESULTS_REPORTING_CHANNEL)
    if not forum:
        return 0, 0

    threads = list(forum.threads)
    async for thread in forum.archived_threads(limit=None):
        if thread not in threads:
            threads.append(thread)

    if after_date:
        threads = [t for t in threads if t.created_at and t.created_at >= after_date]

    # Load startup-recheck state once up front
    loop = asyncio.get_running_loop()
    attempted_keys: set[str] = set()
    if startup:
        state = await loop.run_in_executor(None, load_bot_state)
        attempted_keys = {k for k in state if k.startswith('recheck:')}

    missed = []
    for thread in threads:
        try:
            starter_msg = await thread.fetch_message(thread.id)
        except Exception:
            continue
        bot_reactions = {r.emoji for r in starter_msg.reactions if r.me}
        if "✅" not in bot_reactions:
            missed.append((thread, starter_msg))

    for thread, starter_msg in missed:
        state_key = f'recheck:{thread.id}'

        if startup and state_key in attempted_keys:
            # Already tried this thread on a previous boot and it crashed us.
            # Skip it, add ❌, ping the admin — don't retry.
            print(f"  ⛔ Startup recheck: skipping '{thread.name}' — previously caused a crash, pinging admin")
            try:
                await starter_msg.add_reaction("❌")
            except Exception:
                pass
            try:
                await thread.send(
                    embed=make_embed(
                        title="❌ Processing Failed",
                        description=(
                            f"This thread failed to process on a previous bot restart and was skipped "
                            f"to prevent a crash loop.\n"
                            f"<@{ADMIN_USER_ID}> Manual intervention required."
                        ),
                        colour=discord.Colour.red()
                    )
                )
            except Exception:
                pass
            continue

        if startup:
            # Mark as attempted before trying — if we OOM mid-process the key
            # will already be set when the bot restarts, preventing a loop.
            await loop.run_in_executor(None, set_bot_state_key, state_key, '1')

        print(f"  🔄 {'Startup recheck' if startup else 'Rechecking'} missed thread: '{thread.name}'")
        await thread.join()
        success = await run_results_reporting_pipeline(thread, starter_msg, is_retry=False)

        if startup and success:
            # Completed cleanly — remove the marker so it doesn't linger
            await loop.run_in_executor(None, delete_bot_state_key, state_key)

    return len(missed), len(threads)


@tree.command(name="recheck",
              description=f"Reprocess any unhandled threads in #{RESULTS_REPORTING_CHANNEL} (admins only)")
@app_commands.describe(
    after="Only recheck threads created on or after this date (YYYY-MM-DD). Leave blank to check all.")
async def recheck(interaction: discord.Interaction, after: str = ""):
    """
    Scans all threads in the results-reporting forum channel.
    Any thread without a ✅ or ❌ reaction from the bot is reprocessed.
    """
    await interaction.response.defer(ephemeral=True)

    if not interaction.user.guild_permissions.manage_guild:
        await interaction.followup.send("⚠️ Admins only.", ephemeral=True)
        return

    after_date = None
    if after:
        try:
            after_date = datetime.strptime(after, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            await interaction.followup.send(
                "⚠️ Invalid date format. Use YYYY-MM-DD (e.g. `2025-01-15`).", ephemeral=True
            )
            return

    forum = discord.utils.get(interaction.guild.forums, name=RESULTS_REPORTING_CHANNEL)
    if not forum:
        await interaction.followup.send(
            f"⚠️ Could not find forum channel `#{RESULTS_REPORTING_CHANNEL}`.", ephemeral=True
        )
        return

    await interaction.followup.send(
        embed=make_embed(
            title="🔄 Rechecking...",
            description="Scanning for unprocessed threads...",
            colour=discord.Colour.blurple()
        ),
        ephemeral=True
    )

    missed, total = await _find_and_reprocess_missed_threads(interaction.guild, after_date)

    if missed == 0:
        await interaction.followup.send(
            embed=make_embed(
                title="✅ All caught up!",
                description=f"All {total} thread(s) in `#{RESULTS_REPORTING_CHANNEL}` have already been processed.",
                colour=discord.Colour.green()
            ),
            ephemeral=True
        )
    else:
        await interaction.followup.send(
            embed=make_embed(
                title="✦ Recheck Complete",
                description=f"Finished processing {missed} missed thread(s) out of {total} total.",
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
    embed.add_field(name="/schedule", value="Show upcoming events", inline=False)
    embed.add_field(name="/results", value="Post tournament results *(organizers only)*", inline=False)
    embed.add_field(name="/decklist", value="Submit your decklist to the community", inline=False)
    embed.add_field(name="/rank", value="Self-assign Casual / Competitive / Judge role", inline=False)
    embed.add_field(name="/welcome @member", value="Manually welcome a member *(admins only)*", inline=False)
    embed.add_field(name="🔁 Auto-sync",
                    value=f"Posts in `#{ANNOUNCEMENTS_CHANNEL}` appear on the website automatically", inline=False)
    embed.add_field(name="🧵 Results Threads",
                    value=f"New threads in `#{RESULTS_REPORTING_CHANNEL}` are processed automatically. Edit to retry on bad URL.",
                    inline=False)
    embed.add_field(name="/recheck",
                    value=f"Reprocess any missed threads in `#{RESULTS_REPORTING_CHANNEL}` *(admins only)*",
                    inline=False)
    embed.add_field(name="/wheretoplay", value="Manually push the Where to Play post *(admins only)*", inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)


# ═══════════════════════════════════════════════════════════════
# WHERE TO PLAY — MANUAL TRIGGER
# ═══════════════════════════════════════════════════════════════

@tree.command(name="wheretoplay", description="Manually push the Where to Play post (admins only)")
async def wheretoplay_command(interaction: discord.Interaction):
    if interaction.user.id != ADMIN_USER_ID:
        await interaction.response.send_message("❌ Admins only.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)

    try:
        loop = asyncio.get_running_loop()
        store_analysis = await loop.run_in_executor(None, analyse_stores, date.today())
        gc.collect()  # TODO: remove when upgraded to 1GB RAM — analyse_stores holds a full season of RPH events

        channel = get_channel(interaction.guild, WHERE_TO_PLAY_CHANNEL)
        if not channel:
            await interaction.followup.send(f"⚠️ #{WHERE_TO_PLAY_CHANNEL} channel not found.", ephemeral=True)
            return

        messages = _build_where_to_play_messages(store_analysis, date.today())

        global _where_to_play_msg_ids
        new_ids = []
        for i, content in enumerate(messages):
            msg_id = _where_to_play_msg_ids[i]
            if msg_id:
                try:
                    existing = await channel.fetch_message(msg_id)
                    await existing.edit(content=content)
                    new_ids.append(msg_id)
                    continue
                except discord.NotFound:
                    pass
            msg = await channel.send(content)
            new_ids.append(msg.id)
        _where_to_play_msg_ids = new_ids
        await loop.run_in_executor(None, save_bot_state, {
            'wtp_msg_0': str(new_ids[0]) if new_ids[0] else '',
            'wtp_msg_1': str(new_ids[1]) if new_ids[1] else '',
            'wtp_msg_2': str(new_ids[2]) if new_ids[2] else '',
        })
        await interaction.followup.send(f"✅ #{WHERE_TO_PLAY_CHANNEL} updated.", ephemeral=True)

    except Exception as e:
        await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)


@tree.command(name="testwhosgoing", description="Manually trigger today's #whos_going polls (admins only)")
@app_commands.describe(date="Optional date to test (YYYY-MM-DD), defaults to today")
async def testwhosgoing_command(interaction: discord.Interaction, date: str = None):
    if interaction.user.id != ADMIN_USER_ID:
        await interaction.response.send_message("❌ Admins only.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)

    try:
        if date:
            from datetime import date as date_type
            target_date = date_type.fromisoformat(date)
        else:
            target_date = _now_et().date()
        await _post_whos_going_polls(target_date, interaction=interaction)
    except ValueError:
        await interaction.followup.send("❌ Invalid date format — use YYYY-MM-DD.", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)


if __name__ == "__main__":
    missing = [v for v in ["DISCORD_BOT_TOKEN", "WORKER_URL", "WORKER_SECRET"] if not os.getenv(v)]
    if missing:
        raise ValueError(f"Missing environment variables: {', '.join(missing)}")
    bot.run(DISCORD_BOT_TOKEN)
