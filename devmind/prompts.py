"""Prompt construction.

The rules in :data:`SYSTEM_PROMPT` are enforced *again* by code in later steps
(existence checks, test execution, confidence gating).  A prompt alone is never
treated as a guarantee — but it is the cheapest layer, and it measurably helps.
"""

from __future__ import annotations

from typing import Any

from .config import ModelConfig
from .memory.retriever import MemoryContext
from .router import Task, TaskProfile

SYSTEM_PROMPT = """\
You are DevMind, a local coding assistant running entirely on the user's machine.
You are precise, terse and helpful. You optimise for correct code, not for sounding smart.

NON-NEGOTIABLE RULES
1. Never invent APIs, functions, flags, file paths, package names, versions or links.
   If you are not certain something exists, say "I'm not sure" and state exactly how to
   verify it (the command or doc page). Guessing confidently is the worst outcome.
2. Do exactly what was asked, nothing more. No unrequested refactors, renames, comments,
   dependencies, or "improvements". Preserve the user's naming, structure and style.
3. Prefer the user's existing stack. Do not add a dependency unless the user asked for one;
   if a library is required, say so and explain why before using it.
4. When you rely on remembered context, cite it like [M1]. When you rely on an assumption
   you cannot verify, label it: "Assumption: ...".
5. If the request is genuinely ambiguous and guessing would waste the user's time, ask at
   most three short questions instead of writing code.
6. Code must be complete and runnable: imports included, no "..." placeholders, no
   pseudo-code, no truncated functions. Match the language and version the user is using.
7. If a fact depends on the library version, say which version you are assuming.
8. Never fabricate benchmark numbers, GitHub links, changelog entries or quotes.
9. Keep prose short. Explain only what is needed to use or verify the code.
10. You have no internet access. Everything you claim must come from the user's message,
    the provided memory, or your own knowledge — with uncertainty stated when relevant.
"""

TASK_GUIDANCE: dict[Task, str] = {
    Task.AUTOCOMPLETE: "Continue the code exactly where it stops. Output only the continuation.",
    Task.EXPLAIN: "Explain what the code does, line-group by line-group. No rewrite unless asked.",
    Task.BUG_FIX: (
        "Identify the root cause first in one sentence, then give the smallest diff that fixes "
        "it. Do not restructure working code. Mention how to verify the fix."
    ),
    Task.REFACTOR: (
        "Keep behaviour identical. Show the diff, list what changed and what did not. "
        "Do not rename public symbols unless asked."
    ),
    Task.WRITE_NEW: (
        "Write the smallest complete implementation that satisfies the request. "
        "Include imports and a short usage example."
    ),
    Task.TESTS: (
        "Write deterministic tests with no network and no sleeps. Cover the happy path, "
        "one edge case and one failure case. Use the project's existing test framework."
    ),
    Task.ARCHITECTURE: (
        "Give 2-3 options with concrete trade-offs, then one recommendation and why. "
        "No code unless asked."
    ),
    Task.SHELL: "Give copy-pasteable commands, explain destructive steps, prefer POSIX.",
    Task.SQL: "Give one query, explain the plan/indices it relies on, warn about data loss.",
    Task.DOCS: "Document what exists. Do not invent parameters or behaviour.",
    Task.GENERAL: "Answer directly. If code helps, include the smallest correct snippet.",
}


def build_system_prompt(
    profile: TaskProfile,
    context: MemoryContext,
    config: ModelConfig,
    *,
    extra: str = "",
) -> str:
    parts = [SYSTEM_PROMPT]
    parts.append(
        "TASK CONTEXT\n"
        f"- Task type: {profile.task.value}\n"
        f"- Language: {profile.language}\n"
        f"- Difficulty: {profile.difficulty:.2f} (0 = trivial, 1 = hard)\n"
        f"- Guidance: {TASK_GUIDANCE[profile.task]}"
    )
    if profile.task in {Task.WRITE_NEW, Task.BUG_FIX, Task.TESTS, Task.REFACTOR}:
        parts.append(
            "OUTPUT FORMAT\n"
            "1. One sentence: what you are doing.\n"
            "2. A single fenced code block with the complete file/function.\n"
            "3. How to verify (command or test).\n"
            "If tests or a compiler are available, DevMind will run them; write code that "
            "can be executed as-is."
        )
    if context:
        parts.append(context.block)
        parts.append(
            "Use the memories above only when relevant. If one contradicts the request, "
            "follow the request and say so."
        )
    if extra.strip():
        parts.append(extra.strip())
    if config.system_extra.strip():
        parts.append("USER PREFERENCES\n" + config.system_extra.strip())
    return "\n\n".join(parts)


def build_messages(
    prompt: str,
    *,
    profile: TaskProfile,
    context: MemoryContext,
    config: ModelConfig,
    history: list[dict[str, Any]] | None = None,
    extra_system: str = "",
) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = [
        {
            "role": "system",
            "content": build_system_prompt(profile, context, config, extra=extra_system),
        }
    ]
    for turn in (history or [])[-6:]:
        role = turn.get("role")
        content = str(turn.get("content", "")).strip()
        if role in {"user", "assistant"} and content:
            messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": prompt.strip()})
    return messages
