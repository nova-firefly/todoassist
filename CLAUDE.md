# CLAUDE.md

Context file for AI-assisted development on **todoassist**. Read this first;
follow the pointers into `docs/` only when you need the detail.

## What this is

Single-user, single-account Todoist automation service. FastAPI + SQLite +
APScheduler in one container. Deployed behind Authelia via
[nova-config](https://github.com/nova-firefly/nova-config). Auth is external —
this app assumes any request that reaches it is authenticated.

## Core intent (do not violate)

- **Single user, single account.** No multi-tenant abstractions, no user table,
  no role-based access. One Todoist token, stored encrypted in SQLite.
- **No plugin framework.** New modules are hard-coded in `app/main.py`. Adding
  indirection to "make it extensible" is out of scope.
- **State is disposable.** `/data/state.db` can be nuked and rebuilt from the
  UI (paste token, reconfigure modules). No migrations framework — schema is
  created inline in `db.init_schema()`.
- **Dry-run first.** Any module that mutates Todoist state must default to
  `dry_run=True` and expose it in the UI. Recurring hygiene is the reference.
- **Preserve recurrence rules.** When rescheduling recurring tasks, use
  Todoist's Sync API `item_update` with `due: {string: "today"}` so the server
  re-parses against the existing rrule. Do **not** set `due.date` directly on
  recurring items — it destroys the recurrence.
- **Configurable, not opinionated.** Modules must expose their behavior via
  config (labels, projects, cadence, thresholds), not bake in workflow
  choices. The user has no fixed workflow — the app should adapt to whatever
  they set up. When defaults or example values are needed, prefer GTD
  vocabulary (contexts like `@home`/`@computer`, statuses like `next`,
  `waiting`, `someday`, `inbox`).

## Layout

```
app/
  main.py                 FastAPI routes, scheduler wiring, token helpers
  db.py                   SQLite + Fernet crypto + event log
  todoist.py              Minimal Todoist /api/v1 client (Sync API)
  recurring_hygiene.py    First module: stale recurring task detector
  templates/              Jinja2 server-rendered UI (no JS framework)
.github/workflows/        GHCR image build (ubuntu-latest, WUD redeploys)
Dockerfile                python:3.12-alpine, non-root from PID 1
docs/                     Deeper context (architecture, module spec, conventions)
```

## Fast facts

- Python 3.12, FastAPI, Jinja2 server-rendered HTML, no frontend framework.
- SQLite at `/data/state.db` (WAL). Two tables: `kv`, `events`.
- Todoist token is encrypted with Fernet (`ENCRYPTION_KEY` env var).
- Scheduler is `apscheduler.BackgroundScheduler(timezone="UTC")` — cron
  expressions are parsed with `CronTrigger.from_crontab`.
- Todoist API base: `https://api.todoist.com/api/v1` (the retired `/sync/v9`
  endpoints return 410).
- Image published to `ghcr.io/nova-firefly/todoassist:{latest, sha-<short>}`.
  Redeploy is handled by Watchtower/WUD on the nova-config host, not by CI.
- Container runs as UID/GID 1000 (`app:app`) with `cap_drop: ALL` — no
  privileged startup needed. `/data` is pre-`chown`ed in the image.

## When making changes

- **Before editing:** read the file. Match its style — the codebase is small
  and consistent (type hints, `from __future__ import annotations`, dataclasses
  for API DTOs, docstrings only where the *why* is non-obvious).
- **New module:** see `docs/MODULES.md` — the pattern is a single file under
  `app/`, a `MODULE` string, a `get_config`/`set_config` pair, and a `run()`
  entrypoint. Wire it into `app/main.py` by hand.
- **Todoist API:** don't add HTTP helpers speculatively. `todoist.py` only has
  what the existing module needs; grow it one call at a time. See
  `docs/ARCHITECTURE.md` for the Sync API quirks.
- **Schema changes:** update `db.init_schema()`. If the change is not
  backwards-compatible, bump the schema and let the user reset — no Alembic.
- **Secrets:** never log the Todoist token or write it to disk in plaintext.
  `ENCRYPTION_KEY` is the only thing between the token and the filesystem.
- **UI:** Jinja2 templates only. No React, no HTMX, no client-side JS beyond
  what the browser gives us. Forms POST and redirect (303).

## Non-goals

- Multi-user or team support.
- OAuth flow for Todoist — the personal API token from Settings → Integrations
  is the intended auth path.
- Generic "task automation platform." This is a Todoist-specific tool for one
  person's workflow.
- Test suite. There isn't one and one is not planned. If you write tests,
  discuss first — they must not require live Todoist creds.

## Deeper context

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — data flow, Todoist API notes,
  scheduler behavior, why certain decisions were made.
- [docs/MODULES.md](docs/MODULES.md) — module spec + adding a new module.
- [docs/CONVENTIONS.md](docs/CONVENTIONS.md) — coding style, event log
  conventions, config storage patterns.
- [docs/OPERATIONS.md](docs/OPERATIONS.md) — deployment, env vars, log
  levels, recovery from a lost encryption key.
