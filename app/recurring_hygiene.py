"""Recurring task hygiene.

Finds recurring tasks whose next `due.date` is more than `grace_days` in
the past and either reschedules them to today (preserving recurrence) or
just logs them to the activity feed.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Literal

from . import db
from .todoist import Task, TodoistClient, TodoistError

log = logging.getLogger(__name__)

MODULE = "recurring_hygiene"

CONFIG_KEY = "module.recurring_hygiene.config"
DEFAULT_CONFIG = {
    "enabled": False,
    "grace_days": 3,
    "action": "report",     # "report" | "reschedule"
    "dry_run": True,
    "schedule_cron": "0 6 * * *",   # daily 06:00 local
    "keep_events_days": 30,
}


@dataclass(frozen=True)
class RunResult:
    scanned: int
    candidates: list[Task]
    rescheduled: int
    dry_run: bool
    action: str


def get_config() -> dict:
    return {**DEFAULT_CONFIG, **(db.kv_get(CONFIG_KEY, {}) or {})}


def set_config(patch: dict) -> dict:
    current = get_config()
    current.update(patch)
    # Normalise
    current["grace_days"] = max(0, int(current["grace_days"]))
    current["keep_events_days"] = max(1, int(current["keep_events_days"]))
    if current["action"] not in ("report", "reschedule"):
        current["action"] = "report"
    db.kv_set(CONFIG_KEY, current)
    return current


def _parse_due_date(task: Task) -> date | None:
    if not task.due_date:
        return None
    try:
        return datetime.fromisoformat(task.due_date[:10]).date()
    except ValueError:
        return None


def find_candidates(tasks: list[Task], grace_days: int, today: date) -> list[Task]:
    threshold = today - timedelta(days=grace_days)
    out: list[Task] = []
    for t in tasks:
        if not t.due_is_recurring:
            continue
        due = _parse_due_date(t)
        if due is None:
            continue
        if due < threshold:
            out.append(t)
    return out


def run(client: TodoistClient, *, today: date | None = None) -> RunResult:
    """Execute one hygiene pass. Returns what was found + what was done."""
    cfg = get_config()
    today = today or date.today()

    try:
        tasks = client.all_tasks()
    except TodoistError as exc:
        db.log_event(MODULE, "sync_error", level="error", detail=str(exc))
        raise

    candidates = find_candidates(tasks, cfg["grace_days"], today)

    if not candidates:
        db.log_event(
            MODULE,
            "run",
            detail={
                "scanned": len(tasks),
                "candidates": 0,
                "grace_days": cfg["grace_days"],
                "dry_run": cfg["dry_run"],
                "action": cfg["action"],
            },
        )
        return RunResult(
            scanned=len(tasks),
            candidates=[],
            rescheduled=0,
            dry_run=cfg["dry_run"],
            action=cfg["action"],
        )

    for t in candidates:
        db.log_event(
            MODULE,
            "candidate",
            task_id=t.id,
            detail={
                "content": t.content,
                "due_date": t.due_date,
                "due_string": t.due_string,
                "project_id": t.project_id,
            },
        )

    rescheduled = 0
    if cfg["action"] == "reschedule" and not cfg["dry_run"]:
        client.reschedule_recurring_to_today([t.id for t in candidates])
        rescheduled = len(candidates)
        for t in candidates:
            db.log_event(
                MODULE,
                "rescheduled",
                task_id=t.id,
                detail={"from": t.due_date, "to": today.isoformat()},
            )

    db.log_event(
        MODULE,
        "run",
        detail={
            "scanned": len(tasks),
            "candidates": len(candidates),
            "rescheduled": rescheduled,
            "grace_days": cfg["grace_days"],
            "dry_run": cfg["dry_run"],
            "action": cfg["action"],
        },
    )

    db.prune_events(cfg["keep_events_days"])

    return RunResult(
        scanned=len(tasks),
        candidates=candidates,
        rescheduled=rescheduled,
        dry_run=cfg["dry_run"],
        action=cfg["action"],
    )


ActionLiteral = Literal["report", "reschedule"]
