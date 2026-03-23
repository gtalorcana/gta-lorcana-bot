"""
GTA Lorcana — Discord Bot
=========================
Features:
  - /schedule        — shows upcoming events
  - /watch-rph-event — subscribe to DM alerts when a spot opens at a full event
  - /unwatch-rph-event — unsubscribe from a watched event
  - /list-watches    — see all currently watched events
  - /help            — list all commands
  - /recheck         — reprocess missed results threads (admins only)
  - /link            — manually link a Discord member to a Playhub ID (admins only)
  - /sync-roles      — apply Uncommon/Rare upgrades from standings (admins only)
  - /invitational-roles     — assign Legendary/Super Rare (admins only)
  - /wheretoplay     — manually push the where-to-play post (admins only)
  - on_member_join   — auto-assigns Common rarity role to new members
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
  CURRENT_SEASON              default: S11
  RPH_RETRY_ATTEMPTS          default: 2
  RPH_RETRY_DELAY             default: 300 (seconds)
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

from clients import gs as _gs, rph_api as _rph_api
from results import process_event_data, remove_event_data
from stores import analyse_stores, get_expected_stores_for_date, load_bot_state, save_bot_state, refresh_set_champs, set_bot_state_key, delete_bot_state_key, fetch_event_status

from constants import (
    DISCORD_BOT_TOKEN,
    WORKER_URL,
    WORKER_SECRET,
    CHANNELS,
    MOD_CHANNEL_ID,
    EVENTS_URL_RE,
    RPH_RETRY_DELAY,
    RPH_RETRY_ATTEMPTS,
    ADMIN_USER_IDS,
    UPCOMING_EVENTS_JSON_URL,
    WHERE_TO_PLAY_POST_DAY,
    WHERE_TO_PLAY_POST_HOUR_ET,
    SET_CHAMPS_SPREADSHEET_ID,
    COMMON_ROLE_ID,
    UNCOMMON_ROLE_ID,
    RARE_ROLE_ID,
    LEGENDARY_ROLE_ID,
    SUPER_RARE_ROLE_ID,
    LEAGUE_SPREADSHEET_ID,
    DISCORD_GUILD_ID,
)
import season
from roles import (
    fuzzy_match_member,
    get_unlinked_players,
    get_player_registry,
    link_player,
    upsert_player_roles,
    batch_upsert_player_roles,
    compute_earned_roles,
    RARITY_ROLE_IDS,
    RARITY_ROLE_NAMES,
    FUZZY_HIGH_CONFIDENCE,
    FUZZY_LOW_CONFIDENCE,
)

# ── Bot setup ─────────────────────────────────────────────────
intents = discord.Intents.default()
intents.message_content = True  # read message text
intents.members = True  # on_member_join event

class GtaLorcanaBot(commands.Bot):
    async def setup_hook(self):
        if os.getenv("SYNC_COMMANDS_ONLY") == "1":
            guild = discord.Object(id=int(DISCORD_GUILD_ID))
            print(f"  SYNC_COMMANDS_ONLY mode — guild_id={DISCORD_GUILD_ID}, commands registered={len(self.tree.get_commands())}")
            # Copy to guild FIRST, then clear globals
            self.tree.copy_global_to(guild=guild)
            synced = await self.tree.sync(guild=guild)
            print(f"✓ Synced {len(synced)} command(s) to guild {DISCORD_GUILD_ID}:")
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


def get_channel_by_id(guild: discord.Guild, channel_id: int):
    """Find a channel by ID."""
    return guild.get_channel(channel_id)


def _ch(key: str) -> str:
    """Return '#channel-name' for a CHANNELS key, resolved live from Discord's cache."""
    ch = bot.get_channel(CHANNELS[key])
    return f"#{ch.name}" if ch else f"#{key.replace('_', '-')}"


def _is_admin(interaction: discord.Interaction) -> bool:
    """True if the user is in ADMIN_USER_IDS or has Manage Guild permission."""
    return interaction.user.id in ADMIN_USER_IDS or interaction.user.guild_permissions.manage_guild


def _last_sunday(d: date) -> date:
    """Return the most recent Sunday on or before d — consistent reference date for store analysis."""
    days_since_sunday = (d.weekday() + 1) % 7  # Mon=1 … Sat=6, Sun=0
    return d - timedelta(days=days_since_sunday)


def _fmt(format_str: str) -> str:
    """Shorten format label — strip ' Constructed' suffix to save characters."""
    return format_str.replace(' Constructed', '')


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
            lines.append(f"• **{e['store_name']}**{city}{time} · {_fmt(e['format'])}")
    return "\n".join(lines)


_WTP_CHAR_LIMIT = 1950  # leave headroom below Discord's 2000-char limit


