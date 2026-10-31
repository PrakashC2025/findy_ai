from __future__ import annotations

import subprocess
import time
from pathlib import Path

import httpx
import pytest

from devmind.github import (
    CredentialStore,
    DeviceFlow,
    GitHubClient,
    GitHubError,
    GitWorkspace,
    UnsafeBranchError,
    ensure_safe_branch,
    propose_fix,
)


# ----------------------------------------------------------------- credentials


def test_token_is_encrypted_and_roundtrips(tmp_path: Path) -> None:
    store = CredentialStore(tmp_path / "credentials.enc")
    token = "ghp_" + "a" * 36
    store.save(token, "correct horse battery", login="prakash")
    raw = (tmp_path / "credentials.enc").read_bytes()
    assert token.encode() not in raw
    assert oct((tmp_path / "credentials.enc").stat().st_mode & 0o777) == "0o600"

    payload = store.load("correct horse battery")
    assert payload["token"] == token
    assert payload["login"] == "prakash"

    with pytest.raises(GitHubError):
        store.load("wrong passphrase")
    assert store.delete() is True
    assert not store.exists()


def test_salt_containing_a_newline_still_roundtrips(tmp_path: Path, monkeypatch) -> None:
    """Regression: the salt is raw random bytes, so it may contain 0x0A."""
    monkeypatch.setattr("devmind.github.os.urandom", lambda _n: b"\n" * 16)
    store = CredentialStore(tmp_path / "credentials.enc")
    store.save("token-value", "correct horse battery")
    assert store.load("correct horse battery")["token"] == "token-value"


def test_short_passphrase_is_rejected(tmp_path: Path) -> None:
    store = CredentialStore(tmp_path / "credentials.enc")
    with pytest.raises(GitHubError):
        store.save("token", "short")


# ------------------------------------------------------------------ device flow


def test_device_flow_polls_until_approved(monkeypatch, tmp_path: Path) -> None:
    calls = {"token": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/login/device/code":
            return httpx.Response(
                200,
                json={
                    "device_code": "dev123",
                    "user_code": "ABCD-1234",
                    "verification_uri": "https://github.com/login/device",
                    "expires_in": 900,
                    "interval": 1,
                },
            )
        calls["token"] += 1
        if calls["token"] == 1:
            return httpx.Response(200, json={"error": "authorization_pending"})
        return httpx.Response(200, json={"access_token": "gho_token", "token_type": "bearer"})

    monkeypatch.setattr("devmind.github.time.sleep", lambda seconds: None)
    flow = DeviceFlow("client123", transport=httpx.MockTransport(handler))
    code = flow.start("repo read:user")
    assert code.user_code == "ABCD-1234"
    assert "github.com/login/device" in code.instructions
    assert flow.poll(code) == "gho_token"
    assert calls["token"] == 2


def test_device_flow_requires_client_id() -> None:
    with pytest.raises(GitHubError) as excinfo:
        DeviceFlow("")
    assert "OAuth App" in str(excinfo.value)


# ----------------------------------------------------------------- branch guard


@pytest.mark.parametrize("branch", ["main", "master", "trunk", "feature-default"])
def test_protected_branches_are_refused(branch: str) -> None:
    default = "feature-default" if branch == "feature-default" else "main"
    with pytest.raises(UnsafeBranchError):
        ensure_safe_branch(branch, default)


def test_feature_branch_is_allowed() -> None:
    ensure_safe_branch("devmind/fix-refund", "main")


# --------------------------------------------------------------------- API client


def test_api_client_reads_repos_and_gates_pull_requests() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/user/repos":
            return httpx.Response(
                200,
                json=[
                    {
                        "full_name": "me/private-app",
                        "private": True,
                        "default_branch": "main",
                        "updated_at": "2025-10-01T00:00:00Z",
                        "language": "Python",
                    }
                ],
            )
        if request.url.path.endswith("/pulls") and request.method == "POST":
            return httpx.Response(
                201,
                json={"number": 5, "html_url": "https://github.com/me/private-app/pull/5"},
            )
        return httpx.Response(404, json={"message": "Not Found"})

    client = GitHubClient("token", transport=httpx.MockTransport(handler))
    repos = client.list_repos()
    assert repos[0]["full_name"] == "me/private-app"
    assert repos[0]["private"] is True

    pull = client.create_pull("me/private-app", title="Fix", head="devmind/fix", base="main")
    assert pull["number"] == 5

    with pytest.raises(UnsafeBranchError):
        client.create_pull("me/private-app", title="Fix", head="main", base="main")
    client.close()


def test_api_errors_are_readable() -> None:
    client = GitHubClient(
        "token",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(403, json={"message": "Bad credentials"})
        ),
    )
    with pytest.raises(GitHubError) as excinfo:
        client.whoami()
    assert "403" in str(excinfo.value)
    assert "Bad credentials" in str(excinfo.value)
    client.close()


