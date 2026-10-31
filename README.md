# DevMind

**A free, local-first coding assistant that learns your codebase — and stays fast on a laptop.**

DevMind runs open-weight models on hardware **you own**: a llama.cpp server on your PC, Ollama on a
desktop, or (planned) a 0.5B-3B model inside the phone app via WebLLM/wllama. It indexes your
repository, cites `file:line` for every claim about your code, remembers your conventions, and can
open a pull request on your own private repo for you to approve. No API keys. No cloud account.
No telemetry.

```
$ devmind serve --open           # or double-click scripts/devmind.command / .bat
DevMind 0.6.0 on http://127.0.0.1:8787

$ devmind index .
✓ shop: 214 indexed, 0 unchanged, 0 removed, 3120 chunks, 2680 symbols in 8.3s - complete

$ devmind ask --repo . "why does refund_order double-charge retried orders?"
◆ bug_fix/python → qwen2.5-coder-7b
· bug_fix/python difficulty 0.60 → llama.cpp (your model) (Q: 78/100, ~37 tok/s, 4.7 GB)
· context: 2 memory item(s), 4 code chunk(s) from shop
<streamed answer citing services/billing/refund.py:41-67>
```

> **Status: steps 1-4 complete (step 4 in progress), 214 tests.** Backend abstraction, llama.cpp + Ollama + demo
> backends, portable SQLite schema, incremental code indexer, cited retrieval, GitHub module
> (device login, encrypted token, clone/branch/PR), a limit-enforcing sandbox, a security scanner,
> a self-repair loop that only accepts patches the project's own checks pass, language packs and a
> confidence score built from evidence, a semantic answer cache, a difficulty-aware two-tier pipeline
> with self-consistency sampling, and a local web app for PC and phone (PWA-installable).
> Web grounding, LoRA fine-tuning, Drive backup, on-device WebLLM and the evaluation suite are
> **not built yet** — see the roadmap and "What is real today".

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

## Use it like an app (PC and phone)

```bash
devmind serve --open                  # laptop: http://127.0.0.1:8787
devmind serve --host 0.0.0.0          # phone on the same Wi-Fi; prints a token URL
```

