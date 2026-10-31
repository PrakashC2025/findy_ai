# DevMind architecture

> Scope of this document: what is implemented today (step 1), the contracts that later
> steps plug into, and the reasoning behind the choices. Anything not implemented is
> labelled **planned** so nobody mistakes a roadmap for a feature.

## 1. Goals, ranked

1. **Correct over clever.** A wrong-but-confident answer is the failure mode we optimise against.
2. **Do exactly what was asked.** No drive-by refactors, renames or dependencies.
3. **Free.** Open weights through Ollama, open-source Python, no paid code path.
4. **Local and private.** Loopback only, one SQLite file, no telemetry, explicit delete commands.
5. **Fast.** Interactive on a laptop; background work never blocks the UI (planned for the API/UI steps).
6. **Language-agnostic.** The router understands common languages; nothing is Python-only.

Non-goals: beating frontier cloud models on raw benchmarks; training foundation models;
being a general chat assistant.

## 2. Component map

```
                 ┌──────────────────────────────────────────────┐
   user ────────▶│  cli.py   (argparse, streaming, JSON output) │
                 └───────┬───────────────┬──────────────┬───────┘
                         │               │              │
                 ┌───────▼──────┐ ┌──────▼───────┐ ┌────▼─────────┐
                 │ router.py    │ │ prompts.py   │ │ memory/      │
                 │ classify +   │ │ system rules │ │ retriever.py │
                 │ pick model   │ │ + task guide │ │ (RAG)        │
                 └───┬───────┬──┘ └──────────────┘ └────┬─────────┘
                     │       │                          │
        ┌────────────▼──┐ ┌──▼─────────┐        ┌───────▼─────────┐
        │ registry.py   │ │ hardware.py│        │ memory/store.py │
        │ models.toml   │ │ RAM/VRAM,  │        │ SQLite + FTS5   │
        │ roles, sizes  │ │ budget,    │        │ + embeddings    │
        │ licenses      │ │ tok/s      │        │ + feedback      │
        └───────────────┘ └────────────┘        └─────────────────┘
                     │
              ┌──────▼───────────────────────────────────────────┐
              │ backends.py                                      │
              │  OllamaBackend  (loopback HTTP, stream, pulls)   │
              │  DemoBackend    (deterministic, clearly labelled)│
              └──────────────────────────────────────────────────┘
```

## 3. Request lifecycle (`devmind ask "…"`)

1. **Load config** (`~/.devmind/config.toml`), detect hardware (RAM, VRAM, bandwidth), load
   the registry (bundled + user override) and open the user's SQLite memory.
2. **Probe Ollama** for installed models (`/api/tags`). Failure is not fatal: the CLI explains
   what to install.
3. **Classify** the prompt: task, language, difficulty, with a list of signals for auditability.
4. **Route**: filter candidates by installed / memory fit / speed floor / optional caps, then
   score by prior quality, estimated speed, difficulty and learned win rate.
5. **Retrieve memory** (relevance-gated), auto-capture explicit preferences from declarative
   clauses.
6. **Build prompts**: hard rules + task guidance + output format + cited memory + history.
7. **Stream** tokens from the chosen model, measuring time-to-first-token and total time.
8. **Record** the episode and the routing decision (model, reason, outcome) for learning.

## 4. Contracts

```python
class Backend(Protocol):
    async def list_models(self) -> list[str]: ...
    async def chat(self, messages, model, *, options=None,
                   keep_alive=None) -> AsyncIterator[str]: ...
    async def aclose(self) -> None: ...
```

*Adding a backend* (llama.cpp server, vLLM, LM Studio) means implementing three methods; the
router, prompts and memory are untouched. `OllamaBackend` recreates its `httpx.AsyncClient`
when the running event loop changes, because the CLI uses `asyncio.run()` per command — a
subtle bug that `test_client_rebinds_across_event_loops` pins down.

`MemoryStore` exposes `remember/search/forget/list_memories/episodes/feedback/model_stats/
routing_log/export/import/wipe`. The retriever depends on this interface only, so a
Google-Drive-backed store (planned) can implement it without changing callers.

## 5. Data model

