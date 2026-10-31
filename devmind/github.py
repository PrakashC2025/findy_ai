"""GitHub access: your account, your repositories, no third-party service.

What this module can do
-----------------------
* **Device-flow login** - you open a URL, type a short code, done. No password
  ever touches DevMind, and no paid API key exists.
* **Encrypted token storage** - the token is encrypted with a key derived from
  your passphrase (scrypt) and written to a 0600 file. Without the passphrase
  the file is useless.
* **Read private repos** - clone over HTTPS with the token supplied through
  git's environment-based config, never through argv, so it cannot leak via
  ``ps`` or shell history.
* **Propose a fix** - branch, commit, push (refused for the default branch) and
  open a pull request for you to review.

Nothing here pushes to ``main``/``master``/the default branch. That is enforced
by :func:`ensure_safe_branch`, which every push path calls.
"""

from __future__ import annotations

import base64
import contextlib
import json
import os
import stat
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

GITHUB_API = "https://api.github.com"
DEVICE_CODE_URL = "https://github.com/login/device/code"
ACCESS_TOKEN_URL = "https://github.com/login/oauth/access_token"
DEFAULT_PROTECTED = {"main", "master", "trunk", "develop"}


class GitHubError(RuntimeError):
    """Anything GitHub or git refused to do, with an actionable message."""


class UnsafeBranchError(GitHubError):
    """Raised when a push would target a protected/default branch."""


# --------------------------------------------------------------------- crypto


def _fernet(passphrase: str, salt: bytes):
    try:
        from cryptography.fernet import Fernet
        from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
    except ImportError as exc:  # pragma: no cover - dependency is in pyproject
        raise GitHubError(
            "Encrypted GitHub token storage needs the 'cryptography' package. "
            "Install DevMind with `pip install devmind` (it is a dependency)."
        ) from exc
    kdf = Scrypt(salt=salt, length=32, n=2**14, r=8, p=1)
    key = kdf.derive(passphrase.encode("utf-8"))
    return Fernet(base64.urlsafe_b64encode(key))


_CREDENTIAL_HEADER = b"DMC1"


class CredentialStore:
    """Encrypted-at-rest token storage (scrypt -> Fernet, file mode 0600)."""

    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser()

    def exists(self) -> bool:
        return self.path.exists()

    def save(self, token: str, passphrase: str, *, login: str = "", scopes: str = "") -> None:
        if not passphrase:
            raise GitHubError("A passphrase is required to store a GitHub token.")
        if len(passphrase) < 8:
            raise GitHubError("Use a passphrase of at least 8 characters.")
        salt = os.urandom(16)
        cipher = _fernet(passphrase, salt)
        payload = {
            "version": 1,
            "token": token,
            "login": login,
            "scopes": scopes,
            "created_at": time.time(),
        }
        blob = cipher.encrypt(json.dumps(payload).encode("utf-8"))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with contextlib.suppress(OSError):
            os.chmod(self.path.parent, stat.S_IRWXU)
        tmp = self.path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as handle:
            # Fixed-width header: the salt is random bytes and may contain 0x0A,
            # so it can never be separated with a newline.
            handle.write(_CREDENTIAL_HEADER + salt + blob)
        tmp.replace(self.path)
        with contextlib.suppress(OSError):
            os.chmod(self.path, 0o600)

    def load(self, passphrase: str) -> dict[str, Any]:
        if not self.path.exists():
            raise GitHubError("No GitHub login stored. Run `devmind github login`.")
        from cryptography.fernet import InvalidToken

        raw = self.path.read_bytes()
        if raw.startswith(_CREDENTIAL_HEADER):
            salt, blob = raw[len(_CREDENTIAL_HEADER) :][:16], raw[len(_CREDENTIAL_HEADER) + 16 :]
        else:  # legacy files written before the header existed
            salt, _, blob = raw.partition(b"\n")
        cipher = _fernet(passphrase, salt)
        try:
            payload = json.loads(cipher.decrypt(blob))
        except (InvalidToken, json.JSONDecodeError, ValueError) as exc:
            raise GitHubError("Wrong passphrase for the stored GitHub token.") from exc
        return payload

    def delete(self) -> bool:
        if self.path.exists():
            self.path.unlink()
            return True
        return False


