# Verification

DevMind's promise is "show me the checks, not the confidence". Two free paths exist today; a third
(sandboxed execution with a self-repair loop) is step 3.

## Local checks

```bash
devmind verify .
```

1. **Detect** the project's own command, in this order: pytest (Python project/tests present),
   `npm test` (a real `test` script exists), `cargo test`, `go test ./...`, `make test`.
   At most three run; nothing is invented and no dependency is installed.
2. **Run** it in the project root with a timeout (`--timeout`, default 600 s), a scrubbed
   environment (`GITHUB_TOKEN`, `OPENAI_API_KEY`, `DEVMIND_PASSPHRASE` and friends removed; `CI=1`)
   and no shell.
3. **Report** exit code, duration and the last 4 000 characters of output. `devmind verify . --json`
   is machine readable.

## GitHub Actions

```bash
devmind verify --github owner/repo --ref branch [--workflow test.yml] [--timeout 900]
```

* With `--workflow`, DevMind dispatches `workflow_dispatch` on that branch first (the workflow must
  declare it).
* It then polls the run list for that branch, waits for `completed`, and reads the jobs to name the
  failed steps.
* Result: `success`, `failure`, `cancelled`, `timed_out`, or `timeout` when no run appears.

## What a green result proves

| Green check | What it actually means |
|---|---|
| `pytest` passes | The project's tests passed for the working tree DevMind ran them in |
| `npm test` passes | The declared test script exited 0 |
| Actions `success` | The CI workflow's steps passed on that branch |
| `devmind index` succeeds | Files were read and chunked; it says nothing about correctness |

It does **not** prove the code is free of bugs, that the tests are meaningful, or that the answer
matches your intent. DevMind presents the evidence rather than a promise.

## Current limits (stated honestly)

* **No sandbox yet.** Local tests run as your user with network access. Step 3 adds container or
  restricted-subprocess isolation with CPU/memory/time limits and network off by default.
* **No automatic fix loop yet.** DevMind does not (yet) re-prompt a model with failing test output;
  that is step 3/4.
* **No coverage or mutation checks.** Coming with the evaluation suite (step 9).
* Test output can contain secrets printed by your own tests; they are not redacted from the
  terminal, only from memory.

## Exit codes

`devmind verify` returns `0` when every executed check passed, `1` on any failure or GitHub error,
`2` on usage errors. That makes it usable in scripts and hooks.
