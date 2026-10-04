# SPDX-License-Identifier: Apache-2.0
# K9-AIF Framework
"""Runs inspections: one at a time, from a queue fed by the UI, the schedule
and GitHub push webhooks.

    manual    an admin (or the demo viewer) presses Inspect on a tracked application
    adhoc     a repository URL (or, for admins with INSPECTOR_ALLOW_LOCAL, a folder) typed in
    schedule  INSPECTOR_SCHEDULE; an application is re-inspected only when its branch moved
    webhook   a GitHub push to a tracked repository and branch
"""

from __future__ import annotations

import logging
import queue
import re
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from k9_aif_abb.k9_inspect import K9Inspector

from inspector import guidelines, repos, store
from inspector.settings import allow_local, schedule

log = logging.getLogger(__name__)

_latest: Dict[str, Any] = {"at": 0.0, "version": ""}


def latest_framework() -> str:
    """Latest k9-aif on PyPI (cached 6 h); the installed version if PyPI cannot be reached."""
    import json as _json
    import urllib.request
    if time.time() - _latest["at"] < 6 * 3600 and _latest["version"]:
        return _latest["version"]
    try:
        with urllib.request.urlopen("https://pypi.org/pypi/k9-aif/json", timeout=8) as r:
            _latest.update(version=_json.loads(r.read())["info"]["version"], at=time.time())
    except Exception:
        try:
            from importlib.metadata import version
            _latest.update(version=version("k9-aif"), at=time.time())
        except Exception:
            pass
    return _latest["version"]


_queue: "queue.Queue[Dict[str, Any]]" = queue.Queue()
STATE: Dict[str, Any] = {"current": None, "last_schedule": None, "next_schedule": None}
_worker_started = threading.Event()


# ── submitting ────────────────────────────────────────────────────────────────
def submit_app(app_id: int, trigger: str, user: str = "") -> int:
    app = store.get_app(app_id)
    if not app:
        raise KeyError(app_id)
    source = app["repo"] + (f" ({app['branch']}" + (f", {app['subdir']}" if app["subdir"] else "") + ")")
    iid = store.start_inspection(source, trigger, app_id, user)
    _queue.put({"iid": iid, "app": app})
    return iid


def submit_adhoc(repo: str = "", branch: str = "main", subdir: str = "", path: str = "", user: str = "",
                 local_ok: bool = False) -> int:
    if path:
        if not (local_ok and allow_local()):
            raise repos.RepoError("inspecting a server folder is disabled")
        if not Path(path).is_dir():
            raise repos.RepoError(f"not a folder: {path}")
        iid = store.start_inspection(path, "adhoc", None, user)
        _queue.put({"iid": iid, "path": path})
        return iid
    repos.validate(repo, branch or "main", subdir)
    iid = store.start_inspection(f"{repo} ({branch or 'main'}" + (f", {subdir}" if subdir else "") + ")",
                                 "adhoc", None, user)
    _queue.put({"iid": iid, "adhoc": {"repo": repo, "branch": branch or "main", "subdir": subdir}})
    return iid


def queued() -> int:
    return _queue.qsize()


# ── running ───────────────────────────────────────────────────────────────────
def _run(job: Dict[str, Any]) -> None:
    iid = job["iid"]
    STATE["current"] = iid
    try:
        commit = None
        if "path" in job:
            folder, source, name = Path(job["path"]), job["path"], Path(job["path"]).name
        else:
            spec = job.get("adhoc") or job["app"]
            folder, commit = repos.checkout(spec["repo"], spec["branch"], spec.get("subdir", ""))
            source = spec["repo"] + (f"/tree/{spec['branch']}/{spec['subdir']}" if spec.get("subdir") else "")
            name = job["app"]["name"] if "app" in job else spec["repo"].rstrip("/").split("/")[-1]
        report = K9Inspector(config={"latest_version": latest_framework()}).inspect(folder, source=source)
        if commit:
            report.commit = commit[:12]
        data = report.to_dict()
        store.finish_inspection(iid, data, report.to_markdown(), guidelines.build(data, name))
        log.info("inspection %s done: %s %s%%", iid, data["verdict"], data["score"])
    except repos.RepoError as exc:
        store.fail_inspection(iid, str(exc))
    except Exception as exc:                       # recorded, never silent
        log.exception("inspection %s failed", iid)
        store.fail_inspection(iid, f"{type(exc).__name__}: {exc}")
    finally:
        STATE["current"] = None


def _worker() -> None:
    while True:
        job = _queue.get()
        try:
            _run(job)
        finally:
            _queue.task_done()


# ── schedule ──────────────────────────────────────────────────────────────────
def _next_due(spec: str, now: float) -> Optional[float]:
    if spec in ("off", "", "none"):
        return None
    m = re.fullmatch(r"every\s+(\d+)\s*([mh])", spec)
    if m:
        return now + int(m.group(1)) * (60 if m.group(2) == "m" else 3600)
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", spec)
    if m:
        t = datetime.fromtimestamp(now).replace(hour=int(m.group(1)), minute=int(m.group(2)), second=0, microsecond=0)
        due = t.timestamp()
        return due if due > now else due + 86400
    log.warning("INSPECTOR_SCHEDULE=%r not understood; schedule off", spec)
    return None


def scheduled_pass(force: bool = False) -> int:
    """Queue every tracked application whose branch moved since its last inspection."""
    n = 0
    for app in store.list_apps():
        try:
            head = repos.remote_head(app["repo"], app["branch"])
        except repos.RepoError as exc:
            log.warning("schedule: %s: %s", app["name"], exc)
            continue
        last = store.last_done_commit(app["id"])
        if force or not head or not last or not head.startswith(last):
            submit_app(app["id"], "schedule", "scheduler")
            n += 1
    STATE["last_schedule"] = time.time()
    return n


def _scheduler() -> None:
    while True:
        due = _next_due(schedule(), time.time())
        STATE["next_schedule"] = due
        if due is None:
            time.sleep(300)
            continue
        time.sleep(max(1.0, due - time.time()))
        try:
            scheduled_pass()
        except Exception:
            log.exception("scheduled pass failed")


# ── webhook ───────────────────────────────────────────────────────────────────
def on_push(repo_url: str, ref: str) -> int:
    """A GitHub push: inspect every tracked application on that repository and branch."""
    branch = ref.removeprefix("refs/heads/")
    norm = repo_url.lower().removesuffix(".git").rstrip("/")
    n = 0
    for app in store.list_apps():
        if app["repo"].lower().removesuffix(".git").rstrip("/") == norm and app["branch"] == branch:
            submit_app(app["id"], "webhook", "github")
            n += 1
    return n


def start() -> None:
    if _worker_started.is_set():
        return
    _worker_started.set()
    store.mark_stale_running()
    store.sync_env_apps()
    threading.Thread(target=_worker, name="inspector-worker", daemon=True).start()
    threading.Thread(target=_scheduler, name="inspector-scheduler", daemon=True).start()
