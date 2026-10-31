"""AST-aware chunking (Python) and a solid generic chunker for everything else.

Why chunk at all: retrieval precision and citations. A whole 800-line file is
useless as a citation and wasteful in a prompt; a function with exact line
numbers is both.

Python uses the real ``ast`` module. Other languages use declaration regexes
(``function``, ``def``, ``func``, ``fn``, ``class``, ``interface``, ``impl``,
``SELECT`` …) with a bounded-window fallback. That is deliberately dependency
free so the same code runs under CPython and in the browser build; tree-sitter
parsers can be added later behind the same ``chunk_file`` contract.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from .code import Chunk, Symbol

#: Files worth indexing. Anything else is ignored by the walker.
CODE_EXTENSIONS: dict[str, str] = {
    ".py": "python",
    ".pyi": "python",
    ".js": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".java": "java",
    ".kt": "kotlin",
    ".kts": "kotlin",
    ".c": "c",
    ".h": "c",
    ".cc": "cpp",
    ".cpp": "cpp",
    ".cxx": "cpp",
    ".hpp": "cpp",
    ".hh": "cpp",
    ".cs": "csharp",
    ".go": "go",
    ".rs": "rust",
    ".rb": "ruby",
    ".php": "php",
    ".swift": "swift",
    ".dart": "dart",
    ".scala": "scala",
    ".ex": "elixir",
    ".exs": "elixir",
    ".sql": "sql",
    ".sh": "bash",
    ".bash": "bash",
    ".zsh": "bash",
    ".html": "html",
    ".css": "css",
    ".scss": "css",
    ".vue": "vue",
    ".svelte": "svelte",
    ".yml": "yaml",
    ".yaml": "yaml",
    ".toml": "toml",
    ".json": "json",
    ".tf": "terraform",
    ".r": "r",
    ".lua": "lua",
    ".pl": "perl",
}

#: Chunks larger than this are split so one citation cannot eat the prompt.
MAX_CHUNK_CHARS = 2400
MAX_CHUNK_LINES = 120

_DECL_RE = re.compile(
    r"^[ \t]*(?:@\w+[^\n]*\n[ \t]*)*"  # annotations/decorators on their own line
    r"(?:export\s+)?(?:default\s+)?(?:public\s+|private\s+|protected\s+|internal\s+|"
    r"static\s+|abstract\s+|final\s+|async\s+|suspend\s+)*"
    r"(?:function|def|func|fn|class|interface|struct|impl|enum|trait|module|namespace|"
    r"type|const|let|var|SELECT|CREATE\s+(?:TABLE|VIEW|INDEX|FUNCTION)|"
    r"BEGIN|PROCEDURE)\b",
    re.M,
)
_NAME_RE = re.compile(
    r"(?:function|def|func|fn|class|interface|struct|impl|enum|trait|module|namespace|"
    r"type|const|let|var)\s+[A-Za-z_$@][\w$.:<>]*",
)


def language_for(path: str | Path) -> str:
    return CODE_EXTENSIONS.get(Path(path).suffix.lower(), "unknown")


def should_index(path: str | Path) -> bool:
    return Path(path).suffix.lower() in CODE_EXTENSIONS


# --------------------------------------------------------------------- public


def chunk_file(text: str, path: str, *, window_lines: int = 60) -> list[Chunk]:
    """Split a source file into citable chunks. Never raises on bad syntax."""
    if not text.strip():
        return []
    language = language_for(path)
    if language == "python":
        chunks = _python_chunks(text, path)
        if chunks:
            return chunks
    return _generic_chunks(text, path, window_lines=window_lines)


def symbols_for(text: str, path: str) -> list[Symbol]:
    """Best-effort symbol list (fed to the existence checker in step 3)."""
    if language_for(path) == "python":
        try:
            tree = ast.parse(text)
        except SyntaxError:
            return _generic_symbols(text, path)
        found: list[Symbol] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                found.append(Symbol(node.name, "class", node.lineno, path))
            elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                found.append(Symbol(node.name, "function", node.lineno, path))
        return found
    return _generic_symbols(text, path)


# --------------------------------------------------------------------- python


def _python_chunks(text: str, path: str) -> list[Chunk]:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    lines = text.splitlines()
    chunks: list[Chunk] = []

    def start_line(node: ast.AST) -> int:
        decorators = getattr(node, "decorator_list", None) or []
        return min([getattr(node, "lineno", 1)] + [d.lineno for d in decorators])

    def emit(start: int, end: int, kind: str, symbol: str) -> None:
        chunks.extend(_split(lines, path, start, end, kind, symbol))

    first_definition = min(
        (
            start_line(node)
            for node in tree.body
            if isinstance(node, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef)
        ),
        default=len(lines) + 1,
    )
    if first_definition > 4:
        emit(1, first_definition - 1, "block", "module header")

    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            emit(start_line(node), node.end_lineno or node.lineno, "class", node.name)
            for child in node.body:
                if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
                    emit(
                        start_line(child),
                        child.end_lineno or child.lineno,
                        "method",
                        f"{node.name}.{child.name}",
                    )
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            emit(start_line(node), node.end_lineno or node.lineno, "function", node.name)
    return chunks


# -------------------------------------------------------------------- generic


def _generic_chunks(text: str, path: str, *, window_lines: int) -> list[Chunk]:
    lines = text.splitlines()
    starts = [match.start() for match in _DECL_RE.finditer(text)]
    if not starts:
        return _windows(lines, path, window_lines)

    line_starts = _line_offsets(lines)
    declaration_lines = sorted({_line_of(offset, line_starts) for offset in starts})
    chunks: list[Chunk] = []

    if declaration_lines[0] > 3:
        chunks.extend(_split(lines, path, 1, declaration_lines[0] - 1, "block", "header"))

    for index, start in enumerate(declaration_lines):
        end = (
            (declaration_lines[index + 1] - 1) if index + 1 < len(declaration_lines) else len(lines)
        )
        snippet = "\n".join(lines[start - 1 : end])
        name_match = _NAME_RE.search(snippet.split("\n", 3)[0]) or _NAME_RE.search(snippet)
        name = _last_token(name_match.group(0)) if name_match else ""
        kind = (
            "class"
            if re.search(r"\b(class|interface|struct|impl|enum|trait)\b", snippet[:200])
            else "block"
        )
        chunks.extend(_split(lines, path, start, end, kind, name))
    return chunks


def _generic_symbols(text: str, path: str) -> list[Symbol]:
    found: list[Symbol] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        match = _NAME_RE.search(line)
        if not match:
            continue
        name = _last_token(match.group(0))
        if not name:
            continue
        kind = (
            "class"
            if re.match(r"^\s*(?:export\s+)?(?:class|interface|struct|enum|trait)\b", line)
            else "function"
        )
        found.append(Symbol(name, kind, line_number, path))
    return found


# --------------------------------------------------------------------- shared


def _split(
    lines: list[str], path: str, start: int, end: int, kind: str, symbol: str
) -> list[Chunk]:
    """Emit a range, breaking it into bounded pieces if it is too big."""
    start = max(1, start)
    end = min(len(lines), max(start, end))
    if end - start + 1 <= 0:
        return []
    body = "\n".join(lines[start - 1 : end])
    if not body.strip():
        return []

    pieces: list[Chunk] = []
    cursor = start
    while cursor <= end:
        piece_end = min(end, cursor + MAX_CHUNK_LINES - 1)
        # Shrink until the text fits the character budget as well.
        while (
            piece_end > cursor and len("\n".join(lines[cursor - 1 : piece_end])) > MAX_CHUNK_CHARS
        ):
            piece_end -= 10
        if piece_end <= cursor:
            piece_end = cursor
        piece_text = "\n".join(lines[cursor - 1 : piece_end])
        if piece_text.strip():
            pieces.append(
                Chunk(
                    path=path,
                    start_line=cursor,
                    end_line=piece_end,
                    kind=kind,
                    symbol=symbol,
                    text=piece_text[:MAX_CHUNK_CHARS],
                )
            )
        cursor = piece_end + 1
    return pieces


def _windows(lines: list[str], path: str, window_lines: int) -> list[Chunk]:
    chunks: list[Chunk] = []
    for start in range(1, len(lines) + 1, window_lines):
        end = min(len(lines), start + window_lines - 1)
        text = "\n".join(lines[start - 1 : end])
        if len([line for line in lines[start - 1 : end] if line.strip()]) < 2:
            continue
        chunks.append(Chunk(path, start, end, "block", "", text[:MAX_CHUNK_CHARS]))
    return chunks


def _line_offsets(lines: list[str]) -> list[int]:
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line) + 1)
    return offsets


def _line_of(offset: int, offsets: list[int]) -> int:
    # Binary search without importing bisect for one call site is not worth it.
    low, high = 0, len(offsets) - 1
    while low < high:
        middle = (low + high + 1) // 2
        if offsets[middle] <= offset:
            low = middle
        else:
            high = middle - 1
    return max(1, low + 1)


def _last_token(declaration: str) -> str:
    tokens = re.findall(r"[A-Za-z_$@][\w$]*", declaration)
    return tokens[-1] if tokens else ""
