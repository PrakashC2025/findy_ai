"""SQLite-backed long-term memory, one database per user.

Tables
------
``memories``     durable knowledge: preferences, facts, snippets, rules
``episodes``     conversation log (what was asked, what was answered)
``feedback``     accepted / edited / rejected labels used by the learning loop
``model_stats``  per-(task, model) win rates that make routing improve
``routing_log``  why each answer used the model it used (auditability)
``skills``       learned procedures the agent re-uses
``meta``         small key/value bag (schema version, embedder name, ...)

The database is a single file under ``$DEVMIND_HOME``. Nothing is sent
anywhere. ``devmind forget`` and ``devmind wipe`` are implemented here.
"""

# Dynamic SQL below interpolates only fixed identifiers (table names, WHERE fragments),
# never user values; every value is a bound parameter.
# devmind: allow-file sql.dynamic

from __future__ import annotations

import contextlib
import json
import math
import os
import sqlite3
import threading
import uuid
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from collections.abc import Callable, Iterable

from ..code import Chunk, CodeHit, Symbol
from ..security import redact_secrets
from .embed import DIM, cosine, embed, from_bytes, to_bytes, tokenize

SCHEMA_PATH = Path(__file__).resolve().parents[1] / "storage" / "schema.sql"

MEMORY_KINDS = ("preference", "fact", "snippet", "rule", "code")
SCHEMA_VERSION = 1


@dataclass
class Hit:
    """A retrieval result handed to the prompt builder and the UI."""

    id: str
    kind: str
    text: str
    score: float
    source: str = "local"
    created_at: float = field(default_factory=time.time)
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "text": self.text,
            "score": round(self.score, 4),
            "source": self.source,
            "created_at": self.created_at,
            "meta": self.meta,
        }


def _new_id(prefix: str) -> str:
    return prefix + os.urandom(6).hex()


