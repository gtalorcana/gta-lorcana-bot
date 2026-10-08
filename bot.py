"""
GTA Lorcana — Discord Bot
=========================
Features:
  - /schedule        — shows upcoming events
  - /watch-rph-event — subscribe to DM alerts when a spot opens at a full event
  - /unwatch-rph-event — unsubscribe from a watched event
  - /list-watches    — see all currently watched events
  - /etb-discount     — verify GTA event attendance and unlock ETB community discount
  - /help            — list all commands
  - /recheck         — reprocess missed results threads (admins only)
  - /link            — manually link a Discord member to a Playhub ID (admins only)
  - /record-rare-and-uncommon        — record Rare/Uncommon from the leaderboard (admins only)
  - /record-legendary-and-super-rare — record Legendary/Super Rare from an invitational (admins only)
  - /assign-roles-from-registry      — assign every rarity role the registry records (admins only)
  - /where-to-play     — manually push the where-to-play post (admins only)
  - /set-champs      — manually refresh and post the Set Champs update (admins only)
  - /seasons         — show the season calendar and each digest's window (admins only)
  - /prereleases     — manually refresh and post the prerelease update (admins only)
  - /ccqs            — manually refresh and post the CCQ update (admins only)
  - event_digests_daily — refreshes the Set Champs and prerelease digests each morning
  - season_close_daily — posts/refreshes the end-of-season checklist in the mod channel
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
import traceback

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands, tasks
from dataclasses import dataclass
from datetime import datetime, timezone, date, timedelta
from typing import Callable

from clients import gs as _gs, rph_api as _rph_api
from results import process_event_data, remove_event_data
from stores import analyse_stores, get_expected_stores_for_date, load_bot_state, save_bot_state, load_season_config, refresh_set_champs, fetch_prereleases, fetch_ccqs, set_bot_state_key, delete_bot_state_key, fetch_event_status, create_season_sheets, archive_season_data, get_etb_approval, append_etb_approval, lookup_player_standings, get_current_display_names

from constants import (
    DISCORD_BOT_TOKEN,
    WORKER_URL,
    WORKER_SECRET,
    CHANNELS,
    MOD_CHANNEL_ID,
    SET_CHAMPS_CHANNEL_ID,
    PRERELEASE_CHANNEL_ID,
    CCQ_CHANNEL_ID,
    DIGEST_HOUR_ET as _DIGEST_HOUR_ET,
    EVENTS_URL_RE,
    RPH_RETRY_DELAY,
    RPH_RETRY_ATTEMPTS,
    ADMIN_USER_IDS,
    UPCOMING_EVENTS_JSON_URL,
    WHERE_TO_PLAY_POST_DAY,
    WHERE_TO_PLAY_POST_HOUR_ET,
    COMMON_ROLE_ID,
    UNCOMMON_ROLE_ID,
    RARE_ROLE_ID,
    LEGENDARY_ROLE_ID,
    SUPER_RARE_ROLE_ID,
    LEAGUE_SPREADSHEET_ID,
    ARCHIVE_SPREADSHEET_ID,
    BOT_DATABASE_SPREADSHEET_ID,
    DISCORD_GUILD_ID,
    SHOPIFY_CLIENT_ID,
    SHOPIFY_STORE_DOMAIN,
)
import season
from util.shopify_api_utils import ShopifyApi as _ShopifyApi
from roles import (
    fuzzy_match_member,
    get_unlinked_players,
    get_player_registry,
    link_player,
    upsert_player_roles,
    batch_upsert_player_roles,
    compact_and_sort_registry,
    _merge_duplicate_rows,
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
        # Season-close checklist buttons route by custom_id, so clicks on a
        # checklist posted before a restart still land.
        self.add_dynamic_items(_SeasonCloseButton)
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


@tree.error
async def on_app_command_error(
    interaction: discord.Interaction,
    error: app_commands.AppCommandError,
):
    """Surface slash-command crashes back to the invoker so it doesn't hang on 'thinking…'."""
    inner = getattr(error, "original", error)
    cmd = interaction.command.name if interaction.command else "?"
    tb = "".join(traceback.format_exception(type(inner), inner, inner.__traceback__))
    print(f"  ✗ /{cmd} crashed:\n{tb}")

    summary = f"❌ `/{cmd}` crashed: `{type(inner).__name__}: {inner}`"
    tb_block = f"```\n{tb[-1500:]}\n```"  # last ~1.5KB so we stay under Discord's 2000-char limit
    body = summary + "\n" + tb_block
    try:
        if interaction.response.is_done():
            await interaction.followup.send(body, ephemeral=True)
        else:
            await interaction.response.send_message(body, ephemeral=True)
    except discord.HTTPException:
        pass  # interaction expired or message too long — log already captured above

# Serializes all sheet writes — prevents concurrent threads from overwriting each other
_sheet_lock = asyncio.Lock()

# Shopify client — initialized in on_ready if credentials are available.
# Price rule ID for ETBGTALORCANA is fetched once at startup and cached here.
_shopify:             _ShopifyApi | None = None
_etb_price_rule_id:  int | None         = None



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


_WTP_CHAR_LIMIT = 1950  # leave headroom below Discord's 2000-char limit (also used by the event digests)


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
    def _save_wtp_ids():
        # strict=True: this reads the whole tab and writes it back, so a quietly
        # failed read would save {} over every other key — see set_bot_state_key.
        state = load_bot_state(strict=True)
        state['wtp_msg_ids'] = '|'.join(str(i) for i in new_ids)
        # Remove legacy per-index keys if present
        for i in range(4):
            state.pop(f'wtp_msg_{i}', None)
        save_bot_state(state)
    await loop.run_in_executor(None, _save_wtp_ids)

# Pending mod-channel reaction prompts keyed by message ID.
# link suggestions: playhub_id, display_name, discord_id, discord_name
_pending_link_suggestions: dict[int, dict] = {}
# invitational assignments: legendary/super_rare candidate lists, event_name
_pending_invitational_assignments: dict[int, dict] = {}
# etb approvals: discord_id, playhub_id, rph_username, email, count, customer_id
_pending_etb_approvals: dict[int, dict] = {}

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


# ── Event digests (Set Championships, Prereleases) ──────────────────────────

def _build_event_digest_messages(title: str, empty_text: str, rows: list, as_of: date) -> list[str]:
    """
    Format digest rows (stores._event_digest_rows) into a list of Discord messages —
    one per unique date, preceded by a header message.
    """
    from collections import defaultdict

    header = (
        f"{title}\n"
        f"*Last updated: {as_of.strftime('%b %-d, %Y')}*"
    )

    if not rows:
        return [header + f"\n\n*{empty_text}*"]

    by_date = defaultdict(list)
    for row in rows:
        by_date[row[0]].append(row)

    messages = [header]
    for date_str in sorted(by_date.keys()):
        event_date = date.fromisoformat(date_str)
        # Year only when it differs from today's: the CCQ digest looks six months
        # ahead, where a bare "Saturday, Feb 6" is ambiguous. Same-year dates keep
        # their existing wording, so the live digests are not rewritten for this.
        day_label  = event_date.strftime('%A, %b %-d')
        if event_date.year != as_of.year:
            day_label += f", {event_date.year}"
        day_header = f"─────────────────────\n\n**{day_label}**"
        current    = day_header
        for row in by_date[date_str]:
            store   = row[3]
            city    = row[4]
            time    = row[1]
            cap     = row[5]
            url     = row[8]
            cap_str = f" · Cap {cap}" if cap else ""
            entry   = f"**{store}** ({city})\n{time}{cap_str} · <{url}>"
            # A busy day overflows one message (Hyperia City prerelease Saturday: 20
            # events, ~2,200 chars) — carry it on in a "(cont.)" message instead.
            if len(current) + 2 + len(entry) > _WTP_CHAR_LIMIT:
                messages.append(current)
                current = f"**{day_label} (cont.)**"
            current += "\n\n" + entry
        messages.append(current)

    return messages


# Digest message IDs, keyed by their Bot State key — restored on ready.
_digest_msg_ids: dict[str, list[int]] = {}


@dataclass(frozen=True)
class _DigestSpec:
    """
    One scheduled digest. Adding a digest is a row in _DIGESTS, not another task:
    the two that existed before were near-identical copies, and the season-overlap
    rule they each got slightly differently now lives in one place.
    """
    key:        str                                 # short name, for logs
    label:      str                                 # human label in messages
    state_key:  str                                 # Bot State key holding '<id>|<id>|…'
    channel_id: int
    minute:     int                                 # minute past _DIGEST_HOUR_ET
    fetch:      Callable[[], tuple[int, list]]      # zero-arg: resolves its own window
    title:      Callable[[], str]
    empty_text: str
    active:     Callable[[date], bool]              # is there anything to post today?


def _set_champs_title() -> str:
    return f"🏆 **GTA Lorcana — {season.CURRENT_SEASON} Set Championships**"


def _prerelease_title() -> str:
    row      = season.active_prerelease()
    set_name = f"{row['set_name']} " if row and row.get('set_name') else ""
    return f"🎁 **GTA Lorcana — {set_name}Prereleases**"


def _set_champs_active(today: date) -> bool:
    """
    Set Champs refresh runs from the season start through the Set Champs end.
    It starts at the season start, not the window start, because stores list their
    Set Championships weeks ahead and the channel is a preview as much as a result.
    """
    if not (season.SEASON_START_DATE and season.SET_CHAMPS_END_DATE):
        print("  ⚠ set_champs digest: season dates not configured — skipping")
        return False
    return date.fromisoformat(season.SEASON_START_DATE) <= today <= date.fromisoformat(season.SET_CHAMPS_END_DATE)


def _prerelease_active(today: date) -> bool:
    """Active whenever some season's prerelease window has not yet ended."""
    return season.active_prerelease(today) is not None


def _ccq_title() -> str:
    return "⚔️ **GTA Lorcana — Upcoming CCQs**"


_DIGESTS: tuple[_DigestSpec, ...] = (
    _DigestSpec('set_champs', 'Set Champs', 'set_champs_msg_ids', SET_CHAMPS_CHANNEL_ID, 0,
                refresh_set_champs, _set_champs_title,
                "No Set Championship events found yet.", _set_champs_active),
    _DigestSpec('prerelease', 'Prerelease', 'prerelease_msg_ids', PRERELEASE_CHANNEL_ID, 5,
                fetch_prereleases, _prerelease_title,
                "No prerelease events found yet.", _prerelease_active),
    # No window to be inside: CCQs are a rolling six-month lookahead, so this one is
    # always active and the only digest that needs no season at all.
    _DigestSpec('ccq', 'CCQ', 'ccq_msg_ids', CCQ_CHANNEL_ID, 10,
                fetch_ccqs, _ccq_title,
                "No CCQs announced in the next six months.", lambda today: True),
)


async def _post_event_digest(label: str, channel_id: int, state_key: str,
                             messages: list[str], loop) -> None:
    """Post or edit a digest's messages (header + one per day) in its channel."""
    guild = bot.guilds[0] if bot.guilds else None
    if not guild:
        return
    channel = guild.get_channel(channel_id)
    if not channel:
        print(f"  ⚠ {label} channel {channel_id} not found")
        return

    old_ids = _digest_msg_ids.get(state_key, [])
    if not old_ids:
        # Nothing in memory: either this digest has never posted, or the startup
        # restore was skipped because Bot State could not be read. Re-read the key
        # before posting — guessing "never posted" duplicates the whole digest.
        try:
            raw = (await loop.run_in_executor(None, load_bot_state)).get(state_key, '')
            old_ids = [int(x) for x in raw.split('|') if x]
            if old_ids:
                print(f"  ✓ {label}: recovered {len(old_ids)} message ID(s) from Bot State")
        except Exception as e:
            print(f"  ⚠ {label}: could not re-read {state_key} ({e}) — not posting, "
                  f"to avoid duplicating the digest")
            return

    new_ids = []

    for i, content in enumerate(messages):
        msg_id = old_ids[i] if i < len(old_ids) else None
        if msg_id:
            try:
                existing = await channel.fetch_message(msg_id)
                await existing.edit(content=content, suppress=True)
                new_ids.append(msg_id)
                continue
            except discord.NotFound:
                pass
        msg = await channel.send(content, suppress_embeds=True)
        new_ids.append(msg.id)

    # Delete orphaned messages if day count decreased
    for old_id in old_ids[len(messages):]:
        if old_id:
            try:
                old_msg = await channel.fetch_message(old_id)
                await old_msg.delete()
            except discord.NotFound:
                pass

    _digest_msg_ids[state_key] = new_ids
    ids_str = '|'.join(str(i) for i in new_ids)
    await loop.run_in_executor(None, set_bot_state_key, state_key, ids_str)
    print(f"  ✓ {label} Discord updated ({len(messages)} message(s))")


async def _run_digest(spec: _DigestSpec, loop) -> int:
    """
    Fetch a digest's events and post or edit its messages. Returns the event count.
    Shared by the scheduled loop and each manual command, so a hand-run digest and a
    7 AM one cannot drift apart.
    """
    count, rows = await loop.run_in_executor(None, spec.fetch)
    # TODO: remove when upgraded to 1GB RAM — each fetch pulls a window of RPH events
    gc.collect()
    messages = _build_event_digest_messages(spec.title(), spec.empty_text, rows, date.today())
    await _post_event_digest(spec.label, spec.channel_id, spec.state_key, messages, loop)
    return count


@tasks.loop(minutes=1)
async def event_digests_daily():
    """
    Refresh one digest per minute from _DIGEST_HOUR_ET, in spec order.

    Staggering by a minute each keeps two RPH window fetches from ever being in
    memory at once, which matters on a 256MB machine. It is a property of the
    schedule rather than a comment repeated in every task.
    """
    now_et = _now_et()
    if now_et.hour != _DIGEST_HOUR_ET:
        return
    spec = next((d for d in _DIGESTS if d.minute == now_et.minute), None)
    if spec is None or not spec.active(now_et.date()):
        return

    print(f"  🗓 {spec.key}_digest: refreshing for {now_et.date()}...")
    loop = asyncio.get_running_loop()
    try:
        count = await _run_digest(spec, loop)
        print(f"  ✓ {spec.label} refreshed ({count} event(s))")
    except Exception as e:
        print(f"  ✗ {spec.key}_digest failed: {e}")


# ═══════════════════════════════════════════════════════════════
# RPH EVENT WATCHER
# ═══════════════════════════════════════════════════════════════

_RPH_WATCH_KEY_PREFIX = "rph_watch:"


def _watch_key(event_id: int) -> str:
    return f"{_RPH_WATCH_KEY_PREFIX}{event_id}"


