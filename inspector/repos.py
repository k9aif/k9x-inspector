# SPDX-License-Identifier: Apache-2.0
# K9-AIF Framework
"""Fetch a GitHub repository for inspection.

Only https://github.com/<owner>/<repo> URLs are accepted. Clones are shallow
(depth 1), time-limited and size-capped, and the code is only read (AST/YAML),
never imported or run. A private repository needs INSPECTOR_GITHUB_TOKEN; it
is passed as an HTTP header for the one git call and never written to disk or
to the log.
"""

from __future__ import annotations

import base64
import hashlib
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Optional, Tuple

from inspector.settings import GITHUB_URL, github_token, max_repo_mb, workdir

_BRANCH = re.compile(r"^[A-Za-z0-9._/-]{1,200}$")
_SUBDIR = re.compile(r"^[A-Za-z0-9._/ -]{0,500}$")


class RepoError(Exception):
    pass


def validate(repo: str, branch: str = "main", subdir: str = "") -> None:
    if not GITHUB_URL.match(repo or ""):
        raise RepoError("only https://github.com/<owner>/<repo> URLs are supported")
    if not _BRANCH.match(branch or "") or ".." in branch:
        raise RepoError("invalid branch name")
    if not _SUBDIR.match(subdir or "") or ".." in (subdir or "").split("/"):
        raise RepoError("invalid subfolder")


def _git(args, cwd: Optional[Path] = None, timeout: int = 180) -> str:
    cmd = ["git"]
    tok = github_token()
    if tok:
        basic = base64.b64encode(f"x-access-token:{tok}".encode()).decode()
        cmd += ["-c", f"http.https://github.com/.extraheader=AUTHORIZATION: basic {basic}"]
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_LFS_SKIP_SMUDGE": "1"}
    try:
        out = subprocess.run(cmd + list(args), cwd=cwd, capture_output=True, text=True, timeout=timeout, env=env)
    except subprocess.TimeoutExpired:
        raise RepoError("git timed out")
    if out.returncode != 0:
        msg = (out.stderr or out.stdout).strip().splitlines()
        raise RepoError((msg[-1] if msg else "git failed").replace(tok, "***") if tok else (msg[-1] if msg else "git failed"))
    return out.stdout.strip()


def remote_head(repo: str, branch: str) -> Optional[str]:
    """Latest commit of the branch without cloning (for the schedule)."""
    out = _git(["ls-remote", repo.removesuffix("/") , f"refs/heads/{branch}"], timeout=60)
    return out.split()[0] if out else None


def _size_mb(path: Path) -> float:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file()) / 1e6


def checkout(repo: str, branch: str = "main", subdir: str = "") -> Tuple[Path, str]:
    """Shallow clone or update; returns (folder to inspect, commit sha)."""
    validate(repo, branch, subdir)
    key = hashlib.sha256(f"{repo}#{branch}".encode()).hexdigest()[:16]
    dest = workdir() / key
    if (dest / ".git").is_dir():
        try:
            _git(["fetch", "--depth", "1", "origin", branch], cwd=dest)
            _git(["reset", "--hard", "FETCH_HEAD"], cwd=dest)
            _git(["clean", "-fdx"], cwd=dest)
        except RepoError:
            shutil.rmtree(dest, ignore_errors=True)
    if not (dest / ".git").is_dir():
        shutil.rmtree(dest, ignore_errors=True)
        _git(["clone", "--depth", "1", "--single-branch", "--branch", branch, repo, str(dest)])
    if _size_mb(dest) > max_repo_mb():
        shutil.rmtree(dest, ignore_errors=True)
        raise RepoError(f"repository larger than {max_repo_mb()} MB")
    commit = _git(["rev-parse", "HEAD"], cwd=dest)
    folder = (dest / subdir).resolve() if subdir else dest
    if not str(folder).startswith(str(dest.resolve())) or not folder.is_dir():
        raise RepoError(f"subfolder not found: {subdir}")
    return folder, commit
