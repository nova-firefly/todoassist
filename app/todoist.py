"""Minimal Todoist API client.

Only what the recurring-hygiene module + settings page need:
  - GET  /api/v1/sync          — pull tasks (full or delta)
  - POST /api/v1/sync          — command batch (item_update with due_string)
  - GET  /api/v1/user          — verify token, fetch account metadata
  - GET  /api/v1/tasks/filter  — filter-query scoped task pull

Todoist retired the /sync/v9 endpoints (HTTP 410); the current URL prefix is
/api/v1. Payload shape for /sync and /user is unchanged.
"""
from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from typing import Any, Iterable

import httpx

log = logging.getLogger(__name__)

BASE_URL = "https://api.todoist.com/api/v1"
DEFAULT_TIMEOUT = httpx.Timeout(30.0, connect=10.0)


class TodoistError(RuntimeError):
    pass


@dataclass(frozen=True)
class Account:
    id: str
    email: str
    full_name: str


@dataclass(frozen=True)
class Task:
    id: str
    content: str
    project_id: str | None
    due_date: str | None       # YYYY-MM-DD or YYYY-MM-DDTHH:MM:SS
    due_string: str | None
    due_is_recurring: bool
    due_timezone: str | None
    checked: bool

    @classmethod
    def from_api(cls, raw: dict) -> "Task":
        due = raw.get("due") or {}
        return cls(
            id=str(raw["id"]),
            content=raw.get("content", ""),
            project_id=str(raw["project_id"]) if raw.get("project_id") is not None else None,
            due_date=due.get("date"),
            due_string=due.get("string"),
            due_is_recurring=bool(due.get("is_recurring")),
            due_timezone=due.get("timezone"),
            checked=bool(raw.get("checked")),
        )


class TodoistClient:
    def __init__(self, token: str) -> None:
        if not token:
            raise TodoistError("empty Todoist API token")
        self._token = token
        self._client = httpx.Client(
            base_url=BASE_URL,
            timeout=DEFAULT_TIMEOUT,
            headers={"Authorization": f"Bearer {token}"},
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "TodoistClient":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            r = self._client.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise TodoistError(f"HTTP error calling {path}: {exc}") from exc
        if r.status_code == 401:
            raise TodoistError("Todoist rejected the token (401 Unauthorized)")
        if r.status_code == 403:
            raise TodoistError("Todoist forbidden (403)")
        if r.status_code >= 400:
            raise TodoistError(f"Todoist API {r.status_code}: {r.text[:200]}")
        return r.json()

    def user(self) -> Account:
        data = self._request("GET", "/user")
        return Account(
            id=str(data.get("id", "")),
            email=data.get("email", ""),
            full_name=data.get("full_name", ""),
        )

    def all_tasks(self) -> list[Task]:
        """Full active-task pull via Sync API."""
        data = self._request(
            "POST",
            "/sync",
            data={
                "sync_token": "*",
                "resource_types": json.dumps(["items"]),
            },
        )
        raw_items = data.get("items", []) or []
        return [Task.from_api(t) for t in raw_items if not t.get("checked")]

    def tasks_by_filter(self, query: str) -> list[Task]:
        """Active tasks matching a Todoist filter query (e.g. ``#Work & @next``).

        Uses ``/api/v1/tasks/filter`` and walks ``next_cursor`` until exhausted.
        Completed tasks are excluded on the server side by this endpoint.
        """
        out: list[Task] = []
        cursor: str | None = None
        while True:
            params: dict[str, str] = {"query": query, "limit": "200"}
            if cursor:
                params["cursor"] = cursor
            data = self._request("GET", "/tasks/filter", params=params)
            # Response shape: {"results": [...], "next_cursor": "..." | null}.
            # Some deployments return the list directly — accept both.
            if isinstance(data, list):
                items = data
                cursor = None
            else:
                items = data.get("results") or data.get("items") or []
                cursor = data.get("next_cursor")
            out.extend(Task.from_api(t) for t in items if not t.get("checked"))
            if not cursor:
                break
        return out

    def fetch_tasks(self, filter_query: str | None) -> list[Task]:
        """Return active tasks, optionally scoped by a Todoist filter query.

        Empty / whitespace-only ``filter_query`` falls back to the full Sync
        pull. Modules that want optional filtering should call this instead of
        picking between :meth:`all_tasks` and :meth:`tasks_by_filter` by hand.
        """
        q = (filter_query or "").strip()
        return self.tasks_by_filter(q) if q else self.all_tasks()

    def reschedule_recurring_to_today(self, task_ids: Iterable[str]) -> dict:
        """Move a recurring task's next due date to today, preserving recurrence.

        Uses `item_update` with `due: {string: "today"}` — the Todoist server
        re-parses "today" against the existing recurrence rule, so subsequent
        occurrences continue to follow the same cadence.
        """
        commands = []
        for tid in task_ids:
            commands.append(
                {
                    "type": "item_update",
                    "uuid": str(uuid.uuid4()),
                    "args": {
                        "id": tid,
                        "due": {"string": "today"},
                    },
                }
            )
        if not commands:
            return {"sync_status": {}}
        data = self._request(
            "POST",
            "/sync",
            data={"commands": json.dumps(commands)},
        )
        status = data.get("sync_status", {})
        failed = {k: v for k, v in status.items() if v != "ok"}
        if failed:
            log.warning("Todoist reported %d failed command(s): %s", len(failed), failed)
        return data
