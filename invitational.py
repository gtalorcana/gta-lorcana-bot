"""
Invitational invites — sheet storage and the rules, with no Discord in it (#3).

The top INVITE_SIZE players of a finished season are invited to the
Invitational and RSVP by button. Every invite is one row of the `S## Invitational`
tab in the League sheet:

    A Rank | B Playhub ID | C Name | D Discord ID | E Status | F Deadline |
    G Defers | H Responded | I Invited | J Reminded | K Note | L Access removed

Status is pending, deferred, accepted, declined or withdrawn. Timestamps are
ISO-8601 UTC. Every write is RAW, so Sheets never turns a timestamp or an ID into
a date or a number. A row with no Discord ID is a qualifier who isn't on Discord.
Staff reach them and record the answer, so they have no deadline and are never
auto-declined.

Its own tab, not columns on the Leaderboard: the Leaderboard is a formula that
re-sorts on every results change, and typed cells beside it drift onto the wrong
rows. The leaderboard is read only to snapshot invitees and pick replacements.
It no longer moves once the results season ends, so "next" is simply the next
rank not already in the tab.

`plan_tick` holds the timed rules as a pure function of the rows and the clock,
so they can be checked without Discord or Sheets.
"""

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from clients import gs as _gs
from constants import (
    LEAGUE_SPREADSHEET_ID,
    INVITE_SIZE,
    INVITE_DEADLINE_HOURS,
    INVITE_DEFER_HOURS,
    INVITE_MAX_DEFERS,
    INVITE_REMINDER_HOURS,
    INVITE_ACCESS_GRACE_HOURS,
    INVITE_LOOP_MINUTES,
    INVITE_LAST_CALL_HOUR_ET,
    INVITE_TIME_SCALE,
)

_TZ_ET = ZoneInfo("America/Toronto")
_MINUTES = INVITE_TIME_SCALE == "minutes"


def hours(n: float) -> timedelta:
    """A configured number of hours — minutes under INVITE_TIME_SCALE=minutes."""
    return timedelta(minutes=n) if _MINUTES else timedelta(hours=n)


# ── Rows ──────────────────────────────────────────────────────────────────────

HEADER = ['Rank', 'Playhub ID', 'Name', 'Discord ID', 'Status', 'Deadline', 'Defers',
          'Responded', 'Invited', 'Reminded', 'Note', 'Access removed']
_WIDTH = len(HEADER)

OPEN    = ('pending', 'deferred')            # still to answer
HOLDING = OPEN + ('accepted',)               # holding a spot
GONE    = ('declined', 'withdrawn')          # spot given up
STATUSES = HOLDING + GONE


def tab_name(season_id: str) -> str:
    return f"{season_id} Invitational"


@dataclass
class Invite:
    row:          int                 # sheet row number (data starts at 2)
    rank:         int
    playhub_id:   str
    name:         str
    discord_id:   int | None = None
    status:       str = 'pending'
    deadline:     datetime | None = None
    defers:       int = 0
    responded:    datetime | None = None
    invited:      datetime | None = None
    reminded:     str = ''            # ISO time, or 'failed'
    note:         str = ''
    access_removed: datetime | None = None

    @property
    def on_discord(self) -> bool:
        return self.discord_id is not None

    def token(self) -> str:
        """Changes whenever the row does — guards a private panel opened before."""
        return f"{self.status}|{iso(self.deadline)}|{self.defers}"

    def to_cells(self) -> list[str]:
        return [str(self.rank), self.playhub_id, self.name,
                str(self.discord_id) if self.discord_id else '', self.status,
                iso(self.deadline), str(self.defers), iso(self.responded),
                iso(self.invited), self.reminded, self.note, iso(self.access_removed)]


def iso(dt: datetime | None) -> str:
    return dt.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ') if dt else ''


def _parse_dt(s: str) -> datetime | None:
    s = (s or '').strip()
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace('Z', '+00:00'))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _parse_row(n: int, cells: list) -> Invite | None:
    c = list(cells) + [''] * (_WIDTH - len(cells))
    try:
        rank = int(c[0])
    except ValueError:
        return None
    status = c[4].strip().lower()
    did = c[3].strip()
    return Invite(
        row=n, rank=rank, playhub_id=c[1].strip(), name=c[2].strip(),
        discord_id=int(did) if did.isdigit() else None,
        status=status if status in STATUSES else 'pending',
        deadline=_parse_dt(c[5]),
        defers=int(c[6]) if c[6].strip().isdigit() else 0,
        responded=_parse_dt(c[7]), invited=_parse_dt(c[8]),
        reminded=c[9].strip(), note=c[10].strip(), access_removed=_parse_dt(c[11]),
    )


# ── Sheet I/O ─────────────────────────────────────────────────────────────────
# Callers hold bot._sheet_lock around every write, like every other sheet write.

def read_invites(season_id: str) -> list[Invite]:
    """Every invite row, in rank order. Raises if the tab can't be read."""
    data = _gs.get_values(LEAGUE_SPREADSHEET_ID, f"'{tab_name(season_id)}'!A2:L")
    invites = [inv for i, cells in enumerate(data.get('values', []))
               if (inv := _parse_row(i + 2, cells))]
    return sorted(invites, key=lambda inv: inv.rank)


def write_invite(season_id: str, inv: Invite) -> None:
    """Write one row to its own range — never append, never the whole tab, so a
    hand edit to another row between a read and this write survives."""
    _gs.update_values(LEAGUE_SPREADSHEET_ID,
                      f"'{tab_name(season_id)}'!A{inv.row}:L{inv.row}",
                      'RAW', [inv.to_cells()])


