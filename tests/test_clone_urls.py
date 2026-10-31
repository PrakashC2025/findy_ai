"""People paste browser URLs; ``git clone`` does not accept them. This normalises the input."""

from __future__ import annotations

import pytest

from devmind.github import normalise_git_url, repo_name_from_url


@pytest.mark.parametrize(
    ("pasted", "expected"),
    [
        (
            "https://github.com/PrakashC2025/findy_ai/tree/main/src",
            "https://github.com/PrakashC2025/findy_ai.git",
        ),
        (
            "https://github.com/PrakashC2025/findy_ai",
            "https://github.com/PrakashC2025/findy_ai.git",
        ),
        (
            "https://github.com/PrakashC2025/findy_ai/",
            "https://github.com/PrakashC2025/findy_ai.git",
        ),
        (
            "https://github.com/o/r/blob/main/README.md?plain=1",
            "https://github.com/o/r.git",
        ),
        ("https://github.com/o/r.git", "https://github.com/o/r.git"),
        ("git@github.com:o/r.git", "git@github.com:o/r.git"),
        ("o/r", "o/r"),
        ("", ""),
    ],
)
def test_pasted_urls_become_clone_urls(pasted: str, expected: str) -> None:
    assert normalise_git_url(pasted) == expected


@pytest.mark.parametrize(
    ("pasted", "name"),
    [
        ("https://github.com/o/repo/tree/main", "repo"),
        ("https://github.com/o/repo.git", "repo"),
        ("git@github.com:o/repo.git", "repo"),
        ("owner/repo", "repo"),
    ],
)
def test_names_are_short_and_clean(pasted: str, name: str) -> None:
    assert repo_name_from_url(pasted) == name


def test_local_paths_are_left_alone(tmp_path) -> None:
    assert normalise_git_url(str(tmp_path)) == str(tmp_path)
    assert repo_name_from_url(str(tmp_path)) == tmp_path.name