def _build_where_to_play_messages(store_analysis: dict, as_of: date) -> list[str]:
    """
    Build #where-to-play messages from a store analysis result.
    Returns 3 messages normally, or 4 if the semi-regular section is too long
    to fit in one Discord message (split at a day boundary).
    """
    date_str = as_of.strftime('%B %d, %Y').replace(' 0', ' ')

    regular_msg = "\n".join([
        f"📍 **Where to Play — GTA Lorcana** — *Updated {date_str}*",
        "",
        "✅ Regular Events — *ran every week for 2+ weeks*",
        _grouped_by_day(store_analysis['regular']),
    ])

    # Build semi-regular day blocks individually so we can split if needed
    semi_header = "\u200b\n🔄 Semi-Regular Events — *ran at least twice in the last 4 weeks*"
    semi_entries = store_analysis.get('semi_regular', [])

    if not semi_entries:
        semi_msgs = [semi_header + "\n*None yet this season*"]
    else:
        groups = {}
        for e in semi_entries:
            groups.setdefault(e['day'], []).append(e)

        day_blocks = []
        for day, day_entries in groups.items():
            lines = [f"__{day}__"]
            for e in day_entries:
                city = f" ({e['city']})" if e.get('city') else ''
                time = f" @ {e['time']}" if e.get('time') else ''
                lines.append(f"• **{e['store_name']}**{city}{time} · {_fmt(e['format'])}")
            day_blocks.append("\n".join(lines))

        # Greedily pack day blocks into message 1; overflow goes to message 2
        msg1 = semi_header
        msg2 = ""
        for block in day_blocks:
            candidate = msg1 + "\n" + block
            if len(candidate) <= _WTP_CHAR_LIMIT:
                msg1 = candidate
            else:
                msg2 = (msg2 + "\n" + block) if msg2 else ("\u200b\n" + block)

        semi_msgs = [msg1, msg2] if msg2 else [msg1]

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

    return [regular_msg] + semi_msgs + [info_msg]


# ═══════════════════════════════════════════════════════════════
# EVENTS
# ═══════════════════════════════════════════════════════════════

@tasks.loop(minutes=30)
async def keepalive():
    """Periodic heartbeat to confirm the bot is alive and connected."""
    print(f"  ♥ Heartbeat — bot alive, watching {_ch('announcements')} and {_ch('results_reporting')}")


# ── Where-to-Play tasks ────────────────────────────────

# Stores the message ID of the current #where-to-play post so we can edit it
# in-place each Sunday rather than posting a new one.
_where_to_play_msg_ids: list[int | None] = [None, None, None, None]  # regular, semi-regular (×1-2), info


async def _post_where_to_play(channel, messages: list[str], loop) -> None:
    """Edit existing where-to-play messages in place, post new ones, delete orphans."""
    global _where_to_play_msg_ids
    new_ids = []
    for i, content in enumerate(messages):
        msg_id = _where_to_play_msg_ids[i] if i < len(_where_to_play_msg_ids) else None
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

    # Delete any previously-tracked messages beyond the new count (e.g. split collapsed)
    for old_id in _where_to_play_msg_ids[len(messages):]:
        if old_id:
            try:
                old_msg = await channel.fetch_message(old_id)
                await old_msg.delete()
            except discord.NotFound:
                pass

    _where_to_play_msg_ids = new_ids + [None] * (4 - len(new_ids))
    await loop.run_in_executor(None, save_bot_state,
        {f'wtp_msg_{i}': str(new_ids[i]) if i < len(new_ids) else '' for i in range(4)}
    )

# Pending mod-channel reaction prompts keyed by message ID.
# link suggestions: playhub_id, display_name, discord_id, discord_name
_pending_link_suggestions: dict[int, dict] = {}
# invitational assignments: legendary/super_rare candidate lists, event_name
_pending_invitational_assignments: dict[int, dict] = {}

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

    print(f"  🗺 where_to_play_weekly: refreshing {_ch('where_to_play')}...")

    loop = asyncio.get_running_loop()
    try:
        store_analysis = await loop.run_in_executor(None, analyse_stores, _last_sunday(now_et.date()))
    except Exception as e:
        print(f"  ✗ where_to_play_weekly: failed to fetch store analysis: {e}")
        return

    gc.collect()  # TODO: remove when upgraded to 1GB RAM — analyse_stores holds a full season of RPH events
    messages = _build_where_to_play_messages(store_analysis, now_et.date())

    for guild in bot.guilds:
        wtp_ch = get_channel_by_id(guild, CHANNELS["where_to_play"])
        if not wtp_ch:
            print(f"  ⚠ where_to_play_weekly: {_ch('where_to_play')} not found in {guild.name}")
            continue

        try:
            await _post_where_to_play(wtp_ch, messages, loop)
            print(f"  ✓ {_ch('where_to_play')} updated ({len(messages)} messages)")
        except Exception as e:
            print(f"  ✗ Failed to update {_ch('where_to_play')}: {e}")


