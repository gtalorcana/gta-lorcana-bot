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
Player runs /etb-discount in Discord
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

## Shopify Whitelist Mechanism (CONFIRMED)

Kris goes into the ETBGTALORCANA discount code in Shopify and adds
customers one by one to a customer-specific allowed list.

This is Shopify's **Price Rule customer selection** feature. The API flow:

```
1. GET /admin/api/2024-01/customers/search.json?query=email:{email}
   → returns customer object including customer_id
   → if not found → error: create ETB account first

2. Check if customer_id already in price rule customer selection
   GET /admin/api/2024-01/price_rules/{ETB_PRICE_RULE_ID}/customer_selection.json
   → if already present → already approved

3. POST /admin/api/2024-01/price_rules/{ETB_PRICE_RULE_ID}/customer_selection.json
   { "customer_selection": { "customer_ids": [customer_id] } }
   → adds customer to allowed list
```

`ETB_PRICE_RULE_ID` is a constant — Kris shares the ID once, hardcoded
in `constants.py`. No need to fetch it dynamically each request.

Required from Kris (see Shopify API Setup section):
- API token with `read_customers`, `read_price_rules`, `write_price_rules`
- `.myshopify.com` domain
- `price_rule_id` for ETBGTALORCANA

---

## Discord Bot Command — `/etb-discount`

### Command signature

```
/etb-discount rph_username:ryanfan email:ryan@enterthebattlefield.ca
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
> Create an account at enterthebattlefield.ca first, then run /etb-discount again.

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

## Deduplication

Approvals are deduped by **Discord ID** (column A of ETB Approvals tab).

One Discord account = one person. If someone runs `/etb-discount` twice with
a different email or RPH username, Step 3 blocks the second attempt —
they're already approved regardless of what they submit.

Email and RPH username are not reliable dedup keys: RPH display names can
change, and someone could have multiple ETB accounts.

**Edge case:** if a player needs to re-link (e.g. switched ETB email),
an admin must manually delete their row from the ETB Approvals sheet.
There is no bot command for this — handle manually.

---

## PII and Privacy

The ETB Approvals sheet stores Discord IDs, RPH usernames, and email addresses.
These are personally identifiable — treat accordingly:

- ETB Approvals sheet must remain private (it already is — same spreadsheet as Bot State)
- Never expose approval records in public Discord channels
- Any admin audit command (`/list-approved` etc.) must be restricted
  to Ryan's Discord user ID and respond via DM only, never public channel
- If adding logging or debugging, never print email addresses to console
  or Discord

---

## Shopify API Setup — COMPLETE ✅

Kris has created the GTA Lorcana Bot app via the Shopify Dev Dashboard
and provided three values. Store as Fly.io secrets:

```bash
fly secrets set SHOPIFY_CLIENT_ID="..." \
  SHOPIFY_CLIENT_SECRET="..." \
  SHOPIFY_STORE_DOMAIN="enterthebattlefield.myshopify.com" \
  --app gta-lorcana-bot
```

### Token Flow (Shopify Dev Dashboard — post January 2026)

Shopify no longer provides static `shpat_` tokens via the Dev Dashboard.
Instead the bot exchanges Client ID + Secret for a short-lived access
token that expires every 24 hours.

```python
async def get_shopify_token():
    url = f"https://{SHOPIFY_STORE_DOMAIN}/admin/oauth/access_token"
    payload = {
        "client_id": SHOPIFY_CLIENT_ID,
        "client_secret": SHOPIFY_CLIENT_SECRET,
        "grant_type": "client_credentials"
    }
    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=payload) as resp:
            data = await resp.json()
            return data["access_token"]  # valid for 24 hours
```

Cache the token in memory at bot startup. Refresh if a Shopify API
call returns 401, or proactively after 23 hours:

```python
_shopify_token = None
_shopify_token_expires_at = None

async def get_cached_shopify_token():
    global _shopify_token, _shopify_token_expires_at
    if _shopify_token is None or datetime.utcnow() >= _shopify_token_expires_at:
        _shopify_token = await get_shopify_token()
        _shopify_token_expires_at = datetime.utcnow() + timedelta(hours=23)
    return _shopify_token
