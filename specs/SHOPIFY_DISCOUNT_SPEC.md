# Shopify Discount Automation — Spec

## Overview

Automate eligibility verification and Shopify customer whitelisting for the
Enter the Battlefield GTA Lorcana community discount.

**Discount code:** `ETBGTALORCANA` (public, fixed — not generated per user)
**Store:** `enterthebattlefield.ca`
**Gating mechanism:** Kris manually whitelists customer email accounts in Shopify
so the code works at checkout. The automation replaces this manual step.

---

## Current Flow (Manual)

```
Player fills Google Form
  → Ryan manually verifies RPH username exists in 3 submitted events
  → Ryan notifies Kris to whitelist the email in Shopify
  → Kris manually updates Shopify customer account
  → Ryan DMs player the code ETBGTALORCANA
```

## Target Flow (Automated)

```
Player runs /link-rph in Discord
  → Bot verifies RPH username exists on RPH
  → Bot verifies player has attended 3+ GTA Lorcana events this season
  → Bot checks email is registered at enterthebattlefield.ca
  → Bot checks email not already whitelisted
  → Bot calls Shopify API to whitelist player's email
  → Bot DMs player: "You're approved! Use ETBGTALORCANA at enterthebattlefield.ca"
  → Bot stores discord_id → rph_id + email in Bot State
```

No Google Form. No manual verification. No manual Shopify update.

---

## Open Question — Shopify Whitelist Mechanism (BLOCKER)

**Need to confirm with Kris:** when he manually approves someone, what does
he actually do in Shopify? This determines the API call:

| Mechanism | What Kris does | API call |
|-----------|---------------|----------|
| Customer tag | Adds tag e.g. `gta-lorcana` to customer | `PUT /customers/{id}` with tags |
| Customer segment | Adds customer to a segment the code is restricted to | Segment membership API |
| Discount code restriction | Adds email to an allowed-emails list on the discount | Price rule customer API |

**→ This is the only remaining blocker. Everything else is ready to build.**
**→ Stub the Shopify call until confirmed (see Build Order).**

---

## Discord Bot Command — `/link-rph`

### Command signature

```
/link-rph rph_username:ryanfan email:ryan@enterthebattlefield.ca
```

Both parameters required. Standard slash command parameters — no modal needed.
Discord autocompletes parameter names as the user types.

### Validation steps

**Step 1 — RPH username exists:**
```
GET https://api.cloudflare.ravensburgerplay.com/hydraproxy/api/v2/users/?username={rph_username}
```
If not found:
> ❌ We couldn't find the RPH username "ryanfan".
> Check your username at tcg.ravensburgerplay.com and try again.

**Step 2 — Current season event attendance:**
Search RPH events for the player's ID filtered to the current season
(use `CURRENT_SEASON` from `constants.py` — same season variable used
throughout the bot).

Filter events to GTA Lorcana store IDs only (same store list used for
Where to Play). Count distinct events where player has
`registration_status` of `COMPLETE`.

Minimum threshold: **3 events.**

If fewer than 3 GTA events this season:
> ❌ You need at least 3 GTA Lorcana events this season to qualify.
> We found X event(s) on your record for this season.
> Keep playing and try again after your next event!

**Step 3 — Already approved check:**
Check Bot State for existing `etb_discount:{discord_id}` key.
If exists:
> You're already approved! 🎉
> Use code `ETBGTALORCANA` at enterthebattlefield.ca
> (Approved on Jan 15, 2026)

**Step 4 — Email registered at ETB check:**
Call Shopify API to look up customer by email:
```
GET /admin/api/2024-01/customers/search.json?query=email:{email}
```
If customer not found in Shopify:
> ❌ That email isn't registered at enterthebattlefield.ca.
> Create an account at enterthebattlefield.ca first, then run /link-rph again.

**Step 5 — Already whitelisted check:**
From the customer record returned in Step 4, check if the whitelist
mechanism is already applied (e.g. tag already present, or already in
segment). If already whitelisted:
> You're already approved! 🎉
> Use code `ETBGTALORCANA` at enterthebattlefield.ca
> Also update Bot State if key was missing (graceful recovery).

**Step 6 — Apply Shopify whitelist:**
Call Shopify API to whitelist the email.
(Exact call TBD — stub until Kris confirms mechanism.)

If Shopify call fails:
> ⚠️ Something went wrong on our end — Ryan has been notified and will
> approve you manually shortly. Sorry for the inconvenience!
> (Bot also DMs Ryan with the error details.)