# ── Set Championships daily refresh ─────────────────────────────────────────

@tasks.loop(minutes=1)
async def set_champs_daily():
    """
    Refreshes the Set Champs sheet once daily at noon ET during the set champs window.
    No-ops outside of SET_CHAMPS_START_DATE to SET_CHAMPS_END_DATE.
    """
    now_et = _now_et()
    if now_et.hour != 7 or now_et.minute != 0:
        return
    _start = date.fromisoformat(season.SET_CHAMPS_START_DATE) - timedelta(weeks=2)
    _end   = date.fromisoformat(season.SET_CHAMPS_END_DATE)
    if not (_start <= now_et.date() <= _end):
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
    print(f"  Watching {_ch('announcements')} for website sync")
    print(f"  Watching {_ch('results_reporting')} for results processing")

    # Load Bot State: initialise season config and restore persisted message IDs
    loop = asyncio.get_running_loop()
    try:
        state = await loop.run_in_executor(None, load_bot_state)
        season.init(state)
    except Exception as e:
        print(f"  ⚠ Could not load bot state for season init: {e}")
        season.init({})
        state = {}

    # Restore persisted where-to-play message IDs so edits work after restarts
    try:
        ids = [
            int(state[f'wtp_msg_{i}']) if f'wtp_msg_{i}' in state and state[f'wtp_msg_{i}'] else None
            for i in range(4)
        ]
        _where_to_play_msg_ids = ids
        print(f"  ✓ Restored where-to-play message IDs: {ids}")
    except Exception as e:
        print(f"  ⚠ Could not restore where-to-play message IDs: {e}")

    if not keepalive.is_running():
        keepalive.start()
        print(f"  ♻ Keepalive task started")
    if not where_to_play_weekly.is_running():
        where_to_play_weekly.start()
        print(f"  ♻ Where-to-play weekly task started (fires Sundays at {WHERE_TO_PLAY_POST_HOUR_ET}:00 ET)")
    if not set_champs_daily.is_running():
        set_champs_daily.start()
        print(f"  ♻ Set Champs daily task started (fires 7AM ET, {season.SET_CHAMPS_START_DATE} → {season.SET_CHAMPS_END_DATE})")
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
    if message.channel.id != CHANNELS["announcements"]:
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



# ═══════════════════════════════════════════════════════════════
# RESULTS REPORTING — Thread Detection
# ═══════════════════════════════════════════════════════════════

# Tracks thread IDs that have already been processed.
# Never cleared — so any duplicate on_thread_create for the same thread is
# always blocked, regardless of which event arrives first.
_seen_threads: set[int] = set()


async def _run_process_event_data(thread: discord.Thread, rph_url: str) -> list[list]:
    """
    Acquire the sheet lock and run process_event_data in a thread executor.
    Returns the full standing_rows written this run.
    Raises on any error — caller is responsible for handling.
    """
    async with _sheet_lock:
        if _sheet_lock._waiters:
            waiter_count = len(_sheet_lock._waiters)
            print(f"  ⏳ Sheet lock acquired for '{thread.name}' ({waiter_count} thread(s) were waiting)")
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, process_event_data, rph_url, thread.id)


async def process_results_reporting_thread(thread: discord.Thread) -> list[list]:
    """
    Validate the thread starter message URL and run the results processing pipeline.
    Returns the full standing_rows written this run.
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
    return await _run_process_event_data(thread, rph_url)


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
        standing_rows = await process_results_reporting_thread(thread)

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

        # Trigger linking flow for any new Playhub IDs in this event
        try:
            loop = asyncio.get_running_loop()
            new_players = await loop.run_in_executor(None, get_unlinked_players, standing_rows or [])
            if new_players:
                asyncio.create_task(_post_linking_suggestions(thread.guild, new_players))
        except Exception as link_err:
            print(f"  ⚠ Linking flow failed after results import: {link_err}")

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
                    f"{' '.join(f'<@{uid}>' for uid in ADMIN_USER_IDS)} Manual intervention required."
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
        standing_rows = await _run_process_event_data(thread, starter_msg.content.strip())

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

        try:
            loop = asyncio.get_running_loop()
            new_players = await loop.run_in_executor(None, get_unlinked_players, standing_rows or [])
            if new_players:
                asyncio.create_task(_post_linking_suggestions(thread.guild, new_players))
        except Exception as link_err:
            print(f"  ⚠ Linking flow failed after auto-retry: {link_err}")

    except Exception as retry_error:
        print(f"  ✗ Auto-retry {attempt} failed for '{thread.name}': {retry_error}")
        await _schedule_auto_retry(thread, starter_msg, error=retry_error, attempt=attempt + 1)


@bot.event
async def on_thread_create(thread: discord.Thread):
    """Detect new threads in #results-reporting and process them."""
    if not thread.parent or thread.parent.id != CHANNELS["results_reporting"]:
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
    if not after.channel.parent or after.channel.parent.id != CHANNELS["results_reporting"]:
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
    if message.channel.id != CHANNELS["announcements"]:
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
    if not thread.parent or thread.parent.id != CHANNELS["results_reporting"]:
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
# MEMBER JOIN — auto-assign Common role
# ═══════════════════════════════════════════════════════════════

