"""FastAPI entrypoint — routes, auth for the Todoist token, scheduler."""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import db, recurring_hygiene
from .todoist import TodoistClient, TodoistError

log = logging.getLogger("todoassist")

TOKEN_KEY = "auth.todoist_token"
ACCOUNT_KEY = "auth.todoist_account"

APP_DIR = Path(__file__).parent
templates = Jinja2Templates(directory=str(APP_DIR / "templates"))


# --------------------------------------------------------------------------
# Token helpers
# --------------------------------------------------------------------------

def load_token() -> str | None:
    enc = db.kv_get(TOKEN_KEY)
    if not enc:
        return None
    return db.decrypt(enc)


def save_token(plain: str, account: dict) -> None:
    db.kv_set(TOKEN_KEY, db.encrypt(plain))
    db.kv_set(ACCOUNT_KEY, account)


def clear_token() -> None:
    db.kv_delete(TOKEN_KEY)
    db.kv_delete(ACCOUNT_KEY)


def require_token() -> str:
    tok = load_token()
    if not tok:
        raise HTTPException(status_code=400, detail="Todoist token not configured")
    return tok


# --------------------------------------------------------------------------
# Scheduler
# --------------------------------------------------------------------------

scheduler = BackgroundScheduler(timezone="UTC")


def _run_recurring_hygiene_job() -> None:
    cfg = recurring_hygiene.get_config()
    if not cfg["enabled"]:
        return
    tok = load_token()
    if not tok:
        db.log_event(
            recurring_hygiene.MODULE,
            "skipped",
            level="warning",
            detail="no Todoist token configured",
        )
        return
    try:
        with TodoistClient(tok) as client:
            recurring_hygiene.run(client)
    except Exception as exc:  # noqa: BLE001
        log.exception("recurring hygiene scheduled run failed")
        db.log_event(
            recurring_hygiene.MODULE,
            "run_error",
            level="error",
            detail=str(exc),
        )


def reload_schedule() -> None:
    cfg = recurring_hygiene.get_config()
    job_id = f"job.{recurring_hygiene.MODULE}"
    existing = scheduler.get_job(job_id)
    if existing:
        existing.remove()
    if not cfg["enabled"]:
        return
    trigger = CronTrigger.from_crontab(cfg["schedule_cron"])
    scheduler.add_job(
        _run_recurring_hygiene_job,
        trigger=trigger,
        id=job_id,
        max_instances=1,
        coalesce=True,
    )


# --------------------------------------------------------------------------
# Lifespan
# --------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s [todoassist] %(levelname)s %(name)s %(message)s",
    )
    db.init_schema()
    scheduler.start()
    reload_schedule()
    log.info("todoassist ready")
    yield
    scheduler.shutdown(wait=False)


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

static_dir = APP_DIR / "static"
if static_dir.is_dir():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


# --------------------------------------------------------------------------
# Health
# --------------------------------------------------------------------------

@app.get("/healthz")
async def healthz() -> dict:
    return {"status": "ok"}


# --------------------------------------------------------------------------
# Overview
# --------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def overview(request: Request) -> HTMLResponse:
    account = db.kv_get(ACCOUNT_KEY)
    cfg = recurring_hygiene.get_config()
    events = db.recent_events(limit=10)
    return templates.TemplateResponse(
        request,
        "overview.html",
        {
            "account": account,
            "module_config": cfg,
            "events": events,
        },
    )


# --------------------------------------------------------------------------
# Settings — Todoist token
# --------------------------------------------------------------------------

@app.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request) -> HTMLResponse:
    account = db.kv_get(ACCOUNT_KEY)
    return templates.TemplateResponse(
        request,
        "settings.html",
        {"account": account, "has_token": load_token() is not None},
    )


@app.post("/settings/token")
async def settings_save_token(token: str = Form(...)) -> RedirectResponse:
    token = token.strip()
    if not token:
        raise HTTPException(status_code=400, detail="token is required")
    try:
        with TodoistClient(token) as client:
            account = client.user()
    except TodoistError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    save_token(
        token,
        {
            "id": account.id,
            "email": account.email,
            "full_name": account.full_name,
        },
    )
    db.log_event("auth", "token_saved", detail={"email": account.email})
    return RedirectResponse(url="/settings", status_code=303)


@app.post("/settings/token/delete")
async def settings_delete_token() -> RedirectResponse:
    clear_token()
    db.log_event("auth", "token_cleared")
    return RedirectResponse(url="/settings", status_code=303)


# --------------------------------------------------------------------------
# Modules — Recurring hygiene
# --------------------------------------------------------------------------

@app.get("/modules/recurring-hygiene", response_class=HTMLResponse)
async def module_page(request: Request) -> HTMLResponse:
    cfg = recurring_hygiene.get_config()
    events = db.recent_events(module=recurring_hygiene.MODULE, limit=100)
    return templates.TemplateResponse(
        request,
        "module.html",
        {
            "cfg": cfg,
            "events": events,
            "has_token": load_token() is not None,
        },
    )


@app.post("/modules/recurring-hygiene/config")
async def module_save_config(
    enabled: str | None = Form(None),
    dry_run: str | None = Form(None),
    grace_days: int = Form(...),
    action: str = Form(...),
    schedule_cron: str = Form(...),
    keep_events_days: int = Form(...),
    filter_query: str = Form(""),
) -> RedirectResponse:
    # Validate cron expression before storing
    try:
        CronTrigger.from_crontab(schedule_cron)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"invalid cron: {exc}") from exc
    recurring_hygiene.set_config(
        {
            "enabled": enabled == "on",
            "dry_run": dry_run == "on",
            "grace_days": grace_days,
            "action": action,
            "schedule_cron": schedule_cron,
            "keep_events_days": keep_events_days,
            "filter_query": filter_query,
        }
    )
    reload_schedule()
    return RedirectResponse(url="/modules/recurring-hygiene", status_code=303)


@app.post("/modules/recurring-hygiene/run")
async def module_run_now() -> RedirectResponse:
    tok = require_token()
    try:
        with TodoistClient(tok) as client:
            recurring_hygiene.run(client)
    except TodoistError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return RedirectResponse(url="/modules/recurring-hygiene", status_code=303)


# --------------------------------------------------------------------------
# Activity
# --------------------------------------------------------------------------

@app.get("/activity", response_class=HTMLResponse)
async def activity_page(request: Request, module: str | None = None) -> HTMLResponse:
    events = db.recent_events(module=module, limit=500)
    return templates.TemplateResponse(
        request,
        "activity.html",
        {"events": events, "module_filter": module},
    )