**Step 7 — Store in Bot State and confirm:**
```
Bot State key: etb_discount:{discord_id}
Value: {
  rph_username,
  rph_id,
  email,
  approved_at,
  events_count
}
```

DM player:
> ✅ **You're approved for the ETB GTA Lorcana discount!**
>
> Discount code: `ETBGTALORCANA`
> Shop: enterthebattlefield.ca
>
> Your account (ryan@example.com) has been activated.
> The code will work at checkout on your next visit.
>
> Questions? Ask in #store-discounts.

---

## RPH Event Attendance Verification

**Season scoping:**
Use `CURRENT_SEASON` from `constants.py` to filter events to the current
season only. Easier and more meaningful than all-time — a player who
attended 3 events two seasons ago but never this season is not an active
community member.

**RPH endpoint:**
```
GET /hydraproxy/api/v2/users/{rph_id}/event-history/
```
Filter to:
- Events where `store_id` is in known GTA Lorcana store list
- Events within `CURRENT_SEASON` date range
- `registration_status` of `COMPLETE` (attended, not just registered)

Count distinct qualifying events. Minimum: **3.**

---

## Error Response Summary

| Situation | Response |
|-----------|----------|
| RPH username not found | ❌ Username not found, check at tcg.ravensburgerplay.com |
| Fewer than 3 GTA events this season | ❌ X of 3 events found, keep playing |
| Already approved (Bot State) | 🎉 Already approved, here's the code |
| Email not in Shopify | ❌ Create ETB account first |
| Already whitelisted (Shopify) | 🎉 Already approved + recover Bot State |
| Shopify API failure | ⚠️ Error, Ryan notified, manual fallback |

---

## PII and Privacy

Bot State stores Discord IDs, RPH usernames, and email addresses.
These are personally identifiable — treat accordingly:

- Google Sheet must remain private (it already is)
- Never expose Bot State contents in public Discord channels
- Any admin audit command (`/list-approved` etc.) must be restricted
  to Ryan's Discord user ID and respond via DM only, never public channel
- If adding logging or debugging, never print email addresses to console
  or Discord

---

## Shopify API Setup (Instructions for Kris)

Kris does not need to share his store password. Steps:

1. Shopify Admin → Settings → Apps and sales channels → Develop apps
2. Click **Create an app** → name it `GTA Lorcana Bot`
3. Under **Configuration** → Admin API access scopes, enable:
   - `write_customers` — to tag/update customer accounts
   - `read_customers` — to look up customers by email
4. Click **Install app** → copy the **Admin API access token**
5. Share the token and the store's `.myshopify.com` domain with Ryan

Token can be revoked at any time from the same screen.
Bot only touches customer whitelist — no access to orders, financials,
or store settings.

---

## Bot State Schema

New keys added to Bot State sheet:

| Key | Value |
|-----|-------|
| `etb_discount:{discord_id}` | JSON: `{rph_username, rph_id, email, approved_at, events_count}` |

---

## Future — Discord ↔ RPH Identity Linking

The `/link-rph` command stores `discord_id → rph_id` which unlocks
future features beyond the discount:

- `/safe-to-id` — no need to pick yourself from dropdown, bot knows your RPH ID
- Event attendance leaderboards
- Season standings
- Automatic recognition milestones ("You've attended 10 GTA events! 🎉")

The discount approval is the first use case but the identity link is
the real long-term value.

---

## Onboarding Integration (Phase 2)

After `/link-rph` is working, add an onboarding prompt.
When a new member joins the Discord server, bot sends a DM:

> Welcome to GTA Lorcana! 🎴
>
> Run /link-rph in the server to:
> ✅ Unlock the Enter the Battlefield community discount
> ✅ Link your RPH account for tournament tools
> ✅ Get recognized for your event attendance

---

## Build Order

1. Implement `/link-rph` command in `bot.py`
2. Implement RPH username lookup (Step 1)
3. Implement current season GTA event attendance check using `CURRENT_SEASON`
   from `constants.py` (Step 2)
4. Implement already-approved Bot State check (Step 3)
5. Implement Shopify customer email lookup — Step 4 (requires Kris token)
6. Implement already-whitelisted check — Step 5
7. **STUB** Shopify whitelist call — Step 6:
   ```python
   # TODO: implement once Kris confirms whitelist mechanism
   # STUB: log and treat as success for demo purposes
   logger.info(f"STUB: would whitelist {email} in Shopify here")
   ```
8. Implement Bot State storage and success DM (Step 7)
9. Test end-to-end with a real Discord account
10. Confirm mechanism with Kris → implement real Shopify call → remove stub
11. Announce in Discord, retire Google Form
12. (Phase 2) Add onboarding DM prompt