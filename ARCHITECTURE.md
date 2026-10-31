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
      └───────────┘  └───────────┘ └─────┬──────┘ │      └────┬─────┘
                                          │        │           │
                        ┌─────────────────┴────────┴───────────┴────┐
                        │  quality gates: sandbox.py (rlimits,      │
                        │  scrubbed env, optional netns) →          │
                        │  security_scan.py → repair.py             │
                        │  (diff apply on a copy, ≤3 rounds) →      │
                        │  confidence.py · difficulty.py ·          │
                        │  languages.py (packs, commands, docs)     │
                        └─────────────────┬─────────────────────────┘
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
8. Record the episode and routing decision.
9. Score the answer with `confidence.assess()` from the citations and any executed checks, and show
   the reasons (never a model self-report).

## 3b. Repair lifecycle (`devmind fix "…"`)

1. Detect the project's checks; if there are none, stop and say so.
2. Retrieve cited code/memory context for the prompt.
3. Ask the model for **one unified diff** (or `CANNOT FIX: reason`).
4. `extract_diff` → `apply_diff` on a throwaway copy (`.git`, deps, `.env*` and large files excluded).
5. `security_scan.scan_diff` on added lines; high-severity findings reject the round before execution.
6. Run the checks on the copy under `rlimits`; on failure, send the real output back and repeat
   (max 3, hard-capped at 5).
7. Report `ok`, the winning diff, per-round evidence and confidence. `--apply` writes the diff and
   re-runs the checks on the real tree; a failing re-run is reported, never hidden.

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
  with failed-step names.
* Sandboxing (`sandbox.py`): `RLIMIT_CPU/AS/DATA/FSIZE/NPROC/CORE`, a process group killed on
  timeout, environment allow-list, fresh `HOME`/`TMPDIR`, and `unshare -n` when the kernel permits.
  Reported as capabilities, never assumed.
* Self-repair (`repair.py`): unified diffs only, applied to copies, security-gated, verified by the
  project's own checks, at most N rounds, honest failure text afterwards.

## 8. Key decisions and trade-offs

| Decision | Why | Cost |
|---|---|---|
| Rules-first routing | Instant, explainable, no extra model | Misses unusual phrasing; logs enable a tiny classifier later |
| llama.cpp as default backend | One GGUF, one server, works on any OS, can be tunnelled | User must download GGUF + start a server |
| Registry as TOML data | Add models without code; licenses visible | Schema must stay stable |
| Portable SQLite schema | Same DB on phone and PC | Sacrifices FTS5 guarantees and SQLite conveniences |
| Feature-hashing embeddings | Zero downloads, microseconds | Lexical-ish recall; real embeddings planned |
| One model per llama.cpp server | No fake routing to models that are not loaded | Quality is whatever you started |
| rlimits sandbox, not a container | Works on macOS/Linux/Windows with no daemon; zero setup | No seccomp/user-namespace isolation; network isolation only where `unshare` works |
| Diffs, not whole files, for repair | Small prompts, reviewable patches, `git apply` gives a hard failure | Weak models struggle to emit correct unified diffs |
| Evidence-based confidence | Auditable, reproducible, no model self-report | Coarse: cannot tell a meaningful test from a trivial one |
| Security scan is heuristics | Fast, explainable, no extra dependency | False positives; suppression comments needed |
| Checks are the project's own | No invented test commands | No checks means no verification, and it says so |
| No silent network calls | Privacy by construction | Web grounding (step 5) must be explicit and cached |

## 9. Roadmap

* **Step 3 - done.** `sandbox.py`, `security_scan.py`, `repair.py`, `confidence.py`, `difficulty.py`,
  `languages.py`; `devmind check|fix|scan|languages`.
* **Step 4 - in progress.** Difficulty classification is live (it gates repair/verify budgets); the
  ensemble (2-3 candidates on hard tasks, scored by checks, judge only for ties, never concatenation),
  two-tier quick-then-strong answering and the reasoning pipeline are next.
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

* 189 tests in ~5 s: config, hardware budgeting, registry overrides, routing maths, chunkers,
  incremental/resumable indexing, retrieval gating, prompts, redaction, backends (MockTransport),
  GitHub flows (local bare repo + mocked API), verification (local + fake Actions), CLI paths.
* Step-3 tests are behavioural, not mock-heavy: the sandbox is checked by actually hitting a timeout,
  a memory cap and an env scrub; repair is checked by applying real diffs to real failing projects
  (including a security-blocked patch and a two-round recovery); the scanner is checked against
  deliberate vulnerabilities and false positives.
* Failure paths are first-class: unreachable servers, unknown models, wrong passphrase, protected
  branches, timeouts, empty checks.
* `ruff check` + `ruff format --check` are clean; both are part of `make lint`.
