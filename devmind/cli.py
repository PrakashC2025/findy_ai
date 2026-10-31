"""DevMind command line.

Design rules: every command works offline (degrading with a clear message),
every command is fast (no model is loaded unless you ask a question), and every
answer shows *why* the router chose the model it did.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from typing import Any
from collections.abc import Sequence

from . import __version__
from .backends import BackendUnavailable, DemoBackend, OllamaBackend
from .config import Config
from .hardware import Hardware
from .memory.retriever import Retriever
from .memory.store import MemoryStore
from .prompts import build_messages
from .registry import Registry
from .security import redact
from .router import Router, classify

# --------------------------------------------------------------------- output

_NO_COLOR = bool(os.environ.get("NO_COLOR")) or not sys.stdout.isatty()
_BOLD, _DIM, _GREEN, _YELLOW, _RED, _CYAN = "1", "2", "32", "33", "31", "36"


def paint(text: str, color: str) -> str:
    return text if _NO_COLOR else f"\033[{color}m{text}\033[0m"


def info(message: str) -> None:
    print(paint(f"· {message}", _DIM), file=sys.stderr)


def ok(message: str) -> None:
    print(paint(f"✓ {message}", _GREEN), file=sys.stderr)


def warn(message: str) -> None:
    print(paint(f"! {message}", _YELLOW), file=sys.stderr)


def fail(message: str) -> None:
    print(paint(f"✗ {message}", _RED), file=sys.stderr)


def table(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> str:
    widths = [len(header) for header in headers]
    for row in rows:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], len(cell))
    header_line = "  ".join(header.ljust(widths[i]) for i, header in enumerate(headers))
    divider = "  ".join("-" * width for width in widths)
    body = ["  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)) for row in rows]
    return "\n".join([paint(header_line, _BOLD), divider, *body])


def human_gb(value: float) -> str:
    return f"{value:.1f} GB"


# -------------------------------------------------------------------- context


class Context:
    """Everything a command needs, assembled lazily and cheaply."""

    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.config = Config.load(getattr(args, "home", None))
        self.hardware = Hardware.detect(
            reserve_gb=self.config.hardware.reserve_gb, home=self.config.home
        )
        self.registry = Registry.load(self.config.registry_override_path)
        self.store = MemoryStore(self.config.db_path, redact=self.config.privacy.redact_secrets)
        self.retriever = Retriever(self.store, self.config.memory)
        self.demo = bool(getattr(args, "demo", False)) or bool(os.environ.get("DEVMIND_DEMO"))
        self._backend: Any = None
        self._installed: set[str] | None = None
        self._ollama_note = ""

    # ------------------------------------------------------------------ backend
    @property
    def backend(self) -> Any:
        if self._backend is None:
            if self.demo or self.config.offline:
                self._backend = DemoBackend()
            else:
                self._backend = OllamaBackend(
                    self.config.ollama.base_url,
                    connect_timeout=self.config.ollama.connect_timeout_s,
                    request_timeout=self.config.ollama.request_timeout_s,
                )
        return self._backend

    @property
    def installed(self) -> set[str]:
        """Installed models, from the sync commands (doctor/models/chat).

        Async callers must use :meth:`ensure_installed` — calling
        ``asyncio.run()`` from inside a running loop is exactly the kind of
        hang this project promises not to have.
        """
        if self._installed is None:
            if isinstance(self.backend, DemoBackend):
                self._installed = set()
                self._ollama_note = "demo mode: showing the registry only"
                return self._installed
            try:
                self._installed = set(asyncio.run(self.backend.list_models()))
                self._ollama_note = ""
            except BackendUnavailable as exc:
                self._installed = set()
                self._ollama_note = str(exc)
        return self._installed

    async def ensure_installed(self) -> set[str]:
        """Async variant used inside the agent loop."""
        if self._installed is not None:
            return self._installed
        if isinstance(self.backend, DemoBackend):
            self._installed = set()
            self._ollama_note = "demo mode: showing the registry only"
            return self._installed
        try:
            self._installed = set(await self.backend.list_models())
            self._ollama_note = ""
        except BackendUnavailable as exc:
            self._installed = set()
            self._ollama_note = str(exc)
        return self._installed

    def router(self, installed: set[str] | None = None) -> Router:
        return Router(
            self.registry,
            self.hardware,
            self.config,
            installed=self.installed if installed is None else installed,
            stats=self.store.model_stats,
        )

    async def aclose(self) -> None:
        await self.backend.aclose()
        self.store.close()


# ------------------------------------------------------------------- commands


def cmd_doctor(args: argparse.Namespace) -> int:
    ctx = Context(args)
    config, hardware = ctx.config, ctx.hardware
    print(paint(f"DevMind {__version__}", _BOLD))
    print()
    checks: list[tuple[str, str, str]] = []

    checks.append(("Python", "ok", f"{sys.version.split()[0]} ({sys.executable})"))
    checks.append(("Config", "ok", str(config.config_path)))
    checks.append(("Memory DB", "ok", str(config.db_path)))

    ram_state = "ok" if hardware.ram_gb >= 8 else ("warn" if hardware.ram_gb >= 4 else "fail")
    checks.append(
        ("RAM", ram_state, f"{hardware.ram_gb:.1f} GB ({hardware.ram_available_gb:.1f} GB free)")
    )
    gpu_state = "ok" if hardware.vram_gb >= 6 else "warn"
    gpu_text = (
        f"{hardware.gpu} — {hardware.vram_gb:.1f} GB"
        if hardware.vram_gb
        else "CPU only (works, but pick a small model)"
    )
    checks.append(("GPU", gpu_state, gpu_text))
    checks.append(("Model budget", "ok", human_gb(hardware.usable_memory_gb)))
    checks.append(
        (
            "Disk free",
            "ok" if hardware.disk_free_gb > 8 else "warn",
            human_gb(hardware.disk_free_gb),
        )
    )

    installed: set[str] = set()
    if isinstance(ctx.backend, DemoBackend):
        checks.append(("Ollama", "warn", "demo/offline mode — no inference available"))
    else:
        try:
            names = asyncio.run(ctx.backend.list_models())
            installed = set(names)
            version = asyncio.run(ctx.backend.version())
            checks.append(
                (
                    "Ollama",
                    "ok",
                    f"{config.ollama.base_url} (v{version or '?'}), "
                    f"{len(names)} model(s) installed",
                )
            )
        except BackendUnavailable as exc:
            checks.append(("Ollama", "fail", str(exc)))

    if installed:
        known = [name for name in installed if ctx.registry.get(name)]
        checks.append(
            (
                "Registry",
                "ok",
                f"{len(known)} of {len(installed)} installed model(s) match the registry",
            )
        )
    else:
        checks.append(("Registry", "warn", "no installed model matches the registry yet"))

    memory_stats = ctx.store.stats()
    checks.append(
        (
            "Memory",
            "ok" if memory_stats["memories"] else "warn",
            f"{memory_stats['memories']} memories, {memory_stats['episodes']} episodes",
        )
    )
    checks.append(("Privacy", "ok", "no telemetry, no cloud calls, loopback only"))

    symbols = {"ok": paint("✓", _GREEN), "warn": paint("!", _YELLOW), "fail": paint("✗", _RED)}
    for name, state, detail in checks:
        print(f"  {symbols[state]} {name:<13} {detail}")

    print()
    print(paint("Hardware summary", _BOLD))
    print(f"  {hardware.summary()}")
    min_tok_s = config.hardware.min_tokens_per_s
    suggestion = ctx.registry.best("strong-coder", hardware, min_tok_s=min_tok_s)
    suggestion_note = ""
    if suggestion is None:
        # Nothing in the strong tier fits: offer the best thing that does.
        suggestion = ctx.registry.best("fast-small", hardware, min_tok_s=min_tok_s)
        if suggestion is not None:
            suggestion_note = " (no strong coder fits this budget; fast tier only)"
    if suggestion is None:
        suggestion = ctx.registry.smallest_for_role("strong-coder")
        suggestion_note = " (nothing fits comfortably; expect swapping)"
    fast = ctx.registry.best("fast-small", hardware)
    if suggestion:
        status = "installed" if suggestion.id in installed else "not installed"
        print(
            f"  Recommended coder : {suggestion.id} ({human_gb(suggestion.size_gb)}, "
            f"~{suggestion.estimated_tok_s(hardware):.0f} tok/s, {status}){suggestion_note}"
        )
    if fast and (not suggestion or fast.id != suggestion.id):
        status = "installed" if fast.id in installed else "not installed"
        print(
            f"  Recommended fast  : {fast.id} ({human_gb(fast.size_gb)}, "
            f"~{fast.estimated_tok_s(hardware):.0f} tok/s, {status})"
        )
    if suggestion and suggestion.id not in installed:
        print()
        print(f"  Next step: {paint(f'devmind pull {suggestion.id}', _CYAN)}")
    asyncio.run(ctx.aclose())
    failures = [name for name, state, _ in checks if state == "fail"]
    return 0 if not failures else 1


def cmd_models(args: argparse.Namespace) -> int:
    ctx = Context(args)
    installed = ctx.installed
    hw = ctx.hardware
    if ctx._ollama_note:
        warn(ctx._ollama_note)
        print()

    recommended = {
        role: ctx.registry.best(role, hw, min_tok_s=ctx.config.hardware.min_tokens_per_s)
        for role in ("fast-small", "strong-coder", "reasoning", "embed")
    }
    recommended_ids = {model.id for model in recommended.values() if model}

    models = sorted(
        ctx.registry.all(),
        key=lambda m: (m.quality, m.size_gb),
        reverse=True,
    )
    if args.role:
        models = [m for m in models if m.has_role(args.role)]
    if args.installed_only:
        models = [m for m in models if m.id in installed]

    if args.json:
        payload = {
            "hardware": hw.to_dict(),
            "installed": sorted(installed),
            "models": [
                {
                    **model.to_dict(hw),
                    "installed": model.id in installed,
                    "recommended": model.id in recommended_ids,
                }
                for model in models
            ],
        }
        print(json.dumps(payload, indent=2))
        asyncio.run(ctx.aclose())
        return 0

    rows = []
    for model in models:
        flags = []
        if model.id in installed:
            flags.append(paint("installed", _GREEN))
        if model.id in recommended_ids:
            flags.append(paint("recommended", _CYAN))
        if not model.fits(hw):
            flags.append(paint("too big", _RED))
        elif not model.usable(hw, ctx.config.hardware.min_tokens_per_s):
            flags.append(paint("slow", _YELLOW))
        rows.append(
            [
                model.id,
                f"{model.params_b:g}B",
                human_gb(model.size_gb),
                f"{model.estimated_tok_s(hw):.0f}",
                f"{model.quality:.0f}",
                ",".join(model.roles),
                " ".join(flags) or "-",
            ]
        )

    print(
        paint(f"Registered models — budget {human_gb(hw.usable_memory_gb)} on {hw.backend}", _BOLD)
    )
    print(table(["model", "params", "size", "tok/s*", "Q", "roles", "status"], rows))
    print()
    print(
        paint(
            "* estimated tokens/second from memory bandwidth; replaced by measurements "
            "after you use a model.",
            _DIM,
        )
    )
    missing = [m for m in models if m.id not in installed and m.id in recommended_ids]
    if missing:
        print()
        print("Suggested downloads for this machine:")
        for model in missing:
            print(
                f"  {paint('devmind pull ' + model.id, _CYAN)}  "
                f"({human_gb(model.size_gb)}, ~{model.estimated_tok_s(hw):.0f} tok/s)"
            )
    asyncio.run(ctx.aclose())
    return 0


async def _answer(
    ctx: Context,
    prompt: str,
    *,
    model: str | None = None,
    session: str = "cli",
    history: list[dict[str, str]] | None = None,
    json_output: bool = False,
    show_route: bool = True,
) -> dict[str, Any]:
    """Run one full turn: route → memory → model → stream → record."""
    config = ctx.config
    profile = classify(prompt)
    installed = await ctx.ensure_installed()
    decision = ctx.router(installed).route(prompt, model=model)

    if decision.model is None:
        fail("No usable model. Run `devmind doctor`.")
        return {"error": "no model"}

    if decision.needs_download and not isinstance(ctx.backend, DemoBackend):
        if ctx._ollama_note:
            fail(ctx._ollama_note)
        if show_route:
            fail(decision.reason)
        return {
            "error": "model-not-downloaded",
            "model": decision.model.id,
            "hint": f"devmind pull {decision.model.id}",
        }

    if show_route and not json_output:
        print(
            paint(
                f"◆ {decision.profile.task.value}/{decision.profile.language} "
                f"→ {decision.model.id}",
                _CYAN,
            ),
            file=sys.stderr,
        )
        info(decision.reason)

    if redact(prompt).changed:
        warn(
            "this prompt looks like it contains a secret; memory stores a redacted copy, "
            "and it is only sent to your local model"
        )
    context = ctx.retriever.context_for(prompt)
    if context and show_route and not json_output:
        info(
            f"memory: {len(context.hits)} item(s) injected "
            f"({', '.join(hit.kind for hit in context.hits[:4])})"
        )
    captured = ctx.retriever.capture_preferences(prompt, session=session)
    if captured and not json_output:
        info(f"learned {len(captured)} preference(s) — `devmind memory` to review")

    messages = build_messages(
        prompt,
        profile=profile,
        context=context,
        config=config.models,
        history=history,
    )
    options = {
        "temperature": config.models.temperature,
        "top_p": config.models.top_p,
        "seed": config.models.seed,
        "num_ctx": config.models.num_ctx,
        "num_predict": config.models.max_tokens,
    }

    ctx.store.record_episode(session, "user", prompt, {"task": profile.task.value})
    route_id = (
        ctx.store.log_route(
            session=session,
            task=profile.task.value,
            language=profile.language,
            difficulty=profile.difficulty,
            model=decision.model.id,
            reason=decision.reason,
            prompt_chars=len(prompt),
        )
        if config.router.log_decisions
        else ""
    )

    started = time.perf_counter()
    first_token_ms: float | None = None
    pieces: list[str] = []
    error = ""
    try:
        async for piece in ctx.backend.chat(
            messages,
            decision.model.id,
            options=options,
            keep_alive=config.ollama.keep_alive,
        ):
            if first_token_ms is None:
                first_token_ms = (time.perf_counter() - started) * 1000
            pieces.append(piece)
            if not json_output:
                print(piece, end="", flush=True)
    except BackendUnavailable as exc:
        error = str(exc)
        if not json_output:
            print()
            fail(error)
    except KeyboardInterrupt:
        error = "interrupted"
        if not json_output:
            print()
            warn("interrupted by user")
    elapsed_ms = (time.perf_counter() - started) * 1000
    answer = "".join(pieces)

    if not json_output and answer and not answer.endswith("\n"):
        print()

    ctx.store.record_episode(
        session,
        "assistant",
        answer,
        {
            "task": profile.task.value,
            "model": decision.model.id,
            "latency_ms": round(elapsed_ms, 1),
        },
    )
    if route_id:
        ctx.store.set_route_outcome(route_id, "error" if error else "answered")

    result = {
        "prompt": prompt,
        "answer": answer,
        "route": decision.to_dict(),
        "memory": context.to_dict(),
        "learned": captured,
        "timings": {
            "first_token_ms": round(first_token_ms or 0, 1),
            "total_ms": round(elapsed_ms, 1),
        },
        "error": error or None,
        "demo": isinstance(ctx.backend, DemoBackend),
    }
    if json_output:
        print(json.dumps(result, indent=2))
    elif not error:
        print(
            paint(
                f"— {decision.model.id} · first token {first_token_ms or 0:.0f} ms · "
                f"total {elapsed_ms / 1000:.1f} s",
                _DIM,
            ),
            file=sys.stderr,
        )
    return result


def cmd_ask(args: argparse.Namespace) -> int:
    prompt = " ".join(args.prompt).strip()
    if not prompt:
        fail('Nothing to ask. Example: devmind ask "write a Python retry decorator"')
        return 2
    ctx = Context(args)
    try:
        result = asyncio.run(_answer(ctx, prompt, model=args.model, json_output=args.json))
    finally:
        asyncio.run(ctx.aclose())
    if result.get("error") == "model-not-downloaded":
        return 2
    return 1 if result.get("error") else 0


def cmd_chat(args: argparse.Namespace) -> int:
    ctx = Context(args)
    session = f"chat-{int(time.time())}"
    print(paint("DevMind chat", _BOLD))
    print(paint("Commands: /route <text> · /memory · /teach <fact> · /models · /exit", _DIM))
    history: list[dict[str, str]] = []
    try:
        while True:
            try:
                prompt = input(paint("you > ", _CYAN)).strip()
            except EOFError:
                break
            if not prompt:
                continue
            if prompt in {"/exit", "/quit", ":q"}:
                break
            if prompt == "/memory":
                for memory in ctx.store.list_memories(limit=20):
                    print(f"  [{memory['id']}] ({memory['kind']}) {memory['text']}")
                continue
            if prompt == "/models":
                print(", ".join(sorted(ctx.installed)) or "none installed")
                continue
            if prompt.startswith("/teach "):
                memory_id = ctx.store.remember(prompt[7:], kind="fact", source="cli")
                ok(f"remembered as {memory_id}")
                continue
            if prompt.startswith("/route "):
                decision = ctx.router().route(prompt[7:])
                print(json.dumps(decision.to_dict(), indent=2))
                continue
            if prompt.startswith("/"):
                warn("unknown command")
                continue

            result = asyncio.run(
                _answer(ctx, prompt, model=args.model, session=session, history=history)
            )
            if result.get("answer"):
                history.append({"role": "user", "content": prompt})
                history.append({"role": "assistant", "content": result["answer"]})
                del history[:-12]
    except KeyboardInterrupt:
        print()
    finally:
        asyncio.run(ctx.aclose())
    return 0


def cmd_teach(args: argparse.Namespace) -> int:
    text = " ".join(args.text).strip()
    if not text:
        fail("Nothing to teach.")
        return 2
    ctx = Context(args)
    memory_id = ctx.store.remember(text, kind=args.kind, source="cli")
    ok(f"remembered ({ctx.store.get_memory(memory_id)['kind']}): {text}")
    asyncio.run(ctx.aclose())
    return 0


def cmd_memory(args: argparse.Namespace) -> int:
    ctx = Context(args)
    if args.search:
        hits = ctx.store.search(args.search, top_k=args.limit)
        rows = [[hit.id, hit.kind, f"{hit.score:.2f}", hit.text[:78]] for hit in hits]
        print(table(["id", "kind", "score", "text"], rows) if rows else "No matches.")
    else:
        memories = ctx.store.list_memories(limit=args.limit, kind=args.kind)
        if not memories:
            print('Memory is empty. Teach it something: devmind teach "we use pnpm"')
        else:
            rows = [
                [
                    memory["id"],
                    memory["kind"],
                    time.strftime("%Y-%m-%d", time.localtime(memory["created_at"])),
                    memory["text"][:78],
                ]
                for memory in memories
            ]
            print(table(["id", "kind", "created", "text"], rows))
        stats = ctx.store.stats()
        print(
            paint(
                f"\n{stats['memories']} memories · {stats['episodes']} episodes · "
                f"{stats['db_bytes'] / 1024:.0f} KB · {stats['path']}",
                _DIM,
            )
        )
    asyncio.run(ctx.aclose())
    return 0


def cmd_forget(args: argparse.Namespace) -> int:
    ctx = Context(args)
    if args.all:
        if not args.yes and input("Delete ALL DevMind memory? type 'yes': ").strip() != "yes":
            warn("cancelled")
            asyncio.run(ctx.aclose())
            return 1
        ctx.store.wipe()
        ok("all memory deleted")
    else:
        if not args.id:
            fail("Give a memory id, or --all.")
            asyncio.run(ctx.aclose())
            return 2
        if ctx.store.forget(args.id):
            ok(f"forgot {args.id}")
        else:
            fail(f"no memory with id {args.id}")
            asyncio.run(ctx.aclose())
            return 1
    asyncio.run(ctx.aclose())
    return 0


async def _pull(ctx: Context, model_id: str) -> int:
    backend = ctx.backend
    if isinstance(backend, DemoBackend):
        fail("Pulling requires Ollama; demo mode is active.")
        return 2
    spec = ctx.registry.get(model_id)
    if spec and not spec.fits(ctx.hardware):
        warn(
            f"{model_id} needs {human_gb(spec.size_gb)} but the budget is "
            f"{human_gb(ctx.hardware.usable_memory_gb)}; it will be slow."
        )
    print(f"Pulling {model_id} …")
    last = ""
    try:
        async for event in backend.pull(model_id):
            status = event.get("status", "")
            completed, total = event.get("completed") or 0, event.get("total") or 0
            if total:
                percent = completed / total * 100
                line = (
                    f"\r  {status:<24} {percent:5.1f}%  {completed / 1e9:.1f}/{total / 1e9:.1f} GB"
                )
            else:
                line = f"\r  {status:<60}"
            if line != last:
                print(line, end="", flush=True)
                last = line
    except BackendUnavailable as exc:
        print()
        fail(str(exc))
        return 1
    print()
    ok(f"{model_id} installed")
    return 0


def cmd_pull(args: argparse.Namespace) -> int:
    ctx = Context(args)
    try:
        return asyncio.run(_pull(ctx, args.model))
    finally:
        asyncio.run(ctx.aclose())


def cmd_config(args: argparse.Namespace) -> int:
    ctx = Context(args)
    if args.init:
        ctx.config.save()
        ok(f"wrote {ctx.config.config_path}")
    elif args.path:
        print(ctx.config.config_path)
    else:
        print(
            ctx.config.config_path.read_text()
            if ctx.config.config_path.exists()
            else "# no config file yet — run `devmind config --init`"
        )
    asyncio.run(ctx.aclose())
    return 0


def cmd_stats(args: argparse.Namespace) -> int:
    ctx = Context(args)
    stats = ctx.store.stats()
    print(json.dumps(stats, indent=2))
    routes = ctx.store.recent_routes(limit=args.limit)
    if routes:
        print(paint("\nRecent routing decisions", _BOLD))
        rows = [
            [
                time.strftime("%H:%M", time.localtime(route["at"])),
                route["task"],
                route["language"],
                route["model"] or "-",
                route["outcome"] or "-",
            ]
            for route in routes
        ]
        print(table(["time", "task", "lang", "model", "outcome"], rows))
    asyncio.run(ctx.aclose())
    return 0


# --------------------------------------------------------------------- parser


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="devmind",
        description="DevMind — free, local-first coding assistant.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  devmind doctor\n"
            "  devmind models\n"
            "  devmind pull qwen2.5-coder:7b\n"
            '  devmind ask "add retries with backoff to this requests call"\n'
            '  devmind teach "we use pnpm, never npm"\n'
        ),
    )
    parser.add_argument("--version", action="version", version=f"devmind {__version__}")
    parser.add_argument("--home", help="data directory (default ~/.devmind)")
    parser.add_argument(
        "--demo", action="store_true", help="use the deterministic demo backend (no model)"
    )
    sub = parser.add_subparsers(dest="command")

    # The same two global flags are accepted *after* the subcommand as well.
    # SUPPRESS keeps the subparser from clobbering a value given before it.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--home", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    common.add_argument(
        "--demo", action="store_true", default=argparse.SUPPRESS, help=argparse.SUPPRESS
    )

    doctor = sub.add_parser("doctor", help="check the installation and hardware", parents=[common])
    doctor.set_defaults(func=cmd_doctor)

    models = sub.add_parser(
        "models", help="list registered models and what fits this machine", parents=[common]
    )
    models.add_argument(
        "--role", choices=["fast-small", "strong-coder", "reasoning", "judge", "draft", "embed"]
    )
    models.add_argument("--installed", dest="installed_only", action="store_true")
    models.add_argument("--json", action="store_true")
    models.set_defaults(func=cmd_models)

    ask = sub.add_parser("ask", help="ask a one-shot question", parents=[common])
    ask.add_argument("prompt", nargs="+")
    ask.add_argument("-m", "--model", help="force a model id")
    ask.add_argument("--json", action="store_true")
    ask.set_defaults(func=cmd_ask)

    chat = sub.add_parser("chat", help="interactive session", parents=[common])
    chat.add_argument("-m", "--model", help="force a model id")
    chat.set_defaults(func=cmd_chat)

    teach = sub.add_parser("teach", help="remember a fact, preference or snippet", parents=[common])
    teach.add_argument("text", nargs="+")
    teach.add_argument(
        "--kind", default="preference", choices=["preference", "fact", "snippet", "rule", "code"]
    )
    teach.set_defaults(func=cmd_teach)

    memory = sub.add_parser(
        "memory", help="list or search what DevMind remembers", parents=[common]
    )
    memory.add_argument("--kind", choices=["preference", "fact", "snippet", "rule", "code"])
    memory.add_argument("--search", metavar="QUERY", help="semantic+lexical search query")
    memory.add_argument("--limit", type=int, default=30)
    memory.set_defaults(func=cmd_memory)

    forget = sub.add_parser("forget", help="delete one memory, or everything", parents=[common])
    forget.add_argument("id", nargs="?")
    forget.add_argument("--all", action="store_true")
    forget.add_argument("--yes", action="store_true", help="skip the confirmation")
    forget.set_defaults(func=cmd_forget)

    pull = sub.add_parser("pull", help="download a model with Ollama", parents=[common])
    pull.add_argument("model")
    pull.set_defaults(func=cmd_pull)

    config = sub.add_parser("config", help="show or create the config file", parents=[common])
    config.add_argument("--init", action="store_true")
    config.add_argument("--path", action="store_true")
    config.set_defaults(func=cmd_config)

    stats = sub.add_parser(
        "stats", help="memory stats and recent routing decisions", parents=[common]
    )
    stats.add_argument("--limit", type=int, default=10)
    stats.set_defaults(func=cmd_stats)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return 0
    try:
        return int(args.func(args))
    except SystemExit as exc:
        if isinstance(exc.code, str):
            fail(exc.code)
            return 1
        return int(exc.code or 0)
    except BackendUnavailable as exc:
        fail(str(exc))
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
