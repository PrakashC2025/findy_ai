# Run DevMind as an app (PC + phone)

DevMind is one local service with a web interface. The PC does the work - indexing, checks,
inference - and the phone is a client, so the phone never needs the model or the repository.

```
┌──────────── PC (the brain) ────────────┐         ┌──────── phone ────────┐
│ devmind serve                          │  Wi-Fi  │ browser / installed   │
│  · llama.cpp / Ollama (or demo)        │ <─────> │ PWA at http://PC:8787 │
│  · SQLite memory + code index          │         │ same UI, small screen │
│  · sandbox + checks + repair loop      │         │                       │
└────────────────────────────────────────┘         └───────────────────────┘
```

## Start on the PC

Three ways, in order of convenience:

1. **Double-click**
   * Windows: `START-DEVMIND.bat` (at the top of the folder)
   * macOS: `START-DEVMIND.command`
   * Linux / WSL: `bash scripts/devmind.sh`

   The first double-click creates `.venv`, installs the dependencies (needs internet once), picks a
   free port if 8787 is busy, starts the server and opens the browser. *Leave the black window open* —
   it is the server; closing it stops DevMind.
2. **One command**
   ```bash
   devmind serve --open
   ```
3. **From a fresh clone**
   ```bash
   make install
   devmind serve --open
   ```

The app opens at <http://127.0.0.1:8787>. It binds loopback only by default, so nothing on the network
can reach it.

### If double-click does nothing

The launcher is built to keep its window open and say what went wrong. Check these in order:

| Symptom | Cause | Fix |
|---|---|---|
| Window flashes and closes | No Python 3.11+, or the folder came out of a `.zip` it is still inside | Install Python (tick **Add python.exe to PATH** on Windows), and **extract** the zip before running |
| macOS: "cannot be opened because it is from an unidentified developer" | Gatekeeper quarantine on a downloaded file | Right-click the file → **Open** → **Open**, or run `xattr -d com.apple.quarantine START-DEVMIND.command` |
| macOS: "permission denied" | Executable bit lost when downloading | `chmod +x START-DEVMIND.command scripts/devmind.command scripts/devmind.sh` |
| Linux: "ensurepip is not available" | `python3-venv` missing | `sudo apt install python3-venv` (Fedora: `sudo dnf install python3`) |
| "Installing DevMind failed" | No internet, or a proxy | Set `HTTPS_PROXY`, or run `.venv/bin/pip install -e .` manually in a terminal |
| The page opens but answers say "no model" | The app is up; the model server is not | Start llama.cpp / Ollama, or try the UI without a model: `DEVMIND_DEMO=1` (Windows: `set DEVMIND_DEMO=1`) then double-click again |

Full log: `%TEMP%\devmind-launch.log` on Windows, `$TMPDIR/devmind-launch.log` on macOS and Linux.

To see exactly which Python and command the launcher would use, without starting anything:

```bash
# macOS / Linux
DEVMIND_LAUNCH_DRY_RUN=1 ./scripts/devmind.sh
```

```bat
rem Windows
set DEVMIND_LAUNCH_DRY_RUN=1
START-DEVMIND.bat
```

You can always bypass the launcher:

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/python -m devmind serve --open
```

## Start on the phone

On the PC, expose it to your local network (a token is generated automatically):

```bash
devmind serve --host 0.0.0.0 --port 8787
```

The terminal prints two URLs:

```
DevMind 0.6.1 on http://127.0.0.1:8787
Open: http://127.0.0.1:8787/?token=6Qk...
Phone (same Wi-Fi): http://192.168.1.24:8787/?token=6Qk...
```

Open the phone URL once. The token is stored in the browser and set as an HTTP-only cookie, so you
do not need to keep it in the address bar. If you open the bare URL instead, the app asks for the
token once and remembers it. Use **Install** (or *Add to Home Screen*) to get a
standalone app; the manifest and service worker are included.

To reach the PC from anywhere, use a private tunnel instead of exposing a port:

```bash
tailscale serve 8787          # or: tailscale funnel 8787 (public, still token-protected)
```

Then open `http://<pc-name>:8787/?token=…` from the phone. No port forwarding, no cloud account.

> **Token warning:** plain HTTP on a shared Wi-Fi can be observed. Use the token, prefer your own
> network, and use Tailscale/WireGuard when you are away from home. DevMind refuses to bind a
> non-loopback address without a token.

