# DevMind

**A free, local-first coding assistant that learns your codebase — and stays fast on a laptop.**

DevMind runs open-weight models on hardware **you own**: a llama.cpp server on your PC, Ollama on a
desktop, or (planned) a 0.5B-3B model inside the phone app via WebLLM/wllama. It indexes your
repository, cites `file:line` for every claim about your code, remembers your conventions, and can
open a pull request on your own private repo for you to approve. No API keys. No cloud account.
No telemetry.

```
$ devmind index .
✓ shop: 214 indexed, 0 unchanged, 0 removed, 3120 chunks, 2680 symbols in 8.3s - complete

$ devmind ask --repo . "why does refund_order double-charge retried orders?"
◆ bug_fix/python → qwen2.5-coder-7b
· bug_fix/python difficulty 0.60 → llama.cpp (your model) (Q: 78/100, ~37 tok/s, 4.7 GB)
· context: 2 memory item(s), 4 code chunk(s) from shop
<streamed answer citing services/billing/refund.py:41-67>
```

> **Status: steps 1-2 complete, 130 tests.** Backend abstraction, llama.cpp + Ollama + demo
> backends, portable SQLite schema, incremental code indexer, cited retrieval, GitHub module
> (device login, encrypted token, clone/branch/PR) and a verification runner (local tests or
> GitHub Actions). The PWA, ensemble judge, web grounding, fine-tuning, Drive backup and the
> evaluation suite are **not built yet** — see the roadmap and "What is real today".

---

## What it does differently

| Principle | How DevMind enforces it |
|---|---|
| **Correct over clever** | Low temperature; prompts forbid invented APIs; code answers cite `file:line` from a real index; tests run before you see a "fix" (locally or in CI). |
| **Do exactly what was asked** | The system prompt bans unrequested refactors, renames and dependencies. |
| **100% free** | Open-weight models, open-source Python, GitHub Actions on your own repos, free device-flow login. No paid API path exists in the code. |
| **Local & private** | Loopback HTTP, one SQLite file, encrypted GitHub token, secret redaction before storage. |
| **Fast** | Worker-friendly limits, resumable indexing with hard time caps, small-model routing, streamed tokens. |
| **Focus** | Task + language detection for Python, JS/TS, Java, C/C++, C#, Go, Rust, PHP, Kotlin, Swift, SQL, Bash and more. |

## Two machines, one brain

```
 Phone / PWA (planned)                 Your PC (implemented)
 ┌──────────────────────┐              ┌──────────────────────────────┐
 │ chat UI, diffs       │  private     │ llama.cpp server (your GGUF) │
 │ on-device 0.5-3B     │  tunnel      │ DevMind core: routing,       │
 │ SQLite-WASM memory   │ ───────────▶ │ index, memory, GitHub, tests │
 └──────────────────────┘  Tailscale   └──────────────────────────────┘
```

* **Phone only** → run a small model in the browser (step 8). Good for autocomplete, explanations
  and small fixes; not for hard bugs.
* **Phone + home PC** → the phone is the interface, the PC does inference over a private tunnel.
  This is how "close to the big assistants, still free" is realistic.
* The core is already backend-agnostic: `llamacpp`, `ollama` and `demo` implement the same
  `Backend` protocol, and the PWA will implement the same four methods in JavaScript.

## Install

Requirements: Python 3.11+, git. A model server is needed for answers (see below).

```bash
git clone <your-fork> devmind && cd devmind
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/devmind doctor
```

### Backend A — llama.cpp on your PC (default)

```bash
llama-server -m qwen2.5-coder-7b-instruct-q4_k_m.gguf \
    --host 127.0.0.1 --port 8080 -c 16384 -ngl 99
```

```toml
# ~/.devmind/config.toml
[backend]
kind = "llamacpp"

[llamacpp]
base_url = "http://127.0.0.1:8080"
model = "qwen2.5-coder-7b"      # a label; the server serves one model
model_quality = 78              # router prior, 0-100
```

### Backend B — Ollama on a desktop

```bash
ollama serve &
devmind pull qwen2.5-coder:7b
```

```toml
[backend]
kind = "ollama"
```

No backend yet? Every command still works; answers are clearly labelled demo output:

```bash
devmind --demo ask "explain this traceback"
```

## Commands

| Command | What it does |
|---|---|
| `devmind doctor` | Hardware, backend health, registry, index, memory and privacy checks |
| `devmind models` | Registry + what fits this machine, with download hints |
| `devmind ask "…" [--repo PATH] [-m MODEL] [--json]` | Streamed, cited answer; `--repo` indexes + cites the project |
| `devmind chat` | Interactive session (`/route`, `/memory`, `/teach`, `/exit`) |
| `devmind index [PATH] [--force] [--json]` | Incremental, resumable code index |
| `devmind search "…" [--repo R] [-k N]` | Code search with `file:line` |
| `devmind repos` | Indexed repositories and sizes |
| `devmind verify [PATH]` | Run the project's tests locally |
| `devmind verify --github OWNER/REPO --ref BRANCH [--workflow test.yml]` | Trigger/read GitHub Actions |
| `devmind github login` | Device-flow login (or `--token` for a fine-grained PAT) |
| `devmind github status\|repos\|clone` | Account, repositories, private clone |
| `devmind github pr --repo … --branch … --title … --path CLONE` | Push a branch and open a PR (never to `main`) |
| `devmind teach "we use pnpm, never npm"` | Store a durable preference/fact |
| `devmind memory [--search Q]` / `forget --all` | Inspect / delete what is stored |
| `devmind pull MODEL` | Ollama model download |
| `devmind config [--init\|--path]`, `devmind stats` | Settings, decisions, statistics |

