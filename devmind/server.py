"""Local web app: the same DevMind, reachable from a PC or a phone.

Design constraints that shape this file:

* **Loopback by default.** ``devmind serve`` binds ``127.0.0.1`` unless you ask for
  ``--host 0.0.0.0``, and refuses to expose the network without a token.
* **Explicit roots.** The API only reads or indexes paths under roots you passed
  with ``--allow`` (plus the current directory and your home directory).
* **No duplicate brain.** The server reuses the same store, retriever, prompt
  builder, repair loop and verification code as the CLI.
* **Streaming first.** ``/api/ask`` streams server-sent events so the phone shows
  the quick answer immediately, then upgrades it if the pipeline finds better.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import time
from pathlib import Path
from typing import Any

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, Field

from . import __version__
from .backends import (
    BackendUnavailable,
    DemoBackend,
    get_backend,
    register_remote_models,
)
from .confidence import assess
from .config import Config
from .difficulty import classify_difficulty
from .github import GitHubError, GitWorkspace, normalise_git_url, repo_name_from_url
from .hardware import Hardware
from .indexer import CodeIndexer
from .languages import project_packs, style_block
from .memory.retriever import Retriever
from .memory.store import MemoryStore
from .pipeline import AnswerPipeline
from .prompts import build_messages
from .registry import Registry
from .repair import SelfRepairer, apply_diff
from .router import Router, classify
from .sandbox import SandboxLimits, scan_protections
from .security_scan import blocking, scan_paths, summarise as scan_summarise
from .verify import detect_checks, run_local_checks, summarise

WEB_DIR = Path(__file__).resolve().parent / "web"
STATIC_FILES = {
    "app.css": "text/css",
    "app.js": "text/javascript",
    "manifest.webmanifest": "application/manifest+json",
    "sw.js": "text/javascript",
    "icon.svg": "image/svg+xml",
}


# --------------------------------------------------------------------- models


class AskRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=20000)
    repo: str = ""
    deep: bool = True


class IndexRequest(BaseModel):
    path: str
    repo: str = ""
    force: bool = False


class FixRequest(BaseModel):
    problem: str = Field(min_length=1, max_length=20000)
    repo: str = ""
    rounds: int = 3
    apply: bool = False


class CheckRequest(BaseModel):
    repo: str
    run: bool = True


class BriefRequest(BaseModel):
    text: str = Field(default="", max_length=4000)
    clear: bool = False


class CloneRequest(BaseModel):
    url: str = Field(min_length=1, max_length=500)
    name: str = ""


# -------------------------------------------------------------------- runtime


class Runtime:
    """State shared by every request: config, store, backend, model choice."""

    def __init__(
        self,
        home: str | Path | None = None,
        *,
        demo: bool = False,
        allow_roots: list[str] | None = None,
    ):
        self.config = Config.load(home)
        self.demo = demo or self.config.backend.kind == "demo"
        self.store = MemoryStore(self.config.db_path, redact=self.config.privacy.redact_secrets)
        self.retriever = Retriever(self.store, self.config.memory)
        self.registry = Registry.load(self.config.registry_override_path)
        register_remote_models(self.registry, self.config)
        self.hardware = Hardware.detect(
            reserve_gb=self.config.hardware.reserve_gb, home=self.config.home
        )
        self.backend = get_backend(self.config, demo=self.demo)
        roots = [Path(item).expanduser().resolve() for item in (allow_roots or [])]
        self.allow_roots = roots or [Path.cwd().resolve(), Path.home().resolve(), self.config.home]
        self._installed: set[str] | None = None
        self._style: dict[str, tuple[str, list[str]]] = {}

    async def installed(self) -> set[str]:
        if self._installed is None:
            if isinstance(self.backend, DemoBackend):
                self._installed = set()
            elif getattr(self.backend, "single_model", False):
                self._installed = {self.config.llamacpp.model}
            else:
                try:
                    self._installed = set(await self.backend.list_models())
                except BackendUnavailable:
                    self._installed = set()
        return self._installed

    async def aclose(self) -> None:
        await self.backend.aclose()
        self.store.close()

    # -------------------------------------------------------------- paths
    def allowed(self, path: Path) -> bool:
        try:
            resolved = path.expanduser().resolve()
        except OSError:
            return False
        return any(resolved == root or root in resolved.parents for root in self.allow_roots)

    def repo_root(self, repo: str) -> Path | None:
        for entry in self.store.list_repos():
            if entry.get("repo") == repo and entry.get("root"):
                root = Path(entry["root"])
                if root.is_dir():
                    return root
        return None

    def require_repo_root(self, repo: str) -> Path:
        root = self.repo_root(repo)
        if root is None:
            raise HTTPException(status_code=404, detail=f"repository '{repo}' is not indexed")
        if not self.allowed(root):
            raise HTTPException(status_code=403, detail="repository is outside the allowed roots")
        return root

    # ------------------------------------------------------------- models
    async def models_for(self, prompt: str) -> tuple[str, str | None, str | None]:
        """(quick, strong, judge) - never invents a model that is not loaded."""
        if isinstance(self.backend, DemoBackend):
            return "demo", None, None
        if getattr(self.backend, "single_model", False):
            model = self.config.llamacpp.model
            return model, model, model
        installed = await self.installed()
        if not installed:
            return self.config.models.default or "local-model", None, None
        decision = Router(
            self.registry,
            self.hardware,
            self.config,
            installed=installed,
            stats=self.store.model_stats,
        ).route(prompt)
        quick = decision.model.id if decision.model else sorted(installed)[0]

        def pick(role: str, exclude: set[str]) -> str | None:
            for model in self.registry.models:
                if role in model.roles and model.id in installed and model.id not in exclude:
                    return model.id
            return None

        strong = pick("strong-coder", {quick}) or pick("judge", {quick})
        judge = pick("judge", {quick, strong or ""})
        return quick, strong, judge

    def style_for(self, repo: str) -> tuple[str, list[str]]:
        root = self.repo_root(repo)
        if root is None:
            return "", []
        key = str(root)
        if key not in self._style:
            self._style[key] = (style_block(root), [pack.id for pack in project_packs(root)])
        return self._style[key]

    # ------------------------------------------------------------- payloads
    def status(self) -> dict[str, Any]:
        return {
            "version": __version__,
            "demo": self.demo,
            "backend": self.config.backend.kind,
            "cloud": self.config.backend.kind == "remote" and self.config.remote.enabled,
            "model": "demo"
            if self.demo
            else (
                self.config.llamacpp.model
                if getattr(self.backend, "single_model", False)
                else self.config.models.default
            ),
            "hardware": {
                "summary": self.hardware.summary(),
                "backend": self.hardware.backend,
                "model_budget_gb": round(self.hardware.usable_memory_gb, 2),
                "ram_gb": round(self.hardware.ram_gb, 1),
                "cores": self.hardware.cores,
            },
            "counts": {
                "memories": self.store.memory_count(),
                "repos": len(self.store.list_repos()),
                "cache": self.store.cache_stats(),
            },
            "sandbox": scan_protections(SandboxLimits()),
            "roots": [str(root) for root in self.allow_roots],
            "offline": self.config.offline,
        }


# ------------------------------------------------------------------ rendering


def _asset(name: str) -> str:
    return (WEB_DIR / name).read_text(encoding="utf-8")


def _sse(event: str, **payload: Any) -> str:
    return f"data: {json.dumps({'event': event, **payload}, ensure_ascii=False)}\n\n"


# ---------------------------------------------------------------------- app


def create_app(
    *,
    home: str | Path | None = None,
    demo: bool = False,
    token: str = "",
    allow_roots: list[str] | None = None,
) -> FastAPI:
    runtime = Runtime(home, demo=demo, allow_roots=allow_roots)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):  # type: ignore[no-untyped-def]
        if not runtime.demo:
            try:
                health = await runtime.backend.health()
            except Exception as exc:  # never block startup on a broken backend
                health = None
                print(f"DevMind: could not check the model backend: {exc}")
            if health is not None and not health.ok:
                print("")
                print("DevMind: the web UI is starting, but NO model is answering yet.")
                for line in health.detail.splitlines():
                    print(f"  {line}")
                print("  This is shown in red at the top of the app until it is fixed.")
                print("")
        yield
        await runtime.aclose()

    app = FastAPI(
        title="DevMind",
        version=__version__,
        docs_url=None,
        redoc_url=None,
        lifespan=lifespan,
    )
    app.state.runtime = runtime
    app.state.token = token

    if token:

        @app.middleware("http")
        async def _auth(request: Request, call_next):  # type: ignore[no-untyped-def]
            public = request.url.path == "/" or request.url.path in {
                f"/static/{name}" for name in STATIC_FILES
            }
            if public or request.url.path == "/healthz":
                return await call_next(request)
            supplied = (
                request.query_params.get("token")
                or request.headers.get("x-devmind-token")
                or request.cookies.get("devmind_token")
            )
            if supplied != token:
                return JSONResponse({"detail": "unauthorized"}, status_code=401)
            response = await call_next(request)
            if request.query_params.get("token") == token:
                response.set_cookie(
                    "devmind_token", token, httponly=True, samesite="strict", max_age=30 * 86400
                )
            return response

    # ------------------------------------------------------------------ static
    @app.get("/", response_class=HTMLResponse)
    async def index() -> str:
        return _asset("index.html")

    @app.get("/static/{name}")
    async def static(name: str) -> Response:
        if name not in STATIC_FILES:
            raise HTTPException(status_code=404)
        headers = {"service-worker-allowed": "/"} if name == "sw.js" else None
        return Response(_asset(name), media_type=STATIC_FILES[name], headers=headers)

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    # -------------------------------------------------------------------- api
    @app.get("/api/status")
    async def api_status() -> dict[str, Any]:
        payload = runtime.status()
        payload["checks"] = [spec.name for spec in detect_checks(Path.cwd())]
        try:
            health = await runtime.backend.health()
            payload["health"] = health.to_dict()
        except Exception as exc:  # status must never 500
            payload["health"] = {"ok": False, "detail": str(exc), "version": "", "models": 0}
        payload["health"]["base_url"] = getattr(runtime.backend, "base_url", "")
        return payload

    @app.get("/api/repos")
    async def api_repos() -> list[dict[str, Any]]:
        repos = runtime.store.list_repos()
        for entry in repos:
            entry["indexed_ago_s"] = round(time.time() - float(entry.get("updated_at") or 0), 1)
        return repos

    @app.post("/api/index")
    async def api_index(request: IndexRequest) -> dict[str, Any]:
        target = Path(request.path)
        if not target.exists() or not target.is_dir():
            raise HTTPException(status_code=400, detail=f"{target} is not a directory")
        if not runtime.allowed(target):
            raise HTTPException(status_code=403, detail="path is outside the allowed roots")
        report = await asyncio.to_thread(
            CodeIndexer(runtime.store, runtime.config.index).index,
            target,
            force=request.force,
            repo=request.repo or None,
        )
        return {
            "repo": report.repo,
            "root": str(report.root),
            "indexed": report.indexed,
            "chunks": report.chunks,
            "symbols": report.symbols,
            "complete": report.complete,
            "summary": report.summary(),
        }

    @app.get("/api/brief")
    async def api_brief() -> dict[str, Any]:
        rows = runtime.store.list_memories(kind="business", limit=20)
        return {
            "notes": [row["text"] for row in rows],
            "block": runtime.retriever.business_block(),
        }

    @app.post("/api/brief")
    async def api_brief_save(request: BriefRequest) -> dict[str, Any]:
        if request.clear:
            removed = runtime.store.forget_kind("business")
            return {"removed": removed, "notes": []}
        text = request.text.strip()
        if not text:
            raise HTTPException(status_code=400, detail="text is required")
        runtime.store.remember(text, "business")
        rows = runtime.store.list_memories(kind="business", limit=20)
        return {"removed": 0, "notes": [row["text"] for row in rows]}

    @app.post("/api/clone")
    async def api_clone(request: CloneRequest) -> dict[str, Any]:
        """Clone a repository into $DEVMIND_HOME/repos and index it.

        Public repositories work with the URL alone. Private ones need the
        authenticated CLI (`devmind github login` + `devmind github clone`)
        because the web app never holds your passphrase.
        """
        url = normalise_git_url(request.url)
        if not url:
            raise HTTPException(status_code=400, detail="paste a git URL or owner/name")
        raw_name = request.name.strip() or repo_name_from_url(request.url)
        safe = re.sub(r"[^A-Za-z0-9._-]", "-", raw_name.removesuffix(".git"))[:80] or "repo"
        dest = runtime.config.home / "repos" / safe
        already = dest.exists() and any(dest.iterdir())
        if not already:
            dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            target = await asyncio.to_thread(GitWorkspace(dest.parent).clone, url, dest, depth=1)
        except GitHubError as exc:
            message = str(exc)
            if "Authentication" in message or "could not read" in message.lower():
                raise HTTPException(
                    status_code=502,
                    detail=(
                        "clone failed (private repository?). Run `devmind github login` and "
                        f"`devmind github clone {url} --dest {dest}` in a terminal, then open "
                        "the folder here."
                    ),
                ) from exc
            raise HTTPException(status_code=502, detail=f"clone failed: {message[-300:]}") from exc
        report = await asyncio.to_thread(
            CodeIndexer(runtime.store, runtime.config.index).index, target, repo=safe
        )
        stats = next(
            (entry for entry in runtime.store.list_repos() if entry["repo"] == report.repo), {}
        )
        return {
            "already": already,
            "repo": report.repo,
            "root": str(target),
            "indexed": report.indexed,
            "files": stats.get("files", 0),
            "chunks": report.chunks,
            "symbols": report.symbols,
            "summary": report.summary(),
        }

    @app.get("/api/files")
    async def api_files(repo: str) -> list[str]:
        runtime.require_repo_root(repo)
        return runtime.store.repo_files(repo)

    @app.get("/api/file")
    async def api_file(repo: str, path: str) -> dict[str, Any]:
        root = runtime.require_repo_root(repo)
        candidate = (root / path).resolve()
        if candidate != root and root not in candidate.parents:
            raise HTTPException(status_code=400, detail="path escapes the repository")
        if not candidate.is_file():
            raise HTTPException(status_code=404, detail="file not found")
        try:
            text = candidate.read_text(encoding="utf-8")[:200_000]
        except (OSError, UnicodeDecodeError) as exc:
            raise HTTPException(status_code=400, detail=f"cannot read file: {exc}") from exc
        return {"path": path, "text": text}

    @app.post("/api/ask")
    async def api_ask(request: AskRequest) -> StreamingResponse:
        prompt = request.prompt.strip()
        repo = request.repo or ""
        root = runtime.repo_root(repo) if repo else None
        if repo and root is None:
            raise HTTPException(status_code=404, detail=f"repository '{repo}' is not indexed")

        async def events():  # type: ignore[no-untyped-def]
            try:
                profile = classify(prompt)
                difficulty = classify_difficulty(prompt)
                context = runtime.retriever.context_for(prompt, repo=repo or None)
                style, packs = runtime.style_for(repo) if repo else ("", [])
                messages = build_messages(
                    prompt,
                    profile=profile,
                    context=context,
                    config=runtime.config.models,
                    extra_system=style,
                )
                quick_model, strong_model, judge_model = await runtime.models_for(prompt)
                cited_paths = [hit.location.split(":")[0] for hit in context.code_hits]
                yield _sse(
                    "meta",
                    difficulty=difficulty.to_dict(),
                    task=profile.task.value,
                    language=profile.language,
                    model=quick_model,
                    strong_model=strong_model,
                    packs=packs,
                    code_hits=[hit.to_dict() for hit in context.code_hits],
                    memories=len(context.hits),
                )
                options = {
                    "temperature": runtime.config.models.temperature,
                    "top_p": runtime.config.models.top_p,
                    "seed": runtime.config.models.seed,
                    "num_ctx": runtime.config.models.num_ctx,
                    "num_predict": runtime.config.models.max_tokens,
                }

                started = time.perf_counter()
                pieces: list[str] = []
                first_token_ms: float | None = None
                async for chunk in runtime.backend.chat(messages, quick_model, options=options):
                    if first_token_ms is None:
                        first_token_ms = (time.perf_counter() - started) * 1000
                    pieces.append(chunk)
                    yield _sse("token", text=chunk)
                quick = "".join(pieces)

                pipeline = AnswerPipeline(
                    runtime.backend,
                    store=runtime.store,
                    config=runtime.config,
                    repo=repo,
                )
                result = await pipeline.run(
                    prompt,
                    messages,
                    difficulty=difficulty.level,
                    language=profile.language,
                    cited_paths=cited_paths,
                    quick_model=quick_model,
                    strong_model=strong_model if request.deep else None,
                    judge_model=judge_model if request.deep else None,
                    options=options,
                    sources=len(context.citations),
                    quick_text=quick,
                    demo=runtime.demo,
                )
                confidence = result.confidence or assess(
                    result.answer, sources=len(context.citations)
                )
                if result.answer != quick:
                    yield _sse(
                        "replace",
                        text=result.answer,
                        model=result.model,
                        candidates=[candidate.to_dict() for candidate in result.candidates],
                        plan=result.plan,
                        judge_note=result.judge_note,
                    )
                yield _sse(
                    "done",
                    answer=result.answer,
                    model=result.model,
                    cached=result.cached,
                    confidence=confidence.to_dict(),
                    citations=context.citations,
                    timings={
                        "first_token_ms": round(first_token_ms or 0, 1),
                        "total_ms": round((time.perf_counter() - started) * 1000, 1),
                    },
                )
                runtime.store.record_episode(
                    "web",
                    "user",
                    prompt,
                    {"task": profile.task.value, "repo": repo},
                )
                runtime.store.record_episode(
                    "web",
                    "assistant",
                    result.answer,
                    {"model": result.model, "confidence": confidence.score},
                )
            except BackendUnavailable as exc:
                yield _sse("error", message=str(exc))
            except Exception as exc:
                yield _sse("error", message=f"{type(exc).__name__}: {exc}")

        return StreamingResponse(
            events(),
            media_type="text/event-stream",
            headers={"cache-control": "no-store", "x-accel-buffering": "no"},
        )

    @app.get("/api/checks")
    async def api_checks(repo: str, run: bool = True) -> dict[str, Any]:
        root = runtime.require_repo_root(repo)
        specs = detect_checks(root)
        if not run:
            return {"checks": [{"name": spec.name, "command": spec.command} for spec in specs]}
        results = await asyncio.to_thread(run_local_checks, root, timeout=300.0)
        findings = await asyncio.to_thread(scan_paths, root)
        return {
            "checks": [result.to_dict() for result in results],
            "security": [finding.to_dict() for finding in findings],
            "summary": summarise(results),
            "security_summary": scan_summarise(findings),
            "blocked": bool(blocking(findings)),
        }

    @app.post("/api/fix")
    async def api_fix(request: FixRequest) -> dict[str, Any]:
        root = runtime.require_repo_root(request.repo)
        if isinstance(runtime.backend, DemoBackend):
            raise HTTPException(status_code=409, detail="demo mode cannot propose patches")
        specs = detect_checks(root)
        if not specs:
            raise HTTPException(
                status_code=409, detail="no test, lint or build command detected in this project"
            )
        quick, strong, _judge = await runtime.models_for(request.problem)
        model = strong or quick
        context = runtime.retriever.context_for(request.problem, repo=request.repo or None)
        style, _packs = runtime.style_for(request.repo)
        repairer = SelfRepairer(
            runtime.backend,
            model=model,
            root=root,
            checks=specs,
            max_rounds=max(1, min(5, request.rounds)),
            timeout=300.0,
        )
        outcome = await repairer.run(
            request.problem,
            context_block=context.block + ("\n\n" + style if style else ""),
            language=classify(request.problem).language,
        )
        payload = outcome.to_dict()
        payload["applied"] = False
        if outcome.ok and request.apply:
            applied, message = apply_diff(root, outcome.diff)
            payload["applied"] = applied
            payload["apply_message"] = message
            if applied:
                post = run_local_checks(root, timeout=300)
                payload["post_checks"] = [check.to_dict() for check in post]
        return payload

    @app.post("/api/cache/clear")
    async def api_cache_clear() -> dict[str, int]:
        return {"removed": runtime.store.cache_clear()}

    return app


# ---------------------------------------------------------------------- CLI


def _lan_url(port: int) -> str:
    try:
        import socket

        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("8.8.8.8", 80))
            host = probe.getsockname()[0]
    except OSError:
        return ""
    return f"http://{host}:{port}"


def serve(
    *,
    home: str | Path | None = None,
    host: str = "127.0.0.1",
    port: int = 8787,
    token: str = "",
    open_browser: bool = False,
    demo: bool = False,
    allow_roots: list[str] | None = None,
) -> int:
    import uvicorn

    network = host not in {"127.0.0.1", "localhost", "::1"}
    if network and not token:
        token = os.environ.get("DEVMIND_TOKEN") or _random_token()
        print("Network access requires a token; one was generated for this run.")
    app = create_app(home=home, demo=demo, token=token, allow_roots=allow_roots)
    local = f"http://{'127.0.0.1' if host in {'0.0.0.0', '::'} else host}:{port}"
    print(f"DevMind {__version__} on {local}")
    if token:
        print(f"Open: {local}/?token={token}")
    if host in {"0.0.0.0", "::"}:
        lan = _lan_url(port)
        if lan:
            print(f"Phone (same Wi-Fi): {lan}/?token={token}")
    if open_browser:
        import webbrowser

        webbrowser.open(f"{local}/?token={token}" if token else f"{local}/")
    uvicorn.run(app, host=host, port=port, log_level="warning")
    return 0


def _random_token() -> str:
    import secrets

    return secrets.token_urlsafe(18)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="devmind serve")
    parser.add_argument("--home")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--token", default="")
    parser.add_argument("--allow", action="append", default=[], help="extra allowed root")
    parser.add_argument("--open", dest="open_browser", action="store_true")
    parser.add_argument("--demo", action="store_true")
    args = parser.parse_args(argv)
    return serve(
        home=args.home,
        host=args.host,
        port=args.port,
        token=args.token,
        open_browser=args.open_browser,
        demo=args.demo,
        allow_roots=args.allow,
    )


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
