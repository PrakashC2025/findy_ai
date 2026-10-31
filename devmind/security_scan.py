"""Static security checks for generated or changed code.

Note for scanners: this module contains the rule patterns and human messages
(including the words they look for), so it opts itself out below.
# devmind: allow-file code.eval, code.exec

Deliberately small and explainable: a fixed set of rules that catch the
mistakes coding assistants actually make - fake keys pasted into a file,
``eval`` on model output, string-built SQL, ``shell=True``, unsafe
deserialisation, path traversal.  It is not a replacement for a real SAST
product and does not pretend to have no false positives; every finding points
at a line so the user can judge it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}
_ALLOW = re.compile(r"devmind:\s*allow-file\s+([\w.,\s-]+)|devmind:\s*allow\s+([\w.,\s-]+)")
_ALLOW_FILE = re.compile(r"devmind:\s*allow-file\b")

_SKIP_DIRS = {
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    "dist",
    "build",
    "target",
    ".mypy_cache",
    ".ruff_cache",
    ".pytest_cache",
}
_MAX_BYTES = 300_000


@dataclass(frozen=True)
class Rule:
    id: str
    severity: str
    message: str
    pattern: re.Pattern[str]
    languages: tuple[str, ...] = ()  # empty = every language
    hint: str = ""


def _rule(
    rule_id: str,
    severity: str,
    message: str,
    pattern: str,
    *,
    languages: tuple[str, ...] = (),
    hint: str = "",
    ignore_case: bool = False,
) -> Rule:
    flags = re.MULTILINE | (re.IGNORECASE if ignore_case else 0)
    return Rule(rule_id, severity, message, re.compile(pattern, flags), languages, hint)


RULES: tuple[Rule, ...] = (
    _rule(
        "secret.aws_key",
        "high",
        "AWS access key id committed in source",
        r"\bAKIA[0-9A-Z]{16}\b",
        hint="revoke it in IAM and load it from the environment instead",
    ),
    _rule(
        "secret.openai_key",
        "high",
        "OpenAI-style API key committed in source",
        r"\bsk-[A-Za-z0-9_-]{20,}",
        hint="revoke the key and read it from the environment at runtime",
    ),
    _rule(
        "secret.github_token",
        "high",
        "GitHub token committed in source",
        r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})",
        hint="revoke the token in GitHub settings",
    ),
    _rule(
        "secret.private_key",
        "high",
        "Private key material in source",
        r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----",
        hint="move the key to a secret store; never commit it",
    ),
    _rule(
        "secret.generic",
        "medium",
        "Hard-coded credential-looking value",
        r"""(?i)\b(?:api[_-]?key|secret|token|passwd|password)\b\s*[:=]\s*["'][^"'\s]{12,}["']""",
        hint="use an environment variable or the OS keychain",
    ),
    _rule(
        "code.eval",
        "high",
        "eval() on data that may come from outside",
        r"(?<![\w.])eval\s*\(",
        languages=("python",),
        hint="parse the data (ast.literal_eval, json.loads) instead of executing it",
    ),
    _rule(
        "code.exec",
        "high",
        "exec() runs arbitrary code",
        r"(?<![\w.])exec\s*\(",
        languages=("python",),
        hint="call a function explicitly; do not build code from strings",
    ),
    _rule(
        "code.shell_true",
        "high",
        "subprocess with shell=True enables command injection",
        r"subprocess\.(?:run|call|Popen|check_output)\s*\([^)]*shell\s*=\s*True",
        languages=("python",),
        hint="pass an argument list and keep shell=False",
    ),
    _rule(
        "code.os_system",
        "medium",
        "os.system interpolates into a shell",
        r"\bos\.system\s*\(",
        languages=("python",),
        hint="use subprocess.run([...]) with an argument list",
    ),
    _rule(
        "code.pickle",
        "high",
        "Unpickling untrusted data can execute code",
        r"\b(?:pickle|dill|marshal)\.loads?\s*\(",
        languages=("python",),
        hint="prefer json or a schema-validated format",
    ),
    _rule(
        "code.yaml_unsafe",
        "high",
        "yaml.load without SafeLoader executes arbitrary constructors",
        r"yaml\.load\s*\((?![^)]*SafeLoader)",
        languages=("python",),
        hint="use yaml.safe_load",
    ),
    _rule(
        "sql.interpolation",
        "high",
        "SQL built by interpolating a value",
        r"""\.execute\s*\(\s*f["'][^"']*\{[^}]*"""
        r"""(?:user|request|input|args|params|id|name|key|value|query|email|payload|data|q)\w*\}""",
        languages=("python",),
        hint="use bound parameters: execute(sql, (value,))",
    ),
    _rule(
        "sql.dynamic",
        "medium",
        "Dynamic SQL: interpolated text is treated as SQL structure",
        r"""\.execute\s*\(\s*f["']|\.execute\s*\(\s*[^)]*\+""",
        languages=("python",),
        hint="interpolate only trusted identifiers (table names, WHERE fragments), never values",
    ),
    _rule(
        "sql.interpolation_js",
        "high",
        "SQL built with template interpolation",
        r"""\.query\s*\(\s*`[^`]*\$\{""",
        languages=("javascript", "typescript"),
        hint="use parameterised queries ($1, ?) instead",
    ),
    _rule(
        "code.inner_html",
        "medium",
        "innerHTML with interpolated values is an XSS risk",
        r"\.innerHTML\s*=\s*(?:`[^`]*\$\{|\w+\s*\+)",
        languages=("javascript", "typescript", "html"),
        hint="use textContent or a sanitizer",
    ),
    _rule(
        "code.reflect",
        "medium",
        "Reflection / dynamic invocation with user input",
        r"\bClass\.forName\s*\(|\bgetDeclaredMethod\s*\(",
        languages=("java", "kotlin"),
    ),
    _rule(
        "path.traversal",
        "medium",
        "Path built from a variable without normalisation",
        r"""open\s*\(\s*(?:f?["'][^"']*["']\s*\+|os\.path\.join\([^)]*request)""",
        languages=("python",),
        hint="resolve the path and verify it stays inside the allowed root",
    ),
)


