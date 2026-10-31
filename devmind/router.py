"""Task classification and model routing.

Three layers, cheapest first:

1. **Rules** (this file) - deterministic, explainable, no model required.
   They cover ~90 % of real prompts and always produce a reason string.
2. **Learned priors** - per-(task, model) win rates stored in SQLite.  They
   nudge the ranking but can never override a hard constraint (memory fit,
   speed floor, explicit user choice).
3. **Judge/ensemble** - only for hard tasks, wired in step 4.

The router never picks a paid API because DevMind has no paid code paths.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from collections.abc import Callable, Iterable

from .config import Config
from .hardware import Hardware
from .registry import Model, Registry

# --------------------------------------------------------------------- tasks


class Task(StrEnum):
    AUTOCOMPLETE = "autocomplete"
    EXPLAIN = "explain"
    BUG_FIX = "bug_fix"
    REFACTOR = "refactor"
    WRITE_NEW = "write_new"
    TESTS = "tests"
    ARCHITECTURE = "architecture"
    SHELL = "shell"
    SQL = "sql"
    DOCS = "docs"
    GENERAL = "general"


#: Which registry role each task prefers, in order.
TASK_ROLES: dict[Task, tuple[str, ...]] = {
    Task.AUTOCOMPLETE: ("fast-small", "draft"),
    Task.EXPLAIN: ("reasoning", "strong-coder"),
    Task.BUG_FIX: ("strong-coder", "reasoning"),
    Task.REFACTOR: ("strong-coder", "reasoning"),
    Task.WRITE_NEW: ("strong-coder",),
    Task.TESTS: ("strong-coder",),
    Task.ARCHITECTURE: ("reasoning", "judge", "strong-coder"),
    Task.SHELL: ("strong-coder",),
    Task.SQL: ("strong-coder",),
    Task.DOCS: ("reasoning", "strong-coder"),
    Task.GENERAL: ("strong-coder", "reasoning"),
}

#: Difficulty floor: harder tasks demand higher-quality models.
TASK_BASE_DIFFICULTY: dict[Task, float] = {
    Task.AUTOCOMPLETE: 0.05,
    Task.EXPLAIN: 0.25,
    Task.DOCS: 0.20,
    Task.SHELL: 0.35,
    Task.SQL: 0.35,
    Task.TESTS: 0.40,
    Task.WRITE_NEW: 0.45,
    Task.REFACTOR: 0.55,
    Task.BUG_FIX: 0.60,
    Task.ARCHITECTURE: 0.70,
    Task.GENERAL: 0.35,
}

_LANG_HINTS: dict[str, tuple[str, ...]] = {
    "python": (
        "python",
        ".py",
        "pytest",
        "django",
        "flask",
        "fastapi",
        "pandas",
        "numpy",
        "def ",
        "import ",
        "pip ",
    ),
    "javascript": ("javascript", "node", ".js", "npm", "express", "react", "const ", "=>"),
    "typescript": ("typescript", ".ts", "tsx", ".tsx", "interface ", "type "),
    "java": ("java", ".java", "spring", "maven", "gradle", "public class "),
    "c": ("c language", ".c", "#include", "malloc", "printf"),
    "cpp": ("c++", "cpp", ".cpp", ".hpp", "std::", "template<"),
    "csharp": ("c#", "csharp", ".cs", "dotnet", ".net", "unity"),
    "go": ("golang", " go ", ".go", "func ", "goroutine", "go mod"),
    "rust": ("rust", ".rs", "cargo", "rustc", "fn main", "&mut "),
    "php": ("php", ".php", "laravel", "composer"),
    "kotlin": ("kotlin", ".kt", "android", "jetpack"),
    "swift": ("swift", ".swift", "swiftui", "xcode"),
    "sql": ("sql", "select ", "join ", "postgres", "mysql", "sqlite", " query "),
    "bash": ("bash", "shell", "zsh", "docker", "kubernetes", "systemd", "devops", "cron"),
    "html": ("html", "css", ".html", ".css"),
    "ruby": ("ruby", ".rb", "rails", "gem "),
    "r": ("r language", ".r ", "ggplot", "dplyr"),
    "scala": ("scala", ".scala", "spark"),
    "elixir": ("elixir", ".ex", "phoenix"),
    "dart": ("dart", "flutter", ".dart"),
}

_EXT_TO_LANG = {
    ".py": "python",
    ".js": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".jsx": "javascript",
    ".java": "java",
    ".c": "c",
    ".h": "c",
    ".cc": "cpp",
    ".cpp": "cpp",
    ".hpp": "cpp",
    ".cs": "csharp",
    ".go": "go",
    ".rs": "rust",
    ".rb": "ruby",
    ".php": "php",
    ".kt": "kotlin",
    ".swift": "swift",
    ".sql": "sql",
    ".sh": "bash",
    ".bash": "bash",
    ".html": "html",
    ".css": "html",
    ".dart": "dart",
    ".scala": "scala",
    ".ex": "elixir",
    ".exs": "elixir",
    ".r": "r",
}


@dataclass
class TaskProfile:
    task: Task
    language: str
    difficulty: float
    signals: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "task": self.task.value,
            "language": self.language,
            "difficulty": round(self.difficulty, 2),
            "signals": self.signals,
        }


@dataclass
class RouteDecision:
    profile: TaskProfile
    model: Model | None
    reason: str
    alternatives: list[Model] = field(default_factory=list)
    needs_download: bool = False
    estimated_tok_s: float = 0.0

    def to_dict(self) -> dict:
        return {
            "task": self.profile.task.value,
            "language": self.profile.language,
            "difficulty": round(self.profile.difficulty, 2),
            "signals": self.profile.signals,
            "model": self.model.id if self.model else None,
            "model_quality": self.model.quality if self.model else None,
            "reason": self.reason,
            "alternatives": [m.id for m in self.alternatives],
            "needs_download": self.needs_download,
            "estimated_tok_s": self.estimated_tok_s,
        }


# ---------------------------------------------------------------- classifier

_FENCE_RE = re.compile(r"```([A-Za-z0-9+#.-]*)")
_PATH_RE = re.compile(
    r"[\w./-]+\.(?:py|js|ts|tsx|jsx|java|c|h|cc|cpp|hpp|cs|go|rs|rb|php|kt|swift|sql|sh|html|css|dart|scala|ex|r)\b",
    re.I,
)
_TRACEBACK_RE = re.compile(
    r"traceback \(most recent|^\s*at .+\(.+:\d+\)|Exception in thread|Error: .+ line \d+",
    re.I | re.M,
)
_ERROR_RE = re.compile(
    r"\b(error|bug|fail(s|ed|ing)?|exception|crash|broken|not working|"
    r"doesn'?t work|undefined|segfault|panic|keeps? (restarting|dying|failing)|"
    r"stuck|hangs?|not starting|won'?t start)\b",
    re.I,
)
_EXPLAIN_RE = re.compile(
    r"\b(explain|what does|what is|how does|why (is|does|do)|walk me through|"
    r"teach me|meaning of|document)\b",
    re.I,
)
_REFACTOR_RE = re.compile(
    r"\b(refactor|clean ?up|simplify|extract|rename|split|deduplicate|"
    r"restructure|optimise|optimize)\b",
    re.I,
)
_TEST_RE = re.compile(r"\b(tests?|pytest|unittest|jest|vitest|mocha|rspec|coverage|tdd)\b", re.I)
_ARCH_RE = re.compile(
    r"\b(architecture|design|trade-?offs?|should i use|which (is|one)|"
    r"scalab\w*|microservice|monolith|system design|pattern)\b",
    re.I,
)
_WRITE_RE = re.compile(
    r"\b(write|create|implement|add|build|generate|make|code|script|function|"
    r"class|method|program|endpoint|component|migration)\b",
    re.I,
)
_DOCS_RE = re.compile(r"\b(docstring|readme|documentation|comments?|jsdoc|api docs)\b", re.I)
_SHELL_RE = re.compile(
    r"\b(bash|shell|zsh|docker|dockerfile|kubernetes|kubectl|systemd|"
    r"makefile|ci|github actions|devops|terminal|command line)\b",
    re.I,
)
_SQL_RE = re.compile(
    r"\b(sql|query|select .+ from|join|postgres|mysql|sqlite|index|schema)\b", re.I | re.S
)
_COMPLETE_RE = re.compile(r"\b(complete|autocomplete|finish this|continue this|fill in)\b", re.I)


def detect_language(text: str) -> str:
    """Cheap language guess from fences, file paths and keywords."""
    for fence in _FENCE_RE.findall(text):
        fence = fence.lower().strip()
        if fence in {"py", "python"}:
            return "python"
        if fence in {"js", "javascript"}:
            return "javascript"
        if fence in {"ts", "typescript", "tsx"}:
            return "typescript"
        if fence in {"c++", "cpp", "cc"}:
            return "cpp"
        if fence in {"c#", "cs", "csharp"}:
            return "csharp"
        if fence in {"sh", "bash", "shell", "zsh"}:
            return "bash"
        if fence in {"rs", "rust"}:
            return "rust"
        if fence in {"go", "golang"}:
            return "go"
        if fence in {"kt", "kotlin"}:
            return "kotlin"
        if fence in {"sql"}:
            return "sql"
        if fence:
            return fence
    for path in _PATH_RE.findall(text):
        lang = _EXT_TO_LANG.get("." + path.rsplit(".", 1)[-1].lower())
        if lang:
            return lang
    lowered = f" {text.lower()} "
    # Weight by hint length: ".cs" must beat ".c", "typescript" must beat ".ts".
    scores: dict[str, int] = {}
    for lang, hints in _LANG_HINTS.items():
        score = sum(len(hint) for hint in hints if hint in lowered)
        if score:
            scores[lang] = score
    if scores:
        return max(scores, key=lambda lang: scores[lang])
    return "unknown"


def classify(prompt: str) -> TaskProfile:
    """Rules-first task classification with an auditable signal list."""
    text = prompt.strip()
    lowered = text.lower()
    signals: list[str] = []
    language = detect_language(text)
    if language != "unknown":
        signals.append(f"language:{language}")

    task = Task.GENERAL
    if _COMPLETE_RE.search(text) or (
        text.startswith(("```", "def ", "func ", "class ", "function ")) and len(text) < 400
    ):
        task = Task.AUTOCOMPLETE
        signals.append("completion phrasing")
    elif _TRACEBACK_RE.search(text) or _ERROR_RE.search(text):
        task = Task.BUG_FIX
        signals.append("error/traceback syntax")
    elif _ARCH_RE.search(text) and not _WRITE_RE.search(text):
        task = Task.ARCHITECTURE
        signals.append("design vocabulary")
    elif _REFACTOR_RE.search(text):
        task = Task.REFACTOR
        signals.append("refactor vocabulary")
    elif _TEST_RE.search(text) and re.search(r"\b(write|add|generate|create|cover)\b", lowered):
        task = Task.TESTS
        signals.append("test request")
    elif _DOCS_RE.search(text) and re.search(r"\b(write|add|generate|create|improve)\b", lowered):
        task = Task.DOCS
        signals.append("docs request")
    elif _SHELL_RE.search(text):
        task = Task.SHELL
        signals.append("shell/devops vocabulary")
    elif _SQL_RE.search(text) and ("select" in lowered or "query" in lowered or "join" in lowered):
        task = Task.SQL
        signals.append("SQL vocabulary")
    elif _EXPLAIN_RE.search(text):
        task = Task.EXPLAIN
        signals.append("explanation phrasing")
    elif _WRITE_RE.search(text):
        task = Task.WRITE_NEW
        signals.append("creation phrasing")

    difficulty = TASK_BASE_DIFFICULTY[task]
    if len(text) > 1200:
        difficulty += 0.10
        signals.append("long prompt")
    if len(_PATH_RE.findall(text)) >= 3:
        difficulty += 0.10
        signals.append("multi-file")
    if re.search(
        r"\b(concurren\w*|race condition|deadlock|distributed|migration|"
        r"performance|optimi[sz]e|security|cryptograph\w*)\b",
        lowered,
    ):
        difficulty += 0.10
        signals.append("hard topic")
    if task in {Task.AUTOCOMPLETE, Task.DOCS}:
        difficulty -= 0.05

    return TaskProfile(
        task=task,
        language=language,
        difficulty=max(0.0, min(1.0, round(difficulty, 2))),
        signals=signals,
    )


# -------------------------------------------------------------------- router

#: stats(task) -> {model_id: {"runs": int, "wins": int, ...}}
StatsProvider = Callable[[str], dict[str, dict[str, float]]]


class Router:
    def __init__(
        self,
        registry: Registry,
        hardware: Hardware,
        config: Config,
        installed: Iterable[str] | None = None,
        stats: StatsProvider | None = None,
    ):
        self.registry = registry
        self.hardware = hardware
        self.config = config
        self.installed = set(installed) if installed is not None else None
        self.stats = stats

    # ------------------------------------------------------------------ public
    def route(self, prompt: str, *, model: str | None = None) -> RouteDecision:
        profile = classify(prompt)
        forced = model or self.config.models.default
        if forced:
            return self._explicit(profile, forced)
        return self._choose(profile)

    # ----------------------------------------------------------------- helpers
    def _explicit(self, profile: TaskProfile, name: str) -> RouteDecision:
        spec = self.registry.resolve(name)
        if spec is None:
            raise SystemExit(
                f"Unknown model '{name}'. Run `devmind models` to see registered models, "
                f"or add it to {self.config.registry_override_path}."
            )
        tok_s = spec.estimated_tok_s(self.hardware)
        reason = f"explicit choice ({name})"
        if not spec.fits(self.hardware):
            reason += (
                f" — WARNING: needs {spec.size_gb:.1f} GB, budget is "
                f"{self.hardware.usable_memory_gb:.1f} GB; expect swapping"
            )
        return RouteDecision(
            profile=profile,
            model=spec,
            reason=reason,
            needs_download=self._missing(spec),
            estimated_tok_s=tok_s,
        )

    def _missing(self, spec: Model) -> bool:
        if spec.cloud:
            return False  # nothing to download: it runs at the provider
        return self.installed is not None and spec.id not in self.installed

    def _choose(self, profile: TaskProfile) -> RouteDecision:
        min_quality = self.config.router.min_quality * 100
        required = max(min_quality, 45 + 40 * profile.difficulty)
        min_tok_s = self.config.hardware.min_tokens_per_s
        roles = TASK_ROLES[profile.task]

        pool: list[Model] = []
        for role in roles:
            pool.extend(m for m in self.registry.by_role(role) if m not in pool)
        if not pool:
            return RouteDecision(profile, None, "registry is empty")

        def tok_s(model: Model) -> float:
            return model.estimated_tok_s(self.hardware)

        def measured(model: Model) -> float:
            if not self.stats:
                return 0.0
            row = self.stats(profile.task.value).get(model.id)
            if not row or not row.get("runs"):
                return 0.0
            return float(row.get("wins", 0)) / max(1.0, float(row["runs"]))

        def score(model: Model) -> float:
            # 60 tok/s is "instant" for a human; above that extra speed is invisible.
            speed = min(1.0, tok_s(model) / 60.0)
            base = model.quality / 100.0
            if profile.task is Task.AUTOCOMPLETE or self.config.router.prefer_speed:
                value = 0.70 * speed + 0.30 * base
            else:
                # Harder work buys more quality with the extra seconds it costs.
                speed_weight = max(0.05, 0.22 - 0.17 * profile.difficulty)
                value = (1.0 - speed_weight) * base + speed_weight * speed
            value += 0.10 * measured(model)
            if model.size_gb > self.hardware.usable_memory_gb * 0.85:
                value -= 0.05
            return value

        def permissible(model: Model) -> bool:
            if model.cloud:
                return True  # runs on the provider, so local memory/speed do not apply
            cap = self.config.hardware.max_model_gb
            if cap and model.size_gb > cap:
                return False
            if self.hardware.backend == "cpu" and not self.config.hardware.allow_cpu_only:
                return False
            return model.usable(self.hardware, min_tok_s)

        installed = self.installed
        local_pool = [m for m in pool if m.cloud or installed is None or m.id in installed]
        fit_pool = [m for m in local_pool if permissible(m) and m.quality >= min(required, 60)]
        if not fit_pool:
            fit_pool = [m for m in local_pool if permissible(m)]
        if not fit_pool and installed:
            # Preferred roles are not installed, but *something* is.  Using the
            # best installed model beats refusing to answer.
            fallback = [
                m
                for m in self.registry.all()
                if (m.cloud or m.id in installed) and permissible(m) and "embed" not in m.roles
            ]
            if fallback:
                fit_pool = fallback
        if not fit_pool:
            # Nothing runnable: degrade gracefully but say exactly why.
            if installed is not None and not local_pool:
                smallest = self.registry.smallest_for_role(roles[0]) or pool[0]
                return RouteDecision(
                    profile=profile,
                    model=smallest,
                    reason=(
                        f"no model installed for '{profile.task.value}'; "
                        f"suggested pull: {smallest.id} ({smallest.size_gb:.1f} GB)"
                    ),
                    needs_download=True,
                    estimated_tok_s=smallest.estimated_tok_s(self.hardware),
                )
            smallest = min(pool, key=lambda m: m.size_gb)
            return RouteDecision(
                profile=profile,
                model=smallest,
                reason=(
                    f"nothing fits the {self.hardware.usable_memory_gb:.1f} GB budget at "
                    f">={min_tok_s:.0f} tok/s; {smallest.id} is the smallest option and may be slow"
                ),
                needs_download=self._missing(smallest),
                estimated_tok_s=smallest.estimated_tok_s(self.hardware),
            )

        ranked = sorted(fit_pool, key=score, reverse=True)
        best = ranked[0]
        alternatives = ranked[1:4]
        quality_note = (
            "below difficulty floor; answer will be verified and hedged"
            if best.quality < required
            else "meets quality floor"
        )
        reason = (
            f"{profile.task.value}/{profile.language} difficulty {profile.difficulty:.2f} "
            f"→ {best.family} {best.params_b:g}B (Q: {best.quality:.0f}/100, "
            f"~{tok_s(best):.0f} tok/s, {best.size_gb:.1f} GB; {quality_note})"
        )
        if measured(best) > 0:
            reason += f", learned win-rate {measured(best):.0%}"
        if self._missing(best):
            reason += f"; not downloaded yet — run `devmind pull {best.id}`"
        return RouteDecision(
            profile=profile,
            model=best,
            reason=reason,
            alternatives=alternatives,
            needs_download=self._missing(best),
            estimated_tok_s=tok_s(best),
        )
