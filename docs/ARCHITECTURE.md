# Architecture

## Runtime shape

One process, one container:

```
uvicorn (ASGI)
  └── FastAPI app (app.main)
        ├── HTTP routes: /, /settings, /modules/*, /activity, /healthz
        ├── APScheduler BackgroundScheduler (UTC)
        │     └── job.recurring_hygiene → recurring_hygiene.run()
        └── db (SQLite + Fernet)
              ├── kv          — encrypted token, module config, account meta
              └── events      — append-only activity log (pruned by module)
```

No background workers, no message queue, no external cache. Everything is
in-process. The scheduler holds a reference to `_run_recurring_hygiene_job`
which re-reads config from SQLite on every fire.

## Request path

The service assumes upstream auth (Authelia via nova-config Traefik). It does
not do its own login. If the container is ever exposed without a gate, the
Todoist token is trivially exfiltrated via the Settings page. **Never bind it
directly to a public interface.**

`--proxy-headers --forwarded-allow-ips=*` on uvicorn is deliberate — we want
the reverse proxy to set `X-Forwarded-*` for correct redirect URLs.

## Data model

Two tables, both created by `db.init_schema()`:

**`kv`** — small key/value store, JSON-encoded values.
Known keys:
- `auth.todoist_token`      — Fernet-encrypted Todoist API token
- `auth.todoist_account`    — `{id, email, full_name}` from `/user`
- `module.recurring_hygiene.config` — see `recurring_hygiene.DEFAULT_CONFIG`

**`events`** — append-only activity log. Every module writes here via
`db.log_event(module, action, ...)`. Retention is per-module: the module's
own `keep_events_days` config drives `db.prune_events()` after each run.

The event log doubles as the audit trail *and* the user-visible activity
feed. `id, ts, module, level, task_id, action, detail(JSON string)`.

## Todoist API notes

Client lives in `app/todoist.py`. Only three endpoints are used:

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/v1/user` | GET | Token validation + account metadata |
| `/api/v1/sync` | POST (form) | Full pull (`sync_token=*`, `resource_types=["items"]`) |
| `/api/v1/sync` | POST (form) | Command batch (`commands=[{type: "item_update", ...}]`) |

Historical gotcha: Todoist retired the `/sync/v9/*` endpoints — they return
HTTP 410. The base URL is now `/api/v1`. Payload shapes are unchanged. See
commit `4d9d93e` for the migration.

**Recurrence preservation:** to move a recurring task's next occurrence to
today without breaking the recurrence rule, submit an `item_update` command
with `due: {"string": "today"}`. The server re-parses "today" against the
existing rrule so subsequent occurrences continue on the same cadence.
Setting `due.date` directly on a recurring item strips the recurrence.

**Rate limits:** we do a single full sync per run. No incremental sync token
is stored yet — the module runs infrequently enough (daily by default) that
this is fine. If more modules land or run frequency increases, adopt an
incremental sync token stored in `kv`.

## Scheduler

`app.main.scheduler` is a single `BackgroundScheduler` in UTC. Each module
that runs on a cron owns a job whose id is `job.<module_name>`.
`reload_schedule()` is idempotent: it removes the existing job (if any) and
re-adds it from the module's current config. Called on startup and after
every config POST.

`max_instances=1, coalesce=True` — if a run is still in flight when the next
trigger fires, it's skipped, not queued. This matches the intent: hygiene
runs are idempotent and losing a beat is fine.

## Crypto

`ENCRYPTION_KEY` is a Fernet key (URL-safe base64, 32 bytes decoded). It
encrypts only the Todoist API token. If the key is lost or rotated, the
stored token becomes unreadable — the user re-enters it via Settings.
This is by design: no key-recovery flow, no key rotation logic.

## Health

`GET /healthz` returns `{"status": "ok"}` unconditionally. It confirms the
process is up and serving — it does *not* check Todoist connectivity or the
scheduler. Watchtower/WUD uses the container's Docker HEALTHCHECK (defined
in the Dockerfile) which hits `/healthz`.

## Deployment coupling

CI (`.github/workflows/build.yml`) only builds and pushes to GHCR. Redeploy
is Watchtower/WUD on the host reading the new digest. The nova-config repo
owns:
- The compose service definition
- Authelia rules
- Traefik labels
- The named volume for `/data`

Do not add deploy steps here. If a new env var is needed, coordinate with
nova-config to add it to the compose file.
