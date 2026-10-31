"""Secret redaction.

Anything that reaches long-term memory is redacted first: API keys, tokens,
private keys, credentials in URLs and obvious `password=`/`api_key=` pairs.
The point is not to catch every possible secret (impossible) but to make the
common mistake — pasting a `.env` line into a chat — harmless.

Redaction happens locally before storage and before prompts are built, so it
also protects the future Google Drive backend (step 7).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "private_key",
        re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S),
    ),
    ("aws_access_key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("aws_secret", re.compile(r"(?i)\baws_secret_access_key\s*[=:]\s*['\"]?([A-Za-z0-9/+=]{40})")),
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b")),
    ("openai_key", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b")),
    ("groq_key", re.compile(r"\bgsk_[A-Za-z0-9]{40,}\b")),
    ("openrouter_key", re.compile(r"\bsk-or-v1-[A-Za-z0-9]{32,}\b")),
    ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("slack_token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b")),
    ("url_credentials", re.compile(r"(?P<scheme>[a-z][a-z0-9+.-]*://)[^/\s:@]+:[^/\s@]+@")),
    (
        "assignment",
        re.compile(
            r"(?i)\b(?P<name>[A-Za-z0-9_.-]*(?:api[_-]?key|secret|token|password|passwd|"
            r"access[_-]?key|private[_-]?key)[A-Za-z0-9_.-]*)\s*[=:]\s*"
            r"(?P<quote>['\"]?)(?P<value>[^\s'\"]{6,})(?P=quote)"
        ),
    ),
)

_SAFE_VALUES = {
    "changeme",
    "password",
    "example",
    "your_key_here",
    "your-api-key",
    "xxx",
    "***",
    "redacted",
    "none",
    "null",
    "true",
    "false",
    "string",
}


@dataclass
class Redaction:
    text: str
    found: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.found)


def redact(text: str) -> Redaction:
    """Return the text with secrets replaced by ``[REDACTED:<kind>]``."""
    if not text:
        return Redaction("", [])
    found: list[str] = []
    result = text

    for kind, pattern in _PATTERNS:

        def _replace(match: re.Match[str], kind: str = kind) -> str:
            if kind == "assignment":
                value = match.group("value")
                if value.lower() in _SAFE_VALUES or value.startswith("$"):
                    return match.group(0)
                name = match.group("name")
                found.append(kind)
                quote = match.group("quote") or ""
                return f"{name}=[REDACTED:credential]{quote}"
            if kind == "url_credentials":
                found.append(kind)
                return f"{match.group('scheme')}[REDACTED:credentials]@"
            replacement = f"[REDACTED:{kind}]"
            if match.group(0) != replacement:
                found.append(kind)
            return replacement

        result = pattern.sub(_replace, result)

    return Redaction(result, sorted(set(found)))


def redact_secrets(text: str) -> str:
    return redact(text).text


def looks_like_secret(text: str) -> bool:
    return redact(text).changed
