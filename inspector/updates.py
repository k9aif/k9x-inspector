# SPDX-License-Identifier: Apache-2.0
# K9-AIF Framework
"""Version information and the pull-based update request.

The running container never updates itself. "Update" in the UI only writes
``request.json`` into the mounted ``runtime/update/`` folder; the host's
updater (``ubuntu/update.sh``, started by a systemd path unit installed with
``ubuntu/build-run.sh install-updater``) sees the file and does the work:
git pull, rebuild with the latest k9-aif, restart, health check, roll back to
the previous image if the new one is unhealthy. Nothing outside the host can
reach in; the host only makes outbound calls (GitHub, PyPI).

Idempotent at both ends: the API writes no request when the build commit and
the installed k9-aif are already the latest, and the host re-checks before it
builds anything.

Files in runtime/update/ (the host writes all but request.json):
    request.json   {"user", "at"}                     the UI asks for an update
    status.json    {"state", "message", "at", ...}     the host's last result
    updater.json   {"installed_at"}                    the host updater is installed
    update.log     the host updater's log (admin only)
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from inspector import runner
from inspector.settings import ROOT, env

GITHUB_COMMITS = "https://api.github.com/repos/k9aif/k9x-inspector/commits/main"
COOLDOWN_S = 10 * 60          # between two update requests that reach the host
PICKUP_S = 2 * 60             # a request the host has not taken by then = updater not running
STALE_RUN_S = 45 * 60         # "running" older than this = the host updater died mid-run
_lock = threading.Lock()
_latest_commit: Dict[str, Any] = {"at": 0.0, "sha": ""}


# ── what is running ───────────────────────────────────────────────────────────
def build_info() -> Dict[str, str]:
    """Inspector version (VERSION file), build commit and build time (baked in by build-run.sh)."""
    version = (ROOT / "VERSION").read_text().strip() if (ROOT / "VERSION").is_file() else "unknown"
    commit = env("INSPECTOR_COMMIT")
    if not commit:                               # a checkout (development): ask git
        try:
            out = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"],
                                 capture_output=True, text=True, timeout=5)
            commit = out.stdout.strip() if out.returncode == 0 else ""
        except Exception:
            commit = ""
    return {"version": version, "commit": commit or "unknown", "built": env("INSPECTOR_BUILT") or "unknown"}


def framework_installed() -> str:
    try:
        from importlib.metadata import version
        return version("k9-aif")
    except Exception:
        return "unknown"


def rule_count() -> int:
    try:
        from inspector import guidelines
        return len(guidelines.rule_catalog())
    except Exception:
        return 0


# ── what is latest ────────────────────────────────────────────────────────────
def latest_commit() -> str:
    """Latest commit on k9x-inspector main (GitHub API, cached 10 min); '' if GitHub cannot be reached."""
    if time.time() - _latest_commit["at"] < 600 and _latest_commit["sha"]:
        return _latest_commit["sha"]
    try:
        req = urllib.request.Request(GITHUB_COMMITS, headers={"Accept": "application/vnd.github+json",
                                                              "User-Agent": "k9x-inspector"})
        with urllib.request.urlopen(req, timeout=8) as r:
            _latest_commit.update(sha=json.loads(r.read())["sha"], at=time.time())
    except Exception:
        return ""
    return _latest_commit["sha"]


def _ver(v: str) -> Tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", v)[:4])


def check() -> Dict[str, Any]:
    """Installed vs latest, for the About page and the Update button.

    ``update_available`` is True when either is behind, False when both are
    known to be current, None when one of them could not be determined (the
    host then decides).
    """
    info = build_info()
    installed, latest = framework_installed(), runner.latest_framework()
    framework_behind: Optional[bool] = None
    if installed != "unknown" and latest:
        framework_behind = _ver(installed) < _ver(latest)
    head = latest_commit()
    code_behind: Optional[bool] = None
    if info["commit"] != "unknown" and head:
        code_behind = not head.startswith(info["commit"])
    if framework_behind or code_behind:
        available: Optional[bool] = True
    elif framework_behind is False and code_behind is False:
        available = False
    else:
        available = None
    return {**info, "k9_aif": installed, "k9_aif_latest": latest or "unknown",
            "latest_commit": head[:len(info["commit"])] if head and info["commit"] != "unknown" else (head[:7] or "unknown"),
            "framework_behind": framework_behind, "code_behind": code_behind,
            "update_available": available, "rules": rule_count()}


# ── the request and the host's answer ─────────────────────────────────────────
def update_dir() -> Path:
    default = Path(env("INSPECTOR_DB_PATH", str(ROOT / "runtime" / "inspector.db"))).parent / "update"
    p = Path(env("INSPECTOR_UPDATE_DIR", str(default)))
    p.mkdir(parents=True, exist_ok=True)
    return p


def _read(name: str) -> Dict[str, Any]:
    try:
        return json.loads((update_dir() / name).read_text())
    except Exception:
        return {}


def _write(name: str, data: Dict[str, Any]) -> None:
    path = update_dir() / name
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data))
    os.replace(tmp, path)


def status() -> Dict[str, Any]:
    """The host updater's state, plus a pending request the host has not yet taken."""
    out = {"updater_installed": bool(_read("updater.json")), "last": _read("status.json") or None,
           "pending": None}
    req = _read("request.json")
    if req:
        age = time.time() - float(req.get("at", 0))
        out["pending"] = {**req, "age_s": int(age), "stuck": age > PICKUP_S}
    return out


def request(user: str) -> Dict[str, Any]:
    """Ask the host for an update. Returns {"result": ..., "message": ...}; never builds anything itself."""
    with _lock:
        st = status()
        if not st["updater_installed"]:
            return {"result": "no_updater",
                    "message": "The updater is not installed on this host (ubuntu/build-run.sh install-updater)."}
        last = st["last"] or {}
        if last.get("state") == "running" and time.time() - float(last.get("at", 0)) < STALE_RUN_S:
            return {"result": "running", "message": last.get("message") or "An update is already running."}
        if st["pending"] and not st["pending"]["stuck"]:
            return {"result": "pending", "message": "An update was already requested; the host is picking it up."}
        info = check()
        if info["update_available"] is False:
            return {"result": "up_to_date",
                    "message": f"Already up to date: Inspector {info['version']} ({info['commit']}), "
                               f"k9-aif {info['k9_aif']}."}
        last_req = _read("last_request.json")
        wait = COOLDOWN_S - (time.time() - float(last_req.get("at", 0)))
        if wait > 0:
            return {"result": "cooldown",
                    "message": f"An update was requested {int((COOLDOWN_S - wait) // 60)} min ago; "
                               f"try again in {int(wait // 60) + 1} min."}
        now = time.time()
        _write("request.json", {"user": user, "at": now})
        _write("last_request.json", {"user": user, "at": now})
        what = []
        if info["framework_behind"]:
            what.append(f"k9-aif {info['k9_aif']} → {info['k9_aif_latest']}")
        if info["code_behind"]:
            what.append(f"Inspector {info['commit']} → {info['latest_commit']}")
        return {"result": "requested",
                "message": "Update requested" + (f" ({'; '.join(what)})" if what else "")
                           + ". The host rebuilds and restarts; this page reloads when it is back."}


def log_tail(lines: int = 60) -> str:
    try:
        return "\n".join((update_dir() / "update.log").read_text(errors="replace").splitlines()[-lines:])
    except Exception:
        return ""
