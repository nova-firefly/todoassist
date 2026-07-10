# todoassist

Single-user, single-account Todoist automation add-ons. Runs as a small
FastAPI service, gated by Authelia when deployed via
[nova-config](https://github.com/nova-firefly/nova-config).

## Modules

| Module | What it does |
|--------|--------------|
| **Recurring task hygiene** | Detects recurring tasks whose next due date is more than `grace_days` in the past and either **reschedules** them to today (preserving the recurrence rule via the Todoist Sync API) or just **reports** them to the activity log. Runs on a schedule; also has a **Run now** button. Dry-run first. |

New modules land as files in `app/modules_impl/`. There is no plugin
framework — it's a single-user app; hard-code registration in
`app/main.py`.

## Configuration

All persistent state lives in `/data/state.db` (SQLite). Only two things
come from env:

| Env var | Required | Notes |
|---------|----------|-------|
| `ENCRYPTION_KEY` | yes | Fernet key. Encrypts the Todoist API token at rest. Losing it means re-entering the token via the UI. |
| `PUBLIC_BASE_URL` | yes | E.g. `https://todoassist.example.com`. Used in redirects. |
| `LOG_LEVEL` | no | Default `INFO`. |
| `DATA_DIR` | no | Default `/data`. |

The Todoist API token is entered through the web UI (Settings → paste
token → Test connection) and never touches disk in plaintext.

## Deploy

Image is published to `ghcr.io/nova-firefly/todoassist:{latest, sha-<short>}`
by the workflow in `.github/workflows/build.yml`, which runs on a
self-hosted runner (`nova-config/infra/compose.yaml → runner-todoassist`).

The nova-config side of the deploy — compose file, Authelia gate, WUD
auto-redeploy — lives at `todoassist/compose.yaml` in the nova-config
repo.

## Local dev

```
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
ENCRYPTION_KEY=$(python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())") \
PUBLIC_BASE_URL=http://localhost:8000 \
DATA_DIR=./data \
uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```
