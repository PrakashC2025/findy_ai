"""Starters must produce real, runnable-looking projects - not empty folders."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from devmind.starters import available, starter, write_starter


def test_every_kind_is_described() -> None:
    kinds = available()
    assert set(kinds) == {"website", "react", "api", "mobile"}
    assert all(description for description in kinds.values())


@pytest.mark.parametrize("kind", ["website", "react", "api", "mobile"])
def test_starter_writes_files(kind: str, tmp_path: Path) -> None:
    written = write_starter(kind, tmp_path / kind)
    assert written and all(path.exists() for path in written)
    template = starter(kind)
    assert template.checks


def test_website_is_mobile_ready_and_accessible(tmp_path: Path) -> None:
    write_starter("website", tmp_path / "site")
    html = (tmp_path / "site" / "index.html").read_text()
    css = (tmp_path / "site" / "styles.css").read_text()
    js = (tmp_path / "site" / "app.js").read_text()
    assert 'name="viewport"' in html
    assert "prefers-reduced-motion" in css
    assert ":focus-visible" in css
    assert "aria-live" in html
    assert "addEventListener" in js


def test_api_starter_parses_and_has_tests(tmp_path: Path) -> None:
    write_starter("api", tmp_path / "api")
    source = (tmp_path / "api" / "app" / "main.py").read_text()
    ast.parse(source)
    assert "/health" in source
    assert "HTTPException" in source
    assert (tmp_path / "api" / "tests" / "test_api.py").exists()


def test_react_starter_has_entrypoint_and_build_script(tmp_path: Path) -> None:
    write_starter("react", tmp_path / "app")
    package = (tmp_path / "app" / "package.json").read_text()
    assert '"build"' in package and "vite" in package
    assert (tmp_path / "app" / "src" / "App.jsx").exists()


def test_mobile_starter_is_flutter(tmp_path: Path) -> None:
    write_starter("mobile", tmp_path / "phone")
    pubspec = (tmp_path / "phone" / "pubspec.yaml").read_text()
    main = (tmp_path / "phone" / "lib" / "main.dart").read_text()
    assert "flutter:" in pubspec
    assert "MaterialApp" in main
    assert "NavigationBar" in main
    assert (tmp_path / "phone" / "test" / "widget_test.dart").exists()


def test_never_overwrites_without_force(tmp_path: Path) -> None:
    target = tmp_path / "existing"
    target.mkdir()
    (target / "keep.txt").write_text("do not delete")
    with pytest.raises(FileExistsError):
        write_starter("website", target)
    write_starter("website", target, force=True)
    assert (target / "index.html").exists()
    assert (target / "keep.txt").exists()
