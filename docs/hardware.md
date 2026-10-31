# Hardware guide

DevMind picks models from what your machine can actually run. This page explains how that
decision is made, what the numbers mean, and how to override it.

## Choose a backend first

DevMind is backend-agnostic. The default is a **llama.cpp server** (`llama-server -m model.gguf`),
which is the recommended setup for a home PC because one GGUF file, one process, any OS, and it can
be reached from your phone through a private tunnel. **Ollama** remains supported for desktop
convenience (`[backend] kind = "ollama"`). The sizing rules below apply to both — they describe the
model, not the runtime.

## What is detected

| Signal | Linux | macOS | Windows |
|---|---|---|---|
| RAM total/available | `/proc/meminfo` | `hw.memsize` (available ≈ 70 %) | `GlobalMemoryStatusEx` |
| CPU name/cores | `/proc/cpuinfo`, `os.cpu_count` | `machdep.cpu.brand_string` | `platform` |
| GPU + VRAM | `nvidia-smi`, `rocm-smi` | Apple unified memory | `nvidia-smi` if present |
| Memory bandwidth | GPU name table, else 40 GB/s | Apple chip tier table | GPU table, else 40 GB/s |

Everything is read-only and best-effort. `devmind doctor` prints the result **and** the source
of each value, so a wrong guess is visible instead of silent.

## The memory budget

```
GPU / Apple:  budget = VRAM × 0.90 − reserve_gb
CPU only:     budget = min(RAM_available, RAM × 0.70) − reserve_gb
```

`reserve_gb` defaults to 1.5 GB and exists so your desktop, browser and editor stay alive.
A model is considered to fit when `size_gb + 0.4 ≤ budget` (the extra 0.4 GB is KV cache and
CUDA/Metal overhead at 8k context).

`hardware.max_model_gb` is a hard cap if you want to keep the machine quiet; `allow_cpu_only =
false` refuses to run anything when no GPU is available.

## Speed estimate

Tokens/second is bandwidth-bound, not FLOPs-bound:

```
tok/s ≈ bandwidth_GB_s × efficiency / model_size_GB
efficiency = 0.60 (GPU/Metal) or 0.30 (CPU)
```

Examples on a 100 GB/s machine: a 4.7 GB model ≈ 13 tok/s, a 1 GB model ≈ 60 tok/s. This is an
estimate with roughly ±30 % error; after you use a model, DevMind stores the measured latency
in `model_stats` and routing uses that instead.

The router refuses models below `hardware.min_tokens_per_s` (default 6) unless nothing else
fits — in which case it says so instead of silently making you wait.

## What to run on what

| Machine | Recommended coder | Fast lane | Notes |
|---|---|---|---|
| 8 GB RAM, no GPU | `qwen2.5-coder:1.5b` | `qwen2.5-coder:0.5b` | Expect 3-8 tok/s; use it for explanations and small edits |
| 16 GB RAM, no GPU | `qwen2.5-coder:3b` | `qwen2.5-coder:1.5b` | 7B works but feels slow (`OLLAMA_NUM_THREADS` = physical cores) |
| 16 GB RAM, 8-12 GB VRAM | `qwen2.5-coder:7b` | `qwen2.5-coder:1.5b` | The sweet spot for most laptops |
| 32 GB RAM, 16 GB VRAM | `qwen2.5-coder:14b` | `qwen2.5-coder:7b` | Best quality/latency balance |
| 64 GB RAM, 24 GB+ VRAM | `qwen2.5-coder:32b` | `qwen2.5-coder:7b` | Hard debugging, architecture |
| Apple M-series, 16-32 GB | `qwen2.5-coder:7b` / `14b` | `qwen2.5-coder:1.5b` | Unified memory = "VRAM"; Metal backend |

These are recommendations from `devmind models`, computed for your machine — the table is a
sanity check, not a constant.

## Knobs

`~/.devmind/config.toml`:

```toml
[hardware]
reserve_gb = 1.5        # raise it if your machine swaps
min_tokens_per_s = 6.0  # raise to 10 if you prefer only fast models
allow_cpu_only = true
max_model_gb = 0.0      # 0 = derive from detected memory

[ollama]
keep_alive = "30m"      # keep the model warm between prompts (lower latency)
request_timeout_s = 300 # per-request cap; lower it if you hate waiting
```

Environment equivalents: `DEVMIND_OLLAMA_URL`, `DEVMIND_NUM_CTX`, `DEVMIND_MODEL`.

## Troubleshooting

* **"too big" for every model** – the budget is small (containers, VMs, CI). Set
  `max_model_gb` or use `devmind --demo`.
* **First token takes seconds** – the model is loading. Raise `keep_alive`, or use a smaller
  model for `fast-small` tasks.
* **Machine freezes during a long answer** – lower `num_ctx` (KV cache) and raise `reserve_gb`.
* **GPU not detected** – check `nvidia-smi` works outside DevMind; WSL needs the NVIDIA driver
  installed on the Windows host.
