"""Security rules must fire on real mistakes and stay quiet on clean code.

This file is full of deliberate vulnerabilities used as fixtures.
# devmind: allow-file *
"""

from __future__ import annotations

from pathlib import Path

from devmind.security_scan import (
    blocking,
    language_for,
    scan_diff,
    scan_paths,
    scan_text,
    summarise,
)


def test_clean_python_has_no_findings() -> None:
    code = (
        "import json\n"
        "from pathlib import Path\n\n"
        "def load(path: Path) -> dict:\n"
        "    return json.loads(path.read_text())\n"
    )
    assert scan_text("app.py", code, language="python") == []


def test_eval_exec_and_shell_are_flagged() -> None:
    code = "value = eval(user_input)\nexec(code)\n"
    rules = {finding.rule for finding in scan_text("x.py", code, language="python")}
    assert {"code.eval", "code.exec"} <= rules

    shell = "subprocess.run(cmd, shell=True)\n"
    rules = {finding.rule for finding in scan_text("x.py", shell, language="python")}
    assert "code.shell_true" in rules


def test_unsafe_deserialisation_and_yaml() -> None:
    code = "import pickle\ndata = pickle.loads(blob)\ncfg = yaml.load(stream)\nsafe = yaml.load(stream, Loader=yaml.SafeLoader)\n"
    findings = scan_text("x.py", code, language="python")
    rules = [finding.rule for finding in findings]
    assert rules.count("code.yaml_unsafe") == 1
    assert "code.pickle" in rules


def test_sql_built_by_interpolation_is_high_severity() -> None:
    code = 'cursor.execute(f"SELECT * FROM users WHERE id = {user_id}")\n'
    findings = scan_text("db.py", code, language="python")
    assert any(
        finding.rule == "sql.interpolation" and finding.severity == "high" for finding in findings
    )
    assert blocking(findings)

    parameterised = 'cursor.execute("SELECT * FROM users WHERE id = ?", (user_id,))\n'
    assert scan_text("db.py", parameterised, language="python") == []


def test_secrets_are_detected_without_echoing_them() -> None:
    key = "sk-" + "a" * 32
    code = f'OPENAI = "{key}"\n'
    findings = scan_text("config.py", code, language="python")
    assert any(finding.rule == "secret.openai_key" for finding in findings)


def test_language_filter_prevents_cross_language_noise() -> None:
    code = "eval(x)\n"
    assert scan_text("a.js", code, language="javascript") == []
    assert scan_text("a.py", code, language="python")


def test_scan_diff_only_judges_added_lines() -> None:
    diff = (
        "--- a/app.py\n"
        "+++ b/app.py\n"
        "@@ -1,2 +1,3 @@\n"
        " import os\n"
        "-safe = 1\n"
        "+safe = 2\n"
        "+danger = eval(payload)\n"
    )
    findings = scan_diff(diff, language="python")
    assert [finding.rule for finding in findings] == ["code.eval"]
    assert "eval(payload)" in findings[0].excerpt


def test_scan_paths_skips_vendored_directories(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("value = eval(user_input)\n")
    vendor = tmp_path / "node_modules" / "pkg"
    vendor.mkdir(parents=True)
    (vendor / "index.js").write_text("eval(x)\n")
    findings = scan_paths(tmp_path)
    assert {finding.path for finding in findings} == {"app.py"}


def test_summarise_and_language_helpers() -> None:
    findings = scan_text("x.py", "eval(1)\n", language="python")
    assert "1 high" in summarise(findings)
    assert summarise([]) == "security scan: clean"
    assert language_for("a/b/c.tsx") == "typescript"
    assert language_for("README.md") == ""


def test_dynamic_sql_is_a_warning_not_a_blocker() -> None:
    findings = scan_text(
        "db.py", 'cursor.execute(f"SELECT * FROM {table} WHERE id = ?", (x,))\n', language="python"
    )
    assert [finding.rule for finding in findings] == ["sql.dynamic"]
    assert findings[0].severity == "medium"
    assert not blocking(findings)


def test_allow_comment_suppresses_a_specific_rule() -> None:
    line = "value = eval(x)  # devmind: allow code.eval\n"
    assert scan_text("x.py", line, language="python") == []
    other = "subprocess.run(cmd, shell=True)  # devmind: allow code.eval\n"
    assert [finding.rule for finding in scan_text("x.py", other, language="python")] == [
        "code.shell_true"
    ]


def test_allow_comment_covers_the_line_below() -> None:
    text = "# devmind: allow code.eval\nvalue = eval(x)\n"
    assert scan_text("x.py", text, language="python") == []


def test_allow_file_directive_suppresses_everything_in_that_file() -> None:
    text = "# devmind: allow-file *\nvalue = eval(x)\nsubprocess.run(cmd, shell=True)\n"
    assert scan_text("x.py", text, language="python") == []


def test_allow_file_can_name_a_single_rule() -> None:
    text = "# devmind: allow-file code.eval\nvalue = eval(x)\nsubprocess.run(cmd, shell=True)\n"
    rules = [finding.rule for finding in scan_text("x.py", text, language="python")]
    assert rules == ["code.shell_true"]