# ---------------------------------------------------------------- device flow


@dataclass
class DeviceCode:
    device_code: str
    user_code: str
    verification_uri: str
    expires_in: int
    interval: int

    @property
    def instructions(self) -> str:
        return (
            f"1. Open {self.verification_uri} on any device\n"
            f"2. Enter the code: {self.user_code}\n"
            "3. Approve access for DevMind (scope: repo)"
        )


class DeviceFlow:
    """OAuth device flow against github.com (no client secret shipped)."""

    def __init__(
        self,
        client_id: str,
        *,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 30.0,
    ):
        if not client_id:
            raise GitHubError(
                "No GitHub OAuth client id configured.\n"
                "Create a free OAuth App at https://github.com/settings/developers\n"
                "(any name, any homepage; no callback URL is needed) and put its Client ID in\n"
                '~/.devmind/config.toml as [github] client_id = "...".\n'
                "Or create a fine-grained token yourself and store it with "
                "`devmind github token` (coming with the CLI)."
            )
        self.client_id = client_id
        self._client = httpx.Client(timeout=timeout, transport=transport)

    def start(self, scopes: str = "repo read:user") -> DeviceCode:
        response = self._client.post(
            DEVICE_CODE_URL,
            data={"client_id": self.client_id, "scope": scopes},
            headers={"Accept": "application/json"},
        )
        payload = _json(response, "starting the GitHub device flow")
        if payload.get("error"):
            raise GitHubError(f"GitHub refused the device flow: {payload['error']}")
        return DeviceCode(
            device_code=payload["device_code"],
            user_code=payload["user_code"],
            verification_uri=payload.get("verification_uri", "https://github.com/login/device"),
            expires_in=int(payload.get("expires_in", 900)),
            interval=int(payload.get("interval", 5)),
        )

    def poll(self, code: DeviceCode, *, timeout: float | None = None) -> str:
        """Block until the user approves (or the code expires). Returns the token."""
        deadline = time.monotonic() + (timeout if timeout is not None else code.expires_in)
        interval = max(1, code.interval)
        while time.monotonic() < deadline:
            response = self._client.post(
                ACCESS_TOKEN_URL,
                data={
                    "client_id": self.client_id,
                    "device_code": code.device_code,
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                },
                headers={"Accept": "application/json"},
            )
            payload = _json(response, "polling the GitHub device flow")
            if payload.get("access_token"):
                return str(payload["access_token"])
            error = payload.get("error")
            if error == "authorization_pending":
                time.sleep(interval)
                continue
            if error == "slow_down":
                interval += 5
                time.sleep(interval)
                continue
            if error == "expired_token":
                raise GitHubError("The GitHub device code expired. Run login again.")
            if error == "access_denied":
                raise GitHubError("You cancelled the GitHub authorization.")
            raise GitHubError(f"GitHub login failed: {error or payload}")
        raise GitHubError("Timed out waiting for GitHub authorization.")

    def close(self) -> None:
        self._client.close()


# ------------------------------------------------------------------ API client