| Table | Purpose |
|---|---|
| `memories` | Durable knowledge (`preference`, `fact`, `snippet`, `rule`, `code`) + 1024-d vector + weight |
| `episodes` | Conversation log per session (redacted) |
| `feedback` | `rating`, correction text, task and model — the learning signal |
| `model_stats` | Beta-Bernoulli posterior per (task, model) + latency EMA |
| `routing_log` | One row per answer: task, language, difficulty, model, reason, outcome |
| `skills` | Learned procedures (planned to be written by the agent loop) |
| `meta` | Schema version, embedder identity for migration |

All writes pass through secret redaction when `privacy.redact_secrets` is on (default).

## 6. Retrieval and learning

* **Hybrid retrieval**: FTS5 bm25 (normalised to the best hit) + hashed-vector cosine, combined
  into a *relevance* score. Recency and weight only *boost* an already-relevant memory; they can
  never pull an unrelated memory into the prompt. This is why "we use pnpm" is injected for a
  question about npm but not for a question about quicksort.
* **Preferences** are captured only from declarative clauses; questions and conditionals are
  rejected (`test_preference_capture_is_conservative`).
* **Routing learns** through `model_stats`: once feedback exists, win rates add up to ±0.10 to a
  model's score. Hard constraints (fit, speed floor, explicit choice) always win.

## 7. Key decisions and trade-offs

| Decision | Why | Cost |
|---|---|---|
| Rules-first classifier instead of a model | Instant, explainable, no extra model in memory; covers common phrasing | Misses unusual prompts; a tiny classifier is **planned** as a tie-breaker and its label is logged |
| Registry as TOML data | Users add models without code; licenses are visible | Slightly more parsing code; schema must stay stable |
| Feature-hashing embeddings | Zero downloads, microseconds, deterministic | Purely lexical-ish recall; real embeddings (`nomic-embed-text`) are **planned** with automatic re-embedding |
| Bandwidth-based tok/s estimate | Useful before the first run | ±30 % error; replaced by measured `latency_ema` |
| One SQLite file per user | Trivial backup/sync/delete story | No concurrent multi-process writes (WAL covers typical use) |
| Demo backend | Lets tests and the CLI run with no model **without lying** | Must stay clearly labelled; the router never counts it as a real model |
| Loopback only | Privacy by construction | No remote inference fallback; remote use requires a tunnel the user owns |

## 8. Planned steps and how they plug in

* **Step 2 — code indexer.** Incremental file walker with content hashes → AST-aware chunks
  (Python `ast` first, tree-sitter optional) → new `code_chunks` table + FTS. Symbol and import
  extraction feeds the existence checks below. The retriever gains a `kinds=("code",)` lane.
* **Step 3 — verification.** A `verify.py` pipeline (parse → lint → type-check → run tests in a
  restricted subprocess with CPU/time limits) plus a self-repair loop capped at N rounds. It
  consumes `Candidate`s and writes confidence back to the answer and to `feedback`.
* **Step 4 — ensemble/judge.** Ask 2-3 models only when difficulty crosses a threshold; score each
  answer with step-3 checks + agreement; the judge model picks/rejects, never concatenates.
* **Step 5 — web knowledge.** Cached fetches from official docs, version detection from project
  manifests, provenance attached to every claim, and prompt-injection defences (content is data,
  never instructions).
* **Step 6 — feedback and LoRA.** Accept/edit/reject capture in the CLI and API; dataset export;
  QLoRA via PEFT/Unsloth when idle; adapter registry with automatic evaluation and rollback.
* **Step 7 — Google Drive.** OAuth with the token in the OS keychain, app-data folder only,
  encrypted snapshots; the store's export/import already produce the payload.
* **Step 8 — API/UI.** FastAPI on loopback with an OpenAI-compatible endpoint so existing editors
  work; SSE for the web UI; the VS Code extension reuses the same client.
* **Step 9 — eval suite.** 100+ tasks (HumanEval/MBPP-style + user-repo tasks) reported as pass
  rate, hallucinated-symbol rate, latency and RSS; CI fails on regression.
* **Step 10 — docs** grow with each step (`docs/`).

## 9. Testing strategy

* Unit tests for every pure function (classification, routing maths, hardware budgeting,
  redaction, retrieval gating).
* A deterministic `DemoBackend` so CLI tests exercise the full path without Ollama.
* A `fake_hw` fixture modelling a 16 GB laptop so routing decisions are reproducible.
* Failure-path tests: unreachable Ollama, unknown model, memory wipe, empty registry fallbacks.
* `pytest` runs in ~1.3 s, which keeps the "run tests at every step" loop honest.