async def _reload_season(loop) -> tuple[dict, list[str]]:
    """
    Reload Bot State and the Seasons calendar, and rebuild season.py from both.
    Returns (bot_state, problems).

    On a read failure the in-memory calendar is left alone rather than cleared:
    every season window, and with it the results pipeline's date checks, hangs off
    one tab read now, so an outage must not look like "no seasons configured".
    """
    try:
        state, calendar, problems = await loop.run_in_executor(None, load_season_config)
    except Exception as e:
        print(f"  ⚠ Could not load season config: {e} — keeping the calendar already in memory")
        return {}, [f"Could not read the Seasons tab or Bot State: {e}"]

    season.init(state, calendar)
    return state, problems


async def _try_delete_state_key(loop, key: str, what: str) -> bool:
    """
    Delete a Bot State key, logging instead of raising on failure.

    For background-task cleanup only. delete_bot_state_key reads the tab with
    strict=True, so a Sheets outage raises — and an exception escaping a
    tasks.loop body kills that loop until the next restart.
    """
    try:
        await loop.run_in_executor(None, delete_bot_state_key, key)
        return True
    except Exception as e:
        print(f"  ⚠ Could not remove {what} ({key}): {e} — will retry next tick")
        return False


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

        # Auto-expire past end_date. Cleanup failures are logged and retried on the
        # next tick rather than raised: an unhandled exception in a tasks.loop body
        # stops the loop for good, and a stale watch is a far smaller problem than a
        # dead watcher.
        if end_date and today > end_date:
            print(f"  🗑 rph_watcher: {name} (id={event_id}) past end_date {end_date} — removing")
            await _try_delete_state_key(loop, key, f"expired watch {event_id}")
            continue

        if not subs:
            await _try_delete_state_key(loop, key, f"empty watch {event_id}")
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

    try:
        if subs:
            watch['subscribers'] = subs
            await loop.run_in_executor(None, set_bot_state_key, key, json.dumps(watch))
        else:
            # Last subscriber — remove the whole key
            await loop.run_in_executor(None, delete_bot_state_key, key)
    except Exception as e:
        await interaction.followup.send(f"❌ Could not update the watch: {e}", ephemeral=True)
        return

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
    state, problems = await _reload_season(loop)

    if season.SEASON_START_DATE is None:
        print(f"  ✗ CRITICAL: Season dates not configured — season-dependent tasks will not run.")
        mod_ch = bot.get_channel(MOD_CHANNEL_ID)
        if mod_ch:
            await mod_ch.send(
                f"⚠️ **Season dates not configured.** The Seasons tab has no usable row for "
                f"`{season.CURRENT_SEASON}`. Add one, then run `/seasons` to re-check."
            )

    # A hand-edited tab with a bad row has to be loud now, not discovered as a
    # digest that silently posts nothing at 7 AM.
    if problems:
        print(f"  ⚠ Seasons tab problems: {len(problems)}")
        mod_ch = bot.get_channel(MOD_CHANNEL_ID)
        if mod_ch:
            await mod_ch.send("⚠️ **Seasons tab problems**\n"
                              + "\n".join(f"• {p}" for p in problems[:10]))

    # Initialise Shopify client and pre-cache token + price rule ID
    global _shopify, _etb_price_rule_id
    shopify_secret = os.getenv('SHOPIFY_CLIENT_SECRET')
    if SHOPIFY_CLIENT_ID and shopify_secret:
        _shopify = _ShopifyApi(SHOPIFY_CLIENT_ID, shopify_secret, SHOPIFY_STORE_DOMAIN)
        try:
            await loop.run_in_executor(None, _shopify.prefetch_token)
            _etb_price_rule_id = await loop.run_in_executor(None, _shopify.get_price_rule_id, _ETB_DISCOUNT_CODE)
            print(f"  ✓ Shopify token cached, price rule ID: {_etb_price_rule_id}")
        except Exception as e:
            print(f"  ⚠ Shopify init failed: {e} — /etb-discount Shopify steps will be skipped")
            _shopify = None
    else:
        print(f"  ⚠ SHOPIFY_CLIENT_ID / SHOPIFY_CLIENT_SECRET not set — /etb-discount Shopify steps stubbed")

    # Restore persisted where-to-play message IDs so edits work after restarts
    try:
        raw = state.get('wtp_msg_ids', '')
        if not raw:
            # Fall back to legacy per-index keys for one-time migration
            raw_ids = [
                int(state[f'wtp_msg_{i}']) if state.get(f'wtp_msg_{i}') else None
                for i in range(4)
            ]
        else:
            raw_ids = [int(x) for x in raw.split('|') if x]
        _where_to_play_msg_ids = raw_ids + [None] * (4 - len(raw_ids))
        print(f"  ✓ Restored where-to-play message IDs: {_where_to_play_msg_ids}")
    except Exception as e:
        print(f"  ⚠ Could not restore where-to-play message IDs: {e}")

    # Restore persisted digest message IDs so the daily refresh edits in place
    for key in (spec.state_key for spec in _DIGESTS):
        try:
            raw = state.get(key, '')
            _digest_msg_ids[key] = [int(x) for x in raw.split('|') if x] if raw else []
            print(f"  ✓ Restored {key}: {_digest_msg_ids[key]}")
        except Exception as e:
            print(f"  ⚠ Could not restore {key}: {e}")

    if not keepalive.is_running():
        keepalive.start()
        print(f"  ♻ Keepalive task started")
    if not where_to_play_weekly.is_running():
        where_to_play_weekly.start()
        print(f"  ♻ Where-to-play weekly task started (fires Sundays at {WHERE_TO_PLAY_POST_HOUR_ET}:00 ET)")
    if not event_digests_daily.is_running():
        event_digests_daily.start()
        today = _now_et().date()
        for spec in _DIGESTS:
            state = "active" if spec.active(today) else "idle"
            print(f"  ♻ {spec.label} digest scheduled for "
                  f"{_DIGEST_HOUR_ET}:{spec.minute:02d} ET ({state})")
    if not rph_watcher.is_running():
        rph_watcher.start()
        print(f"  ♻ RPH event watcher started (polls every 15 min)")
    if not season_close_daily.is_running():
        season_close_daily.start()
        print(f"  ♻ Season-close checklist scheduled for {_DIGEST_HOUR_ET}:20 ET")
        # Once at startup too, so a season that ended while the bot was down —
        # or before this shipped — gets its checklist now rather than tomorrow.
        _sc_log("· season-close: startup pass")
        await _season_close_tick()

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
        standing_rows, warnings = await loop.run_in_executor(None, process_event_data, rph_url, thread.id)
        return standing_rows, warnings