`--home DIR` and `--demo` work before or after the subcommand.

## Code index and memory

* **Incremental**: files are sha256-hashed; unchanged files are never re-read or re-embedded.
  Deleted files are pruned. A run that hits `index.max_seconds` stops, keeps its progress and
  resumes next time — no full re-index, no frozen UI.
* **Bounded**: `max_files`, `max_file_bytes`, chunk character budgets, excluded directories
  (`.git`, `node_modules`, `dist`, venvs) and secret-looking paths.
* **Cited**: Python is chunked with `ast` (functions, classes, methods, exact line ranges); other
  languages use declaration regexes plus bounded windows. Retrieval returns
  `path:start-end` citations `[C1]`, never a guessed path.
* **Scoped**: an indexed repository is used automatically only when your shell is inside it, or
  when you pass `--repo PATH`. Code from an unrelated project is never injected into a prompt.
* **Memory**: preferences, facts, snippets, episodes, feedback and routing decisions in SQLite.
  Hybrid FTS5 + hashed-vector retrieval with a relevance gate: an unrelated preference is not
  injected just because it is recent.
* **Secret redaction** before anything is stored (API keys, tokens, private keys, URL credentials).

## GitHub, without an API key

Device-flow login against your own account (`repo read:user`), token encrypted with scrypt →
Fernet in a `0600` file. Git authentication is passed through git's **environment config**, never
argv or the URL, so it cannot leak via `ps`.

`devmind github pr` refuses to push to `main`, `master`, `trunk`, `develop` or the repository's
default branch — it always pushes a new branch and opens a pull request for your review.
See [docs/github.md](docs/github.md) for the one-time OAuth App setup (free).

## Verification before presentation

`devmind verify .` detects the project's own test command (pytest, `npm test`, `cargo test`,
`go test`, `make test`), runs it with a timeout and a scrubbed environment (no tokens, `CI=1`) and
shows the tail of the output. When the suite only runs in CI, `devmind verify --github …` dispatches
the workflow on your branch and reports the conclusion plus the failed job/step.

An honest limit: green tests mean the project's checks passed, not that the code is correct, and
local checks are not yet a security sandbox (step 3).

## Privacy

Nothing leaves your machine except traffic to your own inference server (loopback or your private
tunnel) and the GitHub API when you ask for it. Token storage is encrypted; memory is redacted;
`devmind forget --all` wipes memories, episodes, feedback, routing log and statistics. See
[docs/privacy.md](docs/privacy.md) for the exact file list and the encryption status.

## What is real today

| Step | Deliverable | State |
|---|---|---|
| 1 | Config, hardware detection, registry, router, Ollama, CLI, memory | **Done** |
| 2 | Backend interface + llama.cpp, portable SQLite schema, indexer, cited retrieval, GitHub, verification | **Done, 130 tests** |
| 3 | Full verification pipeline (sandbox, self-repair loop) | Not started |
| 4 | Ensemble + judge, answer confidence | Not started |
| 5 | Web knowledge layer (cache, version detection, injection defence) | Not started |
| 6 | Feedback capture + LoRA with auto-rollback | Feedback storage done; training not started |
| 7 | Google Drive backend with encryption | Not started |
| 8 | PWA + local API (on-device WebLLM backend, workers, offline) | Not started |
| 9 | Evaluation suite (100+ tasks, regression gate) | Not started |
| 10 | Docs | Started (`docs/`) |

### Honest deviations

* **PWA not built yet.** Step 2 ships the groundwork it needs: a backend-agnostic core and a
  portable SQLite schema (`devmind/storage/schema.sql`) with no WAL/FTS5 hard dependency. The
  browser app is step 8.
* **On-device inference is a contract, not code.** The `Backend` protocol includes it; the
  WebLLM/wllama implementation lives in the PWA (JavaScript), so there is nothing to fake in Python.
* **`models.toml` instead of YAML**: `tomllib` is stdlib. Same schema semantics.
* **Hashing embeddings first**: zero-download, microsecond retrieval. Real embeddings
  (`nomic-embed-text`) come with the PWA work; the schema/interface already supports swapping.
* **Local test execution is not sandboxed yet.** Timeouts and a scrubbed environment only; real
  isolation is step 3.

## Develop

```bash
make install    # editable install + dev extras
make test       # 130 tests
make lint       # ruff check + format check
make demo       # doctor + models + a routed demo answer, no model needed
```

```
devmind/
  cli.py            commands, streaming, routing output
  config.py         typed TOML config + env overrides
  hardware.py       RAM/VRAM/bandwidth detection, budgets, tok/s estimates
  registry.py       model registry loader (+ models.toml)
  router.py         task/language/difficulty classification + model choice
  backends/         base contract, llama.cpp, Ollama, demo
  chunkers.py       Python AST + generic declaration chunkers
  indexer.py        incremental, resumable, bounded code indexing
  code.py           Chunk / Symbol / CodeHit / IndexReport types
  memory/           embed, SQLite store, retriever (memory + cited code)
  prompts.py        hard rules, task guidance, prompt assembly
  security.py       secret redaction
  github.py         device flow, encrypted token, API client, git workspace, PR flow
  verify.py         local test runner + GitHub Actions verifier
  storage/schema.sql  portable schema shared with the PWA
tests/              130 tests · docs/ · ARCHITECTURE.md
```

## License

MIT for DevMind itself. Models keep their own licenses — see [docs/models.md](docs/models.md) and
the `license` / `training_ok` fields in the registry.
