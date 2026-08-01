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

1. Every time results are processed, the bot fuzzy-matches any new Playhub players against Discord members and posts suggestions to the mod channel. Mods react ✅/❌ to confirm or skip each match.
2. At season end, run `/sync-roles` to compute and apply Uncommon/Rare upgrades based on the full standings. It reads the `Leaderboard` tab and matches each earner to the registry by **Playhub ID** (the `Player ID` column), falling back to display name only when the ID is blank — so a player who renamed on RPH since linking still gets their roles.
3. After an invitational, run `/invitational-roles <event_url> [season]` to preview and assign Super Rare / Legendary. On ✅ it assigns the Discord roles **and** records the finish in registry columns G/H — for all eight finishers, including any who aren't linked yet, so the role is granted automatically whenever they do link.

   `season` defaults to the current season. Pass it explicitly when backfilling an older invitational (e.g. `/invitational-roles <url> S11`) so the finish is recorded against the season it belongs to. Backfills are safe in any order: a recorded season is only replaced by an *earlier* one, never a later one.

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

Role columns are only written if blank — the earliest season earned is always preserved. `/invitational-roles` additionally replaces a recorded season with an earlier one, so backfilling old events can't be defeated by a later season already sitting in the cell.

New rows are written to an explicitly computed row (`A{n}:J{n}`), never via the Sheets `values.append` API. Append picks its own anchor column by table detection, and rows created by `/sync-roles` hold data only in A and I, leaving a B–H gap that can make it anchor on the wrong column.

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
