# GTA Lorcana — Onboarding, Server Guide and #start-here

Copy for Discord's built-in Onboarding and Server Guide (set by hand in Server Settings →
Onboarding), plus a draft #start-here post. Channel names follow the server as it is today;
revisit them after the channel cleanup in issue #7.

---

## Roles

### Region roles

Subscriptions: "tell me about events in this area". Members pick any number and change them
any time in Channels & Roles; someone who wants every event picks all six. Not mentionable by
@everyone. Only staff and **Event Organizer** can ping them, so a spam account that gets past
the rules screen can't ping a whole region.

| Role | Covers |
|---|---|
| **Toronto** | Toronto, North York, Scarborough, Etobicoke |
| **York Region** | Newmarket, Richmond Hill, Markham, Vaughan |
| **Peel** | Mississauga, Brampton |
| **Halton/Hamilton** | Oakville, Burlington, Hamilton, Ancaster |
| **Durham/Peterborough** | Oshawa, Whitby, Cobourg, Peterborough |
| **Southwest** | London, St. Thomas, Kitchener-Waterloo |

Drawn from where S12–S14 events were actually reported. Niagara, Ottawa, Kingston and the north
have had none, so they get no role. Members outside them skip the question.

There is deliberately **no catch-all @Events role**. Organizers would ping it every time, as
the biggest net they can reach, and it would become @everyone by another name.

### Ping role

**League News**: season start and end, and Invitational qualifiers. Granted with
Common (opt-out); members untick it in Channels & Roles. Staff ping it instead of @everyone,
which is kept for emergencies.

### Event Organizer

For store owners and event organizers. Granted by staff on request (DM a staff member). It
carries a #local-store-events channel override with **Mention @everyone, @here, and All Roles**, so
organizers can ping region roles there and nowhere else. That permission also covers
@everyone, which is why the role is staff-granted and not self-assigned, and why AutoMod
blocks @everyone and @here for everyone but staff (below).

**Ping rule** (end of rule 5 in `community-rules.md`): ping your event's region, plus a
neighbouring one if you're near the border, once per event. An event that draws from everywhere
goes through staff, who decide whether it's worth a League News post.

---

## AutoMod

Set by hand in Server Settings → AutoMod. Both rules exempt **Enchanted** and **GTA Lorcana Bot**;
alerts go to #moderator-only. The bot needs the exemption: the Invitational announcement
mentions every qualifier (up to 32) in one message, and the bot role has no Manage Server, so
Discord doesn't exempt it automatically.

| Rule | Setting | Why |
|---|---|---|
| Block @everyone / @here | Custom keyword rule: `@everyone`, `@here`. Block message | Event Organizer's permission includes @everyone; this takes it back |
| Mention spam | Limit **3** mentions per message; block, and time out | Region ping plus one or two neighbours fits; tagging all six to dodge the block doesn't |
| Spam content, flagged words, member profile names | On (from #8) | |

---

## Onboarding

**Default channels:** every public channel. That's the top channels (#welcome, #community-rules,
both announcement channels) and everything in League, Find Events and Community. Onboarding hides
any channel that isn't a default and wasn't picked in a question, and with ~20 channels there's
nothing worth hiding. So the questions only hand out roles; they don't decide what members see.

**No default role in Onboarding.** League News is opt-out instead: the bot grants it with Common
when a member accepts the rules (`_welcome_member`), because @everyone is kept for emergencies and
a player who skipped an optional question would never hear a season start. Question 1 is where
members untick it. Channels & Roles only lists roles that are Onboarding answers, so the question
has to stay even though the role is already ticked. A store owner gets a few pings a season until
they untick it. Existing members get it through the one-off below.

Questions: two, both optional. A "What brings you here?" question would only exist to show or
hide channels, which the defaults already settle.

**1. What do you want to be pinged for?** (multiple answers; not required; pre-join)

| Title | Description | Role |
|---|---|---|
| 📣 League News | Season news and Invitational qualifiers | League News |

One answer for now; a future ping role is another answer here.

**2. Where do you want to hear about events?** (multiple answers; not required; pre-join)

Each answer assigns its region role and no channels, since every channel is already a default.
Answer titles have a short limit, so the cities go in the description. There's no "Elsewhere":
Discord requires every answer to grant a role or channel, and the question is optional anyway.

| Title | Description | Role |
|---|---|---|
| Toronto | Toronto, North York, Scarborough, Etobicoke | Toronto |
| York Region | Markham, Richmond Hill, Newmarket, Vaughan | York Region |
| Peel | Mississauga, Brampton | Peel |
| Halton / Hamilton | Oakville, Burlington, Hamilton, Ancaster | Halton/Hamilton |
| Durham / Peterborough | Oshawa, Whitby, Cobourg, Peterborough | Durham/Peterborough |
| Southwest | London, St. Thomas, Kitchener-Waterloo | Southwest |

---

## Existing members