class GitHubClient:
    """Thin, typed wrapper around the parts of the REST API DevMind needs."""

    def __init__(
        self,
        token: str,
        *,
        api_url: str = GITHUB_API,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 30.0,
    ):
        self._client = httpx.Client(
            base_url=api_url.rstrip("/"),
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "devmind/0.2",
            },
            timeout=timeout,
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    # ------------------------------------------------------------------ reads
    def whoami(self) -> dict[str, Any]:
        return self._request("GET", "/user")

    def list_repos(self, *, per_page: int = 100, max_pages: int = 3) -> list[dict[str, Any]]:
        repos: list[dict[str, Any]] = []
        for page in range(1, max_pages + 1):
            batch = self._request(
                "GET",
                "/user/repos",
                params={
                    "per_page": per_page,
                    "page": page,
                    "sort": "updated",
                    "affiliation": "owner,collaborator,organization_member",
                },
            )
            if not isinstance(batch, list) or not batch:
                break
            repos.extend(
                {
                    "full_name": repo.get("full_name", ""),
                    "private": bool(repo.get("private")),
                    "default_branch": repo.get("default_branch", "main"),
                    "updated_at": repo.get("updated_at", ""),
                    "language": repo.get("language") or "",
                }
                for repo in batch
            )
            if len(batch) < per_page:
                break
        return repos

    def get_repo(self, repo: str) -> dict[str, Any]:
        return self._request("GET", f"/repos/{repo}")

    def default_branch(self, repo: str) -> str:
        return str(self.get_repo(repo).get("default_branch") or "main")

    def get_file(self, repo: str, path: str, ref: str | None = None) -> str:
        response = self._client.get(
            f"/repos/{repo}/contents/{path}",
            params={"ref": ref} if ref else None,
            headers={"Accept": "application/vnd.github.raw"},
        )
        if response.status_code == 404:
            raise GitHubError(f"{repo}:{path} does not exist at {ref or 'the default branch'}.")
        if response.status_code >= 400:
            raise GitHubError(f"GitHub returned {response.status_code} reading {path}.")
        return response.text

    # ----------------------------------------------------------------- writes
    def create_pull(
        self,
        repo: str,
        *,
        title: str,
        head: str,
        base: str,
        body: str = "",
        draft: bool = False,
    ) -> dict[str, Any]:
        if head == base:
            raise UnsafeBranchError(f"Refusing to open a pull request from '{head}' into itself.")
        return self._request(
            "POST",
            f"/repos/{repo}/pulls",
            json={"title": title, "head": head, "base": base, "body": body, "draft": draft},
        )

    def list_pulls(self, repo: str, *, head: str | None = None, state: str = "open") -> list:
        params: dict[str, Any] = {"state": state, "per_page": 20}
        if head:
            params["head"] = head
        result = self._request("GET", f"/repos/{repo}/pulls", params=params)
        return result if isinstance(result, list) else []

    # ------------------------------------------------------------- workflows
    def dispatch_workflow(
        self, repo: str, workflow: str, ref: str, inputs: dict[str, Any] | None = None
    ) -> int:
        response = self._client.post(
            f"/repos/{repo}/actions/workflows/{workflow}/dispatches",
            json={"ref": ref, "inputs": inputs or {}},
        )
        if response.status_code not in (201, 204):
            raise GitHubError(_error_message(response, f"dispatching {workflow}"))
        return response.status_code

    def workflow_runs(
        self,
        repo: str,
        *,
        branch: str | None = None,
        per_page: int = 10,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"per_page": per_page}
        if branch:
            params["branch"] = branch
        payload = self._request("GET", f"/repos/{repo}/actions/runs", params=params)
        runs = payload.get("workflow_runs", []) if isinstance(payload, dict) else []
        return list(runs)

    def run_jobs(self, repo: str, run_id: int) -> list[dict[str, Any]]:
        payload = self._request("GET", f"/repos/{repo}/actions/runs/{run_id}/jobs")
        return list(payload.get("jobs", [])) if isinstance(payload, dict) else []

    # --------------------------------------------------------------- helpers
    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        response = self._client.request(method, path, **kwargs)
        if response.status_code >= 400:
            raise GitHubError(_error_message(response, f"{method} {path}"))
        if not response.content:
            return {}
        try:
            return response.json()
        except json.JSONDecodeError:
            return {"text": response.text}

    @property
    def client(self) -> httpx.Client:
        return self._client


def _json(response: httpx.Response, action: str) -> dict[str, Any]:
    if response.status_code >= 400:
        raise GitHubError(_error_message(response, action))
    try:
        return response.json()
    except json.JSONDecodeError as exc:
        raise GitHubError(f"Unexpected response from GitHub while {action}.") from exc


def _error_message(response: httpx.Response, action: str) -> str:
    try:
        payload = response.json()
        message = payload.get("message") or payload.get("error_description") or ""
    except (json.JSONDecodeError, ValueError):
        message = response.text[:200]
    return f"GitHub error while {action}: HTTP {response.status_code} {message}".strip()


# ------------------------------------------------------------------ local git