class MemoryStore:
    def __init__(
        self,
        path: str | Path,
        *,
        embedder: Callable[[str], Any] | None = None,
        redact: bool = True,
    ):
        self.path = Path(path)
        self.redact = redact
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._local = threading.local()
        self._embed = embedder or embed
        self._fts = self._initialise()
        self._set_meta("schema_version", str(SCHEMA_VERSION))
        self._set_meta("embedder", getattr(self._embed, "__name__", "custom"))
        self._migrate_embeddings()

    def _migrate_embeddings(self) -> None:
        """Recompute vectors when the embedding dimension/implementation changed."""
        current = f"{getattr(self._embed, '__name__', 'custom')}:{DIM}"
        stored = self.get_meta("embedding_model", "")
        if stored == current:
            return
        if stored:
            rows = self._connection().execute("SELECT id, text FROM memories").fetchall()
            with self._lock:
                for row in rows:
                    self._connection().execute(
                        "UPDATE memories SET embedding=? WHERE id=?",
                        (to_bytes(self._embed(row["text"])), row["id"]),
                    )
                self._connection().commit()
        self._set_meta("embedding_model", current)

    # ------------------------------------------------------------------ setup
    def _connection(self) -> sqlite3.Connection:
        connection = getattr(self._local, "connection", None)
        if connection is None:
            connection = sqlite3.connect(self.path, check_same_thread=False, timeout=10)
            connection.row_factory = sqlite3.Row
            # WAL is unsupported in some SQLite-WASM builds; there the
            # browser's OPFS setup handles durability instead.
            with contextlib.suppress(sqlite3.OperationalError):
                connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=NORMAL")
            connection.execute("PRAGMA foreign_keys=ON")
            self._local.connection = connection
        return connection

    def _initialise(self) -> bool:
        with self._lock:
            connection = self._connection()
            connection.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
            try:
                connection.executescript(
                    "CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(id UNINDEXED, text);"
                    "CREATE VIRTUAL TABLE IF NOT EXISTS code_fts USING fts5(id UNINDEXED, text);"
                )
                fts = True
            except sqlite3.OperationalError:  # SQLite built without FTS5
                fts = False
            connection.commit()
        return fts

    def close(self) -> None:
        connection = getattr(self._local, "connection", None)
        if connection is not None:
            connection.close()
            self._local.connection = None

    def _set_meta(self, key: str, value: str) -> None:
        with self._lock:
            connection = self._connection()
            connection.execute(
                "INSERT INTO meta(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )
            connection.commit()

    def get_meta(self, key: str, default: str = "") -> str:
        row = self._connection().execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

    # --------------------------------------------------------------- memories
    def remember(
        self,
        text: str,
        kind: str = "fact",
        *,
        source: str = "local",
        meta: dict[str, Any] | None = None,
        weight: float = 1.0,
        dedupe_threshold: float = 0.94,
    ) -> str:
        """Store a durable memory; near-duplicates reinforce instead of piling up."""
        text = (text or "").strip()
        if self.redact:
            text = redact_secrets(text)
        if not text:
            raise ValueError("memory text is empty")
        if kind not in MEMORY_KINDS:
            kind = "fact"
        vector = self._embed(text)
        with self._lock:
            connection = self._connection()
            if dedupe_threshold < 1.0:
                for row in connection.execute(
                    "SELECT id, embedding FROM memories WHERE kind=? "
                    "ORDER BY created_at DESC LIMIT 400",
                    (kind,),
                ):
                    if (
                        row["embedding"]
                        and cosine(vector, from_bytes(row["embedding"])) >= dedupe_threshold
                    ):
                        connection.execute(
                            "UPDATE memories SET weight=MIN(weight+0.25, 4.0), updated_at=? "
                            "WHERE id=?",
                            (time.time(), row["id"]),
                        )
                        connection.commit()
                        return row["id"]

            memory_id = _new_id("mem_")
            now = time.time()
            connection.execute(
                "INSERT INTO memories(id,kind,text,source,created_at,updated_at,weight,meta,embedding)"
                " VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    memory_id,
                    kind,
                    text,
                    source,
                    now,
                    now,
                    weight,
                    json.dumps(meta or {}),
                    to_bytes(vector),
                ),
            )
            if self._fts:
                connection.execute(
                    "INSERT INTO memory_fts(id, text) VALUES(?, ?)", (memory_id, text)
                )
            connection.commit()
        return memory_id

    # Friendly alias used by the CLI's `teach` command.
    add_memory = remember

    def forget(self, memory_id: str) -> bool:
        with self._lock:
            connection = self._connection()
            cursor = connection.execute("DELETE FROM memories WHERE id=?", (memory_id,))
            if self._fts:
                connection.execute("DELETE FROM memory_fts WHERE id=?", (memory_id,))
            connection.commit()
            return cursor.rowcount > 0

    def get_memory(self, memory_id: str) -> dict[str, Any] | None:
        row = (
            self._connection().execute("SELECT * FROM memories WHERE id=?", (memory_id,)).fetchone()
        )
        return self._row_to_memory(row) if row else None

    def list_memories(
        self, *, limit: int = 100, offset: int = 0, kind: str | None = None
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM memories"
        params: list[Any] = []
        if kind:
            sql += " WHERE kind=?"
            params.append(kind)
        sql += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        return [self._row_to_memory(row) for row in self._connection().execute(sql, params)]

    def repo_files(self, repo: str, *, limit: int = 3000) -> list[str]:
        """Indexed paths for a repository, for the web UI's file browser."""
        connection = self._connection()
        with self._lock:
            rows = connection.execute(
                "SELECT path FROM code_files WHERE repo=? ORDER BY path LIMIT ?", (repo, limit)
            ).fetchall()
        return [row["path"] for row in rows]

    # ------------------------------------------------------------ answer cache
    def cache_put(
        self,
        prompt: str,
        *,
        answer: str,
        model: str,
        repo: str = "",
        citations: list[dict[str, Any]] | None = None,
        confidence: float = 0.0,
        max_rows: int = 400,
    ) -> str:
        """Store an answer with its embedding, pruning the oldest entries."""
        now = time.time()
        entry_id = uuid.uuid4().hex
        connection = self._connection()
        with self._lock:
            connection.execute(
                "INSERT INTO answer_cache("
                "id, prompt, repo, model, answer, citations, confidence, embedding, created_at"
                ") VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    entry_id,
                    prompt.strip(),
                    repo,
                    model,
                    answer,
                    json.dumps(citations or [], ensure_ascii=False),
                    float(confidence),
                    to_bytes(embed(prompt)),
                    now,
                ),
            )
            connection.execute(
                "DELETE FROM answer_cache WHERE id IN ("
                "SELECT id FROM answer_cache ORDER BY created_at DESC LIMIT -1 OFFSET ?)",
                (max_rows,),
            )
            connection.commit()
        return entry_id

    def cache_lookup(
        self,
        prompt: str,
        *,
        repo: str = "",
        threshold: float = 0.92,
        scan_rows: int = 250,
    ) -> dict[str, Any] | None:
        """Best cached answer above ``threshold``; increments its hit counter."""
        vector = embed(prompt)
        best: tuple[float, dict[str, Any]] | None = None
        connection = self._connection()
        with self._lock:
            rows = connection.execute(
                "SELECT * FROM answer_cache WHERE repo=? ORDER BY created_at DESC LIMIT ?",
                (repo, scan_rows),
            ).fetchall()
            for row in rows:
                try:
                    cached_vector = from_bytes(row["embedding"])
                except (TypeError, ValueError):
                    continue
                score = cosine(vector, cached_vector)
                if score < threshold:
                    continue
                if best is None or score > best[0]:
                    best = (score, dict(row))
            if best is None:
                return None
            score, entry = best
            entry["similarity"] = round(score, 4)
            entry["citations"] = json.loads(entry.get("citations") or "[]")
            connection.execute(
                "UPDATE answer_cache SET hits=hits+1, last_hit=? WHERE id=?",
                (time.time(), entry["id"]),
            )
            connection.commit()
        return entry

    def cache_stats(self) -> dict[str, int]:
        connection = self._connection()
        with self._lock:
            row = connection.execute(
                "SELECT COUNT(*) AS entries, COALESCE(SUM(hits), 0) AS hits FROM answer_cache"
            ).fetchone()
        return {"entries": int(row["entries"]), "hits": int(row["hits"])}

    def cache_clear(self) -> int:
        connection = self._connection()
        with self._lock:
            removed = connection.execute("DELETE FROM answer_cache").rowcount
            connection.commit()
        return int(removed or 0)

    def memory_count(self) -> int:
        return int(self._connection().execute("SELECT COUNT(*) AS c FROM memories").fetchone()["c"])

    # -------------------------------------------------------------- retrieval
    def search(
        self, query: str, *, top_k: int = 6, kinds: Iterable[str] | None = None
    ) -> list[Hit]:
        """Hybrid lexical + vector retrieval with recency and importance boosts."""
        query = (query or "").strip()
        if not query:
            return []
        kinds = tuple(kinds) if kinds else None
        query_vector = self._embed(query)
        candidates: dict[str, tuple[sqlite3.Row, float]] = {}

        with self._lock:
            connection = self._connection()
            if self._fts:
                match = self._fts_query(query)
                if match:
                    try:
                        rows = connection.execute(
                            "SELECT m.*, bm25(memory_fts) AS rank FROM memory_fts "
                            "JOIN memories m ON m.id = memory_fts.id "
                            "WHERE memory_fts MATCH ? ORDER BY rank LIMIT 200",
                            (match,),
                        ).fetchall()
                    except sqlite3.OperationalError:
                        rows = []
                    # SQLite's bm25() scale depends on corpus size, so normalise
                    # against the best hit of this query: 1.0 = top lexical match.
                    matched = [row for row in rows if not kinds or row["kind"] in kinds]
                    best_rank = max((-row["rank"] for row in matched), default=0.0) or 1.0
                    for row in matched:
                        candidates[row["id"]] = (row, min(1.0, (-row["rank"]) / best_rank))

            total = self.memory_count()
            scan_limit = int(os.environ.get("DEVMIND_VECTOR_SCAN", "8000"))
            sql = "SELECT * FROM memories"
            if total > scan_limit:
                sql += f" ORDER BY created_at DESC LIMIT {scan_limit}"
            for row in connection.execute(sql):
                if kinds and row["kind"] not in kinds:
                    continue
                candidates.setdefault(row["id"], (row, 0.0))

        now = time.time()
        hits: list[Hit] = []
        for memory_id, (row, lexical) in candidates.items():
            vector = from_bytes(row["embedding"]) if row["embedding"] else None
            semantic = max(0.0, cosine(query_vector, vector)) if vector else 0.0
            age_days = max(0.0, (now - row["created_at"]) / 86400)
            recency = math.exp(-age_days / 45.0)
            importance = float(row["weight"] or 1.0)
            # Relevance gates everything: a fresh, important memory that has
            # nothing to do with the query must not be injected into the prompt.
            relevance = 0.64 * semantic + 0.36 * lexical
            if relevance <= 0.015:
                continue
            score = relevance * (1.0 + 0.15 * recency + 0.10 * min(1.0, importance / 2.0))
            hits.append(
                Hit(
                    id=memory_id,
                    kind=row["kind"],
                    text=row["text"],
                    score=score,
                    source=row["source"],
                    created_at=row["created_at"],
                    meta=json.loads(row["meta"] or "{}"),
                )
            )
        hits.sort(key=lambda hit: hit.score, reverse=True)
        return hits[:top_k]

    @staticmethod
    def _fts_query(query: str) -> str:
        from .embed import tokenize

        tokens = [token for token in tokenize(query) if len(token) > 1][:24]
        return " OR ".join(f'"{token}"' for token in tokens)

    # --------------------------------------------------------------- episodes
    def record_episode(
        self,
        session_id: str,
        role: str,
        content: str,
        meta: dict[str, Any] | None = None,
    ) -> str:
        episode_id = _new_id("ep_")
        if self.redact:
            content = redact_secrets(content)
        with self._lock:
            connection = self._connection()
            connection.execute(
                "INSERT INTO episodes(id,session_id,role,content,created_at,meta) "
                "VALUES(?,?,?,?,?,?)",
                (episode_id, session_id, role, content, time.time(), json.dumps(meta or {})),
            )
            connection.commit()
        return episode_id

    def recent_episodes(self, session_id: str, limit: int = 8) -> list[dict[str, Any]]:
        rows = (
            self._connection()
            .execute(
                "SELECT * FROM episodes WHERE session_id=? ORDER BY created_at DESC LIMIT ?",
                (session_id, limit),
            )
            .fetchall()
        )
        return [
            {
                "id": row["id"],
                "session_id": row["session_id"],
                "role": row["role"],
                "content": row["content"],
                "created_at": row["created_at"],
                "meta": json.loads(row["meta"] or "{}"),
            }
            for row in reversed(rows)
        ]

    # --------------------------------------------------------------- feedback
    def add_feedback(
        self,
        rating: int,
        *,
        answer_id: str = "",
        task: str = "",
        model: str = "",
        correction: str = "",
    ) -> str:
        feedback_id = _new_id("fb_")
        if self.redact:
            correction = redact_secrets(correction)
        with self._lock:
            connection = self._connection()
            connection.execute(
                "INSERT INTO feedback(id,answer_id,rating,task,model,correction,created_at) "
                "VALUES(?,?,?,?,?,?,?)",
                (feedback_id, answer_id, int(rating), task, model, correction, time.time()),
            )
            connection.commit()
        if correction.strip():
            self.remember(
                f"Correction ({task or 'general'}): {correction.strip()}",
                kind="rule",
                source="feedback",
                weight=1.5,
                meta={"model": model, "task": task},
            )
        return feedback_id

    def feedback_summary(self) -> dict[str, Any]:
        row = (
            self._connection()
            .execute(
                "SELECT COUNT(*) AS n, AVG(rating) AS avg, "
                "SUM(rating > 0) AS good, SUM(rating < 0) AS bad FROM feedback"
            )
            .fetchone()
        )
        return {
            "count": row["n"] or 0,
            "average": round(row["avg"] or 0.0, 3),
            "positive": row["good"] or 0,
            "negative": row["bad"] or 0,
        }

    # ------------------------------------------------------------- model stats
    def model_stats(self, task: str) -> dict[str, dict[str, float]]:
        rows = (
            self._connection().execute("SELECT * FROM model_stats WHERE task=?", (task,)).fetchall()
        )
        return {
            row["model"]: {
                "alpha": row["alpha"],
                "beta": row["beta"],
                "runs": row["runs"],
                "wins": row["wins"],
                "latency_ema": row["latency_ema"],
            }
            for row in rows
        }

    def record_outcome(
        self,
        task: str,
        model: str,
        success: bool,
        quality: float,
        latency_ms: float,
    ) -> None:
        """Beta-Bernoulli posterior per (task, model) plus a latency EMA."""
        reward = max(0.0, min(1.0, quality if success else quality * 0.25))
        with self._lock:
            connection = self._connection()
            row = connection.execute(
                "SELECT * FROM model_stats WHERE task=? AND model=?", (task, model)
            ).fetchone()
            if row is None:
                connection.execute(
                    "INSERT INTO model_stats(task,model,alpha,beta,runs,wins,latency_ema,updated_at)"
                    " VALUES(?,?,?,?,?,?,?,?)",
                    (
                        task,
                        model,
                        1.0 + reward,
                        1.0 + (1.0 - reward),
                        1,
                        1 if success else 0,
                        latency_ms,
                        time.time(),
                    ),
                )
            else:
                latency = (
                    0.7 * row["latency_ema"] + 0.3 * latency_ms
                    if row["latency_ema"]
                    else latency_ms
                )
                connection.execute(
                    "UPDATE model_stats SET alpha=?, beta=?, runs=runs+1, wins=wins+?, "
                    "latency_ema=?, updated_at=? WHERE task=? AND model=?",
                    (
                        row["alpha"] + reward,
                        row["beta"] + (1.0 - reward),
                        1 if success else 0,
                        latency,
                        time.time(),
                        task,
                        model,
                    ),
                )
            connection.commit()

    # ------------------------------------------------------------ routing log
    def log_route(
        self,
        *,
        session: str,
        task: str,
        language: str,
        difficulty: float,
        model: str,
        reason: str,
        prompt_chars: int,
    ) -> str:
        route_id = _new_id("rt_")
        with self._lock:
            connection = self._connection()
            connection.execute(
                "INSERT INTO routing_log(id,at,session,task,language,difficulty,model,reason,"
                "prompt_chars,outcome) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    route_id,
                    time.time(),
                    session,
                    task,
                    language,
                    difficulty,
                    model,
                    reason,
                    prompt_chars,
                    "pending",
                ),
            )
            connection.commit()
        return route_id

    def set_route_outcome(self, route_id: str, outcome: str) -> None:
        with self._lock:
            connection = self._connection()
            connection.execute("UPDATE routing_log SET outcome=? WHERE id=?", (outcome, route_id))
            connection.commit()

    def recent_routes(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = (
            self._connection()
            .execute("SELECT * FROM routing_log ORDER BY at DESC LIMIT ?", (limit,))
            .fetchall()
        )
        return [dict(row) for row in rows]

    # ----------------------------------------------------------------- skills
    def upsert_skill(
        self,
        name: str,
        description: str,
        steps: list[str],
        success: bool | None = None,
    ) -> None:
        with self._lock:
            connection = self._connection()
            exists = connection.execute("SELECT 1 FROM skills WHERE name=?", (name,)).fetchone()
            now = time.time()
            if exists:
                connection.execute(
                    "UPDATE skills SET description=?, steps=?, successes=successes+?, "
                    "failures=failures+?, updated_at=? WHERE name=?",
                    (
                        description,
                        json.dumps(steps),
                        1 if success else 0,
                        1 if success is False else 0,
                        now,
                        name,
                    ),
                )
            else:
                connection.execute(
                    "INSERT INTO skills(name,description,steps,successes,failures,updated_at)"
                    " VALUES(?,?,?,?,?,?)",
                    (
                        name,
                        description,
                        json.dumps(steps),
                        1 if success else 0,
                        1 if success is False else 0,
                        now,
                    ),
                )
            connection.commit()

    def list_skills(self) -> list[dict[str, Any]]:
        rows = (
            self._connection()
            .execute("SELECT * FROM skills ORDER BY successes DESC, updated_at DESC")
            .fetchall()
        )
        return [
            {
                "name": row["name"],
                "description": row["description"],
                "steps": json.loads(row["steps"] or "[]"),
                "successes": row["successes"],
                "failures": row["failures"],
                "updated_at": row["updated_at"],
            }
            for row in rows
        ]

    # --------------------------------------------------------------- code index
    def code_files(self, repo: str) -> dict[str, str]:
        """Map path -> sha for one repository (drives incremental indexing)."""
        rows = self._connection().execute("SELECT path, sha FROM code_files WHERE repo=?", (repo,))
        return {row["path"]: row["sha"] for row in rows}

    def replace_code_file(
        self,
        repo: str,
        path: str,
        sha: str,
        lang: str,
        size: int,
        mtime: float,
        chunks: list[Chunk],
        symbols: list[Symbol],
        *,
        commit: bool = True,
    ) -> tuple[int, int]:
        """Replace all chunks/symbols for one file. Returns (chunks, symbols)."""
        now = time.time()
        with self._lock:
            connection = self._connection()
            if self._fts:
                old_ids = [
                    row["id"]
                    for row in connection.execute(
                        "SELECT id FROM code_chunks WHERE repo=? AND path=?", (repo, path)
                    )
                ]
                for chunk_id in old_ids:
                    connection.execute("DELETE FROM code_fts WHERE id=?", (chunk_id,))
            connection.execute("DELETE FROM code_chunks WHERE repo=? AND path=?", (repo, path))
            connection.execute("DELETE FROM code_symbols WHERE repo=? AND path=?", (repo, path))

            for chunk in chunks:
                chunk_id = _new_id("cc_")
                connection.execute(
                    "INSERT INTO code_chunks(id,repo,path,start_line,end_line,kind,symbol,text,sha,embedding)"
                    " VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (
                        chunk_id,
                        repo,
                        path,
                        chunk.start_line,
                        chunk.end_line,
                        chunk.kind,
                        chunk.symbol,
                        chunk.text,
                        sha,
                        to_bytes(self._embed(chunk.text)),
                    ),
                )
                if self._fts:
                    connection.execute(
                        "INSERT INTO code_fts(id, text) VALUES(?, ?)",
                        (chunk_id, f"{path} {chunk.symbol} {chunk.text}"),
                    )
            for symbol in symbols:
                connection.execute(
                    "INSERT INTO code_symbols(id,repo,path,name,kind,line) VALUES(?,?,?,?,?,?)",
                    (_new_id("sym_"), repo, path, symbol.name, symbol.kind, symbol.line),
                )
            connection.execute(
                "INSERT INTO code_files(repo,path,sha,lang,size,mtime,indexed_at) "
                "VALUES(?,?,?,?,?,?,?) ON CONFLICT(repo,path) DO UPDATE SET "
                "sha=excluded.sha, lang=excluded.lang, size=excluded.size, "
                "mtime=excluded.mtime, indexed_at=excluded.indexed_at",
                (repo, path, sha, lang, size, mtime, now),
            )
            if commit:
                connection.commit()
        return len(chunks), len(symbols)

    def delete_code_file(self, repo: str, path: str, *, commit: bool = True) -> None:
        with self._lock:
            connection = self._connection()
            if self._fts:
                for row in connection.execute(
                    "SELECT id FROM code_chunks WHERE repo=? AND path=?", (repo, path)
                ):
                    connection.execute("DELETE FROM code_fts WHERE id=?", (row["id"],))
            connection.execute("DELETE FROM code_chunks WHERE repo=? AND path=?", (repo, path))
            connection.execute("DELETE FROM code_symbols WHERE repo=? AND path=?", (repo, path))
            connection.execute("DELETE FROM code_files WHERE repo=? AND path=?", (repo, path))
            if commit:
                connection.commit()

    def delete_repo(self, repo: str) -> None:
        with self._lock:
            connection = self._connection()
            if self._fts:
                for row in connection.execute("SELECT id FROM code_chunks WHERE repo=?", (repo,)):
                    connection.execute("DELETE FROM code_fts WHERE id=?", (row["id"],))
            for table in ("code_chunks", "code_symbols", "code_files", "index_state"):
                connection.execute(f"DELETE FROM {table} WHERE repo=?", (repo,))
            connection.commit()

    def flush(self) -> None:
        with self._lock:
            self._connection().commit()

    def set_index_state(self, repo: str, root: str, *, complete: bool) -> None:
        with self._lock:
            connection = self._connection()
            connection.execute(
                "INSERT INTO index_state(repo,root,updated_at,complete) VALUES(?,?,?,?) "
                "ON CONFLICT(repo) DO UPDATE SET root=excluded.root, "
                "updated_at=excluded.updated_at, complete=excluded.complete",
                (repo, root, time.time(), 1 if complete else 0),
            )
            connection.commit()

    def get_index_state(self, repo: str) -> dict[str, Any] | None:
        row = (
            self._connection().execute("SELECT * FROM index_state WHERE repo=?", (repo,)).fetchone()
        )
        if row is None:
            return None
        return {
            "repo": row["repo"],
            "root": row["root"],
            "updated_at": row["updated_at"],
            "complete": bool(row["complete"]),
        }

    def list_repos(self) -> list[dict[str, Any]]:
        rows = (
            self._connection()
            .execute(
                "SELECT f.repo AS repo, COUNT(*) AS files, MAX(f.indexed_at) AS updated "
                "FROM code_files f GROUP BY f.repo ORDER BY updated DESC"
            )
            .fetchall()
        )
        repos = []
        for row in rows:
            chunk_row = (
                self._connection()
                .execute("SELECT COUNT(*) AS chunks FROM code_chunks WHERE repo=?", (row["repo"],))
                .fetchone()
            )
            state = self.get_index_state(row["repo"]) or {}
            repos.append(
                {
                    "repo": row["repo"],
                    "files": row["files"],
                    "chunks": chunk_row["chunks"],
                    "updated_at": row["updated"],
                    "root": state.get("root", ""),
                }
            )
        return repos

    def search_code(self, query: str, *, repo: str | None = None, top_k: int = 6) -> list[CodeHit]:
        """Hybrid retrieval over code chunks, with path/symbol bonuses."""
        query = (query or "").strip()
        if not query:
            return []
        query_vector = self._embed(query)
        tokens = [token for token in tokenize(query) if len(token) > 2]
        candidates: dict[str, tuple[sqlite3.Row, float]] = {}

        with self._lock:
            connection = self._connection()
            where = "WHERE repo=?" if repo else ""
            params: tuple[Any, ...] = (repo,) if repo else ()

            if self._fts and tokens:
                match = " OR ".join(f'"{token}"' for token in tokens[:24])
                sql = (
                    "SELECT c.*, bm25(code_fts) AS rank FROM code_fts "
                    "JOIN code_chunks c ON c.id = code_fts.id "
                    f"WHERE code_fts MATCH ? {('AND c.repo=?' if repo else '')} "
                    "ORDER BY rank LIMIT 300"
                )
                try:
                    rows = connection.execute(
                        sql, (match, *params) if repo else (match,)
                    ).fetchall()
                except sqlite3.OperationalError:
                    rows = []
                best_rank = max((-row["rank"] for row in rows), default=0.0) or 1.0
                for row in rows:
                    candidates[row["id"]] = (row, min(1.0, (-row["rank"]) / best_rank))

            for row in connection.execute(f"SELECT * FROM code_chunks {where} LIMIT 8000", params):
                candidates.setdefault(row["id"], (row, 0.0))

        hits: list[CodeHit] = []
        for chunk_id, (row, lexical) in candidates.items():
            vector = from_bytes(row["embedding"]) if row["embedding"] else None
            semantic = max(0.0, cosine(query_vector, vector)) if vector else 0.0
            relevance = 0.62 * semantic + 0.38 * lexical
            if relevance <= 0.03:
                continue
            score = relevance
            haystack = f"{row['path']} {row['symbol']}".lower()
            if any(token in haystack for token in tokens[:8]):
                score += 0.12
            hits.append(
                CodeHit(
                    chunk_id=chunk_id,
                    repo=row["repo"],
                    path=row["path"],
                    start_line=row["start_line"],
                    end_line=row["end_line"],
                    kind=row["kind"],
                    symbol=row["symbol"],
                    text=row["text"],
                    score=score,
                )
            )
        hits.sort(key=lambda hit: hit.score, reverse=True)
        return hits[:top_k]

    def code_stats(self, repo: str | None = None) -> dict[str, Any]:
        connection = self._connection()
        where = "WHERE repo=?" if repo else ""
        params: tuple[Any, ...] = (repo,) if repo else ()
        files = connection.execute(
            f"SELECT COUNT(*) AS c FROM code_files {where}", params
        ).fetchone()["c"]
        chunks = connection.execute(
            f"SELECT COUNT(*) AS c FROM code_chunks {where}", params
        ).fetchone()["c"]
        symbols = connection.execute(
            f"SELECT COUNT(*) AS c FROM code_symbols {where}", params
        ).fetchone()["c"]
        return {"files": files, "chunks": chunks, "symbols": symbols, "fts": self._fts}

    # ------------------------------------------------------------------ stats
    def stats(self) -> dict[str, Any]:
        connection = self._connection()
        by_kind = {
            row["kind"]: row["c"]
            for row in connection.execute("SELECT kind, COUNT(*) AS c FROM memories GROUP BY kind")
        }
        return {
            "memories": sum(by_kind.values()),
            "by_kind": by_kind,
            "episodes": connection.execute("SELECT COUNT(*) AS c FROM episodes").fetchone()["c"],
            "skills": connection.execute("SELECT COUNT(*) AS c FROM skills").fetchone()["c"],
            "feedback": self.feedback_summary(),
            "db_bytes": self.path.stat().st_size if self.path.exists() else 0,
            "fts": self._fts,
            "path": str(self.path),
            "schema_version": self.get_meta("schema_version"),
            "code": self.code_stats(),
        }

    # --------------------------------------------------------------- transfer
    def export_json(self) -> dict[str, Any]:
        return {
            "version": SCHEMA_VERSION,
            "exported_at": time.time(),
            "memories": [
                {
                    key: row[key]
                    for key in ("id", "kind", "text", "source", "created_at", "weight", "meta")
                }
                for row in self._connection().execute("SELECT * FROM memories")
            ],
            "skills": self.list_skills(),
        }

    def import_json(self, payload: dict[str, Any]) -> int:
        existing = {memory["text"] for memory in self.list_memories(limit=100_000)}
        added = 0
        for item in payload.get("memories", []):
            text = (item.get("text") or "").strip()
            if not text or text in existing:
                continue
            self.remember(
                text,
                kind=item.get("kind", "fact"),
                source=item.get("source", "import"),
                meta=item.get("meta") or {},
                weight=float(item.get("weight") or 1.0),
            )
            existing.add(text)
            added += 1
        for skill in payload.get("skills", []):
            if skill.get("name"):
                self.upsert_skill(
                    skill["name"], skill.get("description", ""), skill.get("steps", [])
                )
        return added

    def wipe(self) -> None:
        """Delete *everything* this user has taught DevMind."""
        with self._lock:
            connection = self._connection()
            for table in (
                "memories",
                "episodes",
                "feedback",
                "model_stats",
                "routing_log",
                "skills",
                "meta",
                "memory_fts",
            ):
                try:
                    connection.execute(f"DELETE FROM {table}")
                except sqlite3.OperationalError:
                    continue
            connection.commit()
            connection.execute("VACUUM")

    # ---------------------------------------------------------------- helpers
    @staticmethod
    def _row_to_memory(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "kind": row["kind"],
            "text": row["text"],
            "source": row["source"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "weight": row["weight"],
            "meta": json.loads(row["meta"] or "{}"),
        }
