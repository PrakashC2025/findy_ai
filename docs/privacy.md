# Privacy model

DevMind is local-first by construction, not by policy. This page states exactly what is stored,
what leaves the machine, and how to delete it.

## What is local

Everything DevMind creates lives in `$DEVMIND_HOME` (default `~/.devmind`):

```
~/.devmind/
  config.toml       your settings (no secrets required — keys are not part of DevMind)
  memory.db         SQLite: memories, episodes, feedback, stats, routing log, code index
  memory.db-wal     SQLite write-ahead log (transient; skipped where unsupported)
  credentials.enc   GitHub token, encrypted with your passphrase (0600)
  cache/            reserved for the web-knowledge cache (step 5)
  models.toml       optional registry override
  repos/            clones made by `devmind github clone`
```

The code index contains **source code you chose to index**. Excluded by default: `.git`,
`node_modules`, virtualenvs, build output, lockfiles, `.env*`, `*.pem`, `*.key`, `*secret*`,
`*credential*`. Indexing happens locally; chunks never go anywhere except your inference server
when you ask a question.

There is no account, no sync, no analytics endpoint, no crash reporter, no update ping.

## What leaves the machine

**Nothing, by default.** The only inference traffic goes to the backend you configured:

* `llamacpp` → your own llama.cpp server, loopback or your private tunnel (Tailscale/WireGuard).
* `ollama` → `http://127.0.0.1:11434` on your own machine; `devmind pull` downloads from Ollama's
  registry (that download is Ollama's traffic, not yours).

Two other network paths exist, both only when you ask for them:

* **GitHub** (`devmind github …`) — your account, your token, your repositories.
* **GitHub Actions** (`devmind verify --github …`) — reading workflow results for your repos.

Future steps keep this property:

* Step 5 (web knowledge) only fetches official docs and caches them locally; it is off by default
  and every fetched page is shown as a source URL.
* Step 7 (Google Drive) uploads only encrypted memory snapshots, to an app-data folder you pick,
  using your own OAuth credentials.
* There is no paid API code path at all, so there is no "just this once" cloud call to audit.

## Secrets

Before anything is written to memory or to an episode, DevMind redacts:

* private key blocks (`-----BEGIN … PRIVATE KEY-----`)
* AWS access keys, GitHub tokens, OpenAI/Groq/OpenRouter keys, Google API keys, Slack tokens, JWTs
* credentials embedded in URLs (`postgres://user:pass@host`)
* `*_api_key`, `*token*`, `*password*`, `*secret*` assignments (placeholders like `changeme` and
  indirections like `$MY_KEY` are left intact)

This is defence in depth, not a guarantee — if a secret has an unusual shape it may survive.
Disable with `[privacy] redact_secrets = false` only if you know what you are doing.

## Your controls

```bash
devmind memory                     # see everything stored
devmind memory --search "billing"  # targeted lookup
devmind forget <id>                # delete one memory
devmind forget --all               # delete all memories, episodes, feedback, stats
rm -rf ~/.devmind                  # full reset (config included)
```

`devmind forget --all` also clears the routing log and model statistics, because "how you code"
is itself private information.

## Encryption status

| Data | Status |
|---|---|
| GitHub token | **Encrypted** — scrypt (n=2^14) → Fernet, file mode `0600` |
| Memory, index, routing log | **Not encrypted yet** — protected by OS file permissions |
| Backups / Drive snapshots | Not implemented |

Field-level encryption for memory with a passphrase, and encrypted Drive snapshots, is step 7. It
will ship with a migration path (`encrypted` marker in the DB, `devmind encrypt`/`decrypt`, scrypt →
AES-GCM). Pretending it is encrypted today would be worse than saying it is not.

## What DevMind deliberately does not do

* It does not read files outside the project unless a later step's tool is explicitly approved.
* It does not send your code to a third-party model provider.
* It does not train on public code without a license check (planned, `training_ok` flag already
  records each model's license).
* It does not include telemetry hooks that could be enabled later by a config flag.