@bot.event
async def on_member_join(member: discord.Member):
    """Auto-assign Common rarity role to every new member."""
    if not COMMON_ROLE_ID:
        return
    common_role = member.guild.get_role(COMMON_ROLE_ID)
    if common_role and common_role not in member.roles:
        try:
            await member.add_roles(common_role, reason="auto-assign Common on join")
            print(f"  ✦ Assigned Common role to new member {member.display_name}")
        except discord.HTTPException as e:
            print(f"  ⚠ Failed to assign Common role to {member.display_name}: {e}")


# ═══════════════════════════════════════════════════════════════
# PLAYER LINKING — fuzzy match helpers and reaction handler
# ═══════════════════════════════════════════════════════════════

async def _post_linking_suggestions(guild: discord.Guild, new_players: list[tuple[str, str]]):
    """
    For each (playhub_id, display_name) not yet in player_mapping:
      - High confidence (≥75%) → post ✅/❌ reaction prompt to mod channel
      - Low confidence (50–74%) → post notice, require /link
      - No match → post unmatched notice, require /link
    """
    if not MOD_CHANNEL_ID:
        print("  ⚠ MOD_CHANNEL_ID not set — skipping linking suggestions")
        return
    mod_ch = get_channel_by_id(guild, MOD_CHANNEL_ID)
    if not mod_ch:
        print("  ⚠ Mod channel not found — skipping linking suggestions")
        return

    members = [m for m in guild.members if not m.bot]

    for playhub_id, display_name in new_players:
        best_member, score = fuzzy_match_member(display_name, members)

        if score >= FUZZY_HIGH_CONFIDENCE:
            embed = make_embed(
                title="🔗 Suggested Player Link",
                description=(
                    f"**Playhub:** {display_name} (ID: `{playhub_id}`)\n"
                    f"**Discord:** {best_member.mention} (`{best_member.display_name}`)\n"
                    f"**Confidence:** {score:.0%}\n\n"
                    f"React ✅ to confirm or ❌ to skip."
                ),
                colour=discord.Colour.yellow()
            )
            msg = await mod_ch.send(embed=embed)
            await msg.add_reaction("✅")
            await msg.add_reaction("❌")
            _pending_link_suggestions[msg.id] = {
                'playhub_id':   playhub_id,
                'display_name': display_name,
                'discord_id':   best_member.id,
                'discord_name': best_member.display_name,
            }

        elif score >= FUZZY_LOW_CONFIDENCE and best_member:
            embed = make_embed(
                title="🔗 Low-Confidence Match",
                description=(
                    f"**Playhub:** {display_name} (ID: `{playhub_id}`)\n"
                    f"**Closest Discord match:** {best_member.mention} "
                    f"(`{best_member.display_name}`) — {score:.0%}\n\n"
                    f"Use `/link @member {playhub_id}` to confirm manually."
                ),
                colour=discord.Colour.orange()
            )
            await mod_ch.send(embed=embed)

        else:
            embed = make_embed(
                title="❓ Unmatched Player",
                description=(
                    f"**Playhub:** {display_name} (ID: `{playhub_id}`)\n"
                    f"No confident Discord match found.\n\n"
                    f"Use `/link @member {playhub_id}` to link manually."
                ),
                colour=discord.Colour.red()
            )
            await mod_ch.send(embed=embed)


