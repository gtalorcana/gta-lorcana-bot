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

**Recording and awarding are separate.** The season-close checklist's record steps decide what a player has earned and write it to the registry; assigning reads the registry back and makes Discord match. The registry is the single source of truth — Discord is downstream of it, never the other way round.

1. **Linking.** Every time results are processed, each unlinked player gets one suggestion in the mod channel: a button per close Discord match (top 3, ≥50%), a member picker for anyone else, and Skip. Linking grants the roles the registry records straight away. A player is suggested **once** — column F of their registry row holds `suggested:<message id>` or `skipped:<mod>` — and re-posted only if that message was deleted. When someone joins the server they are checked against every unlinked player (skipped ones included) and suggested at ≥75%; a returning *linked* member gets their earned roles back instead.

2. **Record Rare/Uncommon** — the checklist's **Record & assign roles** button, at season end. Reads the `Leaderboard` tab and writes the season into columns I/J for everyone at rank ≤ 32 or with 10+ events. Matches the registry by **Playhub ID** (the `Player ID` column), falling back to display name only when the ID is blank, so a player who renamed on RPH since linking is still found. Grants no Discord roles.

3. **Record Legendary/Super Rare** — the checklist's **Submit invitational URL** button, after an invitational. Previews the eight finishers with Confirm / Cancel; Confirm re-fetches the standings, writes columns G/H for all eight (including anyone not yet linked) and grants the Discord roles. A recorded season is only replaced by an *earlier* one, never a later one.

4. **Assign** — `/assign-roles-from-registry`. Walks every registry row holding a Discord ID and grants any rarity role recorded in G–J that the member is missing.

   Additive only, never removes, so it is idempotent and safe to re-run at any time. That also makes it the repair path after a hand edit to the registry, a manual removal, or a failed `add_roles` call. A member who leaves and rejoins gets their roles back automatically on join.

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

If a player's Playhub display name changes, column A follows. Whenever a row is matched by **Playhub ID** and the incoming name differs, `link_player` and `batch_upsert_player_roles` rewrite column A to the newer name — so recording results, linking, and the ETB flow all keep it current.

Only on an ID match. A name match tells you nothing new about the name by definition, and acting on one risks renaming a row that was matched loosely. Rows with no Playhub ID are therefore never renamed — name is the only key they have.

That only reaches rows something touches by ID, so a player who stops competing would keep a stale name indefinitely. `/tidy-registry` closes the gap: it builds `{playhub_id: name}` from the current season's Standings and Leaderboard and renames any row whose ID matches and whose name differs. Current season only — a player who hasn't competed this season keeps their existing name until they next appear.
