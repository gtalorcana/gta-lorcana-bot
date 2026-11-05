# Invitational Invites

The top 32 players of the final leaderboard are invited to the Invitational in Discord, and
RSVP by button. This replaced the Google Forms RSVP (#3). Code: `invitational.py` (the sheet and
the rules, no Discord) and the `INVITATIONAL INVITES` section of `bot.py`. Settings are the
`INVITE_*` constants in `constants.py`.

## Sending (season-close step 1)

At season end the checklist's first step is **Send invites**. It previews ranks 1–32 privately,
with each one's Discord member or *not on Discord*, and flags:

- one member holding two ranks (two Playhub accounts) — send, then decline the extra one with
  `/invite-set` to free its spot
- missing bot permissions, a missing staff role or league category, a missing invitational date

Nothing goes out until **Send invites** under the preview is pressed. Then the bot:

1. Creates `#s##-top-32-invites` in the league category (`INVITE_CATEGORY_ID`): hidden from
   `@everyone`, visible to the staff role (`STAFF_ROLE_ID`, Enchanted), the bot, and each invitee by a
   per-member overwrite. There is no per-season role
2. Snapshots the 32 into the `S## Invitational` tab (League sheet)
3. Posts **one** message mentioning every invitee on Discord
4. Posts the roster and pins it

Each step is safe to repeat: a send that failed partway resumes when pressed again, reusing the
channel, the tab and the announcement it already made, so nobody is mentioned twice.

The leaderboard no longer moves once results close, so the 32 are simply ranks 1–32, and "next"
is the next rank not already in the tab. Ties need no handling, because the leaderboard's
tiebreakers make every rank unique.

## The roster

One embed, edited in place on every change, pinned. If it is deleted, the next watch pass
reposts it. Every holder is listed by rank: ✅ accepted, ⏳ pending with its deadline as a live
`<t:…:R>` timestamp, 🕓 deferred (with the count), and ⚠️ *DM failed* where the reminder bounced.
Decliners are folded onto one line. Qualifiers not on Discord are listed separately for staff.

Embeds never ping. The only mentions are the one announcement and one per replacement.

## My invite

The roster has a single **My invite** button. It opens a private panel for whoever pressed it:

| Their row | Buttons |
|---|---|
| pending / deferred | **Accept**, **Defer +24h (n left)**, **Decline** |
| accepted | **Withdraw** |
| declined / withdrawn | none — "message Ryan if that was a mistake" |
| not in the tab | none — "you're not on the invite list" |

Decline and Withdraw ask once more, because the spot goes to the next player at once. A panel
carries its row's state; if the row changed after the panel opened, the click is refused and they
press **My invite** again.

**Defer** works any time while pending: +24h a press, up to 3. A defer resets the reminder.

## Timing (the watch loop)

`invitational_watch` runs every 5 minutes while any invitational is open, and does nothing
otherwise. Each pass re-reads the tab — about 40 rows, so there is no memory concern — and:

- **Auto-declines** a pending invite whose deadline has passed, then invites the next player
- **Never mass-declines on catch-up.** A deadline that passed before this process was last
  watching (the bot was down, or a deadline was hand-edited into the past) is extended 24h and
  noted on the row instead. This is judged against the last successful pass, or the process
  start, not a fixed grace period
- **Reminds once:** one DM to anyone still pending with 24h or less left. The roster shows *DM
  failed* if their DMs are closed. Prompt responders are never DMed
- **Removes access** from a decliner 24h after they declined, so staff can undo in between
- **Closes** the day after the invitational

Invites keep going out until the day before the invitational (Seasons col I). No deadline runs
past 9 PM ET the night before (`INVITE_LAST_CALL_HOUR_ET`), which caps late replacements and
defers alike.

## Staff

`/invite-set <player> <status>` (admins) takes a Playhub ID, name or @mention:

- **accepted / declined / withdrawn** — record an answer, typically from a qualifier not on
  Discord. Declined and withdrawn invite the next player
- **pending** — undo a decline: a fresh 48h deadline, channel access restored. If the replacement
  already went out, the roster then holds 33, and the reply says so. Who keeps the spot is a
  staff call

Qualifiers not on Discord have no deadline and are never auto-declined. When a replacement is
one of them, the bot posts a note to the season-close channel instead of a mention.

## Permissions

The bot needs **Manage Channels**, **Manage Roles** (for channel permission overwrites) and
**Pin Messages**. The preview names whichever are missing. discord.py 2.4 has no named flag for
Pin Messages, so the preview checks permission bit 51 directly.

## Testing

Four environment variables (Fly secrets) turn a run into a test. The preview says **Test mode**
while any is set:

| Variable | Test value | Effect |
|---|---|---|
| `INVITE_TIME_SCALE` | `minutes` | Every hour becomes a minute: 48-minute deadlines, 24-minute defers |
| `INVITE_SIZE` | `1` | One spot, so a single decline reaches a replacement |
| `INVITE_TEST_CHANNEL_ID` | the bot test channel | Used instead of creating a channel; not-on-Discord notes go there too |
| `INVITE_SPREADSHEET_ID` | the Bot Database sheet | `S## Leaderboard` is read, and `S## Invitational` written, there instead of the public League sheet |

Put a fake `S99 Leaderboard` tab in that sheet (A rank, B Playhub ID, C name), with a linked
player at rank 1 and unlinked ones below, and run `/invite-send S99`. Afterwards, unset the four
secrets, delete the `S99 Leaderboard` and `S99 Invitational` tabs, and delete the
`invitational:S99` and `season_close:S99` Bot State keys.
