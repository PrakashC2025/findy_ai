# Using a free online model with DevMind

DevMind runs on your own machine by default: a llama.cpp server on your PC, reached over loopback or
your private tunnel. Nothing about that changes. This page is about the **opt-in** escape hatch for
days when your laptop cannot hold a 30B model but a free tier can.

## What this is not

There is no "connect every free AI on the internet automatically" button, and DevMind does not ship
one. Two reasons, both stated plainly:

* **DevMind ships no provider keys.** A key bundled into an open-source repo is a key that is rate
  limited, revoked, or abused within hours. Any key in this repo would be someone else's, and using
  it would be neither legal nor honest.
* **No site scraping.** DevMind will not log into a chat UI, replay browser traffic, or scrape a
  model provider's website to get around a paid plan. There is no code here that does that, and
  adding it would break the terms of those services.

What it *does* give you is one OpenAI-compatible backend, pointed at an endpoint **you** choose and
**you** hold the key for. That covers every free tier that speaks the OpenAI API:

| Provider | Base URL | Notes |
|---|---|---|
| Groq free tier | `https://api.groq.com/openai/v1` | Fast open-weight models; free-tier rate limits apply |
| OpenRouter `:free` models | `https://openrouter.ai/api/v1` | Model ids end in `:free`; free quotas change, check their page |
| Google AI Studio (OpenAI compatibility) | `https://generativelanguage.googleapis.com/v1beta/openai` | Use their API key; quotas are per project |
| LM Studio / llama.cpp on another PC | `http://192.168.1.20:1234/v1` | Your own hardware, no quota, no third party |
| A work GPU behind a tunnel | `https://your-host/v1` | Treat it like any remote endpoint |

Exact free limits are set by the provider and change without notice. DevMind does not claim them;
`devmind doctor` reports whether **your** endpoint answers, not how much quota you have left.

## Turning it on

1. Put the key in an environment variable. It is never written to `config.toml`, never logged, and
   never sent to the browser:

   ```bash
   export DEVMIND_REMOTE_KEY=...        # macOS/Linux
   setx DEVMIND_REMOTE_KEY "..."        # Windows, new shells only
   ```

2. Edit `~/.devmind/config.toml` (`devmind config --path` prints the location):

   ```toml
   [backend]
   kind = "remote"          # or leave "llamacpp" and switch per project

   [remote]
   enabled = true
   consent = true           # you have read the next section
   provider = "Groq free tier"
   base_url = "https://api.groq.com/openai/v1"
   api_key_env = "DEVMIND_REMOTE_KEY"
   models = ["llama-3.3-70b-versatile"]
   model_quality = 85       # 0-100; the router's opinion of this model
   context = 32768
   ```

3. Check it:

   ```bash
   devmind doctor          # backend line names the provider, or says exactly what is missing
   devmind models          # configured models appear with a "cloud" flag and no download size
   devmind ask "explain this stack trace" --repo .
   ```

If the key is missing, the provider is disabled, consent is off, the base URL is empty, or
`offline = true` is set, the router does not use the endpoint at all — it says which of those it is.

## What leaves the machine

**Your prompts and the retrieved code context.** When a cloud model is selected, the system prompt,
the business brief, the memory hits and the code chunks that would have gone to a local model go to
that provider instead. That is why `consent = true` is a separate flag from `enabled = true`.

* The key stays in your shell environment. The web app never receives it; the browser never stores it.
* `offline = true` in `config.toml` wins over everything: no remote model is registered, no request
  is attempted.
* Errors name the provider and the base URL, never the key.
* `devmind forget --all` clears memories, including the business brief.

If you do not want code leaving your machine, keep `[backend] kind = "llamacpp"`.

## "One powerful model" — what the router actually does

DevMind does not merge weights from several providers into a new model, and it will not claim to.
What it does is the part that is real and useful:

* **Routing.** Difficulty and task type pick a model. Cloud models are registered for
  `strong-coder`, `reasoning` and `judge` roles; small local models stay on autocomplete.
* **Ensemble + judge (web app).** Hard requests are sampled two or three times and scored by the
  project's own checks; the best answer wins, and a judge model breaks near-ties. If the judge is a
  cloud model and the answers are local, only the answers — not your files — go out.
* **Fallback with a reason.** If the endpoint is unreachable, `devmind ask` fails with the provider's
  name and the fix, instead of silently pretending a local model answered.
* **Cost awareness.** Cloud models carry `size_gb = 0` and are never offered as downloads; the model
  table shows `cloud` instead of a memory size so you never think it fits in your RAM.

Combining providers is therefore routing plus scoring, on your terms — not a single mega-model.

## Limits worth knowing before you rely on it

* **Rate limits and quotas** are the provider's, and they change. A 429 is reported as-is.
* **Free tiers may train on your prompts.** Read the provider's policy; that is your decision, not
  DevMind's.
* **Latency** depends on the network. The UI shows first-token and total time so you can compare.
* **No tools, no browsing.** The remote provider is used for generation only. DevMind does not hand
  it a shell, and the knowledge layer (step 5) is not wired yet.
