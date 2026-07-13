# Operations

## Environment variables

| Var | Required | Default | Notes |
|-----|----------|---------|-------|
| `ENCRYPTION_KEY` | yes | — | Fernet key. `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` |
| `PUBLIC_BASE_URL` | yes | — | e.g. `https://todoassist.example.com`. Used in redirects. |
| `LOG_LEVEL` | no | `INFO` | `DEBUG`/`INFO`/`WARNING`/`ERROR`. Applied to root + uvicorn. |
| `DATA_DIR` | no | `/data` | Where `state.db` lives. Must be writable by UID 1000. |

## Volumes

- `/data` — SQLite database + WAL files. Backed by a named docker volume in
  nova-config. Losing this loses config and event history but not the
  encryption key (which is in env).

## Container

- Base: `python:3.12-alpine`.
- Runs as UID/GID 1000 (`app:app`) from PID 1. `cap_drop: ALL` works —
  no `CAP_CHOWN` needed at startup because `/data` is pre-`chown`ed at
  build time.
- Healthcheck: `wget /healthz` every 60s. Failing 3× in a row marks the
  container unhealthy; the compose stack's restart policy handles the rest.

## Deploy path

1. Push to `main` → `.github/workflows/build.yml` runs on `ubuntu-latest`.
2. Image pushed to `ghcr.io/nova-firefly/todoassist:latest` and
   `:sha-<short>`.
3. Watchtower/WUD on the nova-config host detects the new digest and
   restarts the container.
4. No CI step touches the host. Do not add one — nova-config's WUD is the
   source of truth for redeploy.

The image labels `org.opencontainers.image.source` (via
`docker/metadata-action`), which lets GHCR auto-link the package to the
repo and inherit pull permissions.

## First-run setup

1. Start the container with `ENCRYPTION_KEY` and `PUBLIC_BASE_URL` set.
2. Open the UI (through the Authelia gate).
3. Settings → paste Todoist API token → Save. This triggers a `/api/v1/user`
   call to validate the token before persisting.
4. Modules → Recurring hygiene → set `dry_run=true`, `enabled=true`, tune
   `grace_days` and `schedule_cron`, save.
5. Click **Run now** to sanity-check. Verify Activity shows a `run` event.
6. When happy, flip `dry_run=false`.

## Recovery scenarios

**Lost `ENCRYPTION_KEY`:** the stored token cannot be decrypted. `db.decrypt`
will raise `RuntimeError`. Fix: clear the token from Settings (or delete
the row from `kv`), set a new key, restart, re-enter the token.

**Corrupted `state.db`:** delete `/data/state.db*`, restart. Re-enter token,
reconfigure modules. There is nothing here worth recovering forensically.

**Todoist token revoked:** modules log `sync_error` events. Re-enter a fresh
token in Settings.

**Scheduler stopped firing:** check `docker logs` for exceptions during
`lifespan()`. `scheduler.start()` is called once; if it raised, the app
would still serve HTTP but nothing scheduled would run. Restart the
container.

## Logs

- `docker logs todoassist` — uvicorn access log + application log.
- `/activity` (UI) — user-visible event log from SQLite. This is the
  audit trail; use it before `docker logs` when investigating module
  behavior.

## Local dev

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export ENCRYPTION_KEY=$(python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")
export PUBLIC_BASE_URL=http://localhost:8000
export DATA_DIR=./data
uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

No Authelia locally — the dev server is open. Don't paste a real Todoist
token into a dev instance you also expose on the network.
