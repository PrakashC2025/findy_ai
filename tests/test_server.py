"""The local web app: auth, index, ask streaming, checks, fix guardrails."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx

from devmind.server import create_app


def _request(app, method: str, path: str, **kwargs):
    async def go():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.request(method, path, **kwargs)

    return asyncio.run(go())


def _project(root: Path) -> Path:
    (root / "pkg").mkdir(parents=True)
    (root / "pkg" / "__init__.py").write_text("")
    (root / "pkg" / "calc.py").write_text("def total(a, b):\n    return a + b\n")
    (root / "tests").mkdir()
    (root / "tests" / "test_calc.py").write_text(
        "from pkg.calc import total\n\ndef test_total():\n    assert total(2, 3) == 5\n"
    )
    (root / "pyproject.toml").write_text("[project]\nname='demo'\nversion='0.1.0'\n")
    return root


def _app(tmp_path: Path, **kwargs):
    return create_app(home=tmp_path / "home", demo=True, allow_roots=[str(tmp_path)], **kwargs)


def test_shell_and_status_are_served(tmp_path: Path) -> None:
    app = _app(tmp_path)
    page = _request(app, "GET", "/")
    assert page.status_code == 200
    assert "DevMind" in page.text
    assert "/static/app.js" in page.text

    script = _request(app, "GET", "/static/app.js")
    assert script.status_code == 200
    assert "streamAnswer" in script.text

    assert _request(app, "GET", "/static/nope.js").status_code == 404

    status = _request(app, "GET", "/api/status").json()
    assert status["version"].startswith("0.")
    assert status["demo"] is True
    assert "sandbox" in status and "cache" in status["counts"]
    assert status["roots"] == [str(tmp_path)]


def test_manifest_and_service_worker_are_installable(tmp_path: Path) -> None:
    app = _app(tmp_path)
    manifest = _request(app, "GET", "/static/manifest.webmanifest").json()
    assert manifest["display"] == "standalone"
    assert manifest["start_url"] == "/"
    worker = _request(app, "GET", "/static/sw.js")
    assert worker.headers["service-worker-allowed"] == "/"
    assert "caches" in worker.text


def test_token_is_required_and_cookie_is_set(tmp_path: Path) -> None:
    app = _app(tmp_path, token="secret-token")
    assert _request(app, "GET", "/").status_code == 200  # shell is public
    assert _request(app, "GET", "/api/status").status_code == 401

    ok = _request(app, "GET", "/api/status?token=secret-token")
    assert ok.status_code == 200
    assert "devmind_token" in ok.cookies

    with_header = _request(app, "GET", "/api/status", headers={"x-devmind-token": "secret-token"})
    assert with_header.status_code == 200
    assert _request(app, "GET", "/healthz").status_code == 200


def test_index_repos_files_and_traversal_guard(tmp_path: Path) -> None:
    app = _app(tmp_path)
    project = _project(tmp_path / "shop")
    report = _request(app, "POST", "/api/index", json={"path": str(project)}).json()
    assert report["repo"] == "shop"
    assert report["indexed"] >= 3

    repos = _request(app, "GET", "/api/repos").json()
    assert any(entry["repo"] == "shop" for entry in repos)

    files = _request(app, "GET", "/api/files?repo=shop").json()
    assert "pkg/calc.py" in files

    text = _request(app, "GET", "/api/file?repo=shop&path=pkg/calc.py").json()
    assert "def total" in text["text"]

    escape = _request(app, "GET", "/api/file?repo=shop&path=../../etc/passwd")
    assert escape.status_code == 400
    assert _request(app, "GET", "/api/files?repo=missing").status_code == 404


def test_paths_outside_allowed_roots_are_refused(tmp_path: Path) -> None:
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    outside = tmp_path / "outside"
    _project(outside)
    app = create_app(home=tmp_path / "home", demo=True, allow_roots=[str(allowed)])
    response = _request(app, "POST", "/api/index", json={"path": str(outside)})
    assert response.status_code == 403


def test_ask_streams_events_and_a_done_payload(tmp_path: Path) -> None:
    app = _app(tmp_path)
    project = _project(tmp_path / "shop")
    _request(app, "POST", "/api/index", json={"path": str(project)})

    response = _request(
        app, "POST", "/api/ask", json={"prompt": "how does total work?", "repo": "shop"}
    )
    assert response.status_code == 200
    events = [
        json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")
    ]
    kinds = [event["event"] for event in events]
    assert kinds[0] == "meta"
    assert "token" in kinds
    assert kinds[-1] == "done"
    meta = events[0]
    assert meta["difficulty"]["level"] in {"trivial", "normal", "hard"}
    assert meta["packs"] == ["python"]
    done = events[-1]
    assert "Demo mode" in done["answer"]
    assert done["confidence"]["label"] == "none"
    assert done["timings"]["total_ms"] >= 0


def test_ask_rejects_unknown_repo(tmp_path: Path) -> None:
    app = _app(tmp_path)
    response = _request(app, "POST", "/api/ask", json={"prompt": "hi", "repo": "ghost"})
    assert response.status_code == 404


def test_checks_endpoint_reports_checks_and_findings(tmp_path: Path) -> None:
    app = _app(tmp_path)
    project = _project(tmp_path / "shop")
    (project / "pkg" / "unsafe.py").write_text("value = eval(input())\n")
    _request(app, "POST", "/api/index", json={"path": str(project)})

    payload = _request(app, "GET", "/api/checks?repo=shop&run=true").json()
    assert payload["checks"][0]["name"] == "pytest"
    assert payload["checks"][0]["ok"] is True
    assert payload["blocked"] is True
    assert any(item["rule"] == "code.eval" for item in payload["security"])

    planned = _request(app, "GET", "/api/checks?repo=shop&run=false").json()
    assert planned["checks"][0]["name"] == "pytest"


def test_fix_refuses_in_demo_mode(tmp_path: Path) -> None:
    app = _app(tmp_path)
    _request(app, "POST", "/api/index", json={"path": str(_project(tmp_path / "shop"))})
    response = _request(app, "POST", "/api/fix", json={"problem": "fix it", "repo": "shop"})
    assert response.status_code == 409
    assert "demo mode" in response.json()["detail"]


def test_fix_refuses_without_checks(tmp_path: Path) -> None:
    app = create_app(home=tmp_path / "home", demo=False, allow_roots=[str(tmp_path)])
    empty = tmp_path / "empty"
    empty.mkdir()
    (empty / "main.py").write_text("print('hi')\n")
    _request(app, "POST", "/api/index", json={"path": str(empty)})
    response = _request(app, "POST", "/api/fix", json={"problem": "fix it", "repo": "empty"})
    assert response.status_code == 409
    assert "no test, lint or build command" in response.json()["detail"]


def test_cache_clear_endpoint(tmp_path: Path) -> None:
    app = _app(tmp_path)
    app.state.runtime.store.cache_put("a question", answer="an answer", model="m1")
    assert app.state.runtime.store.cache_stats()["entries"] == 1
    removed = _request(app, "POST", "/api/cache/clear").json()["removed"]
    assert removed == 1
