"""Shared value types for the code index (no database or filesystem imports)."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Chunk:
    """A citable slice of a source file."""

    path: str
    start_line: int
    end_line: int
    kind: str  # function | class | method | block | file
    symbol: str
    text: str

    @property
    def label(self) -> str:
        name = f" {self.symbol}" if self.symbol else ""
        return f"{self.path}:{self.start_line}-{self.end_line} ({self.kind}{name})"


@dataclass
class Symbol:
    name: str
    kind: str  # function | class | method | symbol
    line: int
    path: str = ""


@dataclass
class CodeHit:
    """A retrieval result from the code index."""

    chunk_id: str
    repo: str
    path: str
    start_line: int
    end_line: int
    kind: str
    symbol: str
    text: str
    score: float
    meta: dict[str, str] = field(default_factory=dict)

    @property
    def location(self) -> str:
        return f"{self.path}:{self.start_line}-{self.end_line}"

    def to_dict(self) -> dict:
        return {
            "chunk_id": self.chunk_id,
            "repo": self.repo,
            "path": self.path,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "kind": self.kind,
            "symbol": self.symbol,
            "score": round(self.score, 4),
            "text": self.text,
            "location": self.location,
        }


@dataclass
class IndexReport:
    repo: str
    root: str
    scanned: int = 0
    indexed: int = 0
    skipped: int = 0
    removed: int = 0
    chunks: int = 0
    symbols: int = 0
    bytes_read: int = 0
    errors: list[str] = field(default_factory=list)
    seconds: float = 0.0
    complete: bool = True
    reason: str = ""

    def to_dict(self) -> dict:
        data = self.__dict__.copy()
        data["errors"] = self.errors[:20]
        data["seconds"] = round(self.seconds, 2)
        return data

    def summary(self) -> str:
        state = "complete" if self.complete else f"paused ({self.reason})"
        return (
            f"{self.repo}: {self.indexed} indexed, {self.skipped} unchanged, "
            f"{self.removed} removed, {self.chunks} chunks, {self.symbols} symbols "
            f"in {self.seconds:.1f}s - {state}"
        )
