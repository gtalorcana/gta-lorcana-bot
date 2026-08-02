# League Rarity Roles

Members earn rarity roles based on league participation. Roles are additive — earning a higher tier does not remove the lower one. Roles are never downgraded.

| Role | How earned |
|------|------------|
| Common | Assigned automatically on server join |
| Uncommon | 10+ distinct events played in the season |
| Rare | Finished top 32 on the season leaderboard |
| Super Rare | Top 8 finisher at an invitational event |
| Legendary | Winner of an invitational event |

---

## How It Works

**Recording and awarding are separate.** Two commands decide what a player has earned and write it to the registry; a third reads the registry back and makes Discord match. The registry is the single source of truth — Discord is downstream of it, never the other way round.

1. **Linking.** Every time results are processed, the bot fuzzy-matches any new Playhub players against Discord members and posts suggestions to the mod channel. Mods react ✅/❌ to confirm or skip each match.

2. **Record Rare/Uncommon** — `/record-rare-and-uncommon`, at season end. Reads the `Leaderboard` tab and writes the season into columns I/J for everyone at rank ≤ 32 or with 10+ events. Matches the registry by **Playhub ID** (the `Player ID` column), falling back to display name only when the ID is blank, so a player who renamed on RPH since linking is still found. Grants no Discord roles.

3. **Record Legendary/Super Rare** — `/record-legendary-and-super-rare <event_url> [season]`, after an invitational. Previews the eight finishers for ✅, then writes columns G/H. All eight are recorded, including anyone not yet linked. Grants no Discord roles.

   `season` defaults to the current season. Pass it explicitly when backfilling an older invitational (e.g. `/record-legendary-and-super-rare <url> S11`) so the finish is recorded against the season it belongs to. Backfills are safe in any order: a recorded season is only replaced by an *earlier* one, never a later one.

4. **Assign** — `/assign-roles-from-registry`. Walks every registry row holding a Discord ID and grants any rarity role recorded in G–J that the member is missing.

   Additive only, never removes, so it is idempotent and safe to re-run at any time. That also makes it the repair path: if someone loses a role by leaving and rejoining, by manual removal, or because an `add_roles` call failed, this restores it. Nothing else in the bot does.

Anyone recorded but not yet linked simply waits — `link_player` reads G–J when they are eventually linked and grants what it finds.

---

## Player Registry

Stored in the `Player Registry` tab of the Bot Database sheet.

| Column | Field | Notes |
|--------|-------|-------|
| A | Playhub Name | Display name from RPH — auto-updated if player renames |
| B | Playhub ID | Numeric RPH player ID |
| C | Discord ID | Blank = unlinked |
| D | Discord Display Name | |
| E | Linked At | UTC ISO timestamp |
| F | Link Method | `fuzzy-confirmed`, `manual:<mod>`, etc. |
| G | Legendary | Season first earned (e.g. `S10`) |
| H | Super Rare | Season first earned |
| I | Rare | Season first earned |
| J | Uncommon | Season first earned |

Role columns hold the **earliest** season earned. A blank cell always takes the new value; a populated one is replaced only by a genuinely earlier season, compared numerically so `S9` correctly beats `S10`.

Recording seasons in order, that never triggers — the season being recorded is always later than what's stored, so the existing value stands. It matters only when an old season is recorded late (backfilling an invitational, or repointing `CURRENT_SEASON` to fix data that was missed). Both record commands follow this rule, so backfills are safe in any order and can't be defeated by a later season already sitting in the cell.

New rows are written to an explicitly computed row (`A{n}:J{n}`), never via the Sheets `values.append` API. Append picks its own anchor column by table detection, and rows created by `/record-rare-and-uncommon` hold data only in A and I, leaving a B–H gap that can make it anchor on the wrong column.

---

## Mod Channel Flow

After every successful results import, the bot checks which Playhub player IDs aren't yet linked to a Discord account and posts to the mod channel:

| Embed | Confidence | Action |
|-------|------------|--------|
| 🔗 Suggested Match (yellow) | ≥ 75% | React ✅ to confirm, ❌ to skip |
| 🔗 Low-Confidence Match (orange) | 50–74% | Use `/link @member <playhub_id>` manually |
| ❓ No Match (red) | < 50% | Use `/link @member <playhub_id>` manually |

After a mod reacts:
- ✅ → writes Discord ID to registry, assigns any earned roles
- ❌ → skips; player will surface again next event they attend

---

## Fuzzy Matching

Matching compares the RPH display name against Discord `display_name` and `global_name` using `SequenceMatcher`.

- ≥ 75% → high confidence, auto-suggest with reaction prompt
- 50–74% → low confidence, surface for manual `/link`
- < 50% → no match, requires manual `/link`

If a player's Playhub display name changes, `upsert_player_roles` detects the mismatch (when looking up by Playhub ID) and updates column A automatically.
