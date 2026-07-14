# Modules

A "module" is a single-purpose Todoist automation with its own config UI,
scheduled job, and activity log stream. `recurring_hygiene` is the reference
implementation.

## Contract

Every module file (e.g. `app/foo_module.py`) exposes:

```python
MODULE = "foo_module"                      # log/config namespace, stable
CONFIG_KEY = f"module.{MODULE}.config"     # sqlite kv key
DEFAULT_CONFIG = { "enabled": False, ... } # sane, safe defaults

def get_config() -> dict: ...
def set_config(patch: dict) -> dict: ...   # validates + normalises + persists

def run(client: TodoistClient, *, today: date | None = None) -> RunResult: ...
```

Rules for `run()`:

- Always callable, even if `enabled=False` (the scheduler wrapper checks that,
  not `run` itself — allowing a "Run now" button to work while scheduling is
  off).
- Emit a summary `db.log_event(MODULE, "run", detail={...})` at the end of
  every pass, even if there were zero candidates.
- Emit a `candidate` (or module-specific) event for each item considered.
- If the module can mutate Todoist, gate the mutation on
  `cfg["dry_run"] is False`. Log candidates either way.
- On Todoist API failure, log a `sync_error` or `run_error` event with
  `level="error"` and re-raise so the caller can 502.
- Prune the event log at the end of the run using the module's own
  `keep_events_days` config.

## Wiring a new module

1. **Create `app/<name>_module.py`** implementing the contract above.
2. **Add a route section in `app/main.py`** — copy the "Modules — Recurring
   hygiene" block. Three routes: `GET /modules/<slug>`,
   `POST /modules/<slug>/config`, `POST /modules/<slug>/run`.
3. **Add a scheduler wrapper** — copy `_run_recurring_hygiene_job`. Pattern:
   check `cfg["enabled"]`, load the token, open a `TodoistClient`, call
   `module.run(client)`, log errors.
4. **Register the job in `reload_schedule()`.** Currently that function only
   handles one module — split it into per-module `reload_<name>_schedule()`
   helpers or a small loop over registered modules. Keep it explicit; no
   registry decorator.
5. **Add a template** under `app/templates/` — copy `module.html` and wire up
   the form fields.
6. **Add a nav link in `base.html`.**

There is intentionally no `modules/__init__.py` with a discovery mechanism.
Every module is visible in `main.py`.

## Event log conventions

Actions written by modules should follow a small, stable vocabulary so the
Activity view stays readable:

| Action | Meaning |
|--------|---------|
| `run` | Summary at end of a pass. `detail` has scanned/candidate counts. |
| `candidate` | One item was selected for consideration. `task_id` set. |
| `rescheduled` / `<verb>ed` | One item was mutated. `task_id` set. |
| `skipped` | Run was aborted (e.g. no token). `level="warning"`. |
| `sync_error` | Todoist API call failed. `level="error"`. |
| `run_error` | Unexpected exception during a run. `level="error"`. |

Levels: `info` (default), `warning`, `error`. Keep the set small.

## Config validation

Validate in `set_config()`, not in the HTTP handler. The handler should be a
thin adapter that converts form fields to a dict and calls `set_config`.
Cron expressions are the exception — validate those with
`CronTrigger.from_crontab(...)` in the handler so we can return a 400 before
persisting.

## Planned modules

Rough shape only — details firm up when work starts. Listed here so agents
proposing new modules match direction rather than inventing orthogonal
ideas. Not a commitment; drop or reshape as needed.

- **ntfy notifications.** Observe-only. Watch Todoist state and push
  notifications to an [ntfy](https://ntfy.sh) topic to *supplement* Todoist's
  built-in reminders (e.g. digest of tasks due today at a chosen hour,
  escalation when a P1 task is more than N hours overdue, nudges for stale
  `waiting` items). Does not mutate Todoist. Adds one outbound HTTP dependency
  (ntfy) plus a scheduled Todoist sync. Config: ntfy URL/topic, auth token,
  which rules fire and when.
- **Metadata enrichment.** Rule-driven label / priority / section /
  deadline application. User defines rules in the UI (e.g. "tasks in
  project X get label `@home`", "tasks with word 'call' → context
  `@phone`", "overdue P2 → bump to P1"). Applied on schedule or on-demand.
  This one mutates Todoist — dry-run first. Config: an ordered rule list,
  each with a matcher and a set of mutations.

Both fit the existing module contract. The metadata module will need new
Todoist write calls beyond `item_update ... due: {string}` — add them to
`todoist.py` one at a time as needed.

## Testing a module by hand

Dry-run first — always. Recommended flow when developing:

1. Set `enabled=False, dry_run=True`.
2. Hit **Run now** from the module page.
3. Read `/activity?module=<name>` — verify candidates look right.
4. Flip `dry_run=False` and Run now once. Verify the mutation in Todoist.
5. Flip `enabled=True` and let the schedule take over.
