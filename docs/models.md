# Models

DevMind only runs free / open-weight models through [Ollama](https://ollama.com). The registry
(`devmind/models.toml`) is data, so you can add or override entries without touching Python.

## Roles

| Role | Used for | Typical size |
|---|---|---|
| `fast-small` | autocomplete, quick edits, short explanations | 0.5B – 7B |
| `strong-coder` | write-new, bug-fix, refactor, tests, SQL, shell | 7B – 32B |
| `reasoning` | architecture, explanations, judging answers | 3B – 14B |
| `judge` | comparing candidates in ensemble mode (planned) | 14B+ |
| `draft` | speculative decoding draft model (planned) | ≤1.5B |
| `embed` | code memory embeddings (planned) | ~0.1B – 0.6B |

`quality` is a 0-100 prior for coding ability, used only for ranking (the router learns real
win rates from feedback). It is deliberately coarse: 78 vs 79 is not meaningful.

## llama.cpp (default backend)

A llama.cpp server serves exactly one model, so DevMind stops pretending to route between many.
Set the label and a quality prior once:

```toml
[backend]
kind = "llamacpp"

[llamacpp]
base_url = "http://127.0.0.1:8080"   # or your tunnel address
model = "qwen2.5-coder-7b"           # any label; it is what you started
model_quality = 78                   # 0-100, drives routing vs. alternative backends
model_size_gb = 4.7                  # used for memory-fit warnings
context = 16384
```

### "404" or an HTML page instead of an answer

If DevMind reports something like *"answered with HTTP 404 and an HTML page"*, the address in
`[llamacpp] base_url` is not a llama.cpp server. Usually a different program owns that port (a Flask
app, a router admin page, an old project). Check before changing anything:

```bash
curl -s http://127.0.0.1:8080/v1/models
# llama.cpp answers with JSON: {"object":"list","data":[{"id":"..."}]}
```

* JSON with a `data` list → the server is fine; the label in `model =` does not have to match.
* HTML → wrong port or wrong program. Start `llama-server` on a free port and update `base_url`
  (a bare address, no `/v1` suffix), or change the port in the start command.
* Connection refused → nothing is listening there.

`devmind doctor` prints the same diagnosis, the web app shows it in a red banner with a **Retry**
button, and `devmind serve` prints it before the UI opens.

## Adding a model

1. Check it runs locally: `ollama pull <id>`, then `ollama run <id>` once.
2. Copy the bundled registry to your home folder:

   ```bash
   cp "$(python -c 'import devmind.registry as r; print(r.BUNDLED_REGISTRY)')" ~/.devmind/models.toml
   ```

3. Add an entry (or override an existing one by reusing its `id`):

   ```toml
   [[models]]
   id = "qwen2.5-coder:7b-instruct-q8_0"   # must match `ollama list` exactly
   family = "Qwen2.5-Coder"
   params_b = 7.0
   quant = "q8_0"
   size_gb = 8.1
   context = 32768
   license = "Apache-2.0"
   training_ok = true
   roles = ["strong-coder"]
   quality = 78
   strengths = ["higher precision than q4"]
   ```

4. Verify: `devmind models --installed` and `devmind ask -m <id> "hello"`.

Overrides are merged by `id`: same id replaces the bundled entry, new ids are appended.
DevMind still refuses models that do not fit your memory budget unless you force them with
`-m` (which prints a warning).

## Licenses

`training_ok` means "the license clearly permits fine-tuning on your data". It is `false` for
non-OSI community licenses (Llama, Gemma, Codestral/MNPL, BigCode OpenRAIL) so that step 6 can
never train silently on a model whose terms are unclear. Running a model is always allowed;
training is a separate decision.

| License | Models in the registry | `training_ok` |
|---|---|---|
| Apache-2.0 | Qwen2.5-Coder, Granite Code, Nomic Embed | yes |
| MIT | Phi-4, BGE-M3 | yes |
| DeepSeek-License | DeepSeek-Coder-V2 | yes (check terms for redistribution) |
| Llama 3.x Community | Llama 3.1 / 3.2 | no (conservative) |
| Gemma Terms | Gemma 2 | no |
| MNPL (non-commercial) | Codestral | no |
| BigCode-OpenRAIL-M | StarCoder2 | no |

## Why these models

* **Qwen2.5-Coder** is the best open coding family per GB across 0.5B–32B, Apache-2.0, and all
  sizes share a tokenizer — good for routing between fast and strong tiers.
* **DeepSeek-Coder-V2 16B** is strong at algorithmic work and long code reading.
* **Granite Code** covers enterprise languages (Java, SQL, COBOL) and is Apache-2.0.
* **Qwen2.5 / Phi-4 / Llama 3.1** handle explanation and architecture, not just generation.
* **Nomic Embed / BGE-M3** are the embedding candidates for step 2.

## What the router will not do

* Pick a paid model (there are none registered).
* Pick a model that does not fit your memory budget, unless you force it.
* Pick a model that would run below the speed floor when a faster one is available.
* Hide its reasoning: every decision is one line in the CLI and one row in `routing_log`.