```

### Scopes granted in Dev Dashboard
- `read_customers` — look up customer by email
- `read_price_rules` — find ETBGTALORCANA price rule
- `write_price_rules` — add customer to allowed list
- `write_customers` NOT granted — bot never modifies customer profiles

---

## Bot State Schema

New keys added to Bot State sheet:

| Key | Value |
|-----|-------|
| `etb_discount:{discord_id}` | JSON: `{rph_username, rph_id, email, approved_at, events_count}` |

---

## Future — Automatic Discount on Qualification

Currently `/etb-discount` is pull-based: the player must run the command
after they have 3 events. A better UX would be push-based: the bot
automatically applies the discount the moment a player qualifies.

**How it would work:**

Players run `/etb-discount` at any time (even before 3 events) to register
their email. The command saves Discord ID + email to ETB Approvals but does
not require 3 events yet — it just records intent.

After each event is processed by the results pipeline, the bot checks
if any newly-eligible players (just hit 3 events this season) have a
registered email in ETB Approvals. If so, it whitelists them in Shopify
and DMs them the code automatically.

**Data already exists to support this:**
- Player Registry: Playhub ID → Discord ID (via `/link`)
- ETB Approvals: Discord ID → email (via `/etb-discount` registration)
- Standings: Playhub ID → event count (written by results pipeline)

**Requires:**
- Player Registry to be well-populated (most active players linked)
- Results pipeline to trigger the eligibility check after each event write

---

## Onboarding Integration (Phase 2)

After `/etb-discount` is working, add an onboarding prompt.
When a new member joins the Discord server, bot sends a DM:

> Welcome to GTA Lorcana! 🎴
>
> Run /etb-discount in the server to:
> ✅ Unlock the Enter the Battlefield community discount
> ✅ Link your RPH account for tournament tools
> ✅ Get recognized for your event attendance

---

## Build Order

1. Implement `/etb-discount` command in `bot.py`
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

---

## Token Scope Explanation for Kris

The API token the bot uses has the minimum permissions needed to add
customers to the ETBGTALORCANA discount. Here is exactly what it can
and cannot do:

### What the token CAN do

| Scope | Why needed |
|-------|-----------|
| `read_customers` | Look up a customer account by email to get their ID |
| `write_customers` | Not actually needed — can be removed if Kris prefers |
| `read_price_rules` | Find the price rule for ETBGTALORCANA |
| `write_price_rules` | Add a customer to the allowed list on that price rule |

### What the token CANNOT do

- ❌ View orders or purchase history
- ❌ Access financials, payouts, or reports
- ❌ Change store settings
- ❌ Create or modify products
- ❌ Issue refunds
- ❌ Access any other discount codes besides ETBGTALORCANA
- ❌ Remove customers from the discount list
- ❌ Browse or export customer lists

### What the bot actually does with the token

```
1. Search for one specific customer by the email the user provided
2. Get that customer's Shopify ID (a number — not stored or logged)
3. Add that ID to the ETBGTALORCANA price rule's allowed customer list
```

The bot never browses customer lists, never stores customer data from
Shopify beyond the customer_id temporarily in memory, and never logs
personally identifiable information.

### Note on read_customers scope

Shopify does not offer sub-scopes within read_customers — the token
either can or cannot look up customers. There is no "email lookup only"
option. However the bot only ever queries for a specific email that the
Discord user themselves provided, never browses or exports customer data.

### Revoking access

Kris can revoke the token at any time:
Shopify Admin → Settings → Apps and sales channels → GTA Lorcana Bot → Delete app

This immediately invalidates the token with no further action needed.

### Recommended: hardcode the price_rule_id

Rather than having the bot fetch the price rule by name each time
(which would require reading all price rules), Kris can share the
`price_rule_id` for ETBGTALORCANA directly. This means the token
only ever touches that one specific price rule — not any others.

To find the price_rule_id:
Shopify Admin → Discounts → ETBGTALORCANA → the ID is in the URL:
`/discounts/price_rules/{price_rule_id}/...`

Share this ID with Ryan to hardcode as `ETB_PRICE_RULE_ID` in constants.

---

## Token Scope Explanation for Kris

The API token the bot uses has the following scopes:

| Scope | What it allows | What the bot uses it for |
|-------|---------------|--------------------------|
| `read_customers` | Read customer profiles | Look up customer by email to get their ID |
| `read_price_rules` | Read discount/price rules | Look up the ETBGTALORCANA price rule ID |
| `write_price_rules` | Update price rules | Add approved customer to the allowed list |

`write_customers` is NOT needed and should NOT be granted — the bot
never modifies customer profiles.

**What the token CAN'T do:**
- Access orders or order history
- Access financials, payouts, or reports
- Access store settings or staff accounts
- Create or delete products
- Access any other store data

**What the bot actually does with the token:**
1. Searches for a customer by the email the user provided
2. Checks if that customer is already on the ETBGTALORCANA allowed list
3. If not, adds them to the allowed list — same action Kris does manually

The bot never stores the full customer object. It only keeps the
`customer_id` internally to make the API call. No customer data is
logged or exposed anywhere.

**The token lives in:**
- Fly.io secrets (encrypted, never in code or git)
- Only accessible by the bot process itself

Kris can revoke the token at any time from:
Shopify Admin → Settings → Apps and sales channels → GTA Lorcana Bot → Uninstall

---

## Confirmed: Shopify Whitelist Mechanism

Kris uses **customer-specific discount codes** — the `ETBGTALORCANA` code
has a price rule that restricts it to an allowed customer list, and he
adds customers one by one manually.

**API calls required:**

```
# Step 1 — Look up customer by email
GET /admin/api/2024-01/customers/search.json?query=email:{email}
→ get customer_id

# Step 2 — Get price rule for ETBGTALORCANA (fetch once, or hardcode ID)
GET /admin/api/2024-01/price_rules.json
→ find rule where discount code == "ETBGTALORCANA"
→ get price_rule_id

# Step 3 — Check if already on allowed list
GET /admin/api/2024-01/price_rules/{price_rule_id}/customer_selection.json
→ check if customer_id already in customers list

# Step 4 — Add to allowed list
POST /admin/api/2024-01/price_rules/{price_rule_id}/customer_selection.json
{ "customer_selection": { "customers": [{ "id": customer_id }] } }
```

**Note on price_rule_id:**
This ID is static and won't change unless Kris recreates the discount.
Options:
- Ask Kris for the ID once and store as `ETB_PRICE_RULE_ID` in constants
- Or fetch dynamically on each call (one extra API call but more robust)

Recommended: fetch it once during bot startup and cache it in memory.
If it ever changes (Kris recreates the discount), a bot restart picks
up the new ID.

**Required from Kris:**
- Shopify Admin API access token (scopes above)
- Store `.myshopify.com` domain for ETB