async def process_results_reporting_thread(thread: discord.Thread) -> tuple[list[list], list[str]]:
    """
    Validate the thread starter message URL and run the results processing pipeline.
    Returns (standing_rows, warnings).
    Raises ValueError on bad URL, RuntimeError on API/sheet failure.
    """
    # Reporting cutoff — reject threads created after the season's final day,
    # regardless of whether the event itself is valid. Uses the thread's creation
    # time (not "now") so on-time threads can still be retried/reprocessed later.
    if season.is_past_reporting_cutoff(thread.created_at):
        raise ValueError(
            f"Results reporting closed at the end of {season.SEASON_END_DATE}.\n"
            f"This thread was created after that, so it will not be processed."
        )

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
        standing_rows, warnings = await process_results_reporting_thread(thread)

        # ── Success ───────────────────────────────────────────
        description = "Your results have been successfully processed!"
        if warnings:
            description += "\n\n" + "\n".join(warnings)
        await thread.send(
            embed=make_embed(
                title="✅ Results Processed",
                description=description,
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
        standing_rows, warnings = await _run_process_event_data(thread, starter_msg.content.strip())

        description = f"Results successfully processed on retry {attempt}/{RPH_RETRY_ATTEMPTS}!"
        if warnings:
            description += "\n\n" + "\n".join(warnings)
        await thread.send(
            embed=make_embed(
                title="✅ Results Processed",
                description=description,
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


async def _assign_recorded_roles(guild: discord.Guild,
                                 member: discord.Member | None,
                                 role_seasons: dict[int, str],
                                 reason: str) -> tuple[list, list]:
    """
    Grant `member` every rarity role in role_seasons they don't already hold.

    The single implementation of "make Discord match what the registry records".
    /assign-roles-from-registry calls it for every row; /link and the ✅
    fuzzy-confirm call it for one member so a freshly linked player gets their
    roles immediately. Recording what a player earned is a separate step — this
    only applies it.

    Purely additive, matching the rule that rarity never downgrades, which makes
    it idempotent and safe to run repeatedly.

    role_seasons: {role_id: season_str}, as returned by link_player().
    Returns (added, failed) — added is [(role_id, season)], failed [(role_id, err)].
    """
    if not guild or not member or not role_seasons:
        return [], []

    rarity_id_set = set(RARITY_ROLE_IDS)
    current = {r.id for r in member.roles if r.id in rarity_id_set}
    added, failed = [], []

    for role_id, season_label in role_seasons.items():
        if role_id in current:
            continue
        role = guild.get_role(role_id)
        if not role:
            print(f"  ⚠ assign-roles: role {role_id} not found in guild")
            continue
        try:
            await member.add_roles(role, reason=reason)
            added.append((role_id, season_label))
        except discord.HTTPException as e:
            print(f"  ⚠ assign-roles: failed {role_id} for {member.display_name}: {e}")
            failed.append((role_id, str(e)))

    return added, failed


def _fmt_roles(pairs: list) -> str:
    """Render [(role_id, season)] as 'Rare (S7), Uncommon (S8)'."""
    return ", ".join(f"**{RARITY_ROLE_NAMES.get(rid, str(rid))}** ({s})" for rid, s in pairs)


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
            # Apply whatever the registry already records for them.
            added, _failed = await _assign_recorded_roles(
                guild,
                guild.get_member(suggestion['discord_id']) if guild else None,
                role_seasons,
                "fuzzy-link-confirmed",
            )
            if mod_ch:
                roles_str = f"\nRoles assigned: {_fmt_roles(added)}" if added else ""
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

    # ── ETB discount approval ──────────────────────────────────
    elif payload.message_id in _pending_etb_approvals:
        req = _pending_etb_approvals.pop(payload.message_id)
        try:
            user = bot.get_user(req['discord_id']) or await bot.fetch_user(req['discord_id'])
        except Exception:
            user = None

        if emoji == "✅":
            err = await _apply_etb_approval(
                req['discord_id'], req['discord_name'],
                req['playhub_id'], req['rph_username'], req['email'],
                req['count'], req['customer_id'],
            )
            if err:
                if mod_ch:
                    await mod_ch.send(embed=make_embed(
                        title="⚠️ ETB Approval Failed",
                        description=(
                            f"Shopify whitelist failed for <@{req['discord_id']}> "
                            f"(**{req['rph_username']}**).\n`{err}`\n\n"
                            f"Nothing was linked or granted — they'll need to run "
                            f"`/etb-discount` again."
                        ),
                        colour=discord.Colour.red()
                    ))
                return

            dm_ok = False
            if user:
                try:
                    await user.send(_etb_code_message(req['email']))
                    dm_ok = True
                except discord.Forbidden:
                    pass

            if mod_ch:
                await mod_ch.send(embed=make_embed(
                    title="✅ ETB Discount Approved",
                    description=(
                        f"**{req['rph_username']}** (Playhub `{req['playhub_id']}`) "
                        f"→ <@{req['discord_id']}>\n"
                        f"Linked, whitelisted, and "
                        + ("DM'd the code."
                           if dm_ok else
                           "**DM failed** — their DMs are closed, so the code needs "
                           "passing on by hand.")
                    ),
                    colour=discord.Colour.green()
                ))
        else:
            if user:
                try:
                    await user.send(
                        f"❌ Your ETB discount request for **{req['rph_username']}** "
                        f"wasn't approved.\n\n"
                        f"If that's genuinely your Playhub profile, ask a mod to link "
                        f"your Discord account with `/link`, then run `/etb-discount` "
                        f"again."
                    )
                except discord.Forbidden:
                    pass
            if mod_ch:
                await mod_ch.send(embed=make_embed(
                    title="❌ ETB Discount Denied",
                    description=(
                        f"Denied <@{req['discord_id']}>'s claim on "
                        f"**{req['rph_username']}** (Playhub `{req['playhub_id']}`). "
                        f"Nothing was linked or granted."
                    ),
                    colour=discord.Colour.red()
                ))

    # ── Invitational assignment confirmation ───────────────────
    elif payload.message_id in _pending_invitational_assignments:
        assignment = _pending_invitational_assignments.pop(payload.message_id)

        if emoji == "✅":
            # Records only — the Discord roles are granted by
            # /assign-roles-from-registry, which reads columns G–J back out.
            # Unlinked finishers are recorded too, so their role lands as soon
            # as they are linked. prefer_earliest because a backfilled event can
            # predate what is already in the sheet.
            season_label = assignment['season']
            all_candidates = []
            if assignment['legendary']:
                pid, name, member = assignment['legendary']
                all_candidates.append((pid, name, member, LEGENDARY_ROLE_ID, "Legendary"))
            for pid, name, member in assignment['super_rare']:
                all_candidates.append((pid, name, member, SUPER_RARE_ROLE_ID, "Super Rare"))

            earners = [
                (name, {role_id: season_label}, pid or None)
                for pid, name, _member, role_id, _role_name in all_candidates
            ]
            try:
                await loop.run_in_executor(
                    None, lambda: batch_upsert_player_roles(earners, prefer_earliest=True)
                )
            except Exception as e:
                print(f"  ✗ record-legendary-and-super-rare: registry write failed: {e}")
                if mod_ch:
                    await mod_ch.send(embed=make_embed(
                        title="❌ Recording Failed",
                        description=(f"Nothing was written for **{assignment['event_name']}**: `{e}`\n"
                                     f"Re-run the command to retry — recording is idempotent."),
                        colour=discord.Colour.red()
                    ))
                return

            # Marked whether or not any cell changed — see _record_rare_and_uncommon.
            await _season_close_mark(season_label,
                                     invitational=_now_et().date().isoformat(),
                                     invitational_n=len(all_candidates))

            assign_note = "\n\nRun `/assign-roles-from-registry` to grant the Discord roles."
            if assignment.get('assign') and guild:
                try:
                    registry = await loop.run_in_executor(None, get_player_registry)
                    assigned, failed, _gone, _unlinked = await _assign_all_from_registry(guild, registry)
                    assign_note = (f"\n\n{len(assigned)} Discord role(s) granted"
                                   + (f", {len(failed)} failed" if failed else "") + ".")
                except Exception as e:
                    print(f"  ✗ invitational: role assignment failed: {e}")
                    assign_note = (f"\n\n⚠️ Recorded, but granting roles failed: `{e}` — "
                                   f"run `/assign-roles-from-registry`.")

            recorded = [f"**{name}** → {role_name}"
                        + ("" if member else " *(unlinked)*")
                        for _pid, name, member, _rid, role_name in all_candidates]
            unlinked_n = sum(1 for _p, _n, m, _r, _rn in all_candidates if not m)
            if mod_ch:
                await mod_ch.send(embed=make_embed(
                    title=f"🏆 Recorded as {season_label} — {assignment['event_name']}",
                    description="\n".join(recorded)
                                + (f"\n\n{unlinked_n} finisher(s) not yet linked — their role lands on link."
                                   if unlinked_n else "")
                                + assign_note,
                    colour=discord.Colour.gold()
                ))
            await _refresh_season_close_quietly(season_label)
        else:
            if mod_ch:
                await mod_ch.send(embed=make_embed(
                    title="❌ Recording Cancelled",
                    description=f"Nothing was recorded for **{assignment['event_name']}**.",
                    colour=discord.Colour.red()
                ))


# ═══════════════════════════════════════════════════════════════
# SEASON-CLOSE CHECKLIST
# ═══════════════════════════════════════════════════════════════
#
# One mod-channel message per finished season, edited in place, walking the
# end-of-season steps in order: record + assign Rare/Uncommon, record the
# invitational, roll over, archive. Posted the day after the current season's
# end; closed into a one-line summary once every step is done.
#
# A step is ticked by what it produced — the registry, the season pointer, the
# Archive sheet — or by the marker the step writes to Bot State, never by
# remembering which button was pressed. So the slash commands tick it too, and a
# step that got undone un-ticks. The marker is what covers a season whose earners
# all held their roles from an earlier one: earliest-season-wins leaves no trace
# of that season in the registry at all.
#
# State is one Bot State key per season, `season_close:S13`, a JSON object:
#   msg_id, rare_uncommon(+_n), invitational(+_n | 'skipped'), rolled_over,
#   rolled_to, archived, closed — dates as YYYY-MM-DD.
# Buttons are a DynamicItem keyed by custom_id, so they keep working across
# restarts without any in-memory registry of pending prompts.

_SEASON_CLOSE_PREFIX = "season_close:"
_season_close_lock   = asyncio.Lock()   # serialises marker read-modify-writes
_season_close_tick_lock = asyncio.Lock()  # one post/refresh pass at a time
_season_close_busy: set[str] = set()    # seasons with a button action in flight


_season_close_log: list[str] = []      # recent lines, shown by /season-close
_SEASON_CLOSE_RETRY_MINUTES = 10
_season_close_retry_at: datetime | None = None   # set when a post failed


def _sc_log(line: str) -> None:
    """Print a checklist log line and keep it for /season-close, which is how the
    outcome of the startup pass can be seen without the Fly logs."""
    print(f"  {line}")
    _season_close_log.append(f"{_now_et():%b %d %H:%M} {line}")
    del _season_close_log[:-15]


def _season_close_key(season_id: str) -> str:
    return f"{_SEASON_CLOSE_PREFIX}{season_id}"


def _parse_season_close(raw: str) -> dict:
    try:
        entry = json.loads(raw) if raw else {}
    except ValueError:
        return {}
    return entry if isinstance(entry, dict) else {}


def _season_close_entry(season_id: str) -> dict:
    """The season's checklist record ({} if none). Raises if Bot State can't be read."""
    return _parse_season_close(load_bot_state(strict=True).get(_season_close_key(season_id), ''))


async def _season_close_mark(season_id: str, **fields) -> bool:
    """
    Merge fields into a season's checklist record. Returns False on failure.

    Logs rather than raises: every caller has already done the real work (recorded,
    rolled over, archived), and a failed marker must not report that as failed.
    """
    season_id = season_id.strip().upper()
    loop = asyncio.get_running_loop()

    def _write():
        # strict=True: read-then-write — see set_bot_state_key
        state = load_bot_state(strict=True)
        key   = _season_close_key(season_id)
        entry = _parse_season_close(state.get(key, ''))
        entry.update(fields)
        state[key] = json.dumps(entry, separators=(',', ':'))
        save_bot_state(state)
        # save_bot_state logs and swallows its own failures, so confirm the write
        # landed — a checklist posted with an unsaved msg_id is posted twice.
        if load_bot_state(strict=True).get(key) != state[key]:
            raise RuntimeError("Bot State write did not persist")

    async with _season_close_lock:
        try:
            await loop.run_in_executor(None, _write)
            return True
        except Exception as e:
            _sc_log(f"✗ season-close: could not save {fields} for {season_id}: {e}")
            return False


def _season_num(season_id: str) -> int | None:
    m = re.fullmatch(r'S(\d+)', (season_id or '').strip())
    return int(m.group(1)) if m else None


def _short_date(iso: str | None) -> str:
    """'2026-10-08' → 'Oct 8'; anything unparseable comes back as-is."""
    if not iso:
        return "—"
    try:
        d = date.fromisoformat(iso)
    except ValueError:
        return iso
    return f"{d:%b} {d.day}"


def _archive_has_season(season_id: str) -> bool:
    """True if the Archive spreadsheet has `<season> Leaderboard`."""
    from googleapiclient.errors import HttpError as _HttpError
    try:
        _gs.get_values(ARCHIVE_SPREADSHEET_ID, f"{season_id} Leaderboard!A1")
        return True
    except _HttpError as e:
        # A missing tab is "Unable to parse range" — a 400, like every other
        # Sheets complaint, so anything that isn't a 400 is a real failure.
        if getattr(e, 'resp', None) is not None and e.resp.status == 400:
            return False
        raise


def _season_close_snapshot(season_id: str) -> dict:
    """
    Everything the checklist reads from the sheets, in one executor call.
    Raises only if Bot State can't be read — without it the checklist cannot
    tell a fresh season from one it already posted, and would post twice.
    """
    snap = {'entry': _season_close_entry(season_id), 'errors': []}
    entry = snap['entry']

    try:
        snap['registry'] = get_player_registry()
    except Exception as e:
        snap['registry'] = None
        snap['errors'].append(f"Player Registry: {e}")

    snap['earners'] = None
    if not entry.get('rare_uncommon'):
        try:
            snap['earners'] = _rare_uncommon_earners(season_id)
        except Exception as e:
            snap['errors'].append(f"{season_id} Leaderboard: {e}")

    snap['archived_tab'] = None
    if not entry.get('archived'):
        try:
            snap['archived_tab'] = _archive_has_season(season_id)
        except Exception as e:
            snap['errors'].append(f"Archive sheet: {e}")
    return snap


_RARITY_LADDER = ('uncommon', 'rare', 'super_rare', 'legendary')   # low → high
_RARITY_LABEL  = {'uncommon': 'Uncommon', 'rare': 'Rare',
                  'super_rare': 'Super Rare', 'legendary': 'Legendary'}


def _highest(keys: set) -> str:
    """The top rarity among registry keys, or Common for none."""
    return next((_RARITY_LABEL[k] for k in reversed(_RARITY_LADDER) if k in keys), "Common")


def _progression(before: set, new: set) -> str:
    """'Common → Rare', 'Uncommon → Rare (+Uncommon)', or 'Rare (+Uncommon)'."""
    b, a = _highest(before), _highest(before | new)
    extra = sorted((k for k in new if _RARITY_LABEL[k] != a), key=_RARITY_LADDER.index)
    tail  = f" (+{', '.join(_RARITY_LABEL[k] for k in extra)})" if extra else ""
    return f"{b} → {a}{tail}" if a != b else f"{b}{tail}"


def _held_before(reg: dict | None, seq: int) -> set:
    """Registry keys this player already held from a season before `seq`."""
    if not reg:
        return set()
    return {k for k in _RARITY_LADDER if reg[k] and (_season_num(reg[k]) or seq) < seq}


def _season_progression(season_id: str, registry: list[dict], guild: discord.Guild,
                        keys: tuple[str, ...], earners: list[dict] | None = None) -> dict:
    """
    Who moves up a rarity this season, who doesn't, and who isn't on Discord.

    earners given (before recording): reads what each would gain from the
    leaderboard. earners None (after recording): reads it back from the registry,
    where earliest-season-wins means a cell stamped with this season is exactly a
    role first earned in it.

    Returns {'up': [(label, member, missing_keys)], 'unchanged': [names],
             'off': [names with reason], 'missing': n roles a present member lacks}.
    """
    seq      = _season_num(season_id)
    role_for = {key: rid for rid, key in _REGISTRY_ROLE_KEYS}
    by_id    = {r['playhub_id']: r for r in registry if r['playhub_id']}
    by_name  = {r['playhub_name'].lower(): r for r in registry}
    key_for  = {rid: key for rid, key in _REGISTRY_ROLE_KEYS}

    if earners is not None:
        rows = []
        for m in earners:
            reg = (by_id.get(m['id']) if m['id'] else None) or by_name.get(m['name'].lower())
            new = {key_for[r] for r in m['roles']} - _held_before(reg, seq)
            rows.append((m['name'], reg, new))
    else:
        rows = [(r['playhub_name'], r, {k for k in keys if r[k] == season_id})
                for r in registry if any(r[k] == season_id for k in keys)]

    out = {'up': [], 'unchanged': [], 'off': [], 'missing': 0}
    for name, reg, new in rows:
        if not new:
            out['unchanged'].append(name)
            continue
        label  = f"{name}: {_progression(_held_before(reg, seq), new)}"
        member = guild.get_member(reg['discord_id']) if guild and reg and reg['discord_id'] else None
        if not (reg and reg['discord_id']):
            out['off'].append(f"{name} (not linked)")
            continue
        if not member:
            out['off'].append(f"{name} (left the server)")
            continue
        held    = {role.id for role in member.roles}
        missing = {k for k in new if role_for[k] not in held}
        out['missing'] += len(missing)
        out['up'].append((label, member, missing))
    return out


def _progression_lines(prog: dict, pending_only: bool = False, limit: int = 15) -> list[str]:
    """Render _season_progression for the checklist embed."""
    up = [u for u in prog['up'] if u[2]] if pending_only else prog['up']
    lines = []
    if up:
        lines.append(("Still to assign" if pending_only else "⬆️ Moving up") + f" ({len(up)}):")
        for label, member, _missing in up[:limit]:
            lines.append(f"  {member.mention} — {label.split(': ', 1)[1]}")
        if len(up) > limit:
            lines.append(f"  *…and {len(up) - limit} more*")
    if prog['unchanged'] and not pending_only:
        lines.append(f"➖ Already hold theirs ({len(prog['unchanged'])}): {_name_list(prog['unchanged'], 400)}")
    if prog['off']:
        lines.append(f"🔕 Not on Discord ({len(prog['off'])}) — their roles land when linked with "
                     f"`/link`: {_name_list(prog['off'], 400)}")
    return lines


def _name_list(names: list[str], limit: int = 600) -> str:
    out, used = [], 0
    for i, n in enumerate(names):
        if used + len(n) + 2 > limit:
            return ", ".join(out) + f" … +{len(names) - i} more"
        out.append(n)
        used += len(n) + 2
    return ", ".join(out)


class _SeasonCloseButton(discord.ui.DynamicItem[discord.ui.Button],
                         template=r'sc:(?P<season>S\d+):(?P<action>[a-z_]+)'):
    """Every checklist button. Routed by custom_id, so it survives restarts."""

    def __init__(self, season_id: str, action: str, label: str = "…",
                 style: discord.ButtonStyle = discord.ButtonStyle.secondary):
        super().__init__(discord.ui.Button(label=label, style=style,
                                           custom_id=f"sc:{season_id}:{action}"))
        self.season_id = season_id
        self.action    = action

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction,
                             item: discord.ui.Button, match: re.Match, /):
        return cls(match['season'], match['action'], item.label, item.style)

    async def callback(self, interaction: discord.Interaction):
        await _season_close_action(interaction, self.season_id, self.action)


class _InvitationalUrlModal(discord.ui.Modal):
    def __init__(self, season_id: str):
        super().__init__(title=f"{season_id} Invitational", timeout=600)
        self.season_id = season_id
        self.url = discord.ui.TextInput(
            label="RPH event URL or ID",
            placeholder="https://tcg.ravensburgerplay.com/events/123456",
            max_length=200,
        )
        self.add_item(self.url)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        reply = await _post_invitational_preview(
            interaction.guild, self.url.value, self.season_id, assign=True
        )
        await interaction.followup.send(reply, ephemeral=True)


async def _render_season_close(season_id: str, snap: dict, guild: discord.Guild,
                               loop) -> tuple[discord.Embed, discord.ui.View | None, dict | None]:
    """
    Build the checklist message. Returns (embed, view, closing) — closing is the
    summary fields to store once every step is done, else None.
    """
    entry    = snap['entry']
    registry = snap['registry']
    seq      = _season_num(season_id)
    nxt      = f"S{seq + 1}"
    row      = season.get_season(season_id) or {}
    set_name = f" ({row['set_name']})" if row.get('set_name') else ""
    view     = discord.ui.View(timeout=None)
    lines    = []

    def button(action, label, style=discord.ButtonStyle.secondary):
        view.add_item(_SeasonCloseButton(season_id, action, label, style))

    assign_shown = False

    # ── 1. Rare / Uncommon ──────────────────────────────────────────
    stamped1  = [r for r in registry or [] if season_id in (r['rare'], r['uncommon'])]
    recorded1 = bool(entry.get('rare_uncommon') or stamped1)
    n1        = entry.get('rare_uncommon_n', len(stamped1))
    prog1     = (_season_progression(season_id, registry, guild, ('rare', 'uncommon'))
                 if registry is not None and recorded1 else None)
    missing1  = prog1['missing'] if prog1 else 0
    done1     = recorded1 and missing1 == 0

    if recorded1:
        when  = f" {_short_date(entry['rare_uncommon'])}" if entry.get('rare_uncommon') else ""
        moved = len(prog1['up']) + len(prog1['off']) if prog1 else len(stamped1)
        tally = f"{moved} moved up, {max(n1 - moved, 0)} unchanged"
        if done1:
            lines.append(f"**1. ✅ Rare / Uncommon** — recorded{when}: {tally}, roles on Discord")
            if prog1:
                lines += _progression_lines(prog1)
        else:
            lines.append(f"**1. ⚠️ Rare / Uncommon** — recorded{when}: {tally}; "
                         f"**{missing1}** role(s) still to assign")
            lines += _progression_lines(prog1, pending_only=True)
            button('assign', "Assign roles", discord.ButtonStyle.success)
            assign_shown = True
    else:
        earners = snap['earners']
        if earners is None or registry is None:
            what = f"the `{season_id} Leaderboard`" if earners is None else "the Player Registry"
            lines.append(f"**1. ⏳ Rare / Uncommon** — ⚠️ couldn't read {what}")
        elif not earners:
            lines.append(f"**1. ⏳ Rare / Uncommon** — nobody earned Rare or Uncommon. "
                         f"Approving marks {season_id} as recorded.")
        else:
            prog = _season_progression(season_id, registry, guild, ('rare', 'uncommon'), earners)
            lines.append(f"**1. ⏳ Rare / Uncommon** — {len(earners)} player(s) earned roles "
                         f"on the final {season_id} leaderboard")
            lines += _progression_lines(prog)
            if not prog['up'] and not prog['off']:
                lines.append(f"Nothing new to stamp — approving marks {season_id} as recorded.")
        if earners is not None and registry is not None:
            button('record', "Record & assign roles", discord.ButtonStyle.success)
    lines.append("")

    # ── 2. Invitational ─────────────────────────────────────────────
    stamped2  = [r for r in registry or [] if season_id in (r['legendary'], r['super_rare'])]
    inv       = entry.get('invitational')
    recorded2 = bool(inv or stamped2)
    prog2     = (_season_progression(season_id, registry, guild, ('legendary', 'super_rare'))
                 if registry is not None and recorded2 and inv != 'skipped' else None)
    missing2  = prog2['missing'] if prog2 else 0
    done2     = recorded2 and missing2 == 0

    if inv == 'skipped':
        lines.append("**2. ✅ Invitational** — skipped")
    elif recorded2:
        n2    = entry.get('invitational_n', len(stamped2))
        when  = f" {_short_date(inv)}" if inv else ""
        moved = len(prog2['up']) + len(prog2['off']) if prog2 else len(stamped2)
        tally = f"{moved} moved up, {max(n2 - moved, 0)} unchanged"
        if done2:
            lines.append(f"**2. ✅ Invitational** — recorded{when}: {tally}, roles on Discord")
            if prog2:
                lines += _progression_lines(prog2)
        else:
            lines.append(f"**2. ⚠️ Invitational** — recorded{when}: {tally}; "
                         f"**{missing2}** role(s) still to assign")
            lines += _progression_lines(prog2, pending_only=True)
            if not assign_shown:
                button('assign', "Assign roles", discord.ButtonStyle.success)
    else:
        lines.append(f"**2. ⏳ Invitational** — waiting for the {season_id} invitational. "
                     f"Submit its RPH link once it's finished; it doesn't block the rollover.")
        button('invite', "Submit invitational URL", discord.ButtonStyle.primary)
        button('skip_invite', "No invitational")
    lines.append("")

    # ── 3. Rollover ─────────────────────────────────────────────────
    cur_num = _season_num(season.CURRENT_SEASON)
    rolled  = bool(entry.get('rolled_over')) or (cur_num is not None and cur_num > seq)
    nrow    = season.get_season(nxt) or {}
    nname   = f" ({nrow['set_name']})" if nrow.get('set_name') else ""

    if rolled:
        to   = entry.get('rolled_to') or season.CURRENT_SEASON
        when = f" on {_short_date(entry['rolled_over'])}" if entry.get('rolled_over') else ""
        lines.append(f"**3. ✅ Rolled over to {to}**{when}")
    else:
        def d(k):
            return _short_date(nrow.get(k)) if nrow.get(k) else "**blank** ⚠️"
        row_line = (f"{nxt} row: season {d('season_start')} → {d('season_end')} · "
                    f"Set Champs {d('set_champs_start')} → {d('set_champs_end')}"
                    if nrow else f"⚠️ No {nxt} row in the Seasons tab yet.")
        if season.CURRENT_SEASON != season_id:
            lines.append(f"**3. ⚠️ Roll over to {nxt}** — the current season is "
                         f"{season.CURRENT_SEASON}, not {season_id}; nothing to roll over from here.")
        elif not recorded1:
            lines.append(f"**3. 🔒 Roll over to {nxt}{nname}** — needs step 1 first.")
            lines.append(row_line)
        else:
            plan, err = await _plan_rollover(loop, nxt)
            if plan:
                r = plan.resolved
                lines.append(f"**3. ⏳ Roll over to {nxt}{nname}** — ready")
                lines.append(f"Pointer: {season_id} → {nxt}")
                lines.append(f"Season: {_short_date(r['season_start'])} → {_short_date(r['season_end'])}"
                             f" · Set Champs: {_short_date(r['set_champs_start'])} → "
                             f"{_short_date(r['set_champs_end'])}")
                lines.append("Creates: " + ", ".join(f"{nxt} {t}" for t in _SEASON_TAB_SUFFIXES))
                lines.extend(plan.warnings)
                button('rollover', f"Roll over to {nxt}", discord.ButtonStyle.danger)
            else:
                lines.append(f"**3. ⏳ Roll over to {nxt}{nname}** — not ready")
                lines.append(row_line)
                lines.append("Fill the blanks in the Seasons tab, then press **Reload**.")
        view.add_item(discord.ui.Button(
            label="Seasons tab", style=discord.ButtonStyle.link,
            url=f"https://docs.google.com/spreadsheets/d/{BOT_DATABASE_SPREADSHEET_ID}/edit"))
        button('reload', "Reload")
    lines.append("")

    # ── 4. Archive ──────────────────────────────────────────────────
    archived = entry.get('archived') or (_now_et().date().isoformat() if snap['archived_tab'] else None)
    if archived:
        when = f" on {_short_date(entry['archived'])}" if entry.get('archived') else ""
        lines.append(f"**4. ✅ Archived {season_id}**{when}")
    elif not rolled:
        lines.append(f"**4. 🔒 Archive {season_id}** — unlocks after the rollover")
    else:
        lines.append(f"**4. ⏳ Archive {season_id}** — copies the {season_id} tabs to the Archive sheet")
        button('archive', f"Archive {season_id}", discord.ButtonStyle.success)

    if snap['errors']:
        lines += ["", "⚠️ **Couldn't read:** " + "; ".join(snap['errors'])[:500]]

    now = _now_et()
    if done1 and done2 and rolled and archived:
        today = now.date().isoformat()
        inv_s = "skipped" if inv == 'skipped' else str(entry.get('invitational_n', len(stamped2)))
        summary = (f"Rare/Uncommon: {n1} · Invitational: {inv_s} · "
                   f"Rolled to {entry.get('rolled_to') or season.CURRENT_SEASON}"
                   f"{' ' + _short_date(entry['rolled_over']) if entry.get('rolled_over') else ''} · "
                   f"Archived {_short_date(entry.get('archived') or today)}")
        embed = make_embed(title=f"🏁 {season_id} closed — {_short_date(today)}",
                           description=summary, colour=discord.Colour.green())
        return embed, None, {'closed': today, 'summary': summary}

    header = (f"Ended {_short_date(row.get('season_end'))} · "
              f"refreshed {_short_date(now.date().isoformat())}, "
              f"{now.hour % 12 or 12}:{now:%M %p} ET")
    embed = make_embed(
        title=f"🏁 Season {seq}{set_name} is over",
        description=(header + "\n\n" + "\n".join(lines))[:4000],
        colour=discord.Colour.blurple(),
    )
    return embed, view, None


async def _refresh_season_close(season_id: str, post_if_missing: bool = False) -> None:
    """
    Re-render a season's checklist in place. post_if_missing posts it when there
    is no message yet (or it was deleted). Raises if Bot State can't be read.
    """
    guild  = bot.get_guild(int(DISCORD_GUILD_ID)) or (bot.guilds[0] if bot.guilds else None)
    mod_ch = guild.get_channel(MOD_CHANNEL_ID) if guild else None
    if not mod_ch:
        _sc_log("⚠ season-close: mod channel not found")
        return

    loop  = asyncio.get_running_loop()
    snap  = await loop.run_in_executor(None, _season_close_snapshot, season_id)
    entry = snap['entry']
    if entry.get('closed'):
        return
    msg_id = int(entry['msg_id']) if entry.get('msg_id') else None
    if not msg_id and not post_if_missing:
        return

    embed, view, closing = await _render_season_close(season_id, snap, guild, loop)

    msg = None
    if msg_id:
        try:
            msg = await mod_ch.fetch_message(msg_id)
            await msg.edit(embed=embed, view=view)
        except discord.NotFound:
            msg = None
            _sc_log(f"⚠ season-close: {season_id} checklist message {msg_id} is gone — reposting")

    if msg is None:
        msg = await mod_ch.send(embed=embed, view=view)
        if not await _season_close_mark(season_id, msg_id=str(msg.id)):
            # Unsaved, tomorrow's pass would post a second copy — take this one back.
            await msg.delete()
            return
        _sc_log(f"✓ season-close: posted the {season_id} checklist")

    if closing:
        await _season_close_mark(season_id, **closing)
        _sc_log(f"✓ season-close: {season_id} closed")


async def _refresh_season_close_quietly(season_id: str) -> None:
    """Refresh after a slash command or reaction did a step. Never raises."""
    try:
        await _refresh_season_close(season_id)
    except Exception as e:
        _sc_log(f"⚠ season-close: refresh of {season_id} failed: {e}")


async def _refresh_open_season_closes() -> None:
    """Refresh every open checklist — for steps that aren't tied to one season."""
    loop = asyncio.get_running_loop()
    try:
        state = await loop.run_in_executor(None, lambda: load_bot_state(strict=True))
    except Exception as e:
        _sc_log(f"⚠ season-close: could not read Bot State: {e}")
        return
    for key, raw in state.items():
        if key.startswith(_SEASON_CLOSE_PREFIX) and not _parse_season_close(raw).get('closed'):
            await _refresh_season_close_quietly(key.removeprefix(_SEASON_CLOSE_PREFIX))


async def _season_close_tick() -> None:
    """
    Daily pass: refresh open checklists, and post one for the current season once
    its season end has passed. Never raises — it runs inside a tasks.loop.
    """
    global _season_close_retry_at
    async with _season_close_tick_lock:
        loop = asyncio.get_running_loop()
        try:
            state = await loop.run_in_executor(None, lambda: load_bot_state(strict=True))
        except Exception as e:
            # Without Bot State a posted checklist looks unposted — skip, don't duplicate.
            _sc_log(f"⚠ season-close: could not read Bot State ({e}) — skipping")
            return

        seen = set()
        for key, raw in state.items():
            if key.startswith(_SEASON_CLOSE_PREFIX):
                entry = _parse_season_close(raw)
                if entry.get('msg_id') and not entry.get('closed'):
                    sid = key.removeprefix(_SEASON_CLOSE_PREFIX)
                    seen.add(sid)
                    await _refresh_season_close_quietly(sid)

        cur, end = season.CURRENT_SEASON, season.SEASON_END_DATE
        if cur in seen:
            _sc_log(f"· season-close: refreshed open checklist(s): {', '.join(sorted(seen))}")
        elif not (cur and end):
            _sc_log(f"· season-close: {cur} has no season end in the Seasons tab — nothing to post")
        elif _now_et().date() <= date.fromisoformat(end):
            _sc_log(f"· season-close: {cur} runs to {end} — checklist posts the day after")
        elif _parse_season_close(state.get(_season_close_key(cur), '')).get('closed'):
            _sc_log(f"· season-close: {cur} is already closed")
        else:
            try:
                await _refresh_season_close(cur, post_if_missing=True)
            except Exception as e:
                # Usually a Sheets read timing out in the startup rush. Waiting for
                # tomorrow's pass would leave the season with no checklist all day.
                _season_close_retry_at = _now_et() + timedelta(minutes=_SEASON_CLOSE_RETRY_MINUTES)
                _sc_log(f"⚠ season-close: could not post the {cur} checklist: "
                        f"{type(e).__name__}: {e} — retrying at {_season_close_retry_at:%H:%M}")
                print(traceback.format_exc())
                return
        _season_close_retry_at = None


@tasks.loop(minutes=1)
async def season_close_daily():
    """
    Run _season_close_tick once a day, after the digests have had their minutes —
    and again whenever a failed post scheduled a retry.
    """
    now_et = _now_et()
    retry_due = _season_close_retry_at is not None and now_et >= _season_close_retry_at
    if retry_due or (now_et.hour == _DIGEST_HOUR_ET and now_et.minute == 20):
        await _season_close_tick()


# ── Button actions ──────────────────────────────────────────────────

async def _sc_record(interaction, season_id, loop) -> str:
    result = await _record_rare_and_uncommon(interaction.guild, season_id, loop)
    registry = await loop.run_in_executor(None, get_player_registry)
    assigned, failed, _gone, _unlinked = await _assign_all_from_registry(interaction.guild, registry)
    msg = (f"✅ Recorded {result['recorded']} player(s) for {season_id}"
           if result['recorded'] else f"✅ Nobody earned roles in {season_id} — marked as recorded")
    if result['unlinked']:
        msg += f", {len(result['unlinked'])} not yet linked (their roles land on link)"
    msg += f". {len(assigned)} Discord role(s) granted"
    return msg + (f", {len(failed)} failed." if failed else ".")


async def _sc_assign(interaction, season_id, loop) -> str:
    registry = await loop.run_in_executor(None, get_player_registry)
    assigned, failed, gone, _unlinked = await _assign_all_from_registry(interaction.guild, registry)
    msg = f"✅ {len(assigned)} Discord role(s) granted"
    if failed:
        msg += f", {len(failed)} failed"
    if gone:
        msg += f", {len(gone)} linked player(s) no longer in the server"
    return msg + "."


async def _sc_skip_invite(interaction, season_id, loop) -> str:
    await _season_close_mark(season_id, invitational='skipped')
    return f"✅ Marked {season_id} as having no invitational."


async def _sc_reload(interaction, season_id, loop) -> str:
    _, problems = await _reload_season(loop)
    return "🔄 Reloaded the Seasons tab." + (
        "\n" + "\n".join(f"• {p}" for p in problems[:5]) if problems else "")


async def _sc_rollover(interaction, season_id, loop) -> str:
    if season.CURRENT_SEASON != season_id:
        return f"⚠️ The current season is {season.CURRENT_SEASON}, not {season_id} — nothing rolled over."
    nxt = f"S{_season_num(season_id) + 1}"
    # Checked again at click time: the preview may be days old.
    plan, err = await _plan_rollover(loop, nxt)
    if not plan:
        return err
    ok, detail = await _outgoing_roles_recorded(loop, season_id)
    if not ok:
        return detail
    try:
        created = await _execute_rollover(loop, plan)
    except RuntimeError as e:
        return str(e)
    r = plan.resolved
    return (f"✅ **Rolled over to {nxt}.** Season {r['season_start']} → {r['season_end']}, "
            f"Set Champs {r['set_champs_start']} → {r['set_champs_end']}.\n"
            f"Tabs created: {', '.join(created) or 'none (all existed)'}"
            + "".join(f"\n\n{w}" for w in plan.warnings))


async def _sc_archive(interaction, season_id, loop) -> str:
    archived = await loop.run_in_executor(None, archive_season_data, season_id)
    if not archived:
        return f"⚠️ Nothing was archived for {season_id} — all tabs were empty or missing."
    await _season_close_mark(season_id, archived=_now_et().date().isoformat())
    return f"✅ Archived {len(archived)} tab(s): {', '.join(archived)}."


_SEASON_CLOSE_ACTIONS = {
    'record':      _sc_record,
    'assign':      _sc_assign,
    'skip_invite': _sc_skip_invite,
    'reload':      _sc_reload,
    'rollover':    _sc_rollover,
    'archive':     _sc_archive,
}


async def _season_close_action(interaction: discord.Interaction, season_id: str, action: str) -> None:
    if interaction.user.id not in ADMIN_USER_IDS:
        await interaction.response.send_message("⚠️ Admins only.", ephemeral=True)
        return
    if action == 'invite':
        await interaction.response.send_modal(_InvitationalUrlModal(season_id))
        return
    handler = _SEASON_CLOSE_ACTIONS.get(action)
    if not handler:
        await interaction.response.send_message(f"⚠️ Unknown action `{action}`.", ephemeral=True)
        return
    # One action per season at a time — a double-click must not record or roll twice.
    if season_id in _season_close_busy:
        await interaction.response.send_message("⏳ Still working on the last click.", ephemeral=True)
        return

    _season_close_busy.add(season_id)
    try:
        await interaction.response.defer(ephemeral=True, thinking=True)
        loop = asyncio.get_running_loop()
        try:
            reply = await handler(interaction, season_id, loop)
        except Exception as e:
            print(f"  ✗ season-close {season_id}:{action} failed:\n{traceback.format_exc()}")
            reply = f"❌ `{action}` failed: `{type(e).__name__}: {e}`"
        await _refresh_season_close_quietly(season_id)
    finally:
        _season_close_busy.discard(season_id)
    await interaction.followup.send(reply[:1990], ephemeral=True)


# ═══════════════════════════════════════════════════════════════
# SLASH COMMANDS
# ═══════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════
# ETB DISCOUNT — /etb-discount
# ═══════════════════════════════════════════════════════════════

_ETB_DISCOUNT_CODE    = "ETBGTALORCANA"
_ETB_DISCOUNT_MIN_EVENTS = 3


def _etb_code_message(email: str) -> str:
    """The approval DM. Shared by the instant and mod-confirmed paths."""
    return (
        f"✅ **You're approved for the ETB GTA Lorcana discount!**\n\n"
        f"Discount code: `{_ETB_DISCOUNT_CODE}`\n"
        f"Shop: enterthebattlefield.ca\n\n"
        f"Your account ({email}) has been activated.\n"
        f"The code will work at checkout on your next visit."
    )


async def _apply_etb_approval(discord_id: int, discord_display_name: str,
                              playhub_id: str, rph_username: str, email: str,
                              count: int, customer_id) -> str | None:
    """
    The granting half of /etb-discount: Shopify whitelist, approval row,
    registry link.

    Split out so the instant path (caller already linked, so their Playhub ID is
    authoritative) and the mod-confirmed path (caller was unlinked, so a human
    vouched for the identity) share one implementation.

    Returns an error string if the Shopify whitelist failed, else None. The
    approval-row and registry writes are best-effort and only logged — the
    discount is already live in Shopify by then, so failing the caller would be
    a lie.
    """
    loop = asyncio.get_running_loop()

    if _shopify and _etb_price_rule_id and customer_id is not None:
        try:
            await loop.run_in_executor(
                None, _shopify.add_to_whitelist, _etb_price_rule_id, customer_id
            )
        except Exception as e:
            print(f"  ✗ /etb-discount add_to_whitelist failed for {rph_username}: {e}")
            return str(e)

    try:
        await loop.run_in_executor(
            None, append_etb_approval,
            str(discord_id), playhub_id, rph_username, email,
            datetime.now(timezone.utc).isoformat(), count,
        )
    except Exception as e:
        print(f"  ✗ /etb-discount ETB approval write failed for discord_id={discord_id}: {e}")

    try:
        await loop.run_in_executor(
            None, link_player,
            int(discord_id), discord_display_name, "etb-discount",
            playhub_id, rph_username,
        )
        print(f"  ✓ /etb-discount: linked {rph_username} (playhub_id={playhub_id}) → discord {discord_id}")
    except Exception as e:
        print(f"  ⚠ /etb-discount: Player Registry link failed for {rph_username}: {e}")

    return None


async def _post_etb_approval_request(interaction: discord.Interaction,
                                     playhub_id: str, rph_username: str,
                                     email: str, count: int, customer_id) -> bool:
    """
    Ask the mods to vouch for an unlinked caller before anything is granted.

    An unlinked caller offers nothing but a typed name, and RPH display names are
    public — so the name alone must not buy a discount or, worse, a registry link
    that would hand them another player's earned roles. Attendance is already
    verified by the time we get here; only the granting waits.

    Returns True if the prompt was posted.
    """
    guild  = interaction.guild or bot.get_guild(int(DISCORD_GUILD_ID))
    mod_ch = get_channel_by_id(guild, MOD_CHANNEL_ID) if guild and MOD_CHANNEL_ID else None
    if not mod_ch:
        print("  ⚠ /etb-discount: mod channel unavailable — cannot request approval")
        return False

    # How closely the caller's own Discord name resembles the name they claim.
    # Not a decision, just the first thing a mod would check by eye anyway.
    _member, score = fuzzy_match_member(rph_username, [interaction.user])

    msg = await mod_ch.send(embed=make_embed(
        title="🔐 ETB Discount — Identity Check",
        description=(
            f"**Discord:** {interaction.user.mention} (`{interaction.user.display_name}`)\n"
            f"**Claims to be:** {rph_username} (Playhub `{playhub_id}`)\n"
            f"**Events this season:** {count}\n"
            f"**Email:** {email}\n"
            f"**Name similarity:** {score:.0%}\n\n"
            f"This Discord account isn't linked to a Playhub ID yet. Approving "
            f"links it **and** grants the discount — so the linked player's "
            f"earned roles become theirs.\n\n"
            f"React ✅ to approve or ❌ to deny."
        ),
        colour=discord.Colour.yellow(),
    ))
    await msg.add_reaction("✅")
    await msg.add_reaction("❌")

    _pending_etb_approvals[msg.id] = {
        'discord_id':   interaction.user.id,
        'discord_name': interaction.user.display_name,
        'playhub_id':   playhub_id,
        'rph_username': rph_username,
        'email':        email,
        'count':        count,
        'customer_id':  customer_id,
    }
    return True


@tree.command(name="etb-discount", description="Unlock the Enter the Battlefield community discount by verifying your GTA Lorcana event attendance")
@app_commands.describe(
    rph_username="Your RPH display name (as shown on tcg.ravensburgerplay.com)",
    email="Your email address registered at enterthebattlefield.ca",
)
async def etb_discount(interaction: discord.Interaction, rph_username: str, email: str):
    await interaction.response.defer(ephemeral=True)
    loop     = asyncio.get_running_loop()
    discord_id = str(interaction.user.id)

    # ── Step 1: Resolve identity to a Playhub ID ─────────────────────────────────
    #
    # The typed name is only a hint. If this Discord account is already linked,
    # its Playhub ID wins and the name is ignored outright — a linked caller then
    # cannot claim someone else's attendance record no matter what they type.
    registry = []
    known_id = None
    try:
        registry = await loop.run_in_executor(None, get_player_registry)
        known_id = next(
            (e['playhub_id'] for e in registry
             if e['discord_id'] == interaction.user.id and e['playhub_id']),
            None,
        )
    except Exception as e:
        # Non-fatal — fall back to resolving by name below.
        print(f"  ⚠ /etb-discount registry lookup failed, falling back to name: {e}")

    try:
        lookup = await loop.run_in_executor(
            None, lookup_player_standings, rph_username, known_id
        )
    except Exception as e:
        print(f"  ✗ /etb-discount standings lookup failed: {e}")
        await interaction.followup.send(
            "⚠️ Couldn't reach the standings sheet right now — please try again in a moment.",
            ephemeral=True,
        )
        return

    # Two players have competed under this name — refuse rather than pick one.
    if len(lookup['candidate_ids']) > 1:
        print(f"  ⚠ /etb-discount ambiguous name {rph_username!r} → {sorted(lookup['candidate_ids'])}")
        await interaction.followup.send(
            f"⚠️ More than one player has competed under the name **{rph_username}** "
            f"this season, so we can't tell which record is yours.\n"
            f"Please ask a mod to link your account with `/link` and try again.",
            ephemeral=True,
        )
        return

    playhub_id   = lookup['playhub_id']
    count        = lookup['count']
    # Canonical name from standings beats whatever was typed.
    rph_username = lookup['display_name'] or rph_username

    # This Playhub ID already belongs to someone else's Discord account.
    owner = next(
        (e for e in registry
         if e['playhub_id'] and e['playhub_id'] == playhub_id and e['discord_id']),
        None,
    )
    if owner and owner['discord_id'] != interaction.user.id:
        print(f"  ⚠ /etb-discount: {interaction.user} claimed playhub_id={playhub_id} "
              f"owned by discord {owner['discord_id']}")
        try:
            ryan = await bot.fetch_user(ADMIN_USER_IDS[0])
            await ryan.send(
                f"⚠️ /etb-discount ownership conflict — {interaction.user} "
                f"(`{interaction.user.id}`) claimed Playhub `{playhub_id}` "
                f"(**{rph_username}**), already linked to <@{owner['discord_id']}>."
            )
        except Exception:
            pass
        await interaction.followup.send(
            f"⚠️ **{rph_username}** is already linked to a different Discord account.\n"
            f"If that's you, ask a mod to sort it out — we've flagged it for review.",
            ephemeral=True,
        )
        return

    # Nothing matched the name at all — almost always a typo, so say so rather
    # than reporting "0 events" and leaving them to guess.
    if not playhub_id:
        await interaction.followup.send(
            f"❌ We couldn't find a player named **{rph_username}** in "
            f"{season.CURRENT_SEASON} results.\n"
            f"Check the spelling against your name on tcg.ravensburgerplay.com — "
            f"it has to match exactly.",
            ephemeral=True,
        )
        return

    if count < _ETB_DISCOUNT_MIN_EVENTS:
        await interaction.followup.send(
            f'❌ You need at least {_ETB_DISCOUNT_MIN_EVENTS} GTA Lorcana events this season to qualify.\n'
            f'We found {count} event(s) on your record for {season.CURRENT_SEASON}.\n'
            f'Keep playing and try again after your next event!',
            ephemeral=True,
        )
        return

    # ── Step 3: Already approved check (ETB Approvals sheet) ──
    try:
        existing = await loop.run_in_executor(None, get_etb_approval, discord_id)
    except Exception as e:
        print(f"  ✗ /etb-discount ETB approval lookup failed: {e}")
        await interaction.followup.send(
            "⚠️ Couldn't reach the approvals sheet right now — please try again in a moment.",
            ephemeral=True,
        )
        return

    if existing:
        approved_dt  = datetime.fromisoformat(existing['approved_at'])
        approved_str = approved_dt.strftime('%b %-d, %Y')
        await interaction.followup.send(
            f"You're already approved! 🎉\n"
            f"Use code `{_ETB_DISCOUNT_CODE}` at enterthebattlefield.ca\n"
            f"(Approved on {approved_str})",
            ephemeral=True,
        )
        return

    # ── Steps 4 & 5: Shopify customer lookup + whitelist check ─
    customer = None

    if _shopify and _etb_price_rule_id:
        try:
            customer = await loop.run_in_executor(None, _shopify.lookup_customer_by_email, email)
        except Exception as e:
            print(f"  ✗ /etb-discount Shopify lookup failed: {e}")
            await interaction.followup.send(
                "⚠️ Couldn't reach the Shopify API right now — please try again in a moment.",
                ephemeral=True,
            )
            return

        if customer is None:
            await interaction.followup.send(
                f'❌ That email isn\'t registered at enterthebattlefield.ca.\n'
                f'Create an account at enterthebattlefield.ca first, then run /etb-discount again.',
                ephemeral=True,
            )
            return

        # Step 5: already whitelisted in Shopify — recover Bot State and confirm
        try:
            already = await loop.run_in_executor(
                None, _shopify.is_whitelisted, _etb_price_rule_id, customer['id']
            )
        except Exception as e:
            print(f"  ✗ /etb-discount whitelist check failed: {e}")
            already = False  # safe to proceed — worst case we add them again (no-op)

        if already:
            # Recover the missing Bot State row — but only for a linked caller,
            # whose Playhub ID is authoritative. From an unlinked one the name is
            # still just a claim, and this row is the audit trail. The whitelist
            # is theirs either way: it was found by their own email, not the name.
            if known_id:
                try:
                    await loop.run_in_executor(
                        None, append_etb_approval,
                        discord_id, playhub_id, rph_username, email,
                        datetime.now(timezone.utc).isoformat(), count,
                    )
                except Exception as e:
                    print(f"  ✗ /etb-discount ETB approval write failed (recovery): {e}")
            await interaction.followup.send(
                f"You're already approved! 🎉\n"
                f"Use code `{_ETB_DISCOUNT_CODE}` at enterthebattlefield.ca",
                ephemeral=True,
            )
            return
    else:
        print(f"  ⚠ /etb-discount: Shopify not configured — skipping Steps 4–6 for {rph_username}")

    # ── Identity gate: unlinked callers need a mod to vouch ────
    #
    # Everything above only reads — attendance, Shopify account, prior approval
    # — so nothing has been granted yet. A caller already bound to a Playhub ID
    # proved that identity when they were linked, so they carry straight on.
    # Anyone else has offered nothing but a public display name, and that must
    # buy neither the discount nor a registry link.
    if not known_id:
        if any(r['discord_id'] == interaction.user.id for r in _pending_etb_approvals.values()):
            await interaction.followup.send(
                "🕓 You already have a request waiting on a mod — hang tight, "
                "you'll get a DM as soon as it's reviewed.",
                ephemeral=True,
            )
            return

        posted = await _post_etb_approval_request(
            interaction, playhub_id, rph_username, email, count,
            customer['id'] if customer else None,
        )
        if posted:
            await interaction.followup.send(
                f"🔎 We found **{count}** {season.CURRENT_SEASON} events for "
                f"**{rph_username}** — nice work.\n\n"
                f"Your Discord account isn't linked to a Playhub profile yet, so a "
                f"mod needs to confirm it's you. You'll get a DM with the code as "
                f"soon as they do.",
                ephemeral=True,
            )
        else:
            await interaction.followup.send(
                "⚠️ Couldn't reach the mods right now — please try again in a moment.",
                ephemeral=True,
            )
        return

    # ── Steps 6 & 7: Whitelist, record approval, refresh the link ─
    err = await _apply_etb_approval(
        interaction.user.id, interaction.user.display_name,
        playhub_id, rph_username, email, count,
        customer['id'] if customer else None,
    )
    if err:
        try:
            ryan = await bot.fetch_user(ADMIN_USER_IDS[0])
            await ryan.send(
                f"⚠️ /etb-discount Shopify whitelist failed for {interaction.user} "
                f"(rph: {rph_username})\nError: {err}"
            )
        except Exception:
            pass
        await interaction.followup.send(
            "⚠️ Something went wrong on our end — Ryan has been notified and will\n"
            "approve you manually shortly. Sorry for the inconvenience!",
            ephemeral=True,
        )
        return

    try:
        await interaction.user.send(_etb_code_message(email))
        await interaction.followup.send(
            "✅ You're approved! Check your DMs for the discount code.",
            ephemeral=True,
        )
    except discord.Forbidden:
        # DMs disabled — send the code ephemerally instead
        await interaction.followup.send(_etb_code_message(email), ephemeral=True)


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
            date_str = datetime.strptime(e["date"], "%Y-%m-%d").strftime("%a %b %d").replace(" 0", " ")
            name = e.get("name", "Unnamed Event")
            location = e.get("location", "TBA")
            url = e.get("url", "")

            value = f"{icon} {e.get('type', '')} · {location}"
            if url:
                value += f"\n[RSVP here]({url})"

            embed.add_field(name=f"**{date_str}** — {name}", value=value, inline=False)

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

    # Load existing recheck guard keys once up front (both modes): startup uses
    # them to skip crash-looping threads; manual recheck uses them to clear any
    # lingering guard key on success.
    loop = asyncio.get_running_loop()
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
            # If the guard cannot be written, skip the thread: processing it
            # unguarded is what the guard exists to prevent.
            try:
                await loop.run_in_executor(None, set_bot_state_key, state_key, '1')
            except Exception as e:
                print(f"  ⚠ Startup recheck: could not set the crash-loop guard "
                      f"for '{thread.name}': {e} — skipping this thread")
                continue

        print(f"  🔄 {'Startup recheck' if startup else 'Rechecking'} missed thread: '{thread.name}'")
        await thread.join()
        success = await run_results_reporting_pipeline(thread, starter_msg, is_retry=False)

        if success and (startup or state_key in attempted_keys):
            # Completed cleanly — remove the guard key so it doesn't linger.
            # startup: clears the key we just set. manual: clears a stale key
            # left by a prior failed startup attempt (only when one exists, so
            # ordinary threads don't trigger a needless Bot State write).
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
@tree.command(name="link", description="Link a Discord member to a Playhub player (mods only)")
@app_commands.describe(member="Discord member",
                       identifier="Playhub ID (preferred) — or a display name, which must match exactly one player")
async def link_command(interaction: discord.Interaction, member: discord.Member, identifier: str):
    if not _is_admin(interaction):
        await interaction.response.send_message("⚠️ Mods only.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)
    loop = asyncio.get_running_loop()

    # Strip surrounding quotes in case the name was wrapped in them.
    identifier = identifier.strip().strip('"').strip("'")

    registry = await loop.run_in_executor(None, get_player_registry)

    # ── Resolve the identifier to a Playhub ID ───────────────────────────────
    #
    # The ID is what the link is keyed on; a name is only a lookup convenience
    # and must resolve to exactly one player. Previously an unmatched name fell
    # through to creating a fresh row with no Playhub ID — 14 of the registry's
    # ID-less linked rows came in that way, and none of them can ever be matched
    # by ID afterwards. A name that resolves to nothing is now refused.
    playhub_name = None
    if identifier.isdigit():
        playhub_id = identifier
        # A bare ID is trusted even if unseen — mods read these straight off RPH
        # for a player who has not appeared in standings yet. Prefer the current
        # standings name so linking by ID also carries a rename through to the
        # registry; fall back to whatever the row already holds.
        try:
            found = await loop.run_in_executor(
                None, lookup_player_standings, None, playhub_id
            )
            playhub_name = found['display_name']
        except Exception as e:
            print(f"  ⚠ /link: standings lookup failed for id {playhub_id}: {e}")
        if not playhub_name:
            known = next((e for e in registry if e['playhub_id'] == playhub_id), None)
            playhub_name = (known['playhub_name'] or None) if known else None
    else:
        candidates: dict[str, str] = {}   # playhub_id -> best-known name
        for e in registry:
            if e['playhub_id'] and e['playhub_name'].lower() == identifier.lower():
                candidates[e['playhub_id']] = e['playhub_name']
        try:
            found = await loop.run_in_executor(
                None, lookup_player_standings, identifier, None
            )
            for pid in found['candidate_ids']:
                candidates.setdefault(pid, found['display_name'] or identifier)
        except Exception as e:
            print(f"  ⚠ /link: standings lookup failed for {identifier!r}: {e}")

        if not candidates:
            await interaction.followup.send(
                f"❌ No player found matching **{identifier}**.\n"
                f"Check the spelling against tcg.ravensburgerplay.com, or pass the "
                f"numeric Playhub ID instead — the mod-channel suggestions include it.",
                ephemeral=True,
            )
            return
        if len(candidates) > 1:
            listed = "\n".join(f"• `{pid}` — {nm}" for pid, nm in sorted(candidates.items()))
            await interaction.followup.send(
                f"⚠️ **{identifier}** matches more than one Playhub player:\n{listed}\n\n"
                f"Re-run `/link` with the correct ID.",
                ephemeral=True,
            )
            return

        playhub_id, playhub_name = next(iter(candidates.items()))

    # ── Refuse if that Playhub ID belongs to a different Discord account ─────
    # Linking a second Playhub ID to the same member is still allowed.
    owner = next(
        (e for e in registry
         if e['playhub_id'] == playhub_id and e['discord_id'] and e['discord_id'] != member.id),
        None,
    )
    if owner:
        await interaction.followup.send(
            f"⚠️ Playhub ID `{playhub_id}` is already linked to <@{owner['discord_id']}>.",
            ephemeral=True
        )
        return

    role_seasons = await loop.run_in_executor(
        None, link_player,
        member.id, member.display_name,
        f'manual:{interaction.user.display_name}',
        playhub_id, playhub_name,
    )

    # Apply whatever the registry already records for them.
    added, _failed = await _assign_recorded_roles(
        interaction.guild, member, role_seasons, "link-command"
    )

    # Always a resolved ID now; the name is shown alongside when we know it.
    id_str = f"ID `{playhub_id}`" + (f" (**{playhub_name}**)" if playhub_name else "")
    roles_str = f"\nRoles assigned: {_fmt_roles(added)}" if added else ""
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
                + (f"\nRoles assigned: {_fmt_roles(added)}" if added else "")
            ),
            colour=discord.Colour.green()
        ))


# ── /record-rare-and-uncommon ─────────────────────────────────
#
# Records only — it writes the season into registry columns I and J and stops
# there. Granting the Discord roles is /assign-roles-from-registry, so the
# registry stays the single source of truth for who has earned what.
def _rare_uncommon_earners(season_id: str) -> list[dict]:
    """
    Rare/Uncommon earners from a season's leaderboard: [{'id', 'name', 'roles'}].

    Reads `<season_id> Leaderboard` by name rather than season.LEADERBOARD_RANGE_NAME,
    so the season-close checklist can preview a season that is no longer current.
    """
    lb_data = _gs.get_values(LEAGUE_SPREADSHEET_ID, f"{season_id} Leaderboard!A2:E")
    leaderboard_rows = lb_data.get('values', [])

    # Layout: A=rank, B=Player ID, C=Name, D=Points, E=Events Attended.
    # Player ID is the stable key — RPH display names change over time, so we
    # match players to the registry by ID (name only as a fallback).
    earners_meta = []
    seen = set()
    for row in leaderboard_rows:
        if len(row) < 3:
            continue
        try:
            rank = int(row[0])
        except (ValueError, IndexError):
            continue
        playhub_id  = row[1].strip() if len(row) > 1 else ''
        player_name = row[2].strip() if len(row) > 2 else ''
        try:
            events_played = int(row[4]) if len(row) > 4 and row[4] else 0
        except ValueError:
            events_played = 0
        earned = compute_earned_roles(rank, events_played)
        if not earned:
            continue
        key = playhub_id or player_name.lower()   # collapse duplicate rows (e.g. a mid-season rename)
        if key in seen:
            continue
        seen.add(key)
        earners_meta.append({
            'id':    playhub_id,
            'name':  player_name,
            'roles': {r: season_id for r in earned},
        })
    return earners_meta


async def _record_rare_and_uncommon(guild: discord.Guild, season_id: str, loop) -> dict:
    """
    Record a season's Rare/Uncommon earners into the registry and mark the season
    recorded. Shared by the slash command and the season-close checklist.

    Records only — granting the Discord roles is _assign_all_from_registry.
    Returns {'recorded': n, 'unlinked': [names], 'merged': n}. Raises on a failed
    registry write, and nothing is marked in that case.
    """
    earners_meta = await loop.run_in_executor(None, _rare_uncommon_earners, season_id)

    if earners_meta:
        # Batch-upsert all role changes in one registry read + one API write.
        # Pass the Playhub ID so the registry matches by stable ID, not display name.
        #
        # prefer_earliest: running seasons in order, this never fires — the season
        # being recorded is always later than what is stored, so a populated cell
        # is kept either way. It matters when an old season is recorded late, e.g.
        # repointing CURRENT_SEASON to fix data that was missed. Blank-only would
        # keep whatever later season got there first and permanently misattribute
        # the role; earliest-wins corrects it. Same rule the invitational path uses.
        earners = [(m['name'], m['roles'], m['id'] or None) for m in earners_meta]
        await loop.run_in_executor(
            None, lambda: batch_upsert_player_roles(earners, prefer_earliest=True)
        )

    # Marked even when nothing was stamped: a season whose earners all hold their
    # roles from an earlier season leaves no trace in the registry, and this marker
    # is then the only evidence it was recorded at all.
    await _season_close_mark(season_id, rare_uncommon=_now_et().date().isoformat(),
                             rare_uncommon_n=len(earners_meta))

    if not earners_meta:
        return {'recorded': 0, 'unlinked': [], 'merged': 0}

    # Read the registry back to report coverage, and collapse any duplicate
    # rows for the players just recorded (same Discord ID across two rows).
    # Only fires where the snapshot already shows a duplicate, so there is no
    # full registry read per player.
    registry = await loop.run_in_executor(None, get_player_registry)
    registry_by_id   = {r['playhub_id']: r for r in registry if r['playhub_id']}
    registry_by_name = {r['playhub_name'].lower(): r for r in registry}

    unlinked = []                            # earners with no Discord link yet
    matched_discord_ids: set[int] = set()
    for m in earners_meta:
        reg_entry = registry_by_id.get(m['id']) if m['id'] else None
        if reg_entry is None:
            reg_entry = registry_by_name.get(m['name'].lower())
        if reg_entry and reg_entry['discord_id']:
            matched_discord_ids.add(reg_entry['discord_id'])
        else:
            unlinked.append(m['name'])

    dup_counts: dict[int, int] = {}
    for r in registry:
        if r['discord_id']:
            dup_counts[r['discord_id']] = dup_counts.get(r['discord_id'], 0) + 1
    merged = 0
    for did in matched_discord_ids:
        if dup_counts.get(did, 0) > 1:
            try:
                await loop.run_in_executor(None, _merge_duplicate_rows, did)
                merged += 1
            except Exception as e:
                print(f"  ⚠ record-rare-and-uncommon: dedupe failed for discord_id {did}: {e}")

    mod_ch = get_channel_by_id(guild, MOD_CHANNEL_ID) if guild else None
    if mod_ch:
        lines = [f"Recorded **{len(earners_meta)}** player(s) for **{season_id}**."]
        if unlinked:
            lines.append(f"\n**Earned roles but not yet linked ({len(unlinked)}):**")
            lines.extend(f"• {name}" for name in unlinked[:20])
            if len(unlinked) > 20:
                lines.append(f"  *(and {len(unlinked) - 20} more)*")
        await mod_ch.send(embed=make_embed(
            title=f"Rare/Uncommon Recorded — {season_id}",
            description="\n".join(lines),
            colour=discord.Colour.gold()
        ))

    return {'recorded': len(earners_meta), 'unlinked': unlinked, 'merged': merged}


@tree.command(name="record-rare-and-uncommon",
              description="Record Rare/Uncommon earned this season into the Player Registry (mods only)")
async def record_rare_and_uncommon(interaction: discord.Interaction):
    if not _is_admin(interaction):
        await interaction.response.send_message("⚠️ Mods only.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)
    loop = asyncio.get_running_loop()
    season_id = season.CURRENT_SEASON

    try:
        result = await _record_rare_and_uncommon(interaction.guild, season_id, loop)
    except Exception as e:
        print(f"  ✗ record-rare-and-uncommon: failed: {e}")
        await interaction.followup.send(
            f"❌ Recording failed — the registry is unchanged: `{e}`", ephemeral=True
        )
        return

    await _refresh_season_close_quietly(season_id)

    if not result['recorded']:
        await interaction.followup.send(
            f"✅ No players earned roles in {season_id} — marked as recorded.", ephemeral=True
        )
        return

    summary = f"✅ Recorded {result['recorded']} player(s) for {season_id}"
    if result['unlinked']:
        summary += f", {len(result['unlinked'])} not yet linked"
    if result['merged']:
        summary += f", {result['merged']} duplicate registry row(s) merged"
    await interaction.followup.send(
        summary + ".\n\nNow run `/assign-roles-from-registry` to grant the Discord roles.",
        ephemeral=True,
    )


# ── /record-legendary-and-super-rare ──────────────────────────
@tree.command(name="record-legendary-and-super-rare",
              description="Record Legendary/Super Rare from an invitational into the Player Registry (mods only)")
@app_commands.describe(event_url="RPH event URL or bare event ID",
                       season_label="Season to record in the registry, e.g. S11 (default: current season)")
async def invitational_roles(interaction: discord.Interaction, event_url: str, season_label: str = None):
    if not _is_admin(interaction):
        await interaction.response.send_message("⚠️ Mods only.", ephemeral=True)
        return

    # Backfilling an old invitational must record the season the event belongs
    # to, not the season it is being replayed in.
    if season_label:
        season_label = season_label.strip().upper()
        if not re.fullmatch(r"S\d+", season_label):
            await interaction.response.send_message(
                f"⚠️ `{season_label}` isn't a valid season — use the form `S11`.", ephemeral=True
            )
            return
    else:
        season_label = season.CURRENT_SEASON

    await interaction.response.defer(ephemeral=True)
    reply = await _post_invitational_preview(interaction.guild, event_url, season_label)
    await interaction.followup.send(reply, ephemeral=True)


async def _post_invitational_preview(guild: discord.Guild, event_url: str,
                                     season_label: str, assign: bool = False) -> str:
    """
    Fetch an invitational's final standings and post the ✅/❌ recording prompt to
    the mod channel. Shared by /record-legendary-and-super-rare and the
    season-close checklist; returns the ephemeral reply for whoever asked.

    assign: also grant the Discord roles on ✅. The checklist sets it, because its
    step is "record and assign"; the slash command keeps the two separate.
    """
    event_id = event_url.strip().rstrip("/").split("/")[-1]

    loop = asyncio.get_running_loop()
    try:
        event = await loop.run_in_executor(None, _rph_api.get_event_by_id, event_id)
    except Exception as e:
        return f"❌ Failed to fetch event: {e}"

    if not event:
        return f"❌ No event found for ID `{event_id}`."

    if not event.get('tournament_phases') or not event['tournament_phases'][-1].get('rounds'):
        return "❌ Event has no tournament rounds."

    last_round_id = event['tournament_phases'][-1]['rounds'][-1]['id']
    try:
        standings = await loop.run_in_executor(
            None, _rph_api.get_standings_from_tournament_round_id, str(last_round_id)
        )
    except Exception as e:
        return f"❌ Failed to fetch standings: {e}"

    standings.sort(key=lambda s: s['rank'])
    registry_list      = await loop.run_in_executor(None, get_player_registry)
    playhub_to_discord = {r['playhub_id']: r['discord_id'] for r in registry_list if r['playhub_id'] and r['discord_id']}

    def resolve(s):
        pid    = str(s['player']['id'])
        name   = s['user_event_status']['best_identifier']
        did    = playhub_to_discord.get(pid)
        member = guild.get_member(did) if did else None
        return pid, name, member

    rank1 = next((s for s in standings if s['rank'] == 1), None)
    top8  = [s for s in standings if 2 <= s['rank'] <= 8]

    legendary_entry = resolve(rank1) if rank1 else None
    sr_entries      = [resolve(s) for s in top8]

    event_name = event.get('name', f"Event {event_id}")
    seq_label  = _season_num(season_label)
    reg_by_pid = {r['playhub_id']: r for r in registry_list if r['playhub_id']}

    def change(pid, key):
        # Before → after, the same progression the season-close checklist shows.
        before = _held_before(reg_by_pid.get(pid), seq_label)
        new    = {key} - before
        return _progression(before, new) if new else f"already {_RARITY_LABEL[key]}"

    lines = []
    if legendary_entry:
        pid, name, member = legendary_entry
        mention = member.mention if member else f"**{name}** *(unlinked — use /link first)*"
        lines.append(f"🏆 **Legendary** → {mention} — {change(pid, 'legendary')}")
    for i, (pid, name, member) in enumerate(sr_entries, 2):
        mention = member.mention if member else f"**{name}** *(unlinked)*"
        lines.append(f"⭐ **Super Rare** (rank {i}) → {mention} — {change(pid, 'super_rare')}")

    mod_ch = get_channel_by_id(guild, MOD_CHANNEL_ID)
    if not mod_ch:
        return "⚠️ Mod channel not configured."

    roles_note = ("\nApproving also grants the Discord roles." if assign else
                  "\nDiscord roles are granted separately by `/assign-roles-from-registry`.")
    embed = make_embed(
        title=f"🏆 Invitational Results — {event_name}",
        description="\n".join(lines)
                    + f"\n\nWill record these as **{season_label}** in the Player Registry."
                    + roles_note
                    + "\n\nReact ✅ to confirm or ❌ to cancel.",
        colour=discord.Colour.gold()
    )
    try:
        msg = await mod_ch.send(embed=embed)
    except discord.Forbidden:
        return (f"❌ Bot lacks permission to send messages in the mod channel (ID: `{MOD_CHANNEL_ID}`). "
                f"Check channel permissions.")

    await msg.add_reaction("✅")
    await msg.add_reaction("❌")
    _pending_invitational_assignments[msg.id] = {
        'event_name': event_name,
        'season':     season_label,
        'legendary':  legendary_entry,
        'super_rare': sr_entries,
        'assign':     assign,
    }
    return f"Check {mod_ch.mention} to confirm."


# ── /season-rollover ──────────────────────────────────────────
#
# Split into plan → guard → execute so the season-close checklist previews and
# runs exactly what the command does. Two copies of these checks would drift,
# and every one of them exists because a rollover once went wrong without it.

# The tabs create_season_sheets makes — shown in the checklist's preview.
_SEASON_TAB_SUFFIXES = ("Standings", "Events", "Leaderboard", "Results", "Set Champs")


@dataclass
class _RolloverPlan:
    new_season: str
    outgoing:   str
    resolved:   dict          # the four dates, as YYYY-MM-DD strings
    overridden: dict          # the subset that came from command arguments
    warnings:   list


async def _plan_rollover(loop, new_season: str,
                         overrides: dict | None = None) -> tuple[_RolloverPlan | None, str]:
    """
    Resolve and validate a rollover to new_season without changing anything.
    Returns (plan, '') or (None, a message saying what is wrong).

    Re-reads the calendar first, so a row typed moments ago is picked up without a
    restart — which is exactly when a rollover gets attempted.
    """
    if not re.match(r'^S\d+$', new_season):
        return None, f"⚠️ `new_season` must look like `S12` (got `{new_season}`)."

    _, cal_problems = await _reload_season(loop)
    row = season.get_season(new_season) or {}

    overrides  = overrides or {}
    overridden = {k: v.strip() for k, v in overrides.items() if v and v.strip()}
    fields     = ('season_start', 'season_end', 'set_champs_start', 'set_champs_end')
    resolved   = {k: overridden.get(k) or row.get(k) for k in fields}

    parsed = {}
    for field_name, value in resolved.items():
        if not value:
            continue
        try:
            parsed[field_name] = datetime.strptime(value, "%Y-%m-%d").date()
        except ValueError:
            return None, f"⚠️ `{field_name}` must be YYYY-MM-DD, got `{value}`."

    missing = [k for k, v in resolved.items() if not v]
    if missing:
        problem_note = ("\n\n**Seasons tab problems:**\n"
                        + "\n".join(f"  • {p}" for p in cal_problems[:5])) if cal_problems else ""
        known = ", ".join(r['season'] for r in season.SEASONS) or "none"
        return None, (
            f"❌ **{new_season} is not ready to roll over to.**\n\n"
            f"Missing: {', '.join(f'`{m}`' for m in missing)}\n"
            f"Fill those cells in the **Seasons** tab (rows found there: {known}), then re-run. "
            f"A future season normally has its Set Champs dates blank until they are announced."
            f"{problem_note}"
        )

    # Set Champs may end *after* the season end (e.g. S11: season ended Apr 24,
    # set champs Apr 26). But it must start during the season.
    ordered = (
        parsed["season_start"] <= parsed["season_end"]
        and parsed["season_start"] <= parsed["set_champs_start"] <= parsed["set_champs_end"]
        and parsed["set_champs_start"] <= parsed["season_end"]
    )
    if not ordered:
        return None, (
            f"⚠️ Date ordering invalid. Required: "
            f"`season_start` ≤ `season_end`, "
            f"`season_start` ≤ `set_champs_start` ≤ `set_champs_end`, and "
            f"`set_champs_start` ≤ `season_end`. "
            f"Got start={resolved['season_start']}, end={resolved['season_end']}, "
            f"sc_start={resolved['set_champs_start']}, sc_end={resolved['set_champs_end']}."
        )

    outgoing = season.CURRENT_SEASON
    warnings = []
    # The Set Champs digest and its sheet follow CURRENT_SEASON, so flipping the
    # pointer mid-window stops refreshing the outgoing season's tab.
    if outgoing and outgoing != new_season:
        out_sc = (season.get_season(outgoing) or {}).get('set_champs_end')
        if out_sc and date.fromisoformat(out_sc) >= _now_et().date():
            warnings.append(
                f"⚠️ **{outgoing}'s Set Champs run to {out_sc}**, but the Set Champs digest "
                f"and sheet follow the current season — `{outgoing} Set Champs` will stop "
                f"refreshing. Finish it with `/set-champs` before rolling over, or accept the "
                f"tab as final.")

    return _RolloverPlan(new_season, outgoing, resolved, overridden, warnings), ''


async def _outgoing_roles_recorded(loop, outgoing: str) -> tuple[bool, str]:
    """
    Has the outgoing season's Rare/Uncommon been recorded? Returns (ok, message).

    /record-rare-and-uncommon reads the leaderboard for CURRENT_SEASON and
    stamps CURRENT_SEASON. Run after rollover it reads the *new* season's
    empty leaderboard, finds nobody, and reports success — so the finished
    season is silently never recorded. Nothing else would notice.

    Either the registry carries the season in columns I/J, or the season-close
    marker says it was recorded. The marker is what covers a season whose
    earners all held their roles from an earlier one: earliest-season-wins
    leaves no trace of it in the registry. G/H come from invitationals, which
    happen after the season ends and so are not expected to be present yet.
    """
    try:
        entry = await loop.run_in_executor(None, _season_close_entry, outgoing)
        if entry.get('rare_uncommon'):
            return True, f"{outgoing} marked recorded on {entry['rare_uncommon']}"
        registry = await loop.run_in_executor(None, get_player_registry)
        recorded = sum(1 for r in registry
                       if r['rare'] == outgoing or r['uncommon'] == outgoing)
    except Exception as e:
        # Can't verify — say so rather than blocking or silently proceeding.
        print(f"  ⚠ season-rollover: registry check failed: {e}")
        return False, (f"⚠️ Couldn't read the registry to check whether **{outgoing}** roles "
                       f"were recorded: `{e}`")

    if recorded == 0:
        return False, (
            f"❌ **No {outgoing} roles are recorded in the Player Registry.**\n\n"
            f"Run `/record-rare-and-uncommon` first — it reads the "
            f"`{outgoing} Leaderboard`, and once the season rolls over it will "
            f"read the new season's empty one instead, losing {outgoing} for good."
        )
    return True, f"{recorded} {outgoing} role record(s) found"


async def _execute_rollover(loop, plan: _RolloverPlan) -> list[str]:
    """
    Create the new season's tabs, move the season pointer, reload in memory, and
    mark the outgoing season rolled over. Returns the tabs created.
    Raises RuntimeError with a user-facing message if a step fails.
    """
    new_season = plan.new_season

    # 1. Create new season tabs in the League spreadsheet
    try:
        created = await loop.run_in_executor(None, create_season_sheets, new_season)
    except Exception as e:
        raise RuntimeError(f"❌ Failed to create sheet tabs: {e}") from e

    # 2. Move the season pointer. Only the pointer: the dates live in the Seasons
    #    tab now. The retired flat date keys are deliberately left alone rather than
    #    deleted here — flipping the pointer and erasing the fallback in one write is
    #    how you end up with neither source of truth. They are removed by hand once
    #    the tab has proven itself.
    try:
        def _update_state():
            # strict=True: read-then-write — see set_bot_state_key
            state = load_bot_state(strict=True)
            state['season'] = new_season
            save_bot_state(state)
            return state
        new_state = await loop.run_in_executor(None, _update_state)
    except Exception as e:
        raise RuntimeError(f"❌ Sheet tabs created but failed to update Bot State: {e}") from e

    # 3. Reload season in memory, applying any overrides on top of the new row
    calendar = [dict(r) for r in season.SEASONS]
    target   = next((r for r in calendar if r['season'] == new_season), None)
    if target is None:
        target = {'season': new_season, 'set_name': '', 'sheet_row': None, 'source': 'override',
                  'prerelease_start': None, 'prerelease_end': None}
        calendar.append(target)
        calendar.sort(key=lambda r: int(r['season'][1:]))
    target.update(plan.resolved)
    if plan.overridden:
        target['source'] = 'override'
    season.init(new_state, calendar)

    if plan.outgoing and plan.outgoing != new_season:
        await _season_close_mark(plan.outgoing, rolled_over=_now_et().date().isoformat(),
                                 rolled_to=new_season)
    return created


@tree.command(name="season-rollover", description="Roll over to a new season: creates sheet tabs and reloads config (admins only)")
@app_commands.describe(
    new_season="New season identifier, e.g. S12",
    start_date="Override the Seasons tab's season start (YYYY-MM-DD)",
    end_date="Override the Seasons tab's season end (YYYY-MM-DD)",
    set_champs_start="Override the Seasons tab's Set Champs start (YYYY-MM-DD)",
    set_champs_end="Override the Seasons tab's Set Champs end (YYYY-MM-DD)",
    force="Skip the check that the outgoing season's roles were recorded",
)
async def season_rollover(
    interaction: discord.Interaction,
    new_season: str,
    start_date: str = "",
    end_date: str = "",
    set_champs_start: str = "",
    set_champs_end: str = "",
    force: bool = False,
):
    """
    Flip the scoring season to new_season.

    Dates come from that season's row in the Seasons tab. The four date arguments
    are overrides for the rare case where the row is wrong and there is no time to
    fix it — they apply *in memory only*, because the tab is operator-owned and the
    bot does not write to it, so a restart reverts to the row.
    """
    if not _is_admin(interaction):
        await interaction.response.send_message("⚠️ Admins only.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)
    loop = asyncio.get_running_loop()

    plan, err = await _plan_rollover(loop, new_season, {
        'season_start': start_date, 'season_end': end_date,
        'set_champs_start': set_champs_start, 'set_champs_end': set_champs_end,
    })
    if not plan:
        if err.startswith("❌") and "not ready" in err:
            err += ("\nYou can also pass them as arguments, but that applies in memory only — "
                    "a restart reverts to the tab.")
        await interaction.followup.send(err, ephemeral=True)
        return

    outgoing = plan.outgoing
    if not force and outgoing and outgoing != new_season:
        ok, detail = await _outgoing_roles_recorded(loop, outgoing)
        if not ok:
            await interaction.followup.send(
                detail + f"\n\nIf you're sure, re-run with `force: true`.", ephemeral=True
            )
            return
        print(f"  ✓ season-rollover: {detail} — proceeding")

    try:
        created = await _execute_rollover(loop, plan)
    except RuntimeError as e:
        await interaction.followup.send(str(e), ephemeral=True)
        return
    await _refresh_season_close_quietly(outgoing)

    resolved = plan.resolved
    tab_lines = "\n".join(f"  • {t}" for t in created) if created else "  (all tabs already existed)"
    skipped = 4 - len(created)
    skip_note = f"\n⚠️ {skipped} tab(s) already existed and were skipped." if skipped else ""
    # Both remaining steps read the outgoing season, not CURRENT_SEASON, so
    # they are still safe to run now — but nothing prompts for them otherwise.
    todo = (f"\n\n**Still to do for {outgoing}:**\n"
            f"  • `/assign-roles-from-registry` — grant the roles just recorded\n"
            f"  • `/archive-season {outgoing}` — copy the tabs to the Archive sheet"
            ) if outgoing and outgoing != new_season else ""

    override_note = ""
    if plan.overridden:
        fields = ", ".join(f"`{k}`" for k in plan.overridden)
        override_note = (f"\n\n⚠️ **Overridden in memory only:** {fields}. A restart — or any reload, "
                         f"including `/seasons` — reverts to the Seasons tab. Edit the "
                         f"`{new_season}` row there to make this stick.")

    sc_note = "".join(f"\n\n{w}" for w in plan.warnings)

    await interaction.followup.send(
        f"✅ **Season rolled over to {new_season}**\n\n"
        f"**New tabs created in League sheet:**\n{tab_lines}{skip_note}\n\n"
        f"**Season window:** {resolved['season_start']} → {resolved['season_end']}\n"
        f"**Set Champs:** {resolved['set_champs_start']} → {resolved['set_champs_end']}\n"
        f"**Dates from:** {'the Seasons tab' if not plan.overridden else 'the Seasons tab + overrides'}"
        f"{override_note}{sc_note}{todo}",
        ephemeral=True,
    )


# ── /archive-season ───────────────────────────────────────────
@tree.command(name="archive-season", description="Copy a completed season's tabs from the League sheet to the Archive spreadsheet (admins only)")
@app_commands.describe(season_name="Season to archive, e.g. S11")
async def archive_season(interaction: discord.Interaction, season_name: str):
    if not _is_admin(interaction):
        await interaction.response.send_message("⚠️ Admins only.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)

    loop = asyncio.get_running_loop()
    try:
        archived = await loop.run_in_executor(None, archive_season_data, season_name)
    except Exception as e:
        await interaction.followup.send(f"❌ Archive failed: {e}", ephemeral=True)
        return

    if not archived:
        await interaction.followup.send(
            f"⚠️ No data was archived for **{season_name}** — all tabs were empty or missing.",
            ephemeral=True,
        )
        return

    await _season_close_mark(season_name, archived=_now_et().date().isoformat())
    await _refresh_season_close_quietly(season_name)

    tab_lines = "\n".join(f"  • {t}" for t in archived)
    await interaction.followup.send(
        f"✅ **{season_name} archived** ({len(archived)} tab(s))\n\n{tab_lines}\n\n"
        f"Data has been copied to the Archive spreadsheet. "
        f"The League sheet tabs are unchanged — delete them manually when ready.",
        ephemeral=True,
    )


# ── /assign-roles-from-registry ───────────────────────────────
#
# Applies the registry across the whole server. The mirror of the two record
# commands: they decide what a player has earned and write it to columns G–J,
# this reads those columns back and makes Discord match.
#
# Shares _assign_recorded_roles with /link and the ✅ fuzzy-confirm, so there is
# one implementation of role assignment regardless of what triggered it. Being
# additive-only it is idempotent, which makes this the repair path when someone
# loses a role — a rejoin, a manual removal, or an add_roles call that failed.
_REGISTRY_ROLE_KEYS = [
    (LEGENDARY_ROLE_ID,  'legendary'),
    (SUPER_RARE_ROLE_ID, 'super_rare'),
    (RARE_ROLE_ID,       'rare'),
    (UNCOMMON_ROLE_ID,   'uncommon'),
]


async def _assign_all_from_registry(guild: discord.Guild,
                                    registry: list[dict]) -> tuple[list, list, list, int]:
    """
    Grant every linked member the rarity roles their registry row records, and
    post a summary to the mod channel. Shared by /assign-roles-from-registry and
    the season-close checklist.

    Returns (assigned, failed, gone, unlinked): assigned is [(mention, role_id,
    season)], failed is display strings, gone is names of linked players no longer
    in the server, unlinked counts rows holding roles with no Discord ID.
    """
    assigned = []
    failed   = []
    gone     = []
    unlinked = 0

    for entry in registry:
        recorded = {rid: entry[key] for rid, key in _REGISTRY_ROLE_KEYS if entry[key]}
        if not recorded:
            continue
        if not entry['discord_id']:
            unlinked += 1
            continue

        member = guild.get_member(entry['discord_id'])
        if not member:
            gone.append(entry['playhub_name'])
            continue

        added, errs = await _assign_recorded_roles(
            guild, member, recorded, "assign-roles-from-registry"
        )
        assigned.extend((member.mention, rid, s) for rid, s in added)
        failed.extend(f"{member.mention} — {RARITY_ROLE_NAMES.get(rid, rid)}: {e}" for rid, e in errs)

    mod_ch = get_channel_by_id(guild, MOD_CHANNEL_ID)
    if mod_ch and (assigned or failed):
        lines = [f"{mention}: +{_fmt_roles([(rid, s)])}" for mention, rid, s in assigned[:40]]
        if len(assigned) > 40:
            lines.append(f"*(and {len(assigned) - 40} more)*")
        if failed:
            lines.append("\n**Failed:**")
            lines.extend(f"• {f}" for f in failed[:10])
        await mod_ch.send(embed=make_embed(
            title=f"Roles Assigned — {len(assigned)} applied",
            description="\n".join(lines),
            colour=discord.Colour.gold()
        ))

    return assigned, failed, gone, unlinked


@tree.command(name="assign-roles-from-registry",
              description="Assign every Discord rarity role recorded in the Player Registry (mods only)")
async def assign_roles_from_registry(interaction: discord.Interaction):
    if not _is_admin(interaction):
        await interaction.response.send_message("⚠️ Mods only.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)
    loop = asyncio.get_running_loop()

    try:
        registry = await loop.run_in_executor(None, get_player_registry)
    except Exception as e:
        print(f"  ✗ assign-roles-from-registry: registry read failed: {e}")
        await interaction.followup.send(f"❌ Couldn't read the registry: `{e}`", ephemeral=True)
        return

    assigned, failed, gone, unlinked = await _assign_all_from_registry(interaction.guild, registry)
    await _refresh_open_season_closes()

    parts = ([f"✅ **{len(assigned)}** role(s) assigned"] if assigned
             else ["✅ Everyone already holds the roles recorded for them — nothing to do"])
    if failed:
        parts.append(f"{len(failed)} failed")
    if gone:
        parts.append(f"{len(gone)} linked player(s) no longer in the server")
    if unlinked:
        parts.append(f"{unlinked} row(s) with roles but no Discord link")
    await interaction.followup.send(", ".join(parts) + ".", ephemeral=True)


# ── /tidy-registry ────────────────────────────────────────────
@tree.command(name="tidy-registry",
              description="Remove blank rows and sort the Player Registry by rarity (admins only)")
async def tidy_registry(interaction: discord.Interaction):
    if not _is_admin(interaction):
        await interaction.response.send_message("⚠️ Mods only.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)

    # Full-sheet rewrite — hold the sheet lock so it cannot interleave with a
    # results import. Every other registry write touches a single row and is
    # safe unsynchronised; this one is not.
    async with _sheet_lock:
        loop = asyncio.get_running_loop()
        try:
            # Current display names so renames reach rows that nothing has
            # touched by ID. Non-fatal — tidy still works without them.
            try:
                current_names = await loop.run_in_executor(None, get_current_display_names)
            except Exception as e:
                print(f"  ⚠ /tidy-registry: name refresh skipped: {e}")
                current_names = None
            stats = await loop.run_in_executor(
                None, lambda: compact_and_sort_registry(current_names)
            )
        except Exception as e:
            print(f"  ✗ /tidy-registry failed: {e}")
            await interaction.followup.send(
                f"❌ Tidy failed — the registry is unchanged: `{e}`", ephemeral=True
            )
            return

    renamed = stats.get('renamed') or []
    rename_block = ""
    if renamed:
        shown = "\n".join(f"  • {old} → {new}" for old, new in renamed[:15])
        more  = f"\n  *(and {len(renamed) - 15} more)*" if len(renamed) > 15 else ""
        rename_block = f"\n• {len(renamed)} name(s) refreshed from this season:\n{shown}{more}"

    await interaction.followup.send(
        f"✅ **Player Registry tidied**\n"
        f"• {stats['kept']} row(s) kept\n"
        f"• {stats['blanks_removed']} blank row(s) removed\n"
        f"• {stats['moved']} row(s) reordered"
        f"{rename_block}\n\n"
        f"Sorted Legendary → Super Rare → Rare → Uncommon, newest season first, "
        f"unroled players last.",
        ephemeral=True,
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
    embed.add_field(name="/etb-discount", value="Verify your GTA Lorcana event attendance to unlock the Enter the Battlefield community discount", inline=False)
    embed.add_field(name="🧵 Results Threads",
                    value=f"New threads in `{_ch('results_reporting')}` are processed automatically. Edit to retry on bad URL.",
                    inline=False)
    embed.add_field(name="\u200b", value="**Admins only**", inline=False)
    embed.add_field(name="/recheck",
                    value=f"Reprocess any missed threads in `{_ch('results_reporting')}`",
                    inline=False)
    embed.add_field(name="/link", value="Manually link a Discord member to a Playhub ID", inline=False)
    embed.add_field(name="/tidy-registry", value="Remove blank rows and sort the Player Registry by rarity", inline=False)
    embed.add_field(name="/record-rare-and-uncommon", value="Record Rare/Uncommon earned this season into the Player Registry", inline=False)
    embed.add_field(name="/record-legendary-and-super-rare", value="Record Legendary/Super Rare from an invitational into the Player Registry", inline=False)
    embed.add_field(name="/assign-roles-from-registry", value="Assign every Discord rarity role the registry records — safe to re-run", inline=False)
    embed.add_field(name="/where-to-play", value="Manually push the Where to Play post", inline=False)
    embed.add_field(name="/season-rollover", value="Create new season sheet tabs, update Bot State, and reload season config in memory", inline=False)
    embed.add_field(name="/archive-season", value="Copy a completed season's tabs from the League sheet to the Archive spreadsheet", inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)


# ═══════════════════════════════════════════════════════════════
# WHERE TO PLAY — MANUAL TRIGGER
# ═══════════════════════════════════════════════════════════════

@tree.command(name="where-to-play", description="Manually push the Where to Play post (admins only)")
async def where_to_play_command(interaction: discord.Interaction):
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


@tree.command(name="season-close", description="Post or refresh the end-of-season checklist now (admins only)")
async def season_close_command(interaction: discord.Interaction):
    """The daily season-close pass, on demand, with its log shown back."""
    if interaction.user.id not in ADMIN_USER_IDS:
        await interaction.response.send_message("❌ Admins only.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    before = len(_season_close_log)
    earlier = list(_season_close_log)
    await _season_close_tick()
    now_lines = _season_close_log[before:] if len(_season_close_log) > before else _season_close_log[-3:]
    body = "**This run**\n" + ("\n".join(now_lines) or "*(no output)*")
    if earlier:
        body += "\n\n**Earlier (since restart)**\n" + "\n".join(earlier[-8:])
    await interaction.followup.send(body[:1990], ephemeral=True)


@tree.command(name="seasons", description="Show the season calendar and what each digest resolves to (admins only)")
@app_commands.describe(reload="Re-read the Seasons tab first (default true)")
async def seasons_command(interaction: discord.Interaction, reload: bool = True):
    """
    Read-only view of the Seasons tab as the bot understands it.

    The tab is hand-edited, so this is how a season's dates get checked without
    waiting for a 7 AM digest to silently post nothing.
    """
    if not _is_admin(interaction):
        await interaction.response.send_message("❌ Admins only.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)
    problems: list[str] = []
    if reload:
        _, problems = await _reload_season(asyncio.get_running_loop())

    lines = [f"🗓️ **Season calendar** — current: **{season.CURRENT_SEASON}** "
             f"(source: `{season.CALENDAR_SOURCE}`)", ""]
    if season.SEASONS:
        for row in season.SEASONS:
            marker   = " ◀ current" if row['season'] == season.CURRENT_SEASON else ""
            set_name = f" — {row['set_name']}" if row.get('set_name') else ""
            lines.append(f"**{row['season']}**{set_name}{marker}")
            lines.append(f"  Prerelease: {row.get('prerelease_start') or '—'} → {row.get('prerelease_end') or '—'}")
            lines.append(f"  Season:     {row.get('season_start') or '—'} → {row.get('season_end') or '—'}")
            lines.append(f"  Set Champs: {row.get('set_champs_start') or '—'} → {row.get('set_champs_end') or '—'}")
    else:
        lines.append("*No rows loaded from the Seasons tab.*")

    pre = season.active_prerelease()
    lines += ["", "**Digest windows now**",
              f"  Set Champs: {season.CURRENT_SEASON} "
              f"{season.SET_CHAMPS_START_DATE or '—'} → {season.SET_CHAMPS_END_DATE or '—'}",
              f"  Prerelease: " + (f"{pre['season']} {pre.get('set_name') or ''} "
                                   f"{pre['prerelease_start']} → {pre['prerelease_end']}"
                                   if pre else "none open")]

    if season.CALENDAR_SOURCE == 'bot_state':
        lines += ["", f"⚠️ `{season.CURRENT_SEASON}` has no row in the Seasons tab — running on the "
                      f"legacy Bot State date keys. Add the row."]
    elif season.CALENDAR_SOURCE == 'override':
        lines += ["", f"⚠️ `{season.CURRENT_SEASON}`'s dates were overridden on the command line and "
                      f"exist **in memory only** — a restart reverts to the Seasons tab."]
    if problems:
        lines += ["", "**Problems**"] + [f"  • {p}" for p in problems[:10]]

    await interaction.followup.send("\n".join(lines)[:1990], ephemeral=True)


def _spec(key: str) -> _DigestSpec:
    """Look a digest up by key — never by position, so _DIGESTS can be reordered."""
    return next(d for d in _DIGESTS if d.key == key)


async def _digest_command(interaction: discord.Interaction, spec: _DigestSpec) -> None:
    """Manual refresh for one digest — the same path the 7 AM loop takes."""
    if not _is_admin(interaction):
        await interaction.response.send_message("❌ Admins only.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)
    if not spec.active(_now_et().date()):
        await interaction.followup.send(
            f"ℹ️ Nothing to post: no {spec.label.lower()} window is open. "
            f"Check the **Seasons** tab with `/seasons`.", ephemeral=True)
        return

    loop = asyncio.get_running_loop()
    try:
        count = await _run_digest(spec, loop)
        await interaction.followup.send(f"✅ {spec.label} updated ({count} event(s)).", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)


@tree.command(name="set-champs", description="Manually refresh and post the Set Champs update (admins only)")
async def set_champs_command(interaction: discord.Interaction):
    await _digest_command(interaction, _spec('set_champs'))


@tree.command(name="prereleases", description="Manually refresh and post the prerelease update (admins only)")
async def prereleases_command(interaction: discord.Interaction):
    await _digest_command(interaction, _spec('prerelease'))


@tree.command(name="ccqs", description="Manually refresh and post the CCQ update (admins only)")
async def ccqs_command(interaction: discord.Interaction):
    await _digest_command(interaction, _spec('ccq'))


if __name__ == "__main__":
    missing = [v for v in ["DISCORD_BOT_TOKEN", "WORKER_URL", "WORKER_SECRET"] if not os.getenv(v)]
    if missing:
        raise ValueError(f"Missing environment variables: {', '.join(missing)}")
    bot.run(DISCORD_BOT_TOKEN)
