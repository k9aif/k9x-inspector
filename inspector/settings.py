# SPDX-License-Identifier: Apache-2.0
# K9-AIF Framework
"""Configuration for K9X Inspector, all from .env.

Applications to track (any number), one per entry, separated by ';' or newlines:

    INSPECTOR_APPS="DAS|https://github.com/k9aif/dow-k9x|main|src/k9_dow;
                    k9chat|https://github.com/k9aif/examples|main|k9chat"

    name | repository URL | branch (default main) | subfolder (default: whole repository)
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

for _k, _v in list(os.environ.items()):          # podman --env-file keeps quotes; dotenv strips them
    if len(_v) >= 2 and _v[0] == _v[-1] and _v[0] in "'\"":
        os.environ[_k] = _v[1:-1]

REPO_URL = "https://github.com/k9aif/k9x-inspector"
GITHUB_URL = re.compile(r"^https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+?(\.git)?/?$")


@dataclass(frozen=True)
class AppSpec:
    name: str
    repo: str
    branch: str = "main"
    subdir: str = ""


def env(key: str, default: str = "") -> str:
    return os.environ.get(key, default).strip()


def apps() -> List[AppSpec]:
    out = []
    for entry in re.split(r"[;\n]", env("INSPECTOR_APPS")):
        parts = [p.strip() for p in entry.split("|")]
        if len(parts) >= 2 and parts[0] and parts[1]:
            out.append(AppSpec(parts[0], parts[1].rstrip("/"), (parts[2] if len(parts) > 2 and parts[2] else "main"),
                               (parts[3].strip("/") if len(parts) > 3 else "")))
    return out


def credentials() -> Dict[str, str]:
    """The admin login. No password = UI disabled (health only)."""
    return {"user": env("INSPECTOR_USER", "admin"), "password": env("INSPECTOR_PASSWORD")}


def demo_credentials() -> Dict[str, str]:
    """Read-only viewer shown on the sign-in page (INSPECTOR_DEMO_PASSWORD set = enabled)."""
    return {"user": env("INSPECTOR_DEMO_USER", "demo"), "password": env("INSPECTOR_DEMO_PASSWORD")}


def accounts() -> Dict[str, Dict[str, str]]:
    out: Dict[str, Dict[str, str]] = {}
    demo = demo_credentials()
    if demo["password"]:
        out[demo["user"]] = {"password": demo["password"], "role": "viewer"}
    admin = credentials()
    if admin["password"]:
        out[admin["user"]] = {"password": admin["password"], "role": "admin"}
    return out


def schedule() -> str:
    """'HH:MM' = daily at that time; 'every 30m' / 'every 6h' = interval; 'off' = webhook/manual only."""
    return env("INSPECTOR_SCHEDULE", "every 6h").lower()


def webhook_secret() -> str:
    return env("INSPECTOR_WEBHOOK_SECRET")


def github_token() -> str:
    """Optional, read-only token for private repositories (never logged)."""
    return env("INSPECTOR_GITHUB_TOKEN")


def allow_local() -> bool:
    """Admins may inspect a folder on the server (development); off for a public host."""
    return env("INSPECTOR_ALLOW_LOCAL", "false").lower() == "true"


def workdir() -> Path:
    p = Path(env("INSPECTOR_WORKDIR", str(ROOT / "runtime" / "repos")))
    p.mkdir(parents=True, exist_ok=True)
    return p


def db_url() -> str:
    path = Path(env("INSPECTOR_DB_PATH", str(ROOT / "runtime" / "inspector.db")))
    path.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{path}"


def max_repo_mb() -> int:
    return int(env("INSPECTOR_MAX_REPO_MB", "200"))


def public_inspect_per_hour() -> int:
    """Ad-hoc inspections a viewer may start per hour (all viewers together)."""
    return int(env("INSPECTOR_VIEWER_RUNS_PER_HOUR", "20"))
