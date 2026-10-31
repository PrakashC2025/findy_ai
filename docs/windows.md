# Windows, step by step (PowerShell)

This page exists because PowerShell is not bash: `winget` may be missing, the `devmind` command is
not on your PATH, and `cd findy_ai` fails if you are already in the project folder.

## 1. Be in the right folder

Open PowerShell in the folder that contains `START-DEVMIND.bat`, `pyproject.toml`, `devmind.bat` and
the `devmind` folder. For a typical download that is:

```powershell
cd C:\Yugatrend\findy_ai
```

If the prompt already shows that path, **do not run `cd findy_ai` again** — there is no nested
`findy_ai` folder, and PowerShell will say `Cannot find path`.

Check with:

```powershell
dir START-DEVMIND.bat, pyproject.toml
```

## 2. Install DevMind (once)

Either double-click `START-DEVMIND.bat`, or:

```powershell
.\START-DEVMIND.bat
```

That creates `.venv`, installs the dependencies, and opens the app. A black window stays open — it is
the server. Close it to stop DevMind.

`devmind` itself is **not** added to your PATH (that would require changing your system settings). Use
the wrapper in the same folder:

```powershell
.\devmind.bat doctor
.\devmind.bat setup --write
```

or the full path:

```powershell
.\.venv\Scripts\python.exe -m devmind doctor
```

## 3. Install a model without `winget`

`winget` does not exist on older Windows 10 builds, on LTSC, or when the Microsoft Store / App
Installer has been removed. You do not need it.

### Option A — Ollama (simplest)

Download and run the installer. It is **large** (roughly 700 MB - 1 GB, because it bundles the GPU
runtimes), so a slow line takes a while; that is the download, not a hang.

```powershell
# The progress bar makes Invoke-WebRequest several times slower - turn it off.
$ProgressPreference = 'SilentlyContinue'
Invoke-WebRequest https://ollama.com/download/OllamaSetup.exe -OutFile "$env:TEMP\OllamaSetup.exe"
Start-Process "$env:TEMP\OllamaSetup.exe"
```

Faster and resumable (works if the first attempt was interrupted):

```powershell
curl.exe -L --retry 3 -C - -o "$env:TEMP\OllamaSetup.exe" https://ollama.com/download/OllamaSetup.exe
```

Or use `Start-BitsTransfer -Source https://ollama.com/download/OllamaSetup.exe -Destination "$env:TEMP\OllamaSetup.exe"`,
or just download it in your browser from <https://ollama.com/download>.

To see how far it got, in a second PowerShell window:

```powershell
(Get-Item "$env:TEMP\OllamaSetup.exe").Length / 1MB
```

Run it once the size stops growing at roughly its final value (the browser download page shows the
expected size). `Invoke-WebRequest` prints `Number of bytes written: …` as it goes; Ctrl+C cancels it
and `curl -C -` resumes from that partial file.

Finish the installer, then **close and reopen PowerShell** (PATH is only refreshed in a new window).

```powershell
ollama --version
ollama pull qwen2.5-coder:7b
ollama list
```

Pick the model size by RAM:

| RAM | Model | Download |
|---|---|---|
| 8 GB | `qwen2.5-coder:1.5b` | ~1 GB |
| 8-12 GB | `qwen2.5-coder:3b` | ~2 GB |
| 16 GB+ | `qwen2.5-coder:7b` | ~4.7 GB |

Then point DevMind at Ollama (it runs on port 11434, so Apache/XAMPP on 8080 does not matter):

```powershell
.\devmind.bat setup --kind ollama --write
.\START-DEVMIND.bat
```

You can verify Ollama speaks the expected API:

```powershell
curl.exe http://127.0.0.1:11434/v1/models
```

(`curl.exe`, not `curl` — in PowerShell `curl` is an alias for `Invoke-WebRequest`.)

### Option B — llama.cpp (your own GGUF)

1. Download a build from <https://github.com/ggml-org/llama.cpp/releases>: the `llama-*-bin-win-cpu-x64.zip`
   (or the CUDA build for an NVIDIA card). Extract it to `C:\llama`.
2. Download a GGUF, for example `qwen2.5-coder-7b-instruct-q4_k_m.gguf` from Hugging Face, into
   `C:\models`.
3. Start the server and leave the window open:

   ```powershell
   C:\llama\llama-server.exe -m C:\models\qwen2.5-coder-7b-instruct-q4_k_m.gguf --port 8081 -c 16384 -ngl 0
   ```

   `-ngl 0` means CPU only. With an NVIDIA card use `-ngl 99` and a CUDA build.

4. Tell DevMind where it is:

   ```powershell
   .\devmind.bat setup --port 8081 --write
   .\START-DEVMIND.bat
   ```

If the configured address has no model, DevMind also scans 8080-8090, 11434 and 1234 at startup and
uses the first model server it finds — it prints which one, and the app shows it in green.

## 4. Apache / XAMPP on 8080

`Server: Apache` on 8080 means Apache (XAMPP/WAMP usually) owns that port. Leave it alone: it is a web
server, not a model. Use 8081 for llama.cpp or 11434 for Ollama instead. `devmind ports` shows what
is on every port:

```powershell
.\devmind.bat ports
```

## 5. Things that are not part of DevMind

* `npm error enoent` — nothing in this project uses npm or Node. Ignore it; it comes from another
  command in the same window.
* `devmind : The term 'devmind' is not recognized` — expected; use `.\devmind.bat ...` (see above).
* `cd findy_ai` failing — you are already in the folder.

## 6. Check that it works

```powershell
.\devmind.bat doctor
```

You want to see:

```
✓ Backend (ollama) ...
```

or

```
✓ Backend (llamacpp) llama.cpp server at http://127.0.0.1:8081
```

Then open the app (`.\START-DEVMIND.bat`), pick your project, and ask a question.
