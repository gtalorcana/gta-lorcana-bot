# Discord Surface — Design Notes

## Key Design Decisions

- `ADMIN_USER_IDS` is a list (not set) — supports indexing for pings and `in` checks

---

## Crash-Loop Prevention

On startup, the bot automatically rechecks any unprocessed threads from the last 3 days (threads without a ✅ reaction). This catches threads that were mid-flight when the bot crashed or restarted.

To prevent a bad thread from causing an infinite crash loop, the bot tracks each startup recheck attempt in Bot State:

1. Before processing a thread, `recheck:<thread_id>` is written to Bot State
2. If the bot crashes mid-processing and restarts, the key is already set
3. On the next startup, that thread is **skipped** — the bot adds ❌ and pings the admin instead
4. If processing completes successfully, the key is cleared

This means a bad thread will be attempted exactly once on startup. After that it requires manual intervention via `/recheck` or by deleting and resubmitting the thread.