@dataclass(frozen=True)
class Finding:
    rule: str
    severity: str
    path: str
    line: int
    message: str
    hint: str = ""
    excerpt: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule": self.rule,
            "severity": self.severity,
            "path": self.path,
            "line": self.line,
            "message": self.message,
            "hint": self.hint,
            "excerpt": self.excerpt,
        }


def _line_of(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _allow_ids(raw: str) -> set[str]:
    return {part for part in re.split(r"[,\s]+", raw) if part}


def _file_allows(lines: list[str]) -> set[str]:
    """Rules suppressed for the whole file by a directive in its first lines."""
    allowed: set[str] = set()
    for line in lines[:40]:
        if _ALLOW_FILE.search(line):
            match = _ALLOW.search(line)
            ids = _allow_ids(match.group(1)) if match and match.group(1) else set()
            allowed |= ids or {"*"}
    return allowed


def _is_allowed(lines: list[str], line_no: int, rule_id: str, file_allows: set[str]) -> bool:
    if "*" in file_allows or rule_id in file_allows:
        return True
    for probe in (line_no - 1, line_no - 2):  # same line, or the line above
        if 0 <= probe < len(lines):
            match = _ALLOW.search(lines[probe])
            if match is None or _ALLOW_FILE.search(lines[probe]):
                continue
            ids = _allow_ids(match.group(2) or "")
            if not ids or rule_id in ids:
                return True
    return False


def scan_text(path: str, text: str, *, language: str = "") -> list[Finding]:
    """Scan one file's contents (or a diff) and return findings sorted by severity."""
    findings: list[Finding] = []
    seen: set[tuple[str, int]] = set()
    lines = text.splitlines()
    file_allows = _file_allows(lines)
    for rule in RULES:
        if rule.languages and language and language not in rule.languages:
            continue
        if rule.languages and not language:
            continue
        for match in rule.pattern.finditer(text):
            line = _line_of(text, match.start())
            if (rule.id, line) in seen:
                continue
            seen.add((rule.id, line))
            if _is_allowed(lines, line, rule.id, file_allows):
                continue
            excerpt = lines[line - 1][:160] if 0 < line <= len(lines) else ""
            findings.append(
                Finding(
                    rule=rule.id,
                    severity=rule.severity,
                    path=path,
                    line=line,
                    message=rule.message,
                    hint=rule.hint,
                    excerpt=excerpt.strip(),
                )
            )
    findings.sort(key=lambda item: (_SEVERITY_ORDER.get(item.severity, 9), item.path, item.line))
    return findings


_LANGUAGE_BY_SUFFIX = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".java": "java",
    ".kt": "kotlin",
    ".go": "go",
    ".rs": "rust",
    ".php": "php",
    ".rb": "ruby",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".hpp": "cpp",
    ".cs": "csharp",
    ".swift": "swift",
    ".dart": "dart",
    ".sql": "sql",
    ".sh": "bash",
    ".html": "html",
    ".css": "css",
}


