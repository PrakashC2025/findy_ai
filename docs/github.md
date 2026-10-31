# GitHub

DevMind uses **your** GitHub account. There is no DevMind server, no paid API, and no token in the
repository or in shell history.

## One-time setup (free)

Create an OAuth App (a personal app; no review needed) at
<https://github.com/settings/developers> → **New OAuth App**:

* Application name: anything (`devmind-local`)
* Homepage URL: anything (`http://localhost`)
* Authorization callback URL: anything (`http://localhost`) — the device flow does not use it
* Leave "Device flow" enabled (it is on by default for OAuth Apps)

Copy the **Client ID** (not the secret; DevMind never needs a client secret) into
`~/.devmind/config.toml`:

```toml
[github]
client_id = "Iv1.abc123..."
scopes = "repo read:user"
```

Then:

```bash
devmind github login
```

1. Open <https://github.com/login/device>
2. Type the short code DevMind prints
3. Approve access

You will be asked for a passphrase. It derives (scrypt, n=2^14) the key that encrypts the token in
`~/.devmind/credentials.enc` (mode `0600`). Lose the passphrase and the file is useless — log in
again with `devmind github logout && devmind github login`.

### Fine-grained token alternative

```bash
devmind github login --token
# paste the token when prompted
```

Create it at <https://github.com/settings/tokens?type=beta> with read access to contents and write
access to pull requests, limited to the repositories you choose.

## Everyday use

```bash
devmind github status          # who am I, is the token readable
devmind github repos           # includes private repositories
devmind github clone me/app    # into ~/.devmind/repos/me__app
# ... edit, ask DevMind, run tests ...
devmind github pr --repo me/app --branch devmind/fix-refund \
    --title "Fix double refund on retry" --path ~/.devmind/repos/me__app
```

`github pr` stages the clone's working tree, commits, pushes **a new branch**, and opens a pull
request against the default branch. Review and merge it yourself.

## Safety rules (enforced in code)

* `ensure_safe_branch()` refuses `main`, `master`, `trunk`, `develop` and whatever the repository
  says its default branch is. Every push path calls it.
* The token is passed to git through environment-based config
  (`GIT_CONFIG_COUNT` / `GIT_CONFIG_KEY_0`), never in the URL or argv, so `ps` and shell history
  cannot show it. Error output is scrubbed.
* A pull request whose head equals its base is rejected before an HTTP call is made.
* Nothing is force-pushed and no branch is deleted.

## Verification through Actions

If a repo's tests only run in CI:

```bash
devmind verify --github me/app --ref devmind/fix-refund --workflow test.yml
```

DevMind dispatches the workflow on that branch, waits for the run, and prints the conclusion plus
the names of failed jobs/steps. The workflow must have `on: workflow_dispatch` (or be triggered by
a push) and live at `.github/workflows/<file>`. Actions minutes for public repositories are free;
private repositories use the minutes included in your GitHub plan.

## What DevMind cannot do

* Log in on your behalf without the device code / token you approve.
* Read a repository you cannot already access.
* Push to a protected branch, even if you ask it to.
* Guarantee that a pull request is correct — you review it, and tests provide evidence.