Onboarding runs only on join, so the ~240 members already here never see it.

1. **Give League News to every member with Common.** A one-off run, so existing members keep
   getting announcements now that they no longer go to @everyone. Anyone can drop it in
   Channels & Roles.
2. **One @everyone announcement** in #announcements (`<id:customize>` renders as a
   link to Channels & Roles):

> @everyone
>
> 📍 **Action needed: pick your region**
>
> The league covers a lot of ground, and an event on the other side of it isn't much use to
> you. So local event pings now go to **region roles** instead of @everyone, and you only hear
> about events near you.
>
> **If you don't pick a region, you won't be pinged about local events.**
>
> 👉 Open <id:customize> and pick every area you want to hear about:
> Toronto · York Region · Peel · Halton/Hamilton · Durham/Peterborough · Southwest
>
> Pick as many as you like, and change them any time.
>
> 🏪 **Running a store or an event?** DM me for the **Event Organizer** role, so you can ping
> these regions when you post in #local-store-events.
>
> 📣 **League News**
> Season news and Invitational qualifiers now ping **League News** instead of @everyone. You
> already have it, so you won't miss anything. Don't want it? Untick it in the same place.
> @everyone is now for emergencies only.
>
> ❓ **Got a question?** Ask in #questions: the league, results, roles or the bot.
>
> I've also tidied the channels into **League**, **Find Events** and **Community**. Thanks for
> reading! 💜

Existing members never see the Onboarding question, so region roles lead the announcement as an
action. Pin it. After the first few region pings go out, a short unpinged follow-up
("Missed a local event ping? Pick your region in <id:customize>.") reaches whoever skipped it.

3. **#start-here and the #league-rules FAQ** carry the `<id:customize>` link, for anyone who
   missed the announcement.

---

## Server Guide

Server Settings → Onboarding → Server Guide.

**Welcome message**

> Welcome to the GTA Lorcana League! We track results from local Lorcana tournaments across the
> GTA all season, and the top 32 are invited to an Invitational. Play at a store, get your
> results reported, and climb the leaderboard.

**New member to-dos** (each opens a channel; Discord ticks it off)

| # | Title | Channel | Action |
|---|---|---|---|
| 1 | 📋 Learn how the league works | #league-rules | View |
| 2 | 📍 Find a store near you | #where-to-play | View |
| 3 | 🏆 Report an event you played | #results-reporting | View |
| 4 | ⭐ See how roles are earned | #roles | View |
| 5 | 👋 Say hi | #general | Send a message |

No nickname to-do (the rules screen asks for it) and no region to-do (the Onboarding question
covers it).

**Resource pages**

A resource page shows a read-only channel as a cleaner page that starts at the top, and takes
the channel out of the channel list unless the keep-in-list option is on. So only static
reference channels, with that option on if available:

| Channel | Title | Description |
|---|---|---|
| #league-rules | League Rules & FAQ | Seasons, scoring, tiebreakers and the Invitational |
| #roles | Roles | How rarity roles are earned, and where to pick pings and regions |

Not pages: the digests (#where-to-play, #gta-prereleases, …) are checked often from the sidebar;
#etb-discount's post has a button that may not work in page view; #questions isn't read-only.
The bot edits its posts by message ID, so a channel shown as a page still updates.

---

## #start-here (draft, bot-owned later)

👋 **Welcome to GTA Lorcana**

**Playing in the league**
Play constructed events at a local store, and have someone post the results in
#results-reporting. The bot logs them, and the leaderboard updates on its own.
Rules, scoring and the Invitational: #league-rules.

**Finding events**
#where-to-play: stores running weekly events
#gta-prereleases: prereleases for the next set
#gta-set-championships: Set Championships in the area
#ccqs: CCQs within 600 km
#local-store-events: events posted by stores and players

**Getting pinged**
Pick your regions and **League News** in <id:customize>. Choose as many regions as you like.
Run a store or organize events? DM a staff member for the **Event Organizer** role, so you can
ping region roles in #local-store-events.

**Roles**
Rarity roles are earned from league results each season and never taken away. See #roles.

**Questions?** Ask in #questions. To report a problem privately, DM a staff member (Enchanted role).

---

## Setup checklist

Done: region roles and League News (unmentionable), Event Organizer role with its #local-store-events
override, #local-store-events slowmode and topic, Enchanted can mention all roles.

- [ ] AutoMod: @everyone/@here keyword block, mention limit 3 (both exempt Enchanted and GTA Lorcana Bot)
- [ ] Onboarding: every public channel as a default; the pings question, then the region question; turn it on
- [ ] Server Guide: welcome, to-dos, resource pages
- [ ] Paste the updated rule 5 into #community-rules
- [ ] One-off: League News to every Common member (run from the repo)
- [ ] Post the announcement
- [ ] Delete the Promo role (0 members)
- [ ] Verification is **Very High** (verified phone). High (account older than 5 minutes) was
      the #8 plan; pick one
