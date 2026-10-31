# Phone + home PC

The realistic way to get "close to the big assistants, for free" is not a bigger phone model. It is
a small phone model for small jobs and your own PC for the heavy ones.

## Topology

```
┌────────────────────────────┐            ┌──────────────────────────────┐
│ Phone (PWA)                │  Tailscale │ Home PC                      │
│                            │  /WireGuard│                              │
│ • chat, diffs, approvals   │ ─────────▶ │ • llama.cpp server + GGUF    │
│ • on-device 0.5-3B         │  loopback  │ • DevMind core (this repo)   │
│   (WebLLM/wllama)          │  over      │ • code index, memory         │
│ • SQLite-WASM memory       │  tunnel    │ • GitHub + verification      │
│ • offline after first sync │            │ • stays awake, no cloud      │
└────────────────────────────┘            └──────────────────────────────┘
```

The phone is the interface; the PC is the brain. Nothing is sent to a third party: the tunnel is
yours (Tailscale is free for personal use), and the model runs on hardware you own.

## What is implemented today

| Piece | State |
|---|---|
| Backend abstraction with llama.cpp + Ollama + demo | Done (`devmind/backends/`) |
| Portable SQLite schema usable by SQLite-WASM | Done (`devmind/storage/schema.sql`) |
| Index/memory schemas with no WAL or FTS5 requirement | Done (FTS5 optional, retrieval degrades to vectors) |
| HTTP API for the PWA | Planned (step 8) |
| PWA itself, on-device WebLLM/wllama, service worker, OPFS | Planned (step 8) |
| Encrypted sync between phone and PC | Planned (step 7/8) |

This page documents the design so the PWA work has a fixed target. It does not claim the app exists.

## Model choice by device

| Device | Model | Why |
|---|---|---|
| Phone, on-device (WebGPU) | Qwen2.5-Coder **0.5B / 1.5B** (q4), 3B only on 8 GB+ devices | Fits in browser memory; 20-60 tok/s on a recent flagship |
| Phone with PC | PC model, phone streams tokens | Phone stays cool; quality is the PC's |
| Laptop, 8 GB RAM | 1.5B-3B | 7B swaps and feels broken |
| Laptop, 16 GB RAM | 7B q4 | The everyday sweet spot |
| Desktop, 16-24 GB VRAM | 14B / 32B q4 | Best free quality |

The router already encodes these tiers as roles (`fast-small`, `strong-coder`, `reasoning`) and
picks per task, so the PWA and the PC use the same policy.

## Why the schema is portable

* No `AUTOINCREMENT`, `STRICT` tables or generated columns; TEXT ids, REAL timestamps.
* WAL is attempted and skipped if unsupported (some SQLite-WASM builds).
* FTS5 is created opportunistically by the host; if absent, retrieval uses vectors only.
* One file (`devmind/storage/schema.sql`) is the single source of truth for both hosts, so a
  database copied from the PC opens on the phone and vice versa.

## Performance rules for the PWA

1. Every heavy job (indexing, embedding, inference) runs in a Web Worker; the UI thread only paints.
2. Indexing is resumable and time-capped (`index.max_seconds`), so a background tab never dies
   mid-scan.
3. Memory budget is explicit: model size + KV cache must fit; otherwise the app steps down a model.
4. Offline-first: the app opens from cache, answers from the on-device model, and syncs when the
   PC is reachable.
5. The phone never proxies model traffic through a public server.

## Security notes

* Bind the PC's DevMind API and llama.cpp to `127.0.0.1` and expose them **only** through the
  private tunnel, never `0.0.0.0` on a public network.
* GitHub tokens live encrypted on the device that uses them; the phone gets its own token or none.
* Test execution from the phone must be limited to GitHub Actions or the PC, never a random server.