@bot.event
async def on_raw_reaction_add(payload: discord.RawReactionActionEvent):
    """Handle ✅/❌ reactions on pending link suggestions and invitational assignments."""
    if payload.user_id == bot.user.id:
        return

    emoji = str(payload.emoji)
    if emoji not in ("✅", "❌"):
        return

    guild   = bot.get_guild(payload.guild_id)
    mod_ch  = guild.get_channel(payload.channel_id) if guild else None
    loop    = asyncio.get_running_loop()

    # ── Link suggestion confirmation ───────────────────────────
    if payload.message_id in _pending_link_suggestions:
        suggestion = _pending_link_suggestions.pop(payload.message_id)

        if emoji == "✅":
            role_seasons = await loop.run_in_executor(
                None, link_player,
                suggestion['discord_id'], suggestion['discord_name'],
                'fuzzy-confirmed',
                suggestion['playhub_id'], suggestion['display_name'],
            )
            # Assign Discord roles for each role_id in role_seasons
            if role_seasons and guild:
                rarity_id_set = set(RARITY_ROLE_IDS)
                member = guild.get_member(suggestion['discord_id'])
                if member:
                    current = {r.id for r in member.roles if r.id in rarity_id_set}
                    for role_id, season in role_seasons.items():
                        if role_id not in current:
                            role = guild.get_role(role_id)
                            if role:
                                try:
                                    await member.add_roles(role, reason="fuzzy-link-confirmed")
                                except discord.HTTPException as e:
                                    print(f"  ⚠ Could not assign role: {e}")
            if mod_ch:
                roles_str = ""
                if role_seasons:
                    roles_str = "\nRoles assigned: " + ", ".join(
                        f"**{RARITY_ROLE_NAMES.get(r, str(r))}** ({s})"
                        for r, s in role_seasons.items()
                    )
                await mod_ch.send(embed=make_embed(
                    title="✅ Link Confirmed",
                    description=(
                        f"**{suggestion['display_name']}** (Playhub `{suggestion['playhub_id']}`)"
                        f" → <@{suggestion['discord_id']}>{roles_str}"
                    ),
                    colour=discord.Colour.green()
                ))
        else:
            if mod_ch:
                await mod_ch.send(embed=make_embed(
                    title="❌ Link Skipped",
                    description=(
                        f"Skipped **{suggestion['display_name']}** "
                        f"(Playhub `{suggestion['playhub_id']}`). "
                        f"Use `/link` to resolve manually."
                    ),
                    colour=discord.Colour.red()
                ))

    # ── Invitational assignment confirmation ───────────────────
    elif payload.message_id in _pending_invitational_assignments:
        assignment = _pending_invitational_assignments.pop(payload.message_id)

        if emoji == "✅":
            legendary_role = guild.get_role(LEGENDARY_ROLE_ID)
            sr_role        = guild.get_role(SUPER_RARE_ROLE_ID)
            assigned = []
            skipped  = []

            all_candidates = []
            if assignment['legendary']:
                pid, name, member = assignment['legendary']
                all_candidates.append((pid, name, member, legendary_role, "Legendary"))
            for pid, name, member in assignment['super_rare']:
                all_candidates.append((pid, name, member, sr_role, "Super Rare"))

            for pid, name, member, role, role_name in all_candidates:
                if not member or not role:
                    skipped.append(f"**{name}** (Playhub `{pid}`) — unlinked")
                    continue
                try:
                    await member.add_roles(role, reason=f"/invitational-roles: {assignment['event_name']}")
                    assigned.append(f"{member.mention} → **{role_name}**")
                except discord.HTTPException as e:
                    skipped.append(f"{member.mention} — error: {e}")

            lines = assigned + (["\n**Could not assign (unlinked):**"] + skipped if skipped else [])
            if mod_ch:
                await mod_ch.send(embed=make_embed(
                    title=f"🏆 Invitational Roles Assigned — {assignment['event_name']}",
                    description="\n".join(lines) if lines else "No changes made.",
                    colour=discord.Colour.gold()
                ))
        else:
            if mod_ch:
                await mod_ch.send(embed=make_embed(
                    title="❌ Invitational Assignment Cancelled",
                    description=f"Role assignment for **{assignment['event_name']}** was cancelled.",
                    colour=discord.Colour.red()
                ))


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
                            description=f"Could not load events right now — check `{_ch('announcements')}` for the latest.",
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
                description=f"Could not load events right now — check `{_ch('announcements')}` for the latest.",
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
        value=f"Check `{_ch('announcements')}` or visit the GTA Lorcana website.",
        inline=False
    )
    await interaction.followup.send(embed=embed)





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
    forum = guild.get_channel(CHANNELS["results_reporting"])
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
                            f"{' '.join(f'<@{uid}>' for uid in ADMIN_USER_IDS)} Manual intervention required."
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
              description="Reprocess any unhandled threads in #results-reporting (admins only)")
@app_commands.describe(
    after="Only recheck threads created on or after this date (YYYY-MM-DD). Leave blank to check all.")