def ensure_safe_branch(branch: str, default_branch: str) -> None:
    """Refuse to push anywhere that could rewrite a protected branch."""
    if not branch or branch.startswith("-"):
        raise UnsafeBranchError(f"Invalid branch name: {branch!r}")
    if branch == default_branch or branch in DEFAULT_PROTECTED:
        raise UnsafeBranchError(
            f"Refusing to push to protected branch '{branch}'. "
            "DevMind only pushes to a new branch and opens a pull request."
        )


@dataclass
class GitResult:
    command: list[str]
    ok: bool
    exit_code: int
    output: str = ""


class GitWorkspace:
    """A local clone with token auth supplied via environment, not argv."""

    def __init__(self, root: str | Path, *, token: str = "", timeout: float = 180.0):
        self.root = Path(root).expanduser()
        self.token = token
        self.timeout = timeout

    # ------------------------------------------------------------------ setup
    def clone(self, repo: str, dest: str | Path | None = None, *, depth: int | None = None) -> Path:
        target = Path(dest).expanduser() if dest else self.root / repo.split("/")[-1]
        if target.exists() and any(target.iterdir()):
            return target  # already cloned; caller can `fetch`
        target.parent.mkdir(parents=True, exist_ok=True)
        if Path(repo).exists():
            url = str(Path(repo).resolve())  # local path (tests, local remotes)
        elif repo.startswith(("http://", "https://", "git@", "file://")):
            url = repo
        else:
            url = f"https://github.com/{repo}.git"
        args = ["clone"]
        if depth:
            args += ["--depth", str(depth)]
        args += [url, str(target)]
        result = self._run(args, cwd=target.parent)
        if not result.ok:
            raise GitHubError(f"git clone failed for {repo}: {result.output[-500:]}")
        return target

    # ------------------------------------------------------------------ reads
    def current_branch(self, cwd: str | Path | None = None) -> str:
        return self._git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=cwd).output.strip()

    def default_branch(self, cwd: str | Path | None = None) -> str:
        result = self._git(["symbolic-ref", "refs/remotes/origin/HEAD"], cwd=cwd, check=False)
        if result.ok and "/" in result.output:
            return result.output.strip().rsplit("/", 1)[-1]
        for candidate in ("main", "master"):
            if self._git(["rev-parse", "--verify", f"origin/{candidate}"], cwd=cwd, check=False).ok:
                return candidate
        return "main"

    def fetch(self, cwd: str | Path | None = None) -> None:
        self._git(["fetch", "origin"], cwd=cwd, check=False)

    def status(self, cwd: str | Path | None = None) -> list[str]:
        return [
            line
            for line in self._git(["status", "--porcelain"], cwd=cwd).output.splitlines()
            if line.strip()
        ]

    def diff(self, cwd: str | Path | None = None) -> str:
        self._git(["add", "-N", "."], cwd=cwd, check=False)
        return self._git(["diff"], cwd=cwd, check=False).output

    def read_file(self, path: str, cwd: str | Path | None = None) -> str:
        target = (Path(cwd) if cwd else self.root) / path
        return target.read_text(encoding="utf-8", errors="replace")

    # ----------------------------------------------------------------- writes
    def create_branch(
        self, name: str, *, base: str = "main", cwd: str | Path | None = None
    ) -> None:
        ensure_safe_branch(name, base)
        result = self._git(["checkout", "-b", name], cwd=cwd)
        if not result.ok:
            raise GitHubError(f"Could not create branch {name}: {result.output[-400:]}")

    def write_files(self, files: dict[str, str], cwd: str | Path | None = None) -> list[str]:
        base = Path(cwd) if cwd else self.root
        written: list[str] = []
        for relative, content in files.items():
            if relative.startswith("/") or ".." in Path(relative).parts:
                raise GitHubError(f"Refusing to write outside the repo: {relative}")
            target = base / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            written.append(relative)
        return written

    def commit(
        self,
        message: str,
        *,
        files: list[str] | None = None,
        author_name: str = "DevMind",
        author_email: str = "devmind@localhost",
        cwd: str | Path | None = None,
    ) -> str:
        add = ["add", "--", *files] if files else ["add", "-A"]
        self._git(add, cwd=cwd)
        result = self._git(
            [
                "-c",
                f"user.name={author_name}",
                "-c",
                f"user.email={author_email}",
                "commit",
                "-m",
                message,
            ],
            cwd=cwd,
        )
        if not result.ok:
            raise GitHubError(f"git commit failed: {result.output[-400:]}")
        return self._git(["rev-parse", "HEAD"], cwd=cwd).output.strip()

    def push(self, branch: str, *, default_branch: str, cwd: str | Path | None = None) -> None:
        ensure_safe_branch(branch, default_branch)
        result = self._git(["push", "-u", "origin", branch], cwd=cwd)
        if not result.ok:
            raise GitHubError(f"git push failed: {result.output[-500:]}")

    # --------------------------------------------------------------- internals
    def _git(
        self, args: list[str], *, cwd: str | Path | None = None, check: bool = True
    ) -> GitResult:
        return self._run(args, cwd=cwd or self.root, check=check)

    def _run(
        self, args: list[str], *, cwd: str | Path | None = None, check: bool = True
    ) -> GitResult:
        env = self._env()
        try:
            completed = subprocess.run(
                ["git", *args],
                cwd=str(cwd or self.root),
                capture_output=True,
                text=True,
                timeout=self.timeout,
                env=env,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            result = GitResult(["git", *args], False, 1, str(exc))
            if check:
                raise GitHubError(f"git {args[0]} could not run: {exc}") from exc
            return result
        output = (completed.stdout or "") + (completed.stderr or "")
        result = GitResult(
            ["git", *args], completed.returncode == 0, completed.returncode, self._scrub(output)
        )
        if check and not result.ok:
            raise GitHubError(f"git {args[0]} failed: {output[-500:]}")
        return result

    def _env(self) -> dict[str, str]:
        env = os.environ.copy()
        env.pop("GITHUB_TOKEN", None)
        env.pop("GH_TOKEN", None)
        if self.token:
            # Token goes through git config env vars, never argv or the URL.
            credential = base64.b64encode(f"x-access-token:{self.token}".encode()).decode("ascii")
            env.update(
                {
                    "GIT_CONFIG_COUNT": "1",
                    "GIT_CONFIG_KEY_0": "http.https://github.com/.extraheader",
                    "GIT_CONFIG_VALUE_0": f"AUTHORIZATION: basic {credential}",
                    "GIT_TERMINAL_PROMPT": "0",
                }
            )
        return env

    def _scrub(self, text: str) -> str:
        if self.token and self.token in text:
            text = text.replace(self.token, "[REDACTED:github_token]")
        return text


# --------------------------------------------------------------- high-level PR


@dataclass
class PullRequestResult:
    repo: str
    branch: str
    base: str
    number: int | None = None
    url: str = ""
    commit: str = ""
    files: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


def propose_fix(
    *,
    repo: str,
    token: str,
    workdir: str | Path,
    files: dict[str, str],
    branch: str,
    title: str,
    body: str = "",
    commit_message: str | None = None,
    clone_url: str | None = None,
    api_url: str = GITHUB_API,
    transport: httpx.BaseTransport | None = None,
    draft: bool = False,
) -> PullRequestResult:
    """Clone, branch, commit, push and open a PR - never touching the default branch."""
    workspace = GitWorkspace(workdir, token=token)
    root = workspace.clone(clone_url or repo)
    default = workspace.default_branch(cwd=root)
    ensure_safe_branch(branch, default)

    workspace.fetch(cwd=root)
    workspace.create_branch(branch, base=default, cwd=root)
    written = workspace.write_files(files, cwd=root)
    commit_sha = workspace.commit(commit_message or title, files=written, cwd=root)
    workspace.push(branch, default_branch=default, cwd=root)

    client = GitHubClient(token, api_url=api_url, transport=transport)
    try:
        pull = client.create_pull(
            repo, title=title, head=branch, base=default, body=body, draft=draft
        )
    finally:
        client.close()
    return PullRequestResult(
        repo=repo,
        branch=branch,
        base=default,
        number=pull.get("number"),
        url=pull.get("html_url", ""),
        commit=commit_sha,
        files=written,
    )
