"""Hook runner: stdin contract, env, failure reporting, path resolution."""

from __future__ import annotations

import json
import shlex
import subprocess
import sys
from pathlib import Path

from jobradar import hooks
from jobradar.config import load_config

JOB = {
    "uid": "abc123",
    "company": "Acme",
    "title": "ML Platform Engineer",
    "url": "https://example.invalid/1",
    "lane": "de",
    "status": "applied",
}

CONSUMER = """
import json, os, sys
job = json.load(sys.stdin)
print(f"{job['company']}|{os.environ['JOBRADAR_UID']}|{os.environ['JOBRADAR_HOOK']}")
"""


def test_missing_hook_is_a_no_op() -> None:
    ok, message = hooks.run_hook("applied", {}, JOB)
    assert ok is True
    assert message == hooks.NO_HOOK


def test_hook_receives_the_job_on_stdin_and_env() -> None:
    cfg = {"hooks": {"applied": [sys.executable, "-c", CONSUMER]}}
    ok, message = hooks.run_hook("applied", cfg, JOB)
    assert ok is True
    assert message == "Acme|abc123|applied"


def test_hook_is_selected_by_status_not_shared() -> None:
    cfg = {"hooks": {"applied": [sys.executable, "-c", "print('ran')"]}}
    ok, message = hooks.run_hook("shortlist", cfg, JOB)
    assert ok is True and message == hooks.NO_HOOK  # shortlist has no hook


def test_env_var_can_supply_the_hook(monkeypatch) -> None:
    monkeypatch.setenv("JOBRADAR_HOOK_APPLIED", f"{sys.executable} -c \"print('from env')\"")
    ok, message = hooks.run_hook("applied", {}, JOB)
    assert ok is True
    assert message == "from env"


def test_failing_hook_reports_exit_code_and_stderr() -> None:
    cfg = {"hooks": {"applied": [sys.executable, "-c", "import sys; sys.stderr.write('gh: not logged in'); sys.exit(3)"]}}
    ok, message = hooks.run_hook("applied", cfg, JOB)
    assert ok is False
    assert "exit 3" in message and "gh: not logged in" in message


def test_hook_timeout_is_reported_not_raised() -> None:
    cfg = {"hooks": {"applied": [sys.executable, "-c", "import time; time.sleep(30)"]}}
    ok, message = hooks.run_hook("applied", cfg, JOB, timeout=1)
    assert ok is False
    assert "timed out" in message


def test_unstartable_hook_is_reported_not_raised() -> None:
    cfg = {"hooks": {"applied": ["/nonexistent/definitely-not-here.sh"]}}
    ok, message = hooks.run_hook("applied", cfg, JOB)
    assert ok is False
    assert "failed to start" in message


def test_relative_script_path_resolves_against_the_repo_root(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("JOBRADAR_HOME", str(tmp_path))
    script = tmp_path / "hooks" / "echo.sh"
    script.parent.mkdir()
    script.write_text('#!/bin/sh\nread payload\necho "got ${#payload} bytes"\n')
    script.chmod(0o755)

    ok, message = hooks.run_hook("applied", {"hooks": {"applied": "sh hooks/echo.sh"}}, JOB)

    assert ok is True
    assert message == f"got {len(json.dumps(JOB, default=str))} bytes"


def test_default_config_allows_an_applied_hook_from_the_environment(monkeypatch) -> None:
    repo = Path(__file__).resolve().parents[1]
    monkeypatch.setenv("JOBRADAR_HOME", str(repo))
    cfg = load_config(repo / "config.yaml")
    assert "applied" not in (cfg.get("hooks") or {})
    monkeypatch.setenv("JOBRADAR_HOOK_APPLIED", shlex.join([sys.executable, "-c", CONSUMER]))

    ok, message = hooks.run_hook("applied", cfg, JOB)

    assert ok is True
    assert message == "Acme|abc123|applied"


def test_tracked_files_have_no_absolute_macos_home_paths() -> None:
    repo = Path(__file__).resolve().parents[1]
    home_prefix = "/" + "Users/"
    result = subprocess.run(
        ["git", "grep", "-l", "-F", home_prefix, "--"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1, result.stdout or result.stderr


def test_message_is_the_last_stdout_line_and_is_bounded() -> None:
    cfg = {"hooks": {"applied": [sys.executable, "-c", "print('noise'); print('x' * 500)"]}}
    ok, message = hooks.run_hook("applied", cfg, JOB)
    assert ok is True
    assert message.startswith("xxx") and len(message) == 160