Or double-click **`START-DEVMIND.bat`** (Windows) or **`START-DEVMIND.command`** (macOS) at the top of
the folder — Linux users run `bash scripts/devmind.sh`. The first run creates `.venv`, installs the
dependencies, starts the server and opens the browser; the window must stay open. If the configured
model address has nothing on it, DevMind looks for a model on 8080-8090, Ollama's 11434 and LM
Studio's 1234 and says which one it used — or the app's **Find a model** button tells you where to
start one. If nothing happens,
[docs/web.md](docs/web.md#if-double-click-does-nothing) lists the cause and the fix for each symptom.

The same UI works on both: streamed answers with citations and confidence, a project bar that
indexes any folder on the PC or clones a git URL into `~/.devmind/repos`, **Analyze** (files, checks, security scan) and **Fix** (propose a diff,
verify it on a copy, apply only when it passes). On the phone, open the printed URL once and choose
*Add to Home Screen* — the manifest and service worker are included. See
[docs/web.md](docs/web.md) for phone setup, tunnels and the HTTP API.

## Commands

| Command | What it does |
|---|---|
| `devmind doctor` | Hardware, backend health, registry, index, memory and privacy checks |
| `devmind ports [--from 8080] [--to 8090]` | Show what is listening: llama.cpp, another OpenAI server, another app, or free |
| `devmind models` | Registry + what fits this machine, with download hints |
| `devmind ask "…" [--repo PATH] [-m MODEL] [--json] [--show-prompt]` | Streamed, cited answer; `--show-prompt` prints the exact messages sent |
| `devmind chat` | Interactive session (`/route`, `/memory`, `/teach`, `/exit`) |
| `devmind index [PATH] [--force] [--json]` | Incremental, resumable code index |
| `devmind search "…" [--repo R] [-k N]` | Code search with `file:line` |
| `devmind repos` | Indexed repositories and sizes |
| `devmind serve [--host 0.0.0.0] [--open]` | Local web app for PC and phone (PWA) |
| `devmind check [PATH]` | Run tests/lint/build under CPU/memory/time limits + security scan |
| `devmind fix "…" [--apply]` | Ask for a unified diff, verify it on a copy, retry up to 3 rounds |
| `devmind scan [PATH]` | Security scan (secrets, injection, unsafe deserialisation, XSS) |
| `devmind languages [--root PATH]` | Which language packs apply, with commands and docs links |
| `devmind verify [PATH]` | Run the project's tests locally (no limits) |
| `devmind verify --github OWNER/REPO --ref BRANCH [--workflow test.yml]` | Trigger/read GitHub Actions |
| `devmind github login` | Device-flow login (or `--token` for a fine-grained PAT) |
| `devmind github status\|repos\|clone` | Account, repositories, private clone |
| `devmind github pr --repo … --branch … --title … --path CLONE` | Push a branch and open a PR (never to `main`) |
| `devmind teach "we use pnpm, never npm" [--kind business]` | Store a durable preference/fact |
| `devmind brief "we sell furniture in Chennai"` / `brief` / `brief --clear` | Business context that is added to every prompt |
| `devmind new website\|react\|api\|mobile PATH [--force]` | Working starter project (no empty folders) |
| `devmind memory [--search Q]` / `forget --all` | Inspect / delete what is stored |
| `devmind pull MODEL` | Ollama model download |
| `devmind config [--init\|--path]`, `devmind stats` | Settings, decisions, statistics |

`--home DIR` and `--demo` work before or after the subcommand.

## Free online models (opt-in)

DevMind runs local by default and ships **no provider keys**. If your PC cannot hold a strong model,
you can point it at a free tier you signed up for — Groq, OpenRouter `:free`, Google AI Studio's
OpenAI-compatible endpoint, or LM Studio on another machine. It needs `[remote] enabled = true`,
`consent = true` and your key in `DEVMIND_REMOTE_KEY`; `offline = true` and missing consent disable it
entirely. Read [docs/remote.md](docs/remote.md) for the config, what leaves the machine, and why
"one model" means routing + scoring rather than a merged model.

```toml
[backend]
kind = "remote"

[remote]
enabled = true
consent = true
provider = "Groq free tier"
base_url = "https://api.groq.com/openai/v1"
models = ["llama-3.3-70b-versatile"]
```

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

```bash
$ devmind check .
limits: time 300s · cpu 300s · memory 2048MB · file writes 64MB · processes 64 · network: not isolated (unshare unavailable) · filesystem: throwaway copy
✗ pytest (233 ms)
E       assert -1 == 5
security scan: 1 high
  HIGH pkg/calc.py:2 SQL built by interpolating a value (sql.interpolation)

verification: FAIL · 1 check(s), 1 finding(s), 235 ms
```

* `devmind check` runs the project's own commands under `rlimit`s (CPU, memory, file size, processes),
  with host secrets stripped from the environment and an optional empty network namespace.
* `devmind fix "…"` asks for a **unified diff**, applies it to a **throwaway copy**, rejects it if the
  security scan finds something high-severity, runs the checks, and feeds real failing output back to
  the model — at most three rounds, then an honest failure report. `--apply` writes the winning diff
  and re-runs the checks on your tree.
* `devmind scan` covers the mistakes assistants actually make: committed keys, `eval`/`exec`,
  `shell=True`, `pickle`, `yaml.load`, SQL built by interpolation, `innerHTML`, path traversal.
* Every answer carries a confidence score computed from citations, executed checks, repair rounds and
  stray links — never from the model's self-report.

Green tests mean the project's checks passed, not that the code is correct; a weak suite still passes.
Details, limits and suppression syntax: [docs/verification.md](docs/verification.md).
Language packs: [docs/languages.md](docs/languages.md). Command permissions:
[docs/permissions.md](docs/permissions.md).

## Privacy

Nothing leaves your machine except traffic to your own inference server (loopback or your private
tunnel) and the GitHub API when you ask for it. Token storage is encrypted; memory is redacted;
`devmind forget --all` wipes memories, episodes, feedback, routing log and statistics. See
[docs/privacy.md](docs/privacy.md) for the exact file list and the encryption status.

## What is real today

| Step | Deliverable | State |
|---|---|---|
| 1 | Config, hardware detection, registry, router, Ollama, CLI, memory | **Done** |
| 2 | Backend interface + llama.cpp, portable SQLite schema, indexer, cited retrieval, GitHub, verification | **Done** |
| 3 | Sandbox (limits, scrubbed env, optional netns), security scan, self-repair loop, confidence, language packs | **Done** |
| 4 | Semantic cache, two-tier answer, plan-then-solve, 2-3 samples scored by checks, judge for ties | **Done for the web app + CLI cache; measure next** |
| 8 | Local web UI + PWA (installable, phone-friendly), phone ↔ PC | **Partially done**: web app + PWA shell, opt-in OpenAI-compatible *client*; on-device WebLLM and an endpoint for editors are not |
| 5 | Web knowledge layer (cache, version detection, injection defence) | Not started |
| 6 | Feedback capture + LoRA with auto-rollback | Feedback storage done; training not started |
| 7 | Google Drive backend with encryption | Not started |
| 9 | Evaluation suite (100+ tasks, regression gate) | Not started |
| 10 | Docs | Started (`docs/`) |
| 11 | Business brief, prompt inspector, project starters, opt-in remote provider | **Done, 239 tests**; prompt *optimizer* is the inspector today |

### Honest deviations

* **The PWA is a web app, not a native app.** It installs to the home screen via the manifest, but it
  still needs the PC's server for answers. On-device inference in the browser is step 8.
* **On-device inference is a contract, not code.** The `Backend` protocol includes it; the
  WebLLM/wllama implementation lives in the PWA (JavaScript), so there is nothing to fake in Python.
* **`models.toml` instead of YAML**: `tomllib` is stdlib. Same schema semantics.
* **Hashing embeddings first**: zero-download, microsecond retrieval. Real embeddings
  (`nomic-embed-text`) come with the PWA work; the schema/interface already supports swapping.
* **The sandbox is `rlimit`s, not a container.** CPU/memory/file/process limits, an environment
  allow-list and an optional empty network namespace (`unshare -n`). There is no seccomp filter and no
  user namespace, so it stops accidents, not a determined attacker. Stated the same way in the CLI.
* **Self-repair needs a model that emits unified diffs.** Small models often do not; that round is
  recorded as "no diff" instead of being retried forever. Demo mode refuses to propose patches.
* **The security scanner is ~20 regex rules.** Useful, explainable, and false-positive-prone by
  design; it does not replace a real SAST tool. High-severity findings block an automatic patch.
* **The ensemble is same-model sampling on a llama.cpp server.** A llama.cpp server serves one GGUF,
  so "3 candidates" means three samples at different temperatures, not three different models. With
  Ollama, distinct fast/strong/judge models are used when they are installed. With `[remote]` enabled,
  the configured cloud model is registered for the strong/judge roles.
* **The CLI streams one answer.** The cache is used by `devmind ask`; plan/sampling/judge run behind
  the web app (and are tested directly). Wiring `--deep` into the CLI is next.
* **The PWA has no on-device model.** It caches its shell offline, but answers come from the PC.
  WebLLM/wllama in a worker is still step 8.
* **No web grounding yet** (step 5), so "current" library behaviour is answered from the model and
  the project only — and the prompt says so.
* **No provider keys, no scraping, no merged model.** `[remote]` is a consent-gated client for an
  endpoint you own the key for. DevMind cannot sign you into a free site or combine providers into
  one set of weights; it routes between them and scores their answers.
* **The prompt inspector shows, it does not rewrite.** `ask --show-prompt` prints the exact system
  and user messages (profile, packs, sizes); an automatic "rewrite my prompt better" step is not
  implemented, and the UI has no button for it yet.
* **No system control.** DevMind runs the checks a project defines and writes a verified diff when you
  pass `--apply`. Arbitrary shell execution needs the approval-gated design in
  [docs/permissions.md](docs/permissions.md); it is not implemented.

## Develop

```bash
make install    # editable install + dev extras
make test       # 214 tests
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
  verify.py         check detection, local runner, GitHub Actions verifier
  sandbox.py        throwaway copies + rlimits + scrubbed env + optional netns
  security_scan.py  secrets, injection, deserialisation, XSS rules + allow directives
  repair.py         diff extraction, git apply, verified self-repair loop
  confidence.py     evidence-based confidence scoring
  difficulty.py     trivial/normal/hard classifier with time budgets
  languages.py      language packs (commands, docs, notes, pitfalls)
  pipeline.py       semantic cache, quick answer, plan, sampled candidates, judge
  server.py         FastAPI app + SSE + PWA assets (devmind serve)
  web/              index.html, app.css, app.js, manifest, service worker
  storage/schema.sql  portable schema shared with the PWA
tests/              214 tests · docs/ · ARCHITECTURE.md
```

## License

MIT for DevMind itself. Models keep their own licenses — see [docs/models.md](docs/models.md) and
the `license` / `training_ok` fields in the registry.
