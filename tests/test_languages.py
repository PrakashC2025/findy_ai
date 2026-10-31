"""Language packs: detection, docs links and honest command lists."""

from __future__ import annotations

import json
from pathlib import Path

from devmind.languages import (
    LANGUAGE_PACKS,
    detect_languages,
    domains_in,
    language_for_path,
    project_packs,
    style_block,
)


def _react_project(tmp_path: Path) -> Path:
    root = tmp_path / "web"
    (root / "src").mkdir(parents=True)
    (root / "package.json").write_text(
        json.dumps({"dependencies": {"react": "^19.0.0", "next": "^15.0.0"}})
    )
    (root / "tsconfig.json").write_text("{}")
    (root / "src" / "App.tsx").write_text("export const App = () => <div>hi</div>;\n")
    (root / "src" / "util.ts").write_text("export const add = (a: number, b: number) => a + b;\n")
    return root


def test_every_pack_is_documented_and_typed() -> None:
    for pack in LANGUAGE_PACKS.values():
        assert pack.name and pack.extensions
        assert pack.docs.startswith("https://"), pack.id


def test_detects_typescript_beats_javascript_in_a_ts_project(tmp_path: Path) -> None:
    root = _react_project(tmp_path)
    ranked = detect_languages(root)
    assert ranked[0]["id"] == "typescript"
    ids = [pack.id for pack in project_packs(root)]
    assert "typescript" in ids
    assert "javascript" not in ids


def test_python_project_gets_python_pack(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    (tmp_path / "app.py").write_text("print(1)\n")
    packs = [pack.id for pack in project_packs(tmp_path)]
    assert packs == ["python"]


def test_style_block_contains_notes_and_pitfalls(tmp_path: Path) -> None:
    root = _react_project(tmp_path)
    block = style_block(root)
    assert "PROJECT LANGUAGE PACKS" in block
    assert "Pitfalls:" in block


def test_domains_are_detected_from_request_and_manifest(tmp_path: Path) -> None:
    root = _react_project(tmp_path)
    found = domains_in("Add an accessible React form and a Dockerfile", root)
    assert "react" in found
    assert "docker" in found


def test_extension_to_pack_and_empty_project(tmp_path: Path) -> None:
    assert language_for_path("a/b.py").id == "python"  # type: ignore[union-attr]
    assert language_for_path("a/b.unknown") is None
    assert detect_languages(tmp_path / "missing") == []
    assert project_packs(tmp_path) == []
    assert style_block(tmp_path) == ""


def test_sql_pack_admits_it_has_no_runner() -> None:
    sql = LANGUAGE_PACKS["sql"]
    assert sql.test == ""
    assert "dialect" in " ".join(sql.notes).lower()