def create_tab(season_id: str, invites: list[Invite]) -> None:
    """Create the tab and write the snapshot. Raises if the tab already exists."""
    _gs.add_sheet(LEAGUE_SPREADSHEET_ID, tab_name(season_id))
    rows = [HEADER] + [inv.to_cells() for inv in invites]
    _gs.update_values(LEAGUE_SPREADSHEET_ID,
                      f"'{tab_name(season_id)}'!A1:L{len(rows)}", 'RAW', rows)


def read_leaderboard(season_id: str) -> list[tuple[int, str, str]]:
    """[(rank, playhub_id, name)] from `S## Leaderboard`, in rank order, one per ID."""
    data = _gs.get_values(LEAGUE_SPREADSHEET_ID, f"{season_id} Leaderboard!A2:C")
    out, seen = [], set()
    for cells in data.get('values', []):
        if len(cells) < 3:
            continue
        try:
            rank = int(cells[0])
        except ValueError:
            continue
        pid, name = cells[1].strip(), cells[2].strip()
        key = pid or name.lower()
        if key and key not in seen:
            seen.add(key)
            out.append((rank, pid, name))
    return sorted(out)


# ── Rules ─────────────────────────────────────────────────────────────────────

def cutoff(invitational_date: str | None) -> datetime | None:
    """No invite goes out from midnight ET on the invitational day itself."""
    if not invitational_date:
        return None
    d = date.fromisoformat(invitational_date)
    return datetime.combine(d, time.min, tzinfo=_TZ_ET)


def last_call(invitational_date: str | None) -> datetime | None:
    """No deadline runs past 9 PM ET the night before the invitational."""
    if not invitational_date:
        return None
    d = date.fromisoformat(invitational_date) - timedelta(days=1)
    return datetime.combine(d, time(INVITE_LAST_CALL_HOUR_ET), tzinfo=_TZ_ET)


def first_deadline(now: datetime, invitational_date: str | None) -> datetime:
    deadline = now + hours(INVITE_DEADLINE_HOURS)
    cap = last_call(invitational_date)
    return min(deadline, cap) if cap else deadline


def can_invite(now: datetime, invitational_date: str | None) -> bool:
    """Invites go out until the day before the invitational, and only while a new
    invitee would still have some time to answer."""
    cut, cap = cutoff(invitational_date), last_call(invitational_date)
    return not ((cut and now >= cut) or (cap and now >= cap))


def defer_deadline(inv: Invite, invitational_date: str | None) -> datetime | None:
    """The deadline a Defer press would give, or None if it can't be deferred."""
    if inv.status not in OPEN or inv.defers >= INVITE_MAX_DEFERS or not inv.deadline:
        return None
    new = inv.deadline + hours(INVITE_DEFER_HOURS)
    cap = last_call(invitational_date)
    if cap:
        new = min(new, cap)
    return new if new > inv.deadline else None


def next_candidates(invites: list[Invite], leaderboard: list[tuple[int, str, str]],
                    n: int) -> list[tuple[int, str, str]]:
    """The next n leaderboard players below everyone already in the tab."""
    taken = {inv.playhub_id or inv.name.lower() for inv in invites}
    floor = max((inv.rank for inv in invites), default=0)
    picks = []
    for rank, pid, name in leaderboard:
        if len(picks) >= n:
            break
        if rank > floor and (pid or name.lower()) not in taken:
            picks.append((rank, pid, name))
    return picks


def open_spots(invites: list[Invite]) -> int:
    return max(INVITE_SIZE - sum(inv.status in HOLDING for inv in invites), 0)


@dataclass
class TickPlan:
    """What one pass of the loop should do. Rows are already updated in place;
    the bot writes them, then does the Discord side."""
    declined:   list[Invite] = field(default_factory=list)   # auto-declined now
    extended:   list[Invite] = field(default_factory=list)   # deadline missed while down
    remind:     list[Invite] = field(default_factory=list)   # DM, then set .reminded
    revoke:     list[Invite] = field(default_factory=list)   # remove channel access

    @property
    def changed(self) -> list[Invite]:
        return self.declined + self.extended


def plan_tick(invites: list[Invite], now: datetime, watching_since: datetime,
              invitational_date: str | None) -> TickPlan:
    """
    The timed rules, applied to rows read just now.

    watching_since is when this process last looked (its previous successful pass,
    or its start). A deadline that passed before then passed while nobody was
    looking — the bot was down, or the row was edited by hand — so it is extended
    by a day and noted, never declined. That is what keeps a long outage from
    mass-declining everyone on the next pass.
    """
    plan = TickPlan()
    for inv in invites:
        if inv.status in OPEN and inv.on_discord and inv.deadline:
            if now >= inv.deadline:
                if inv.deadline < watching_since:
                    inv.deadline = now + hours(INVITE_DEFER_HOURS)
                    cap = last_call(invitational_date)
                    if cap and cap > now:
                        inv.deadline = min(inv.deadline, cap)
                    inv.note = add_note(inv.note, f"deadline extended {now:%b %d}: bot was down")
                    plan.extended.append(inv)
                else:
                    inv.status, inv.responded = 'declined', now
                    inv.note = add_note(inv.note, "auto-declined (no answer)")
                    plan.declined.append(inv)
            elif not inv.reminded and inv.deadline - now <= hours(INVITE_REMINDER_HOURS):
                plan.remind.append(inv)

        if (inv.status in GONE and inv.on_discord and not inv.access_removed
                and inv.responded and now - inv.responded >= hours(INVITE_ACCESS_GRACE_HOURS)):
            plan.revoke.append(inv)
    return plan


def add_note(note: str, line: str) -> str:
    return f"{note}; {line}" if note else line
