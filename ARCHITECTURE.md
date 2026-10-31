# DevMind architecture

> Scope: what exists today (steps 1-2), the contracts later steps plug into, and the reasoning
> behind the choices. Anything not implemented is labelled **planned**.

## 1. Goals, ranked

1. **Correct over clever.** A wrong-but-confident answer is the failure mode we optimise against.
2. **Do exactly what was asked.** No drive-by refactors, renames or dependencies.
3. **Free.** Open weights on hardware you own, open-source Python, free GitHub device login,
   Actions on your own repos.
4. **Local and private.** Loopback/tunnel only, one SQLite file, encrypted GitHub token, no telemetry.
5. **Fast and mobile-friendly.** Bounded, resumable background work; the phone is a thin client.
6. **Language-agnostic.** Task/language detection and chunkers are not Python-only.

Non-goals: beating frontier cloud models at everything; training foundation models; being a
general chat assistant.

## 2. Component map

```
                    ┌─────────────────────────────────────────────┐
   CLI / (planned)  │ cli.py : commands, streaming, JSON output   │
   API / PWA        └───┬────────────┬────────────┬───────────┬───┘
                        │            │            │           │
              ┌─────────▼──┐  ┌──────▼──────┐ ┌───▼─────┐ ┌───▼────────┐
              │ router.py  │  │ prompts.py  │ │ indexer │ │ github.py  │
              │ classify + │  │ hard rules  │ │ +chunker│ │ device flow│
              │ pick model │  │ + task guide│ │         │ │ git + PR   │
              └──┬──────┬──┘  └──────┬──────┘ └───┬─────┘ └───┬────────┘
                 │      │            │            │           │
      ┌──────────▼┐  ┌──▼────────┐ ┌─▼──────────┐ │      ┌────▼─────┐
      │registry.py│  │hardware.py│ │ retriever  │ │      │ verify.py│
      │models.toml│  │budgets    │ │ memory+code│ │      │ local+CI │
      └───────────┘  └───────────┘ └─────┬──────┘ │      └──────────┘
                                          │        │
                              ┌───────────▼────────▼───────┐
                              │ memory/store.py            │
                              │ SQLite (portable schema)   │
                              │ memories, episodes,        │
                              │ feedback, stats, routing,  │
                              │ code files/chunks/symbols  │
                              └───────────┬────────────────┘
                                          │
                              ┌───────────▼────────────────┐
                              │ backends/                  │
                              │  llamacpp (default)        │
                              │  ollama (desktop)          │
                              │  demo (tests/--demo)       │
                              │  PWA WebLLM (planned, JS)  │
                              └────────────────────────────┘
```

## 3. Request lifecycle (`devmind ask --repo . "…"`)

1. Load config, detect hardware, load registry (+ the configured llama.cpp model), open SQLite.
2. Incrementally index the repo (hash-based; usually milliseconds when nothing changed).
3. Classify the prompt: task, language, difficulty, with audit signals.
4. Route: filter by installed/fit/speed floor/caps, score by quality, difficulty-weighted speed and
   learned win rate. A single-model llama.cpp server is used directly.
5. Retrieve relevance-gated memory and cited code chunks (`[M1]`, `[C1] path:lines`).
6. Build the prompt: hard rules, task guidance, output format, memory, code.
7. Stream tokens, measuring first-token and total time.
8. Record the episode and routing decision; run verification separately (`devmind verify`).

## 4. Contracts

```python
class Backend(Protocol):
    name: str
    async def list_models(self) -> list[str]: ...
    async def health(self) -> Health: ...
    async def chat(self, messages, model, *, options=None,
                   keep_alive=None) -> AsyncIterator[str]: ...
    async def aclose(self) -> None: ...
```

*Adding a backend* (LM Studio, vLLM, the PWA bridge) means implementing four methods. The demo
backend shows how little is required. `LoopClient` recreates the HTTP client when the event loop
changes, because the CLI uses `asyncio.run()` per command.

```python
class MemoryStore:  # storage contract
    remember / search / forget / list_memories
    record_episode / add_feedback / model_stats
    log_route / recent_routes
    code_files / replace_code_file / search_code / list_repos / index_state
    export_json / import_json / wipe
```

The retriever depends on this interface only, so a Drive-backed or WASM-backed store can implement
it without touching callers.

## 5. Data model (portable)

One schema file: [`devmind/storage/schema.sql`](devmind/storage/schema.sql). No `AUTOINCREMENT`,
`STRICT`, generated columns or unguarded WAL — SQLite-WASM in the PWA opens the same file.

