# DevMind

**A free, local-first coding assistant that learns your codebase — and stays fast on a laptop.**

DevMind runs open-weight models through [Ollama](https://ollama.com) on your own machine,
remembers your project's conventions, routes each request to the model that can actually
handle it, and shows you *why* it answered the way it did. No API keys. No cloud. No telemetry.

```
$ devmind ask "add exponential backoff to this requests call"
◆ bug_fix/python → qwen2.5-coder:7b
· bug_fix/python difficulty 0.60 → Qwen2.5-Coder 7B (Q: 78/100, ~37 tok/s, 4.7 GB; meets quality floor)
· memory: 2 item(s) injected (preference, fact)
<streaming answer>
```

> **Status: step 1 of 10 is complete and tested.** This repo currently ships the config
> system, hardware detector, model registry, router, Ollama integration, memory store,
> and CLI. Later steps (code indexer, verification, ensemble judge, web grounding,
> fine-tuning, Google Drive, API/UI, eval suite) are tracked in the roadmap below and are
> **not** implemented yet. Nothing here pretends to be finished — see
> [What is real today](#what-is-real-today).

---

## Why it is different

| Principle | How DevMind enforces it |
|---|---|
| **Correct over clever** | Low temperature by default; prompts forbid invented APIs and require explicit uncertainty; step 3 adds executable verification. |
| **Do exactly what was asked** | The system prompt bans unrequested refactors, renames and dependencies. |
| **100% free** | Only open-weight models run through Ollama, open-source libraries, no paid code path anywhere. |
| **Local & private** | Loopback HTTP only; memory is one SQLite file in `~/.devmind`; `devmind forget --all` erases it. |
| **Fast** | Model choice is bandwidth-aware; tiny models for completion, bigger only when difficulty justifies it; streaming tokens. |
| **Focus** | Language detection + task classification for Python, JS/TS, Java, C/C++, C#, Go, Rust, PHP, Kotlin, Swift, SQL, Bash and more. |

## Install

Requirements: Python 3.11+, [Ollama](https://ollama.com/download), 8 GB RAM (16 GB recommended).

```bash
git clone <your-fork> devmind && cd devmind
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/devmind doctor          # what hardware do I have, what should I run?
.venv/bin/devmind models          # registry + what fits this machine, with download hints
```

Pull the model DevMind recommends (it tells you the exact command), for example:

```bash
ollama serve &
devmind pull qwen2.5-coder:7b
```

Then:

```bash
devmind ask "write a Python dataclass for a retry policy with jitter"
devmind ask -m qwen2.5-coder:14b "refactor this parser and keep behaviour identical"
devmind chat
```

No model installed yet? You can still explore the pipeline — every answer is clearly labelled:

```bash
devmind --demo ask "explain this traceback"
```

## Commands

| Command | What it does |
|---|---|
| `devmind doctor` | Hardware, Ollama, registry, memory and privacy checks with next steps |
| `devmind models [--role R] [--installed] [--json]` | Registered models with estimated tok/s, fit verdict, recommendation |
| `devmind ask "…" [-m MODEL] [--json]` | One routed answer, streamed; `--json` returns route, memory and timings |
| `devmind chat` | Interactive session with `/route`, `/memory`, `/teach`, `/models`, `/exit` |
| `devmind teach "we use pnpm, never npm"` | Store a durable preference/fact/snippet |
| `devmind memory [--search Q] [--kind K]` | Inspect what DevMind remembers |
| `devmind forget <id> \| --all` | Delete one memory or everything |
| `devmind pull MODEL` | Download a model through Ollama with progress |
| `devmind config [--init\|--path]` | Show or create `~/.devmind/config.toml` |
| `devmind stats` | Memory stats and the last routing decisions |

`--home DIR` and `--demo` work before or after the subcommand.

## How routing works (and why it is explainable)

1. **Classify** the request with deterministic rules: task (autocomplete, explain, bug-fix,
   refactor, write-new, tests, architecture, shell, SQL, docs), language, difficulty.
2. **Filter** the registry by what is installed, what fits the memory budget, and what clears
   the speed floor (default 6 tok/s).
3. **Score** candidates with prior quality, estimated speed (difficulty-weighted), and — once
   you have used the tool — the learned win rate for that task stored in SQLite.
4. **Explain** the decision in one line, including the quality floor and download state.

Routing is data-driven: `~/.devmind/models.toml` overrides the bundled
[`devmind/models.toml`](devmind/models.toml) by id, so adding a model never requires Python.

## Memory

* **Preferences and facts** you teach, plus conservative auto-capture of declarative
  statements ("we always use type hints", never questions).
* **Episodes**: what you asked and what was answered.
* **Feedback** (wired in step 6) becomes training signal, not vibes.
* Retrieval is hybrid lexical (SQLite FTS5) + vector with a relevance gate, so an
  unrelated preference is *not* injected into your prompt.
* Anything written to memory is **secret-redacted first** (API keys, tokens, private keys,
  credentials in URLs), so pasting a `.env` line cannot leak into storage.

Embeddings ship as a dependency-free feature-hashing model (1024-d) so first run needs no
downloads. Step 2 adds Ollama embeddings (`nomic-embed-text`, `bge-m3`) behind the same
interface, with automatic re-embedding when the model changes.

## What is real today

| Step | Deliverable | State |
|---|---|---|
| 1 | Repo layout, config, hardware detector, Ollama integration, CLI chat | **Done, 77 tests** |
| 2 | Memory layer + AST code indexer + retrieval | Memory done; indexer next |
| 3 | Verification pipeline + self-repair loop | Not started |
| 4 | Router + ensemble judge + routing log | Rules + learned stats + routing log done; judge next |
| 5 | Web knowledge layer (cache, versions, injection defence) | Not started |
| 6 | Feedback capture + LoRA pipeline with auto-rollback | Feedback storage done; training not started |
| 7 | Google Drive backend with encryption | Not started |
| 8 | Local API, VS Code extension, web UI | Not started |
| 9 | Evaluation suite (100+ tasks, regression gate) | Not started |
| 10 | Docs: install, hardware, privacy, add-a-model, add-a-language | Started (`docs/`) |

### Deliberate deviations from the brief

* **`models.toml` instead of `models.yaml`.** `tomllib` is in the standard library; PyYAML is
  an extra dependency for zero benefit. Same semantics: roles, params, size, license.
* **Hashing embedder first.** A 500 MB embedding model on first run contradicts
  "installable and fast locally". The interface is swappable and step 2 wires the real one.
* **No encryption yet.** Field-level encryption (step 7) must be designed with key handling and
  migration, not bolted on. `devmind forget --all` is the honest current answer.

## Develop

```bash
make install     # editable install + dev extras
make test        # pytest
make demo        # doctor + models + a routed demo answer, no model required
```

Layout:

```
devmind/
  cli.py          command line, streaming, routing output
  config.py       typed TOML config + env overrides
  hardware.py     RAM/VRAM/bandwidth detection, memory budget, tok/s estimate
  registry.py     model registry loader (bundled + user override)
  models.toml     the registry itself
  router.py       task/language/difficulty classification + model choice
  backends.py     Ollama client (streaming chat, pulls) + demo backend
  prompts.py      system rules, task guidance, prompt assembly
  security.py     secret redaction before storage/prompts
  memory/
    embed.py      tokenising + hashing embeddings + similarity
    store.py      SQLite: memories, episodes, feedback, model_stats, routing_log
    retriever.py  relevance-gated prompt context + conservative preference capture
tests/            77 tests: config, hardware, registry, router, memory, prompts,
                  security, CLI, backends
docs/             hardware.md, privacy.md, models.md, adding-a-language.md
ARCHITECTURE.md   contracts, data model, decisions, planned steps
```

## License

MIT for DevMind itself. Models keep their own licenses — see [docs/models.md](docs/models.md)
and the `license`/`training_ok` fields in the registry.
