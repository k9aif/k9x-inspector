# SPDX-License-Identifier: Apache-2.0
# K9-AIF Framework
"""API: sign-in, roles, local inspection end to end, documents, webhook signature."""

import hashlib
import hmac
import json
import textwrap
import time

import pytest
from fastapi.testclient import TestClient

from inspector import repos, runner
from inspector.api import app


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def signin(c, user, pw):
    r = c.post("/api/login", json={"username": user, "password": pw})
    assert r.status_code == 200, r.text


def solution(tmp_path):
    (tmp_path / "agents").mkdir()
    (tmp_path / "agents" / "a.py").write_text(textwrap.dedent("""
        import openai
        from k9_aif_abb.k9_core.agent.base_agent import BaseAgent
        class A(BaseAgent):
            def execute(self, payload):
                return {}
    """))
    return tmp_path


def wait(c, iid):
    for _ in range(100):
        d = c.get(f"/api/inspections/{iid}").json()
        if d["status"] != "running":
            return d
        time.sleep(0.1)
    raise AssertionError("inspection did not finish")


def test_public_endpoints(client):
    assert client.get("/api/health").json() == {"ok": True}
    assert client.get("/api/public").json()["demo"]["user"] == "demo"
    assert client.get("/api/apps").status_code == 401


def test_env_apps_are_tracked(client):
    signin(client, "demo", "demo")
    assert [a["name"] for a in client.get("/api/apps").json()] == ["Demo"]


def test_demo_cannot_change_or_read_server_folders(client, tmp_path):
    signin(client, "demo", "demo")
    assert client.post("/api/apps", json={"name": "x", "repo": "https://github.com/a/b"}).status_code == 403
    assert client.post("/api/inspect", json={"path": str(tmp_path)}).status_code == 403


def test_admin_local_inspection_end_to_end(client, tmp_path):
    signin(client, "admin", "admin-test-pw")
    iid = client.post("/api/inspect", json={"path": str(solution(tmp_path))}).json()["id"]
    d = wait(client, iid)
    assert d["status"] == "done" and d["verdict"] == "NON-COMPLIANT"
    assert any(f["rule_id"] == "K9-LLM-001" for f in d["report"]["findings"])
    g = client.get(f"/api/inspections/{iid}/guidelines.md").text
    assert "Remediation plan" in g and "Governance configuration" in g
    shown = client.get(f"/api/inspections/{iid}/report.md")
    assert shown.text.startswith("# K9-AIF Compliance Report")
    assert shown.headers["content-disposition"].startswith("inline;")
    saved = client.get(f"/api/inspections/{iid}/report.md?download=1")
    assert saved.headers["content-disposition"].startswith("attachment;")
    assert saved.headers["content-disposition"].endswith(f'-{iid}-compliance-report.md"')
    assert client.get(f"/api/inspections/{iid}/report.json?download=1").headers[
        "content-disposition"].endswith(f'-{iid}-compliance-report.json"')
    assert len(client.get("/api/rules").json()) >= 20


def test_framework_itself_is_not_graded(client):
    """Inspecting the K9-AIF framework (the ABBs) reports that it is the framework, with no rules applied."""
    from pathlib import Path
    abb = Path(__file__).resolve().parents[3] / "k9-aif-framework" / "k9_aif_abb"
    if not abb.is_dir():
        pytest.skip("framework checkout not next to k9x-ecosystem")
    signin(client, "admin", "admin-test-pw")
    d = wait(client, client.post("/api/inspect", json={"path": str(abb)}).json()["id"])
    assert d["status"] == "done" and d["verdict"] == "K9-AIF FRAMEWORK"
    assert d["report"]["scope"] == "framework" and d["report"]["findings"] == [] and d["report"]["score"] is None
    assert "This is the K9-AIF framework" in client.get(f"/api/inspections/{d['id']}/guidelines.md").text


def test_only_admin_deletes_an_inspection(client, tmp_path):
    signin(client, "admin", "admin-test-pw")
    iid = wait(client, client.post("/api/inspect", json={"path": str(solution(tmp_path))}).json()["id"])["id"]
    signin(client, "demo", "demo")
    assert client.delete(f"/api/inspections/{iid}").status_code == 403
    signin(client, "admin", "admin-test-pw")
    assert client.delete(f"/api/inspections/{iid}").json() == {"deleted": iid}
    assert client.delete(f"/api/inspections/{iid}").status_code == 404


def test_rejects_non_github_urls(client):
    signin(client, "admin", "admin-test-pw")
    for bad in ("http://github.com/a/b", "https://evil.example/a/b", "file:///etc", "https://github.com/a/b/../c"):
        assert client.post("/api/inspect", json={"repo": bad}).status_code == 400


def test_webhook_signature_and_dispatch(client, monkeypatch):
    queued = []
    monkeypatch.setattr(runner, "submit_app", lambda app_id, trigger, user="": queued.append((app_id, trigger)) or 1)
    body = json.dumps({"ref": "refs/heads/main", "repository": {"html_url": "https://github.com/k9aif/examples"}}).encode()
    bad = client.post("/api/webhook/github", content=body, headers={"X-GitHub-Event": "push", "X-Hub-Signature-256": "sha256=0"})
    assert bad.status_code == 401
    sig = "sha256=" + hmac.new(b"hook-secret", body, hashlib.sha256).hexdigest()
    ok = client.post("/api/webhook/github", content=body, headers={"X-GitHub-Event": "push", "X-Hub-Signature-256": sig})
    assert ok.json() == {"queued": 1} and queued[0][1] == "webhook"


def test_validate():
    repos.validate("https://github.com/k9aif/dow-k9x", "main", "src/k9_dow")
    with pytest.raises(repos.RepoError):
        repos.validate("https://github.com/k9aif/dow-k9x", "main", "../../etc")


def test_landing_is_public_and_app_requires_sign_in():
    with TestClient(app) as c:
        assert c.get("/").status_code == 200 and "Try the demo" in c.get("/").text
        r = c.get("/app", follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/login"
