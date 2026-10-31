# Verification and self-repair

DevMind's promise is "show me the checks, not the confidence". Four pieces enforce it:

1. **`devmind check`** — run the project's own checks under real limits, scan the tree, and fail loudly.
2. **`devmind fix`** — ask for a unified diff, apply it to a *throwaway copy*, run the checks, and retry
   at most three times, feeding the failing output back to the model.
3. **`devmind scan`** — a small, explainable security scanner for the mistakes assistants actually make.
4. **Confidence** — computed from evidence (citations, checks, repair rounds, stray links), never from
   a model's self-report.

## 1. `devmind check`

```bash
devmind check .                 # limits + security scan (default)
devmind check . --json          # machine-readable for hooks and CI
devmind check . --no-sandbox    # plain subprocess (same as devmind verify)
devmind check . --no-scan       # skip the security scan
```

Detection covers pytest, `npm test`, `cargo test`, `go test ./...` and `make test`; at most three run.
Nothing is installed and no command is invented.

### What the sandbox actually enforces

| Limit | Enforcement |
|---|---|
| Wall clock | `timeout` with the whole process group killed (no orphans) |
| CPU | `RLIMIT_CPU` |
| Memory | `RLIMIT_AS` / `RLIMIT_DATA` (default 2 GB) |
| Disk writes by one file | `RLIMIT_FSIZE` (default 64 MB) |
| Process count | `RLIMIT_NPROC` |
| Core dumps | `RLIMIT_CORE = 0` |
| Host secrets | Environment allow-list (`PATH`, `LANG`, `TZ`, `VIRTUAL_ENV`…); `HOME`/`TMPDIR` point at a fresh temp dir that is deleted afterwards |
| Network | Empty network namespace via `unshare -n` **when the kernel allows it**; otherwise the report says "network: not isolated" |

`devmind check` prints which of these are active before it runs anything, for example:

```
limits: time 300s · cpu 300s · memory 2048MB · file writes 64MB · processes 64 · network: not isolated (unshare unavailable) · filesystem: throwaway copy
```

The "filesystem: throwaway copy" line is honest only for `devmind fix`: repair applies candidate
diffs to a copy. `devmind check` runs the project's checks **in your tree** (so `node_modules`,
virtualenvs and fixtures keep working) but under the resource and environment limits above. That is
process isolation, not a container: there is no seccomp filter, so a hostile binary could still read
your files. Do not run `devmind check` on code you already distrust.

## 2. `devmind fix` — the self-repair loop

```bash
devmind fix "the refund test fails after the tax change" --root .
devmind fix "…" --rounds 3 --timeout 300        # defaults
devmind fix "…" --apply                         # write the verified diff to your tree
```

Each round:

1. The model is asked for **one fenced unified diff and nothing else** (or `CANNOT FIX: reason`).
2. DevMind copies the project (skipping `.git`, `node_modules`, virtualenvs, `.env*`, files > 2 MB).
3. `git apply` applies the patch to the copy. A patch that does not apply is a failed round, not a
   crash.
4. `devmind.security_scan.scan_diff` judges the added lines. Any **high-severity** finding rejects the
   patch before it is executed.
5. The project's checks run inside the copy under the limits above.
6. On failure, the real failing output (not a summary) goes back to the model for the next round.

The loop stops at the first passing round. After the last round it reports what still fails and why.
`--apply` writes the winning diff to your tree and then **re-runs the checks on the real tree**. If
that second run fails, the output says so — DevMind never claims a fix it did not verify.

What it refuses to do:

* edit tests to hide a failure (prompt rule; also visible in the diff you are shown),
* add dependencies (prompt rule),
* run a patch that triggers a high-severity security finding,
* touch your files before `--apply`,
* claim success when no check could run.

If the project has no detectable test/lint/build command, `devmind fix` exits immediately with an
explanation instead of guessing.

## 3. `devmind scan`

```bash
devmind scan .            # human output
devmind scan . --json     # findings array
```

| Rule | Severity | Catches |
|---|---|---|
| `secret.aws_key`, `secret.openai_key`, `secret.github_token`, `secret.private_key` | high | credentials committed in source |
| `secret.generic` | medium | `password = "…"`-style literals |
| `code.eval`, `code.exec` | high | executing data |
| `code.shell_true`, `code.os_system` | high / medium | command injection |
| `code.pickle`, `code.yaml_unsafe` | high | unsafe deserialisation |
| `sql.interpolation` | high | a value interpolated into SQL |
| `sql.dynamic` | medium | identifiers/fragments interpolated (usually fine, worth a look) |
| `code.inner_html`, `code.reflect`, `path.traversal` | medium | XSS, reflection, path handling |

False positives are expected — that is why every finding prints `path:line`, the offending line and a
one-line fix. Suppress deliberate ones with a comment on the line, the line above, or at the top of a
file:

```python
value = eval(x)  # devmind: allow code.eval
# devmind: allow-file sql.dynamic
# devmind: allow-file *        (whole file — use sparingly)
```

Exit code is `1` when any **high** finding exists, so it composes with CI. Findings in vendored
directories are skipped; this is not a replacement for a real SAST tool.

## 4. Confidence

Every answer carries `confidence: high|medium|low (0.00–1.00)` and the reasons behind it. The score
starts at 0.40 and moves only on evidence:

| Evidence | Effect |
|---|---|
| Each cited source | +0.06 (max +0.20) |
| All executed checks pass | +0.22 |
| Any check fails | −0.15 each (max −0.30) |
| No checks available | −0.05 |
| Self-repair converged | +0.05 |
| The answer says "not sure" / "I think" | −0.10 / −0.05 |
| Links that were never retrieved | −0.15 |
| Demo mode (no model) | forced to 0.05, label `none` |

The reasons are printed, so a low score is actionable: "no automated checks available" tells you to
add a test command; "3 links not present in the retrieved sources" tells you the model invented
sources.

## What a green result proves

| Green check | What it actually means |
|---|---|
| `pytest` passes | The project's tests passed for the tree that ran |
| `npm test` passes | The declared test script exited 0 |
| Actions `success` | The workflow's steps passed on that branch |
| `check` reports 0 findings | None of the ~20 rules matched — not that the code is secure |

It does **not** prove correctness, that the tests are meaningful, or that the answer matches your
intent. A passing suite with weak assertions still passes.

## Current limits (stated honestly)

* **No container, no seccomp, no user namespaces.** Limits are POSIX `rlimit`s; network isolation
  needs `unshare -n` and is skipped when unavailable (macOS/Windows always).
* **Checks are the project's own.** If the project has no tests, there is nothing to verify and
  DevMind says so.
* **Repair needs a model that emits unified diffs.** Small models often do not; the round is recorded
  as "no diff" rather than retried forever.
* **Test output is not redacted in the terminal.** It is redacted before storage.
* **No coverage, mutation or fuzzing yet.** That belongs with the evaluation suite (step 9).

## Exit codes

`0` pass, `1` failure (or high-severity finding), `2` usage error. `devmind verify` keeps its earlier
behaviour (skips when no command is detected); `devmind check` treats "nothing ran" as a failure
because verification is its whole job.
