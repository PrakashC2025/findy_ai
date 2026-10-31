# Permissions, shell access and "control my system"

DevMind is meant to be useful, not powerful-by-surprise. This page is the exact list of what it may
do today, what it will ask about, and what it deliberately refuses.

## What DevMind does without asking

| Action | Scope | Why it is safe |
|---|---|---|
| Read source files | directories you point it at | never leaves the machine; secrets are redacted before storage |
| Build the code index | your indexed repositories | read-only; skips `.git`, `node_modules`, `.env*`, binaries |
| Read your own git history locally | log, branches | read-only |
| Run the project's **detected** checks | `pytest`, `npm test`, `cargo test`, `go test`, `make test` | under CPU/memory/disk/process limits, scrubbed environment, optional network namespace |
| Copy a project to a temp dir | for repair attempts | the copy is deleted afterwards |
| Talk to your inference server | loopback or your private tunnel | no third-party service |

## What requires an explicit flag or confirmation

| Action | Gate |
|---|---|
| Writing a patch to your tree | `devmind fix --apply`, and only after the checks passed on a copy; then re-verified |
| Cloning a private repo | `devmind github clone`, using your own token |
| Pushing a branch / opening a PR | `devmind github pr` — refuses `main`/`master`/`trunk`/`develop` and the default branch |
| Reading CI results | `devmind verify --github` |
| Signing in to GitHub | device flow (browser) or a fine-grained token you paste |
| Anything network beyond the above | nothing exists yet; the web layer (step 5) will be opt-in with a local cache |

## What DevMind never does

* Run an arbitrary shell command on its own. There is no hidden `exec`.
* Install packages, change your shell profile, or edit files outside the project root.
* Write to the default branch of any repository.
* Send your code, prompts or memory to a cloud API. There is no paid-API code path.
* Start background services, download models, or reach the network while you are offline
  (`offline = true` blocks every outbound call).
* Watch your screen, keyboard or other apps.

## "Can it control my computer or my phone?"

**Not yet, and never silently.** Today DevMind can run the commands a *project* defines, inside
limits, and write a verified diff to the project you name. That is the safe core of system access.

The planned design for broader command execution (part of the agent step) is:

1. **Allow-list first.** A command must match an entry in `[permissions]` in the config
   (e.g. `git status`, `docker compose ps`), or the user types it manually.
2. **Per-action approval.** Anything not on the list prints the exact command, the working directory
   and its effect, and waits for `y`. No auto-approval, no "remember for everything".
3. **Sandbox by default.** Commands run with the same `rlimit`s and environment isolation as
   `devmind check`; a separate `--unsandboxed` flag is required to escape them.
4. **Audit log.** Every approved command is appended to the local routing/episode log so you can see
   what ran and when.
5. **Destructive verbs are never auto-approved.** `rm`, `mv`, `dd`, `mkfs`, `chmod -R`, `git reset
   --hard`, `push --force`, `DROP`, `TRUNCATE`, package installs and anything touching `~` or `/`
   require typing the command's effect back.
6. **No remote control.** DevMind cannot drive your phone or PC from another device. The phone talks
   to your llama.cpp server over *your* private tunnel (Tailscale/WireGuard); it does not send shell
   commands to your desktop, and the desktop cannot command the phone.

If you want that sooner, it is a small, testable increment: an approval-gated `devmind run` command
with the allow-list and audit log above. It is not built yet, and this page will be updated when it
is — not before.

## Data consent

* **Local by default.** One SQLite file in `~/.devmind`. Nothing is uploaded.
* **Google Drive is optional backup, never primary storage.** Before the first upload DevMind will
  ask you to opt in, name the folder (`appDataFolder` in *your* Drive), and choose a passphrase. The
  archive is encrypted on your device before upload; Google sees ciphertext only. There is no email,
  no account on our side, and nothing to sign up for.
* **Phone-first.** The PWA stores its own copy; sync is a backup, not a dependency.
* **Forget means forget.** `devmind forget --all` wipes memories, episodes, feedback, routing log and
  statistics locally; `devmind github logout` deletes the encrypted token file.

See [privacy.md](privacy.md) for the file list and encryption status per item.