## What the web app does

| Tab | Actions |
|---|---|
| **Ask** | Streamed answer with difficulty, model, language packs, `file:line` citations, confidence and timings. Hard questions are sampled and upgraded; a near-identical question is answered from the local semantic cache. |
| **Analyze** | Indexed file list + file preview; **Run checks** executes the project's own tests/lint under CPU/memory/time limits and shows the security scan; **Business context** saves the brief that is prepended to every answer. |
| **Fix** | Describe a problem → the model proposes a unified diff, which is applied to a *copy*, security-scanned and checked. "Propose and apply" writes it only when verified, then re-runs the checks on your tree. |

**Open folder** indexes a directory on the machine running the server: type an absolute path, or pass
`--allow /path/to/projects` to widen what the server may open. Paths outside the allowed roots are
refused with `403`.

**Clone** takes a git URL (`https://…`, `git@…`, a local path, or GitHub's `owner/name` shorthand),
including the address bar URL of a file or branch
(`https://github.com/owner/repo/tree/main/src` is trimmed to `owner/repo`). It clones with depth 1
into `$DEVMIND_HOME/repos/<name>`, indexes it immediately, links it as the active project and shows
the local path with file/chunk counts under the project bar. Cloning the same URL again does not
re-download: it re-indexes the folder and links it, so a project cloned earlier still shows up. Public
repositories need nothing else. Private repositories need the authenticated CLI once
(`devmind github login` → `devmind github clone …`), because the web app never asks for your
passphrase or stores your token in the browser.

## HTTP API

All endpoints except `/`, `/static/*` and `/healthz` require the token (`X-DevMind-Token` header,
`?token=` parameter, or the cookie set by it).

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/status` | version, backend, hardware, counts, sandbox protections, allowed roots |
| GET | `/api/repos` | indexed projects |
| POST | `/api/index` | `{"path": "...", "repo": "..."}` → index a folder |
| POST | `/api/clone` | `{"url": "...", "name": "..."}` → clone into `$DEVMIND_HOME/repos` and index |
| GET | `/api/brief` | business notes + the exact block that is injected into prompts |
| POST | `/api/brief` | `{"text": "..."}` to add a note, `{"clear": true}` to erase them all |
| GET | `/api/files?repo=` | indexed paths |
| GET | `/api/file?repo=&path=` | file text (traversal-guarded, 200 KB cap) |
| POST | `/api/ask` | server-sent events: `meta`, `token`, `replace`, `done`, `error` |
| GET | `/api/checks?repo=&run=` | detected commands, check results, security findings |
| POST | `/api/fix` | `{"problem","repo","rounds","apply"}` → verified patch |
| POST | `/api/cache/clear` | drop cached answers |

The SSE stream is what makes the UI feel fast: the first `token` events arrive while the model is
still writing, and a later `replace` event upgrades the answer if the pipeline finds a better one.

## Honest limits

* **The phone cannot open arbitrary folders by itself.** Browsers do not give a page full filesystem
  access; the PC indexes and the phone reads. On iOS, Safari offers no folder picker at all.
* **The web app needs the PC** (or another machine running `devmind serve`). The service-worker cache
  keeps the shell and static files available offline, but answers need the server. On-device WebLLM in
  the browser is still step 8.
* **`0.0.0.0` is plain HTTP.** The token protects the API, not the wire. Use a tunnel for anything
  beyond your own network.
* **No accounts, no sync, no telemetry.** The server keeps everything in `$DEVMIND_HOME`.
* **A local health banner, not a silent failure.** `/api/status` probes the backend; if nothing is
  answering (or something that is not llama.cpp owns the port), the app shows the reason and the fix
  at the top of the page with a **Retry** button, and a failed answer shows the same text. The banner
  names the page title and `Server` header it found, and `devmind ports` classifies 8080-8090 for
  you. Loopback traffic never goes through `HTTP_PROXY`, so a proxy cannot fake a local model.
  **Find a model** in that banner runs the same scan in the app and tells you which port to use (or
  run `devmind setup --write` in a terminal); if
  DevMind found a model on a different local port at startup, a green line under the project bar says
  so (see [models.md](models.md#if-the-configured-address-has-no-model)).
* **A remote provider is opt-in and server-side.** `/api/status` reports `cloud: true` only when a
  configured remote endpoint is usable; the browser never sees the key. See
  [remote.md](remote.md).
