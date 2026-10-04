# SPDX-License-Identifier: Apache-2.0
# K9-AIF Framework
"""Inspector storage (SQLAlchemy Core, SQLite by default).

applications  tracked solutions (from .env, or added by an admin in the UI)
inspections   one row per run: trigger (manual | schedule | webhook | adhoc),
              status (running | done | failed), commit, verdict, score, counts,
              the full report (JSON) and the generated documents (Markdown)
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import (Column, DateTime, Integer, MetaData, String, Table, Text, create_engine, delete,
                        desc, insert, select, update)

from inspector.settings import AppSpec, apps, db_url

_meta = MetaData()
applications = Table(
    "applications", _meta,
    Column("id", Integer, primary_key=True),
    Column("name", String(200), unique=True, nullable=False),
    Column("repo", String(500), nullable=False),
    Column("branch", String(200), nullable=False, default="main"),
    Column("subdir", String(500), nullable=False, default=""),
    Column("origin", String(20), nullable=False, default="env"),     # env | ui
    Column("created_at", DateTime(timezone=True)),
)
inspections = Table(
    "inspections", _meta,
    Column("id", Integer, primary_key=True),
    Column("app_id", Integer, nullable=True),
    Column("source", String(700), nullable=False),
    Column("trigger", String(20), nullable=False),
    Column("requested_by", String(100), nullable=False, default=""),
    Column("status", String(20), nullable=False),
    Column("commit", String(64)),
    Column("verdict", String(40)),
    Column("score", Integer),
    Column("counts", Text),
    Column("framework", Text),
    Column("report", Text),
    Column("report_md", Text),
    Column("guidelines_md", Text),
    Column("error", Text),
    Column("started_at", DateTime(timezone=True)),
    Column("finished_at", DateTime(timezone=True)),
)

_engine = None
_lock = threading.Lock()


def engine():
    global _engine
    with _lock:
        if _engine is None:
            _engine = create_engine(db_url(), future=True, connect_args={"check_same_thread": False})
            _meta.create_all(_engine)
            with _engine.begin() as c:                 # columns added after the first release
                cols = {r[1] for r in c.exec_driver_sql("PRAGMA table_info(inspections)")}
                if "framework" not in cols:
                    c.exec_driver_sql("ALTER TABLE inspections ADD COLUMN framework TEXT")
    return _engine


def now() -> datetime:
    return datetime.now(timezone.utc)


def _row(r) -> Dict[str, Any]:
    d = dict(r._mapping)
    for k in ("started_at", "finished_at", "created_at"):
        if d.get(k):
            d[k] = d[k].isoformat()
    for k in ("counts", "framework"):
        if d.get(k):
            d[k] = json.loads(d[k])
    return d


# ── applications ──────────────────────────────────────────────────────────────
def sync_env_apps() -> None:
    """Make the env-listed applications match INSPECTOR_APPS (UI-added ones are kept)."""
    wanted = {a.name: a for a in apps()}
    with engine().begin() as c:
        rows = {r.name: r for r in c.execute(select(applications))}
        for name, a in wanted.items():
            vals = {"repo": a.repo, "branch": a.branch, "subdir": a.subdir, "origin": "env"}
            if name in rows:
                c.execute(update(applications).where(applications.c.name == name).values(**vals))
            else:
                c.execute(insert(applications).values(name=name, created_at=now(), **vals))
        for name, r in rows.items():
            if r.origin == "env" and name not in wanted:
                c.execute(delete(applications).where(applications.c.id == r.id))


def add_app(spec: AppSpec) -> int:
    with engine().begin() as c:
        return c.execute(insert(applications).values(name=spec.name, repo=spec.repo, branch=spec.branch,
                                                     subdir=spec.subdir, origin="ui", created_at=now())
                         ).inserted_primary_key[0]


def remove_app(app_id: int) -> None:
    with engine().begin() as c:
        c.execute(delete(applications).where(applications.c.id == app_id))


def list_apps() -> List[Dict[str, Any]]:
    with engine().connect() as c:
        out = [_row(r) for r in c.execute(select(applications).order_by(applications.c.name))]
    for a in out:
        a["latest"] = latest_for_app(a["id"])
    return out


def get_app(app_id: int) -> Optional[Dict[str, Any]]:
    with engine().connect() as c:
        r = c.execute(select(applications).where(applications.c.id == app_id)).first()
    return _row(r) if r else None


# ── inspections ───────────────────────────────────────────────────────────────
def start_inspection(source: str, trigger: str, app_id: Optional[int] = None, requested_by: str = "") -> int:
    with engine().begin() as c:
        return c.execute(insert(inspections).values(app_id=app_id, source=source, trigger=trigger,
                                                    requested_by=requested_by, status="running",
                                                    started_at=now())).inserted_primary_key[0]


def finish_inspection(iid: int, report: Dict[str, Any], report_md: str, guidelines_md: str) -> None:
    with engine().begin() as c:
        c.execute(update(inspections).where(inspections.c.id == iid).values(
            status="done", commit=report.get("commit"), verdict=report["verdict"], score=report["score"],
            counts=json.dumps(report["counts"]), framework=json.dumps(report.get("framework") or {}),
            report=json.dumps(report), report_md=report_md,
            guidelines_md=guidelines_md, finished_at=now()))


def fail_inspection(iid: int, error: str) -> None:
    with engine().begin() as c:
        c.execute(update(inspections).where(inspections.c.id == iid).values(
            status="failed", error=error[:2000], finished_at=now()))


_SUMMARY = [inspections.c[k] for k in ("id", "app_id", "source", "trigger", "requested_by", "status", "commit",
                                       "verdict", "score", "counts", "framework", "error", "started_at", "finished_at")]


def list_inspections(limit: int = 100, app_id: Optional[int] = None) -> List[Dict[str, Any]]:
    q = select(*_SUMMARY).order_by(desc(inspections.c.id)).limit(limit)
    if app_id is not None:
        q = q.where(inspections.c.app_id == app_id)
    with engine().connect() as c:
        return [_row(r) for r in c.execute(q)]


def latest_for_app(app_id: int) -> Optional[Dict[str, Any]]:
    rows = list_inspections(1, app_id)
    return rows[0] if rows else None


def last_done_commit(app_id: int) -> Optional[str]:
    q = select(inspections.c.commit).where(inspections.c.app_id == app_id, inspections.c.status == "done") \
        .order_by(desc(inspections.c.id)).limit(1)
    with engine().connect() as c:
        r = c.execute(q).first()
    return r[0] if r else None


def get_inspection(iid: int) -> Optional[Dict[str, Any]]:
    with engine().connect() as c:
        r = c.execute(select(inspections).where(inspections.c.id == iid)).first()
    if not r:
        return None
    d = _row(r)
    d["report"] = json.loads(d["report"]) if d.get("report") else None
    return d


def mark_stale_running() -> None:
    """Runs interrupted by a restart are failed, not left 'running' forever."""
    with engine().begin() as c:
        c.execute(update(inspections).where(inspections.c.status == "running").values(
            status="failed", error="interrupted (server restarted)", finished_at=now()))
