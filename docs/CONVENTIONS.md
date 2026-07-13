# Conventions

Keep this file short. If a rule needs a paragraph to justify, it probably
shouldn't be a rule.

## Python

- Python 3.12. `from __future__ import annotations` at the top of every
  module.
- Type hints everywhere, including private functions.
- Dataclasses (`@dataclass(frozen=True)`) for API DTOs (`Task`, `Account`,
  `RunResult`). Not Pydantic — we don't need validation on the response side.
- Standard library first. Third-party dependencies listed in
  `requirements.txt`; adding a new one needs justification.
- Log via `logging.getLogger(__name__)`. Don't use `print`. The root logger
  is configured in `lifespan()`.

## Comments and docstrings

- Module docstring: one paragraph, states *what the module is for*, not
  *what it does line-by-line*.
- Function docstrings: only if the *why* is non-obvious. Skip them for
  self-explanatory helpers.
- Inline comments: only for surprising behavior (e.g. the Todoist recurrence
  quirk in `todoist.reschedule_recurring_to_today`). Don't narrate the code.

## HTTP layer

- FastAPI routes live in `app/main.py`. No router split until there are
  many more of them.
- Form-encoded POSTs, 303 redirects on success. No JSON API surface.
- Raise `HTTPException` with a plain string `detail` for user-facing errors.
  4xx for validation, 502 for Todoist upstream failures.

## State

- Any persistent state goes through `db.kv_get/set` (small structured
  values) or `db.log_event` (activity). Do not open sqlite connections
  directly from module code.
- Config keys: `module.<name>.config`. Auth keys: `auth.*`. Keep the
  namespace consistent so `/settings` and the activity page can find
  things.

## Secrets

- The Todoist token is the only secret at rest. It's Fernet-encrypted via
  `ENCRYPTION_KEY`. Never log it, never render it back to HTML, never write
  it to `events.detail`.
- `ENCRYPTION_KEY` is provided by the compose file (Docker secret or env
  var). It is not stored in the repo, ever.

## UI

- Server-rendered Jinja2. No client-side JS beyond native form behavior.
- Dark theme, defined once in `base.html`. Don't add per-page stylesheets.
- Nav is a flat list in `base.html`. Add one link per module.

## Git / CI

- Commits use conventional-commit-ish prefixes (`feat:`, `fix:`, `ci:`,
  `chore:`). See `git log` for the current style.
- CI is one workflow: build + push to GHCR on `main`. PRs build but do not
  push. There is no test job because there are no tests.
- Do not add `--no-verify`, `--no-gpg-sign`, or `--force` push in workflows.

## What not to add

- ORM. SQLite access is direct and the schema is two tables.
- Alembic / migration framework. Schema is created inline; breaking changes
  reset state.
- Structured logging library beyond stdlib `logging`.
- Frontend build step (webpack, vite, tailwind, etc.).
- OAuth flow for Todoist. Personal token via UI is the intended path.
- Prometheus metrics endpoint. If observability is needed, it lives in the
  event log first.