async def recheck(interaction: discord.Interaction, after: str = ""):
    """
    Scans all threads in the results-reporting forum channel.
    Any thread without a ✅ or ❌ reaction from the bot is reprocessed.
    """
    await interaction.response.defer(ephemeral=True)

    if not _is_admin(interaction):
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

    forum = interaction.guild.get_channel(CHANNELS["results_reporting"])
    if not forum:
        await interaction.followup.send(
            f"⚠️ Could not find forum channel `{_ch('results_reporting')}`.", ephemeral=True
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
                description=f"All {total} thread(s) in `{_ch('results_reporting')}` have already been processed.",
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


# ── /link ─────────────────────────────────────────────────────
@tree.command(name="link", description="Link a Discord member to their Playhub ID or display name (mods only)")
@app_commands.describe(member="Discord member", identifier="Playhub ID (numeric) or Playhub display name")
async def link_command(interaction: discord.Interaction, member: discord.Member, identifier: str):
    if not _is_admin(interaction):
        await interaction.response.send_message("⚠️ Mods only.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)
    loop = asyncio.get_running_loop()

    # Determine if identifier is a numeric playhub_id or a display name
    # Strip surrounding quotes in case the user wrapped the name in quotes
    identifier = identifier.strip().strip('"').strip("'")
    if identifier.isdigit():
        playhub_id   = identifier
        playhub_name = None
    else:
        playhub_id   = None
        playhub_name = identifier

    registry = await loop.run_in_executor(None, get_player_registry)

    # Check for existing links — block only if the identifier is claimed by a different member.
    # Linking a second Playhub name to the same member is allowed (rows will be merged).
    for entry in registry:
        if not entry['discord_id']:
            continue
        if playhub_id and entry['playhub_id'] == playhub_id and entry['discord_id'] != member.id:
            await interaction.followup.send(
                f"⚠️ Playhub ID `{playhub_id}` is already linked to <@{entry['discord_id']}>.",
                ephemeral=True
            )
            return
        if playhub_name and entry['playhub_name'].lower() == playhub_name.lower() and entry['discord_id'] != member.id:
            await interaction.followup.send(
                f"⚠️ Playhub name `{playhub_name}` is already linked to <@{entry['discord_id']}>.",
                ephemeral=True
            )
            return

    role_seasons = await loop.run_in_executor(
        None, link_player,
        member.id, member.display_name,
        f'manual:{interaction.user.display_name}',
        playhub_id, playhub_name,
    )

    # Assign any earned Discord roles
    roles_assigned = []
    if role_seasons:
        rarity_id_set = set(RARITY_ROLE_IDS)
        current = {r.id for r in member.roles if r.id in rarity_id_set}
        for role_id, season in role_seasons.items():
            if role_id not in current:
                discord_role = interaction.guild.get_role(role_id)
                if discord_role:
                    try:
                        await member.add_roles(discord_role, reason="link-command")
                        roles_assigned.append(f"**{RARITY_ROLE_NAMES.get(role_id, str(role_id))}** ({season})")
                    except discord.HTTPException as e:
                        print(f"  ⚠ link: failed to assign role {role_id} to {member.display_name}: {e}")

    id_str = f"ID `{playhub_id}`" if playhub_id else f"name `{playhub_name}`"
    roles_str = ("\nRoles assigned: " + ", ".join(roles_assigned)) if roles_assigned else ""
    await interaction.followup.send(
        f"✅ Linked **{member.display_name}** → Playhub {id_str}{roles_str}", ephemeral=True
    )
    mod_ch = get_channel_by_id(interaction.guild, MOD_CHANNEL_ID)
    if mod_ch:
        await mod_ch.send(embed=make_embed(
            title="🔗 Manual Link Added",
            description=(
                f"{member.mention} → Playhub {id_str}\n"
                f"Linked by {interaction.user.mention}"
                + (f"\nRoles assigned: {', '.join(roles_assigned)}" if roles_assigned else "")
            ),
            colour=discord.Colour.green()
        ))


# ── /sync-roles ────────────────────────────────────────────────
@tree.command(name="sync-roles", description="Sync rarity roles for all linked players (mods only)")
async def sync_roles(interaction: discord.Interaction):
    if not _is_admin(interaction):
        await interaction.response.send_message("⚠️ Mods only.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)
    loop = asyncio.get_running_loop()

    # 1. Read leaderboard
    lb_data = await loop.run_in_executor(
        None, _gs.get_values, LEAGUE_SPREADSHEET_ID, season.LEADERBOARD_RANGE_NAME
    )
    leaderboard_rows = lb_data.get('values', [])

    # 2. Build earned dict: {player_name_lower: (display_name, {role_id: CURRENT_SEASON})}
    earned_by_name: dict[str, tuple[str, dict[int, str]]] = {}
    for row in leaderboard_rows:
        if len(row) < 2:
            continue
        try:
            rank = int(row[0])
        except (ValueError, IndexError):
            continue
        player_name = row[1].strip()
        try:
            events_played = int(row[3]) if len(row) > 3 and row[3] else 0
        except ValueError:
            events_played = 0
        earned = compute_earned_roles(rank, events_played)
        if earned:
            earned_by_name[player_name.lower()] = (player_name, {r: season.CURRENT_SEASON for r in earned})

    if not earned_by_name:
        await interaction.followup.send("✅ No players have earned roles this season.", ephemeral=True)
        return

    # 3. Batch-upsert all role changes in one registry read + one API write
    earners = [(name, roles, None) for name, roles in earned_by_name.values()]
    try:
        await loop.run_in_executor(None, batch_upsert_player_roles, earners)
    except Exception as e:
        print(f"  ⚠ sync-roles: batch upsert failed: {e}")

    # 4. Read registry once for Discord role assignment
    registry = await loop.run_in_executor(None, get_player_registry)
    registry_by_name = {r['playhub_name'].lower(): r for r in registry}

    rarity_id_set = set(RARITY_ROLE_IDS)
    applied   = []   # (member_mention, role_name)
    unlinked  = []   # player names who earned roles but have no Discord link

    # 5. Assign Discord roles for linked players
    for player_name_lower, (player_name_display, role_seasons) in earned_by_name.items():
        reg_entry = registry_by_name.get(player_name_lower)
        if not reg_entry or not reg_entry['discord_id']:
            unlinked.append(player_name_display)
            continue

        member = interaction.guild.get_member(reg_entry['discord_id'])
        if not member:
            unlinked.append(player_name_display)
            continue

        current = {r.id for r in member.roles if r.id in rarity_id_set}
        for role_id, season in role_seasons.items():
            if role_id not in current:
                discord_role = interaction.guild.get_role(role_id)
                if discord_role:
                    try:
                        await member.add_roles(discord_role, reason="sync-roles")
                        applied.append((member.mention, RARITY_ROLE_NAMES.get(role_id, str(role_id))))
                    except discord.HTTPException as e:
                        print(f"  ⚠ sync-roles: failed to assign {role_id} to {member.display_name}: {e}")

    mod_ch = get_channel_by_id(interaction.guild, MOD_CHANNEL_ID)
    if mod_ch and (applied or unlinked):
        lines = [f"{mention}: +**{role_name}**" for mention, role_name in applied]
        if unlinked:
            lines.append(f"\n**Earned roles but not yet linked ({len(unlinked)}):**")
            lines.extend(f"• {name}" for name in unlinked[:20])
            if len(unlinked) > 20:
                lines.append(f"  *(and {len(unlinked) - 20} more)*")
        await mod_ch.send(embed=make_embed(
            title=f"Role Sync — {len(applied)} role(s) assigned",
            description="\n".join(lines) if lines else "No changes.",
            colour=discord.Colour.gold()
        ))

    summary = f"✅ Sync complete — {len(applied)} role(s) assigned"
    if unlinked:
        summary += f", {len(unlinked)} unlinked player(s) will get roles when they /link"
    await interaction.followup.send(summary + ".", ephemeral=True)



# ── /invitational-roles ───────────────────────────────────────
@tree.command(name="invitational-roles",
              description="Preview and assign Legendary/Super Rare from an invitational (mods only)")
@app_commands.describe(event_url="RPH event URL or bare event ID")
async def invitational_roles(interaction: discord.Interaction, event_url: str):
    if not _is_admin(interaction):
        await interaction.response.send_message("⚠️ Mods only.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)

    event_id = event_url.strip().rstrip("/").split("/")[-1]

    loop = asyncio.get_running_loop()
    try:
        events = await loop.run_in_executor(None, _rph_api.get_event_by_id, event_id)
    except Exception as e:
        await interaction.followup.send(f"❌ Failed to fetch event: {e}", ephemeral=True)
        return

    if not events:
        await interaction.followup.send(f"❌ No event found for ID `{event_id}`.", ephemeral=True)
        return

    event = events[0]
    if not event.get('tournament_phases') or not event['tournament_phases'][-1].get('rounds'):
        await interaction.followup.send("❌ Event has no tournament rounds.", ephemeral=True)
        return

    last_round_id = event['tournament_phases'][-1]['rounds'][-1]['id']
    try:
        standings = await loop.run_in_executor(
            None, _rph_api.get_standings_from_tournament_round_id, str(last_round_id)
        )
    except Exception as e:
        await interaction.followup.send(f"❌ Failed to fetch standings: {e}", ephemeral=True)
        return

    standings.sort(key=lambda s: s['rank'])
    registry_list      = await loop.run_in_executor(None, get_player_registry)
    playhub_to_discord = {r['playhub_id']: r['discord_id'] for r in registry_list if r['playhub_id'] and r['discord_id']}

    def resolve(s):
        pid    = str(s['player']['id'])
        name   = s['user_event_status']['best_identifier']
        did    = playhub_to_discord.get(pid)
        member = interaction.guild.get_member(did) if did else None
        return pid, name, member

    rank1 = next((s for s in standings if s['rank'] == 1), None)
    top8  = [s for s in standings if 2 <= s['rank'] <= 8]

    legendary_entry = resolve(rank1) if rank1 else None
    sr_entries      = [resolve(s) for s in top8]

    event_name = event.get('name', f"Event {event_id}")
    lines = []
    if legendary_entry:
        pid, name, member = legendary_entry
        mention = member.mention if member else f"**{name}** *(unlinked — use /link first)*"
        lines.append(f"🏆 **Legendary** → {mention}")
    for i, (pid, name, member) in enumerate(sr_entries, 2):
        mention = member.mention if member else f"**{name}** *(unlinked)*"
        lines.append(f"⭐ **Super Rare** (rank {i}) → {mention}")

    mod_ch = get_channel_by_id(interaction.guild, MOD_CHANNEL_ID)
    if not mod_ch:
        await interaction.followup.send("⚠️ Mod channel not configured.", ephemeral=True)
        return

    embed = make_embed(
        title=f"🏆 Invitational Role Assignment — {event_name}",
        description="\n".join(lines) + "\n\nReact ✅ to confirm or ❌ to cancel.",
        colour=discord.Colour.gold()
    )
    try:
        msg = await mod_ch.send(embed=embed)
    except discord.Forbidden:
        await interaction.followup.send(
            f"❌ Bot lacks permission to send messages in the mod channel (ID: `{MOD_CHANNEL_ID}`). "
            f"Check channel permissions.", ephemeral=True
        )
        return

    await msg.add_reaction("✅")
    await msg.add_reaction("❌")
    _pending_invitational_assignments[msg.id] = {
        'event_name': event_name,
        'legendary':  legendary_entry,
        'super_rare': sr_entries,
    }
    await interaction.followup.send(
        f"Check {_ch('mod') if MOD_CHANNEL_ID else '#mod-channel'} to confirm.", ephemeral=True
    )


# ── /help ─────────────────────────────────────────────────────
@tree.command(name="help", description="Show all GTA Lorcana bot commands")
async def help_command(interaction: discord.Interaction):
    embed = make_embed(
        title="GTA Lorcana Bot — Commands",
        description="**Everyone**"
    )
    embed.add_field(name="/schedule", value="Show upcoming events", inline=False)
    embed.add_field(name="/watch-rph-event", value="Subscribe to DM alerts when a spot opens at a full RPH event", inline=False)
    embed.add_field(name="/unwatch-rph-event", value="Unsubscribe from a watched event", inline=False)
    embed.add_field(name="/list-watches", value="Show all active event watches", inline=False)
    embed.add_field(name="🧵 Results Threads",
                    value=f"New threads in `{_ch('results_reporting')}` are processed automatically. Edit to retry on bad URL.",
                    inline=False)
    embed.add_field(name="\u200b", value="**Admins only**", inline=False)
    embed.add_field(name="/recheck",
                    value=f"Reprocess any missed threads in `{_ch('results_reporting')}`",
                    inline=False)
    embed.add_field(name="/link", value="Manually link a Discord member to a Playhub ID", inline=False)
    embed.add_field(name="/sync-roles", value="Compute and apply Uncommon/Rare role upgrades from current standings", inline=False)
    embed.add_field(name="/invitational-roles", value="Assign Legendary/Super Rare from an invitational event", inline=False)
    embed.add_field(name="/wheretoplay", value="Manually push the Where to Play post", inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)


# ═══════════════════════════════════════════════════════════════
# WHERE TO PLAY — MANUAL TRIGGER
# ═══════════════════════════════════════════════════════════════

@tree.command(name="wheretoplay", description="Manually push the Where to Play post (admins only)")
async def wheretoplay_command(interaction: discord.Interaction):
    if not _is_admin(interaction):
        await interaction.response.send_message("❌ Admins only.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)

    try:
        loop = asyncio.get_running_loop()
        ref = _last_sunday(date.today())
        store_analysis = await loop.run_in_executor(None, analyse_stores, ref)
        gc.collect()  # TODO: remove when upgraded to 1GB RAM — analyse_stores holds a full season of RPH events

        channel = get_channel_by_id(interaction.guild, CHANNELS["where_to_play"])
        if not channel:
            await interaction.followup.send(f"⚠️ {_ch('where_to_play')} channel not found.", ephemeral=True)
            return

        messages = _build_where_to_play_messages(store_analysis, ref)
        await _post_where_to_play(channel, messages, loop)
        await interaction.followup.send(f"✅ {_ch('where_to_play')} updated ({len(messages)} messages).", ephemeral=True)

    except Exception as e:
        await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)


if __name__ == "__main__":
    missing = [v for v in ["DISCORD_BOT_TOKEN", "WORKER_URL", "WORKER_SECRET"] if not os.getenv(v)]
    if missing:
        raise ValueError(f"Missing environment variables: {', '.join(missing)}")
    bot.run(DISCORD_BOT_TOKEN)