# ------------------------------------------------------------------- local git


def git(*args: str, cwd: Path | None = None) -> str:
    completed = subprocess.run(
        [
            "git",
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.com",
            *args,
        ],
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        check=True,
    )
    return completed.stdout.strip()


@pytest.fixture()
def bare_remote(tmp_path: Path) -> Path:
    bare = tmp_path / "remote.git"
    git("init", "--bare", "-b", "main", str(bare))
    seed = tmp_path / "seed"
    seed.mkdir()
    git("init", "-b", "main", str(seed))
    (seed / "app.py").write_text("print('hello')\n")
    git("add", "-A", cwd=seed)
    git("commit", "-m", "init", cwd=seed)
    git("remote", "add", "origin", str(bare), cwd=seed)
    git("push", "origin", "main", cwd=seed)
    git("--git-dir", str(bare), "symbolic-ref", "HEAD", "refs/heads/main")
    return bare


def test_workspace_branches_commits_and_pushes(tmp_path: Path, bare_remote: Path) -> None:
    workspace = GitWorkspace(tmp_path, token="ghp_secret")
    clone = workspace.clone(str(bare_remote), tmp_path / "clone")
    assert (clone / "app.py").exists()
    assert workspace.default_branch(cwd=clone) == "main"

    workspace.create_branch("devmind/fix-greeting", base="main", cwd=clone)
    workspace.write_files({"app.py": "print('hello world')\n"}, cwd=clone)
    workspace.commit("fix: greet properly", cwd=clone)
    workspace.push("devmind/fix-greeting", default_branch="main", cwd=clone)

    branches = git("--git-dir", str(bare_remote), "branch", "--list")
    assert "devmind/fix-greeting" in branches

    with pytest.raises(UnsafeBranchError):
        workspace.push("main", default_branch="main", cwd=clone)


def test_workspace_scrubs_the_token_from_errors(tmp_path: Path, bare_remote: Path) -> None:
    # devmind: allow secret.generic
    workspace = GitWorkspace(tmp_path, token="ghp_supersecret")
    assert workspace._scrub("fatal: ghp_supersecret rejected") == (
        "fatal: [REDACTED:github_token] rejected"
    )


def test_propose_fix_opens_a_pull_request(tmp_path: Path, bare_remote: Path) -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        return httpx.Response(
            201, json={"number": 9, "html_url": "https://github.com/me/app/pull/9"}
        )

    result = propose_fix(
        repo="me/app",
        clone_url=str(bare_remote),
        token="ghp_token",
        workdir=tmp_path / "work",
        files={"app.py": "print('fixed')\n"},
        branch=f"devmind/fix-{int(time.time())}",
        title="Fix greeting",
        body="Tests: local pytest",
        api_url="http://api.test",
        transport=httpx.MockTransport(handler),
    )
    assert result.number == 9
    assert result.base == "main"
    assert result.branch.startswith("devmind/fix-")
    assert result.files == ["app.py"]
    assert result.commit
    assert seen["path"] == "/repos/me/app/pulls"
