# SPDX-License-Identifier: Apache-2.0
# K9-AIF Framework
"""K9X Inspector web API and UI.

Everything except /api/health, /api/public, the sign-in page, the About page,
its assets and the GitHub webhook needs a login from .env: the admin
(INSPECTOR_USER / INSPECTOR_PASSWORD) or the read-only demo viewer
(INSPECTOR_DEMO_USER / INSPECTOR_DEMO_PASSWORD). Viewers can read every report
and start inspections of tracked applications or public GitHub repositories
(rate-limited); only the admin changes the tracked list or inspects a server
folder. The webhook is authenticated by GitHub's HMAC signature instead.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import threading
import time
from pathlib import Path
from typing import List, Optional

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel

from inspector import auth, guidelines, repos, runner, store
from inspector.settings import (REPO_URL, AppSpec, allow_local, credentials, demo_credentials,
                                public_inspect_per_hour, schedule, webhook_secret)

log = logging.getLogger(__name__)
WEB = Path(__file__).resolve().parent.parent / "web"

app = FastAPI(title="K9X Inspector", docs_url=None, redoc_url=None, openapi_url=None)
_basic = HTTPBasic(auto_error=False)
SCRIPT_HEADER = "X-Inspector-Client"
NO_STORE = {"Cache-Control": "no-store", "Vary": "Cookie"}
_viewer_runs: List[float] = []
_viewer_lock = threading.Lock()


def _client(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def current_user(request: Request, basic: Optional[HTTPBasicCredentials] = Depends(_basic)) -> Optional[str]:
    if not credentials()["password"] and not demo_credentials()["password"]:
        raise HTTPException(503, "Set INSPECTOR_PASSWORD in .env to enable the UI")
    user = auth.verify(request.cookies.get(auth.COOKIE))
    if user:
        return user
    if basic is not None and request.headers.get(SCRIPT_HEADER):
        addr = _client(request)
        if auth.locked(addr):
            raise HTTPException(429, "Too many failed sign-ins; try again in a few minutes")
        if auth.check_password(basic.username, basic.password):
            auth.record_success(addr)
            return basic.username
        auth.record_failure(addr)
    return None


def login(user: Optional[str] = Depends(current_user)) -> str:
    if not user:
        raise HTTPException(401, "Sign in required")
    return user


def admin(user: str = Depends(login)) -> str:
    if auth.role(user) != "admin":
        raise HTTPException(403, "Read-only demo account")
    return user


def _viewer_quota(user: str) -> None:
    if auth.role(user) == "admin":
        return
    now = time.time()
    with _viewer_lock:
        _viewer_runs[:] = [t for t in _viewer_runs if now - t < 3600]
        if len(_viewer_runs) >= public_inspect_per_hour():
            raise HTTPException(429, "The demo's hourly inspection limit is reached; try again later")
        _viewer_runs.append(now)


@app.on_event("startup")
def _startup() -> None:
    runner.start()


# ── public ────────────────────────────────────────────────────────────────────
@app.get("/api/health")
def health():
    return {"ok": True}


@app.get("/api/public")
def public_info():
    demo = demo_credentials()
    return {"demo": {"user": demo["user"], "password": demo["password"]} if demo["password"] else None,
            "repo": REPO_URL}


# ── read ──────────────────────────────────────────────────────────────────────
@app.get("/api/me")
def me(user: str = Depends(login)):
    return {"user": user, "role": auth.role(user), "allow_local": allow_local() and auth.role(user) == "admin"}


@app.get("/api/status")
def status(user: str = Depends(login)):
    return {"current": runner.STATE["current"], "queued": runner.queued(), "schedule": schedule(),
            "next_schedule": runner.STATE["next_schedule"], "last_schedule": runner.STATE["last_schedule"],
            "webhook": bool(webhook_secret())}


@app.get("/api/apps")
def apps(user: str = Depends(login)):
    return store.list_apps()


@app.get("/api/inspections")
def inspections(app_id: Optional[int] = None, limit: int = 100, user: str = Depends(login)):
    return store.list_inspections(min(limit, 500), app_id)


@app.get("/api/inspections/{iid}")
def inspection(iid: int, user: str = Depends(login)):
    d = store.get_inspection(iid)
    if not d:
        raise HTTPException(404, "No such inspection")
    d.pop("report_md", None)
    d.pop("guidelines_md", None)
    return d


def _doc(iid: int, field: str, suffix: str) -> PlainTextResponse:
    d = store.get_inspection(iid)
    if not d or not d.get(field):
        raise HTTPException(404, "No document for this inspection")
    name = (d["source"].split("/")[-1].split(" ")[0] or "solution") + f"-{iid}-{suffix}.md"
    return PlainTextResponse(d[field], media_type="text/markdown",
                             headers={"Content-Disposition": f'inline; filename="{name}"'})


@app.get("/api/inspections/{iid}/report.md")
def report_md(iid: int, user: str = Depends(login)):
    return _doc(iid, "report_md", "compliance-report")


@app.get("/api/inspections/{iid}/guidelines.md")
def guidelines_md(iid: int, user: str = Depends(login)):
    return _doc(iid, "guidelines_md", "guidelines")


@app.get("/api/inspections/{iid}/report.json")
def report_json(iid: int, user: str = Depends(login)):
    d = store.get_inspection(iid)
    if not d or not d.get("report"):
        raise HTTPException(404, "No report for this inspection")
    return JSONResponse(d["report"])


@app.get("/api/rules")
def rules(user: str = Depends(login)):
    return list(guidelines.rule_catalog().values())


# ── act ───────────────────────────────────────────────────────────────────────
class AdHoc(BaseModel):
    repo: str = ""
    branch: str = "main"
    subdir: str = ""
    path: str = ""


class NewApp(BaseModel):
    name: str
    repo: str
    branch: str = "main"
    subdir: str = ""


@app.post("/api/inspect")
def inspect_adhoc(body: AdHoc, user: str = Depends(login)):
    if body.path and auth.role(user) != "admin":
        raise HTTPException(403, "Only the admin may inspect a server folder")
    _viewer_quota(user)
    try:
        iid = runner.submit_adhoc(body.repo.strip(), body.branch.strip() or "main", body.subdir.strip(),
                                  body.path.strip(), user, local_ok=auth.role(user) == "admin")
    except repos.RepoError as exc:
        raise HTTPException(400, str(exc))
    return {"id": iid}


@app.post("/api/apps/{app_id}/inspect")
def inspect_app(app_id: int, user: str = Depends(login)):
    _viewer_quota(user)
    try:
        return {"id": runner.submit_app(app_id, "manual", user)}
    except KeyError:
        raise HTTPException(404, "No such application")


@app.post("/api/apps")
def add_app(body: NewApp, user: str = Depends(admin)):
    spec = AppSpec(body.name.strip()[:200], body.repo.strip().rstrip("/"), body.branch.strip() or "main",
                   body.subdir.strip().strip("/"))
    try:
        repos.validate(spec.repo, spec.branch, spec.subdir)
    except repos.RepoError as exc:
        raise HTTPException(400, str(exc))
    if not spec.name:
        raise HTTPException(400, "Name required")
    try:
        return {"id": store.add_app(spec)}
    except Exception:
        raise HTTPException(409, "An application with that name exists")


@app.delete("/api/apps/{app_id}")
def remove_app(app_id: int, user: str = Depends(admin)):
    app_row = store.get_app(app_id)
    if not app_row:
        raise HTTPException(404, "No such application")
    if app_row["origin"] == "env":
        raise HTTPException(400, "Listed in .env (INSPECTOR_APPS); remove it there")
    store.remove_app(app_id)
    return {"ok": True}


@app.post("/api/schedule/run")
def run_schedule_now(user: str = Depends(admin)):
    return {"queued": runner.scheduled_pass(force=True)}


# ── GitHub webhook ────────────────────────────────────────────────────────────
@app.post("/api/webhook/github")
async def github_webhook(request: Request):
    secret = webhook_secret()
    if not secret:
        raise HTTPException(404, "Webhook not configured")
    body = await request.body()
    sig = request.headers.get("X-Hub-Signature-256", "")
    good = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, good):
        raise HTTPException(401, "Bad signature")
    event = request.headers.get("X-GitHub-Event", "")
    if event == "ping":
        return {"ok": True}
    if event != "push":
        return {"ignored": event}
    data = json.loads(body or b"{}")
    repo_url = (data.get("repository") or {}).get("html_url", "")
    n = runner.on_push(repo_url, data.get("ref", ""))
    return {"queued": n}


# ── sign-in ───────────────────────────────────────────────────────────────────
class SignIn(BaseModel):
    username: str
    password: str


@app.post("/api/login")
def sign_in(body: SignIn, request: Request):
    addr = _client(request)
    wait = auth.locked(addr)
    if wait:
        raise HTTPException(429, f"Too many failed sign-ins. Try again in {int(wait) // 60 + 1} min.")
    if not auth.check_password(body.username.strip(), body.password):
        auth.record_failure(addr)
        raise HTTPException(401, "Wrong username or password")
    auth.record_success(addr)
    resp = JSONResponse({"ok": True})
    resp.set_cookie(auth.COOKIE, auth.issue(body.username.strip()), max_age=auth.TTL_S, httponly=True,
                    samesite="lax", secure=request.url.scheme == "https", path="/")
    return resp


@app.post("/api/logout")
def sign_out():
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(auth.COOKIE, path="/")
    return resp


# ── pages ─────────────────────────────────────────────────────────────────────
@app.get("/")
def index(user: Optional[str] = Depends(current_user)):
    if not user:
        return RedirectResponse("/login", status_code=303, headers=NO_STORE)
    return FileResponse(WEB / "index.html", headers=NO_STORE)


@app.get("/login")
def login_page(user: Optional[str] = Depends(current_user)):
    if user:
        return RedirectResponse("/", status_code=303, headers=NO_STORE)
    return FileResponse(WEB / "login.html", headers=NO_STORE)


@app.get("/about")
def about():
    return FileResponse(WEB / "about.html")


@app.get("/static/{name}")
def static(name: str):
    p = (WEB / name).resolve()
    if p.parent != WEB.resolve() or not p.is_file() or p.suffix not in (".svg", ".png", ".css", ".js"):
        raise HTTPException(404)
    return FileResponse(p)