| Table | Purpose |
|---|---|
| `memories` | preferences, facts, snippets, rules + 1024-d vector + weight |
| `episodes` | conversation log per session (redacted) |
| `feedback` | rating + correction, the learning signal |
| `model_stats` | Beta-Bernoulli posterior per (task, model) + latency EMA |
| `routing_log` | why each answer used the model it did |
| `skills` | learned procedures (agent loop, planned) |
| `code_files` | path + sha per repo (incremental indexing) |
| `code_chunks` | chunk text, line range, symbol, vector, sha |
| `code_symbols` | name/kind/line for existence checks |
| `index_state` | repo root + completion flag (resumability) |
| `meta` | schema/embedder version |

FTS5 (`memory_fts`, `code_fts`) is created opportunistically; without it retrieval uses vectors only.

## 6. Indexing and retrieval

* **Incremental**: sha256 per file; unchanged files never re-read; deleted files pruned only when
  the walk completed.
* **Resumable**: time/file caps stop the run, persist progress and report `complete=False`.
* **Bounded**: size caps, excluded dirs, secret-looking paths, chunk char/line budgets.
* **Chunking**: Python `ast` with exact line ranges; generic declaration regexes elsewhere;
  bounded-window fallback. One contract (`chunk_file`) so tree-sitter can replace the fallback later.
* **Retrieval**: relevance = 0.62·vector + 0.38·bm25 (normalised to the best hit), with a bonus when
  the path or symbol matches. Recency/weight only boost relevance; they cannot inject an unrelated
  chunk. Code citations are `path:start-end`, never guessed.

## 7. GitHub and verification

* Device flow → token encrypted with scrypt-derived Fernet key in a `0600` file.
* Git auth via environment config, not argv/URL; error output scrubbed.
* `ensure_safe_branch()` blocks the default branch for every push; PR head==base is rejected.
* Verification: local project test command with timeout + scrubbed env, or Actions dispatch + poll
  with failed-step names. **No sandbox yet** (step 3).

## 8. Key decisions and trade-offs

| Decision | Why | Cost |
|---|---|---|
| Rules-first routing | Instant, explainable, no extra model | Misses unusual phrasing; logs enable a tiny classifier later |
| llama.cpp as default backend | One GGUF, one server, works on any OS, can be tunnelled | User must download GGUF + start a server |
| Registry as TOML data | Add models without code; licenses visible | Schema must stay stable |
| Portable SQLite schema | Same DB on phone and PC | Sacrifices FTS5 guarantees and SQLite conveniences |
| Feature-hashing embeddings | Zero downloads, microseconds | Lexical-ish recall; real embeddings planned |
| One model per llama.cpp server | No fake routing to models that are not loaded | Quality is whatever you started |
| Local tests without sandbox | Works today with zero setup | Not safe for untrusted repos; step 3 fixes |
| No silent network calls | Privacy by construction | Web grounding (step 5) must be explicit and cached |

## 9. Roadmap

* **Step 3 - verification pipeline.** `sandbox.py` (container or restricted subprocess, CPU/mem/time
  limits, network off), self-repair loop capped at N rounds, confidence scoring.
* **Step 4 - ensemble/judge.** 2-3 candidates on hard tasks, scored by step-3 checks + agreement;
  judge picks, never concatenates.
* **Step 5 - web knowledge.** Cached official docs, version detection from manifests, provenance,
  prompt-injection defences.
* **Step 6 - feedback + LoRA.** Accept/edit/reject capture, dataset export, QLoRA when idle,
  adapter registry with evaluation and rollback.
* **Step 7 - Drive + encryption.** OAuth, app-data folder, encrypted snapshots, passphrase-derived
  keys for memory (not only the GitHub token).
* **Step 8 - PWA + API.** FastAPI on loopback, SSE, service worker, WebLLM/wllama in a worker,
  OpenAI-compatible endpoint for editors, VS Code extension.
* **Step 9 - evaluation.** 100+ tasks (HumanEval/MBPP-style + repo tasks), pass rate,
  hallucinated-symbol rate, latency, RSS; CI regression gate.
* **Step 10 - docs** grow with each step.

## 10. Testing strategy

* 130 tests in ~3.8 s: config, hardware budgeting, registry overrides, routing maths, chunkers,
  incremental/resumable indexing, retrieval gating, prompts, redaction, backends (MockTransport),
  GitHub flows (local bare repo + mocked API), verification (local + fake Actions), CLI paths.
* Failure paths are first-class: unreachable servers, unknown models, wrong passphrase, protected
  branches, timeouts, empty checks.
* `ruff check` + `ruff format --check` are clean; both are part of `make lint`.
