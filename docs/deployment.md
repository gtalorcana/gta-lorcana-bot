# Deployment

## Fly.io Secrets

Required secrets:

```
DISCORD_BOT_TOKEN
WORKER_URL
WORKER_SECRET
GOOGLE_CREDENTIALS_JSON
GOOGLE_TOKEN_JSON
```

All other IDs (guild, channel, role) are hardcoded in `constants.py` and can be overridden via `.env` locally — they don't need to be Fly.io secrets.

## Deploy

```bash
fly deploy
```

Slash commands are synced automatically on every deploy via the `release_command` in `fly.toml` — no manual step needed.

---

## Google Token Refresh

Google access tokens expire after ~1 hour and are refreshed automatically in-process. The underlying refresh token is long-lived but will eventually expire if unused for 6+ months or if revoked.

If the bot logs `401` or `invalid_grant`:

1. Delete `var/token.json` locally
2. Run `python bot.py` locally — a browser OAuth flow will regenerate it
3. Update the Fly.io secret:
   ```bash
   fly secrets set GOOGLE_TOKEN_JSON="$(cat var/token.json)" --app gta-lorcana-bot
   ```

---

## Local Development

```bash
pip install -r requirements.txt
```

Place `var/token.json` and `var/credentials.json` in the `var/` directory (gitignored).

Create a `.env` for local overrides:

```env
DISCORD_BOT_TOKEN=...
WORKER_URL=...
WORKER_SECRET=...
MOD_CHANNEL_ID=...
WHOS_GOING_POST_HOUR_ET=9
WHERE_TO_PLAY_POST_HOUR_ET=23
```

```bash
python bot.py
```

Use `/wheretoplay` in Discord to trigger the where-to-play post manually without waiting for the schedule.

To test store debug sheet writes against a copy of the spreadsheet without touching production:
```bash
python scripts/test_debug_sheet.py
```
Set `TEST_STORE_SPREADSHEET_ID` in the script to a spreadsheet ID with a blank `Store Debug` tab.

---

## Optional Environment Variables

| Variable | Default | Notes |
|----------|---------|-------|
| `MOD_CHANNEL_ID` | *(hardcoded)* | Mod channel for linking suggestions and role previews |
| `COMMON_ROLE_ID` | *(hardcoded)* | |
| `UNCOMMON_ROLE_ID` | *(hardcoded)* | |
| `RARE_ROLE_ID` | *(hardcoded)* | |
| `SUPER_RARE_ROLE_ID` | *(hardcoded)* | |
| `LEGENDARY_ROLE_ID` | *(hardcoded)* | |
| `WHOS_GOING_POST_HOUR_ET` | `7` | Hour (ET) to post daily polls |
| `WHERE_TO_PLAY_POST_HOUR_ET` | `23` | Hour (ET) to post Sunday where-to-play |
| `CURRENT_SEASON` | `S11` | Used in sheet tab names |
| `RPH_RETRY_ATTEMPTS` | `2` | Auto-retry attempts on RPH API failure |
| `RPH_RETRY_DELAY` | `300` | Seconds between retries |
