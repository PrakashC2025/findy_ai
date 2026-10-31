"""Incremental, resumable, bounded code indexer.

Properties that matter on a phone or a shared laptop:

* **Incremental** - files are hashed (sha256); unchanged files are never re-read
  or re-embedded. Removing a file removes its chunks.
* **Resumable** - the index stops after ``index.max_seconds`` and reports
  ``complete=False``. The next run continues where it stopped; progress is never
  thrown away.
* **Bounded** - file count, file size, byte budget and chunk size are capped, so
  a huge monorepo cannot freeze the machine.
* **Quiet** - excluded directories (`.git`, `node_modules`, `dist`, venvs …) and
  secret-looking files are never read, so they never reach memory or a prompt.

The same walker and chunker run in the browser build (SQLite-WASM) because they
only need ``os``, ``hashlib`` and text.
"""

from __future__ import annotations

import fnmatch
import hashlib
import os
import time
from pathlib import Path, PurePosixPath

from .chunkers import chunk_file, language_for, should_index, symbols_for
from .code import IndexReport
from .config import IndexConfig
from .memory.store import MemoryStore

_BINARY_SNIFF_BYTES = 8192


class CodeIndexer:
    def __init__(self, store: MemoryStore, config: IndexConfig):
        self.store = store
        self.config = config

    # ------------------------------------------------------------------ public
    def index(
        self,
        root: str | Path,
        *,
        repo: str | None = None,
        force: bool = False,
        max_seconds: float | None = None,
    ) -> IndexReport:
        root_path = Path(root).expanduser().resolve()
        if not root_path.is_dir():
            raise NotADirectoryError(f"{root_path} is not a directory")

        repo = repo or root_path.name
        budget = float(max_seconds if max_seconds is not None else self.config.max_seconds)
        started = time.perf_counter()
        report = IndexReport(repo=repo, root=str(root_path))

        known = self.store.code_files(repo)
        seen: set[str] = set()
        pending = 0
        truncated_reason = ""

        for path in self._walk(root_path):
            if report.scanned >= self.config.max_files:
                report.complete = False
                truncated_reason = f"max_files={self.config.max_files}"
                break
            if time.perf_counter() - started > budget:
                report.complete = False
                truncated_reason = f"max_seconds={budget:g}"
                break

            relative = path.relative_to(root_path).as_posix()
            seen.add(relative)
            report.scanned += 1
            try:
                payload = path.read_bytes()
            except OSError as exc:
                report.errors.append(f"{relative}: {exc.__class__.__name__}")
                continue

            if len(payload) > self.config.max_file_bytes:
                report.skipped += 1
                continue
            if b"\x00" in payload[:_BINARY_SNIFF_BYTES]:
                report.skipped += 1
                continue

            sha = hashlib.sha256(payload).hexdigest()
            if not force and known.get(relative) == sha:
                report.skipped += 1
                continue

            text = payload.decode("utf-8", errors="replace")
            language = language_for(path)
            chunks = chunk_file(text, relative, window_lines=self.config.window_lines)
            symbols = symbols_for(text, relative)
            try:
                chunk_count, symbol_count = self.store.replace_code_file(
                    repo,
                    relative,
                    sha,
                    language,
                    len(payload),
                    path.stat().st_mtime,
                    chunks,
                    symbols,
                    commit=False,
                )
            except Exception as exc:  # one bad file must not stop the index
                report.errors.append(f"{relative}: {exc.__class__.__name__}")
                continue

            report.indexed += 1
            report.chunks += chunk_count
            report.symbols += symbol_count
            report.bytes_read += len(payload)
            pending += 1
            if pending >= self.config.batch_size:
                self.store.flush()
                pending = 0

        self.store.flush()

        # Only prune when the traversal actually finished; a truncated walk has
        # not seen the rest of the tree and must not delete its files.
        if report.complete:
            for stale in sorted(set(known) - seen):
                self.store.delete_code_file(repo, stale, commit=False)
                report.removed += 1
            self.store.flush()

        report.seconds = time.perf_counter() - started
        report.complete = report.complete and not truncated_reason
        report.reason = truncated_reason
        self.store.set_index_state(repo, str(root_path), complete=report.complete)
        return report

    # ----------------------------------------------------------------- helpers
    def _walk(self, root: Path):
        for directory, dirnames, filenames in os.walk(root, followlinks=False):
            relative_dir = PurePosixPath(Path(directory).relative_to(root).as_posix())
            dirnames[:] = sorted(
                name for name in dirnames if not self._excluded(relative_dir / name, name)
            )
            for filename in sorted(filenames):
                if not should_index(filename):
                    continue
                if self._excluded(relative_dir / filename, filename):
                    continue
                yield Path(directory) / filename

    def _excluded(self, relative: PurePosixPath, name: str) -> bool:
        text = relative.as_posix()
        for pattern in self.config.exclude:
            if fnmatch.fnmatch(name, pattern) or fnmatch.fnmatch(text, pattern):
                return True
            if pattern in PurePosixPath(text).parts:
                return True
        return False


def index_repository(
    store: MemoryStore,
    config: IndexConfig,
    root: str | Path,
    *,
    repo: str | None = None,
    force: bool = False,
    max_seconds: float | None = None,
) -> IndexReport:
    """Convenience wrapper used by the CLI and the API."""
    return CodeIndexer(store, config).index(root, repo=repo, force=force, max_seconds=max_seconds)
