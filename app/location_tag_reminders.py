"""Location tag reminders.

Watches tasks for user-configured labels and attaches location-based
Todoist reminders to them. Each mapping pairs a Todoist label with a
latitude/longitude, radius, and trigger. Idempotent: existing matching
reminders are left alone. Removing a label from a task does *not* delete
the reminder — cleanup is out of scope.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from typing import Iterable

from . import db
from .todoist import (
    LocationReminderSpec,
    Reminder,
    Task,
    TodoistClient,
    TodoistError,
)

log = logging.getLogger(__name__)

MODULE = "location_tag_reminders"

CONFIG_KEY = f"module.{MODULE}.config"
DEFAULT_CONFIG = {
    "enabled": False,
    "dry_run": True,
    "schedule_cron": "*/30 * * * *",
    "keep_events_days": 30,
    "filter_query": "",
    "mappings": [],   # list of dicts: {label, name, lat, long, radius, trigger}
}

TRIGGERS = ("on_enter", "on_leave")
_COORD_EPSILON = 1e-4        # ~11m at the equator, tolerant of Todoist rounding


@dataclass(frozen=True)
class Candidate:
    task: Task
    mapping: dict


@dataclass(frozen=True)
class RunResult:
    scanned: int
    mappings: int
    candidates: int
    reminders_added: int
    dry_run: bool


def _normalise_mapping(raw: dict) -> dict | None:
    try:
        label = str(raw.get("label", "")).strip().lstrip("@").lower()
        name = str(raw.get("name", "")).strip() or label.title()
        lat = float(raw["lat"])
        long_ = float(raw["long"])
    except (KeyError, TypeError, ValueError):
        return None
    if not label:
        return None
    try:
        radius = int(raw.get("radius", 100))
    except (TypeError, ValueError):
        radius = 100
    radius = max(10, radius)
    trigger = str(raw.get("trigger", "on_enter"))
    if trigger not in TRIGGERS:
        trigger = "on_enter"
    return {
        "label": label,
        "name": name,
        "lat": lat,
        "long": long_,
        "radius": radius,
        "trigger": trigger,
    }


def get_config() -> dict:
    return {**DEFAULT_CONFIG, **(db.kv_get(CONFIG_KEY, {}) or {})}


def set_config(patch: dict) -> dict:
    current = get_config()
    current.update(patch)
    current["keep_events_days"] = max(1, int(current["keep_events_days"]))
    current["filter_query"] = str(current.get("filter_query") or "").strip()
    raw_mappings = current.get("mappings") or []
    normalised: list[dict] = []
    for m in raw_mappings:
        n = _normalise_mapping(m)
        if n is not None:
            normalised.append(n)
    current["mappings"] = normalised
    db.kv_set(CONFIG_KEY, current)
    return current


def _reminder_matches(reminder: Reminder, mapping: dict) -> bool:
    if reminder.loc_lat is None or reminder.loc_long is None:
        return False
    if reminder.loc_trigger != mapping["trigger"]:
        return False
    if abs(reminder.loc_lat - mapping["lat"]) > _COORD_EPSILON:
        return False
    if abs(reminder.loc_long - mapping["long"]) > _COORD_EPSILON:
        return False
    return True


def find_candidates(
    tasks: Iterable[Task],
    reminders_by_item: dict[str, list[Reminder]],
    mappings: list[dict],
) -> list[Candidate]:
    label_to_mappings: dict[str, list[dict]] = {}
    for m in mappings:
        label_to_mappings.setdefault(m["label"], []).append(m)

    out: list[Candidate] = []
    for t in tasks:
        task_labels = {lab.lower() for lab in t.labels}
        existing = reminders_by_item.get(t.id, [])
        for lab in task_labels:
            for mapping in label_to_mappings.get(lab, []):
                if any(_reminder_matches(r, mapping) for r in existing):
                    continue
                out.append(Candidate(task=t, mapping=mapping))
    return out


def run(client: TodoistClient, *, today: date | None = None) -> RunResult:
    del today
    cfg = get_config()
    mappings: list[dict] = cfg["mappings"]

    if not mappings:
        db.log_event(
            MODULE,
            "run",
            detail={
                "scanned": 0,
                "mappings": 0,
                "candidates": 0,
                "reminders_added": 0,
                "dry_run": cfg["dry_run"],
                "filter_query": cfg["filter_query"],
            },
        )
        db.prune_events(cfg["keep_events_days"])
        return RunResult(scanned=0, mappings=0, candidates=0, reminders_added=0, dry_run=cfg["dry_run"])

    try:
        tasks = client.fetch_tasks(cfg["filter_query"])
        reminders = client.fetch_reminders()
    except TodoistError as exc:
        db.log_event(MODULE, "sync_error", level="error", detail=str(exc))
        raise

    reminders_by_item: dict[str, list[Reminder]] = {}
    for r in reminders:
        reminders_by_item.setdefault(r.item_id, []).append(r)

    candidates = find_candidates(tasks, reminders_by_item, mappings)

    for c in candidates:
        db.log_event(
            MODULE,
            "candidate",
            task_id=c.task.id,
            detail={
                "content": c.task.content,
                "label": c.mapping["label"],
                "name": c.mapping["name"],
                "trigger": c.mapping["trigger"],
            },
        )

    reminders_added = 0
    if candidates and not cfg["dry_run"]:
        specs = [
            LocationReminderSpec(
                item_id=c.task.id,
                name=c.mapping["name"],
                loc_lat=c.mapping["lat"],
                loc_long=c.mapping["long"],
                loc_trigger=c.mapping["trigger"],
                radius=c.mapping["radius"],
            )
            for c in candidates
        ]
        try:
            client.add_location_reminders(specs)
        except TodoistError as exc:
            db.log_event(MODULE, "sync_error", level="error", detail=str(exc))
            raise
        reminders_added = len(specs)
        for c in candidates:
            db.log_event(
                MODULE,
                "reminder_added",
                task_id=c.task.id,
                detail={
                    "label": c.mapping["label"],
                    "name": c.mapping["name"],
                    "lat": c.mapping["lat"],
                    "long": c.mapping["long"],
                    "radius": c.mapping["radius"],
                    "trigger": c.mapping["trigger"],
                },
            )

    db.log_event(
        MODULE,
        "run",
        detail={
            "scanned": len(tasks),
            "mappings": len(mappings),
            "candidates": len(candidates),
            "reminders_added": reminders_added,
            "dry_run": cfg["dry_run"],
            "filter_query": cfg["filter_query"],
        },
    )

    db.prune_events(cfg["keep_events_days"])

    return RunResult(
        scanned=len(tasks),
        mappings=len(mappings),
        candidates=len(candidates),
        reminders_added=reminders_added,
        dry_run=cfg["dry_run"],
    )
