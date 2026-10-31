"""Language packs: what "correct" looks like for each ecosystem.

A pack is data, not code - one folder per language would be nicer for plugins
but a frozen dataclass registry keeps the package installable and testable
today.  Each pack lists the commands a project of that language normally uses
and the pitfalls assistants get wrong most often.  Nothing here is executed
automatically; :func:`devmind.verify.detect_checks` decides what to run from
the project's own config files.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class LanguagePack:
    id: str
    name: str
    extensions: tuple[str, ...]
    config_files: tuple[str, ...]
    test: str = ""
    lint: str = ""
    format: str = ""
    typecheck: str = ""
    docs: str = ""
    notes: tuple[str, ...] = ()
    pitfalls: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "name": self.name,
            "extensions": list(self.extensions),
            "config_files": list(self.config_files),
            "test": self.test,
            "lint": self.lint,
            "format": self.format,
            "typecheck": self.typecheck,
            "docs": self.docs,
            "notes": list(self.notes),
            "pitfalls": list(self.pitfalls),
        }


def _pack(**kwargs: object) -> LanguagePack:
    return LanguagePack(**kwargs)  # type: ignore[arg-type]


LANGUAGE_PACKS: dict[str, LanguagePack] = {
    pack.id: pack
    for pack in (
        _pack(
            id="python",
            name="Python",
            extensions=(".py",),
            config_files=("pyproject.toml", "setup.cfg", "requirements.txt", "tox.ini"),
            test="pytest -q",
            lint="ruff check .",
            format="ruff format .",
            typecheck="mypy .",
            docs="https://docs.python.org/3/",
            notes=(
                "Use the project's existing style; type hints on public functions.",
                "Prefer pathlib, dataclasses and context managers over manual bookkeeping.",
                "Never silently swallow exceptions.",
            ),
            pitfalls=(
                "Mutable default arguments.",
                "Catching broad Exception and continuing.",
                "Assuming dict ordering matters across serialisation.",
            ),
        ),
        _pack(
            id="javascript",
            name="JavaScript",
            extensions=(".js", ".jsx", ".mjs", ".cjs"),
            config_files=("package.json", ".eslintrc", ".eslintrc.json", ".eslintrc.cjs"),
            test="npm test",
            lint="npx eslint .",
            format="npx prettier --write .",
            docs="https://developer.mozilla.org/en-US/docs/Web/JavaScript",
            notes=(
                "Match the existing module system (ESM vs CommonJS).",
                "Use the installed framework version, never the newest API by memory.",
            ),
            pitfalls=(
                "Losing `this` when passing methods as callbacks.",
                "Floating promises (missing await / unhandled rejection).",
                "Mutating props or shared objects in React.",
            ),
        ),
        _pack(
            id="typescript",
            name="TypeScript",
            extensions=(".ts", ".tsx", ".mts", ".cts"),
            config_files=("tsconfig.json", "package.json"),
            test="npm test",
            lint="npx eslint .",
            format="npx prettier --write .",
            typecheck="npx tsc --noEmit",
            docs="https://www.typescriptlang.org/docs/",
            notes=(
                "Check tsconfig strictness before suggesting types.",
                "Never cast to `any` to silence an error - narrow or validate instead.",
            ),
            pitfalls=(
                "`as` casts hiding runtime bugs.",
                "Structural typing surprises with optional fields.",
                "Decorators/metadata need the right compiler options.",
            ),
        ),
        _pack(
            id="java",
            name="Java",
            extensions=(".java",),
            config_files=("pom.xml", "build.gradle", "build.gradle.kts"),
            test="mvn test",
            lint="mvn -q checkstyle:check",
            format="mvn -q spotless:apply",
            docs="https://devdocs.io/openjdk/",
            notes=("Use the project's Java version (pom/gradle toolchain).",),
            pitfalls=(
                "Ignoring checked exceptions.",
                "Shared mutable statics in servlets.",
                "Assuming a library version's API exists in the project's version.",
            ),
        ),
        _pack(
            id="kotlin",
            name="Kotlin",
            extensions=(".kt", ".kts"),
            config_files=("build.gradle.kts", "build.gradle"),
            test="./gradlew test",
            lint="./gradlew detekt",
            format="./gradlew ktlintFormat",
            docs="https://kotlinlang.org/docs/home.html",
            notes=("Prefer immutable val and null-safe types.",),
            pitfalls=("Platform types from Java are nullable at runtime.",),
        ),
        _pack(
            id="c",
            name="C",
            extensions=(".c", ".h"),
            config_files=("Makefile", "CMakeLists.txt", "meson.build"),
            test="make test",
            lint="cppcheck .",
            format="clang-format -i <files>",
            docs="https://devdocs.io/c/",
            notes=("Check ownership and error paths for every allocation.",),
            pitfalls=(
                "Buffer sizes and off-by-one bounds.",
                "Ignoring return values from malloc/read.",
                "Using a pointer after free.",
            ),
        ),
        _pack(
            id="cpp",
            name="C++",
            extensions=(".cpp", ".cc", ".cxx", ".hpp", ".hh"),
            config_files=("CMakeLists.txt", "Makefile"),
            test="ctest",
            lint="clang-tidy <files>",
            format="clang-format -i <files>",
            docs="https://en.cppreference.com/w/",
            notes=("Prefer RAII and standard containers over raw new/delete.",),
            pitfalls=("Dangling references from temporaries.", "Unsynchronised shared state."),
        ),
        _pack(
            id="csharp",
            name="C#",
            extensions=(".cs", ".csx"),
            config_files=("*.csproj", "*.sln", "global.json"),
            test="dotnet test",
            lint="dotnet format --verify-no-changes",
            format="dotnet format",
            docs="https://learn.microsoft.com/dotnet/csharp/",
            notes=("Match the target framework in the csproj.",),
            pitfalls=("async void", "Disposing IDisposable only on the happy path."),
        ),
        _pack(
            id="go",
            name="Go",
            extensions=(".go",),
            config_files=("go.mod", "go.sum"),
            test="go test ./...",
            lint="go vet ./...",
            format="gofmt -w .",
            typecheck="go build ./...",
            docs="https://pkg.go.dev/",
            notes=("Return errors, never panic in library code.",),
            pitfalls=(
                "Ignoring the error from a call.",
                "Loop variable capture before Go 1.22.",
                "Goroutine leaks without context cancellation.",
            ),
        ),
        _pack(
            id="rust",
            name="Rust",
            extensions=(".rs",),
            config_files=("Cargo.toml", "Cargo.lock"),
            test="cargo test",
            lint="cargo clippy -- -D warnings",
            format="cargo fmt",
            typecheck="cargo check",
            docs="https://doc.rust-lang.org/std/",
            notes=("Do not unwrap in library paths; return Result.",),
            pitfalls=("Fighting the borrow checker with clones.", "Blocking inside async."),
        ),
        _pack(
            id="php",
            name="PHP",
            extensions=(".php",),
            config_files=("composer.json", "phpunit.xml", "phpunit.xml.dist"),
            test="vendor/bin/phpunit",
            lint="vendor/bin/phpstan analyse",
            format="vendor/bin/php-cs-fixer fix",
            docs="https://www.php.net/manual/en/",
            notes=("Use prepared statements for every query.",),
            pitfalls=("Loose comparison (==) surprises.", "Unvalidated includes."),
        ),
        _pack(
            id="ruby",
            name="Ruby",
            extensions=(".rb", ".erb"),
            config_files=("Gemfile", "Rakefile", ".rubocop.yml"),
            test="bundle exec rspec",
            lint="bundle exec rubocop",
            format="bundle exec rubocop -a",
            docs="https://ruby-doc.org/",
            notes=("Follow the project's Rails version and conventions.",),
            pitfalls=("N+1 queries.", "Mass assignment without strong parameters."),
        ),
        _pack(
            id="swift",
            name="Swift",
            extensions=(".swift",),
            config_files=("Package.swift", "*.xcodeproj"),
            test="swift test",
            lint="swiftlint",
            format="swiftformat .",
            docs="https://developer.apple.com/documentation/swift",
            notes=("Match the deployment target before using new APIs.",),
            pitfalls=("Force unwraps.", "Retain cycles in closures."),
        ),
        _pack(
            id="dart",
            name="Dart / Flutter",
            extensions=(".dart",),
            config_files=("pubspec.yaml", "analysis_options.yaml"),
            test="flutter test",
            lint="dart analyze",
            format="dart format .",
            docs="https://api.flutter.dev/",
            notes=("Use the installed Flutter/Dart SDK version.",),
            pitfalls=("setState after dispose.", "Rebuilding large subtrees."),
        ),
        _pack(
            id="sql",
            name="SQL",
            extensions=(".sql",),
            config_files=("*.sql",),
            docs="https://www.postgresql.org/docs/",
            notes=("State the dialect (Postgres/MySQL/SQLite) before writing SQL.",),
            pitfalls=(
                "Dialect-specific syntax presented as universal.",
                "Implicit casts defeating indexes.",
                "Migrations without a rollback path.",
            ),
        ),
        _pack(
            id="bash",
            name="Bash",
            extensions=(".sh", ".bash"),
            config_files=("*.sh",),
            test="bats tests/",
            lint="shellcheck <files>",
            format="shfmt -w .",
            docs="https://www.gnu.org/software/bash/manual/",
            notes=("Use `set -euo pipefail` and quote expansions.",),
            pitfalls=("Unquoted variables.", "Parsing ls output.", "rm with an empty variable."),
        ),
        _pack(
            id="powershell",
            name="PowerShell",
            extensions=(".ps1", ".psm1"),
            config_files=("*.ps1",),
            test="Invoke-Pester",
            lint="Invoke-ScriptAnalyzer -Path .",
            docs="https://learn.microsoft.com/powershell/",
            notes=("Use approved verbs and -ErrorAction explicitly.",),
        ),
        _pack(
            id="html",
            name="HTML / CSS",
            extensions=(".html", ".htm", ".css", ".scss"),
            config_files=("index.html", "package.json"),
            lint="npx stylelint '**/*.css'",
            format="npx prettier --write .",
            docs="https://developer.mozilla.org/en-US/docs/Web",
            notes=("Semantic elements, labelled inputs, keyboard reachable controls.",),
            pitfalls=("Click handlers on divs.", "Colour-only state indicators."),
        ),
    )
}

_BY_SUFFIX = {extension: pack for pack in LANGUAGE_PACKS.values() for extension in pack.extensions}

#: Config files that strongly identify an ecosystem.
_CONFIG_HINTS = {
    "pyproject.toml": "python",
    "setup.py": "python",
    "requirements.txt": "python",
    "package.json": "javascript",
    "tsconfig.json": "typescript",
    "go.mod": "go",
    "Cargo.toml": "rust",
    "pom.xml": "java",
    "build.gradle": "java",
    "build.gradle.kts": "kotlin",
    "composer.json": "php",
    "Gemfile": "ruby",
    "Package.swift": "swift",
    "pubspec.yaml": "dart",
    "CMakeLists.txt": "cpp",
}

_DOMAIN_HINTS = {
    "react": ("jsx", "tsx", "useState", "useEffect", "next.js", "nextjs"),
    "vue": ("vue", "definecomponent"),
    "django": ("django", "models.py", "manage.py"),
    "fastapi": ("fastapi",),
    "flask": ("flask",),
    "docker": ("dockerfile", "docker-compose", "container"),
    "github-actions": ("github actions", ".github/workflows"),
    "postgres": ("postgres", "postgresql", "psql"),
    "mysql": ("mysql", "mariadb"),
    "sqlite": ("sqlite",),
    "pytorch": ("pytorch", "torch", "nn.module"),
    "pandas": ("pandas", "dataframe"),
    "android": ("android", "jetpack compose", "gradle"),
    "ios": ("ios", "swiftui", "uikit"),
    "flutter": ("flutter", "dart"),
    "security": ("owasp", "xss", "csrf", "injection", "secrets"),
}


def language_for_path(path: str | Path) -> LanguagePack | None:
    """Pack for a single file, by extension."""
    return _BY_SUFFIX.get(Path(path).suffix.lower())


def detect_languages(root: str | Path, *, max_files: int = 2000) -> list[dict[str, object]]:
    """Rank languages in a project by file count, with the evidence used."""
    base = Path(root).resolve()
    counts: dict[str, int] = {}
    for path in base.rglob("*"):
        if len(counts) > max_files:
            break
        if not path.is_file() or any(part.startswith(".") for part in path.parts):
            continue
        pack = _BY_SUFFIX.get(path.suffix.lower())
        if pack is not None:
            counts[pack.id] = counts.get(pack.id, 0) + 1
    detected: list[dict[str, object]] = []
    for pack_id, count in sorted(counts.items(), key=lambda item: (-item[1], item[0])):
        pack = LANGUAGE_PACKS[pack_id]
        detected.append({"id": pack_id, "name": pack.name, "files": count, "docs": pack.docs})
    return detected


def project_packs(root: str | Path) -> list[LanguagePack]:
    """Packs worth loading for a project: file types plus config evidence."""
    base = Path(root).resolve()
    ids: list[str] = [str(item["id"]) for item in detect_languages(base)]
    for name, pack_id in _CONFIG_HINTS.items():
        if (base / name).exists() and pack_id not in ids:
            ids.append(pack_id)
    # A React/Next project is TypeScript first if it has any .tsx/.ts files.
    if "typescript" in ids and "javascript" in ids:
        ids.remove("javascript")
    return [LANGUAGE_PACKS[pack_id] for pack_id in ids if pack_id in LANGUAGE_PACKS]


def domains_in(text: str, root: str | Path | None = None) -> list[str]:
    """Guess the skill areas a request or repository touches (for checklists)."""
    lowered = text.lower()
    found = [name for name, words in _DOMAIN_HINTS.items() if any(w in lowered for w in words)]
    if root:
        base = Path(root).resolve()
        try:
            manifest = json.loads((base / "package.json").read_text())
        except (OSError, json.JSONDecodeError):
            manifest = {}
        deps = {**manifest.get("dependencies", {}), **manifest.get("devDependencies", {})}
        if any(dep in deps for dep in ("react", "next", "vue")):
            found.append("react" if "react" in deps or "next" in deps else "vue")
    return sorted(set(found))


def style_block(root: str | Path) -> str:
    """Compact guidance injected into prompts for the project's languages."""
    packs = project_packs(root)
    if not packs:
        return ""
    lines = [f"PROJECT LANGUAGE PACKS ({', '.join(pack.name for pack in packs)})"]
    for pack in packs[:3]:
        lines.append(f"- {pack.name}: " + " ".join(pack.notes))
        if pack.pitfalls:
            lines.append(f"  Pitfalls: {'; '.join(pack.pitfalls[:3])}")
    return "\n".join(lines)