def language_for(path: str | Path) -> str:
    return _LANGUAGE_BY_SUFFIX.get(Path(path).suffix.lower(), "")


def scan_paths(root: str | Path, *, max_files: int = 400) -> list[Finding]:
    """Walk a project, skipping vendored/build directories and binary files."""
    base = Path(root).resolve()
    findings: list[Finding] = []
    checked = 0
    for path in sorted(base.rglob("*")):
        if checked >= max_files:
            break
        if not path.is_file() or any(part in _SKIP_DIRS for part in path.parts):
            continue
        try:
            if path.stat().st_size > _MAX_BYTES:
                continue
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        checked += 1
        findings.extend(scan_text(str(path.relative_to(base)), text, language=language_for(path)))
    return findings


def _diff_sections(diff: str) -> list[tuple[str, str]]:
    """Split a unified diff into (path, added-lines) sections.

    Blank lines keep the diff's own line numbering so a finding still points
    at roughly the right place in the patch.
    """
    sections: list[tuple[str, list[str]]] = []
    path = "<diff>"
    lines: list[str] = []

    def flush() -> None:
        nonlocal lines
        if lines:
            sections.append((path, lines))
            lines = []

    for line in diff.splitlines():
        if line.startswith("--- "):
            flush()
            continue
        if line.startswith("+++ "):
            target = line[4:].strip()
            path = target[2:] if target.startswith(("b/", "a/")) else target
            continue
        lines.append(line[1:] if line.startswith("+") else "")
    flush()
    return [(name, "\n".join(body)) for name, body in sections]


def scan_diff(diff: str, *, language: str = "") -> list[Finding]:
    """Scan only added lines, judging each file in its own language."""
    findings: list[Finding] = []
    for path, text in _diff_sections(diff):
        detected = language or language_for(path)
        findings.extend(scan_text(path, text, language=detected))
    findings.sort(key=lambda item: (_SEVERITY_ORDER.get(item.severity, 9), item.path, item.line))
    return findings


def summarise(findings: list[Finding]) -> str:
    if not findings:
        return "security scan: clean"
    counts: dict[str, int] = {}
    for finding in findings:
        counts[finding.severity] = counts.get(finding.severity, 0) + 1
    parts = [f"{count} {severity}" for severity, count in sorted(counts.items())]
    return "security scan: " + ", ".join(parts)


def blocking(findings: list[Finding]) -> list[Finding]:
    """Findings that must stop an automatic patch from being applied."""
    return [finding for finding in findings if finding.severity == "high"]
