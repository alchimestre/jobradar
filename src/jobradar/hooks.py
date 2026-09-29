"""Post-action hooks: hand a stored posting to an external command.

A hook is any command that reads one posting as JSON on stdin. It is how the tool
reaches into an external system (issue tracker, notes, spreadsheet) without knowing
anything about it. Failures are reported, never raised: a broken hook must not take
the TUI down or lose the status write that already happened.
"""

from __future__ import annotations

import json
import os
import pathlib
import shlex
import subprocess

from .config import root

NO_HOOK = "no hook configured"


def _resolve(command: list[str]) -> list[str]:
    """Resolve relative paths in a command against the jobradar root."""
    out = list(command)
    for index, part in enumerate(out):
        if index == 0 and part in ("python", "python3", "bash", "sh", "uv", "uvx"):
            continue
        if "/" in part or part.endswith(".py") or part.endswith(".sh"):
            path = pathlib.Path(part)
            if not path.is_absolute() and (root() / path).exists():
                out[index] = str(root() / path)
    return out


def spec_for(name: str, cfg: dict) -> list[str] | str | None:
    configured = (cfg.get("hooks") or {}).get(name)
    if configured:
        return configured
    return os.environ.get(f"JOBRADAR_HOOK_{name.upper()}")


def run_hook(name: str, cfg: dict, job: dict, timeout: int = 120) -> tuple[bool, str]:
    """Run the hook registered for `name` (a status, e.g. "applied").

    Returns (ok, one-line message) for display in the status bar.
    """
    spec = spec_for(name, cfg)
    if not spec:
        return True, NO_HOOK
    command = _resolve(spec) if isinstance(spec, list) else shlex.split(str(spec))
    command = _resolve(command)
    payload = json.dumps(job, default=str)
    env = {
        **os.environ,
        "JOBRADAR_HOOK": name,
        "JOBRADAR_UID": str(job.get("uid", "")),
        "JOBRADAR_COMPANY": str(job.get("company", "")),
        "JOBRADAR_TITLE": str(job.get("title", "")),
        "JOBRADAR_URL": str(job.get("url", "")),
        "JOBRADAR_LANE": str(job.get("lane", "")),
        "JOBRADAR_STATUS": str(job.get("status", name)),
    }
    try:
        completed = subprocess.run(
            command,
            input=payload,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
            cwd=str(root()),
        )
    except subprocess.TimeoutExpired:
        return False, f"hook '{name}' timed out after {timeout}s"
    except OSError as exc:
        return False, f"hook '{name}' failed to start: {exc}"

    def last_line(text: str) -> str:
        lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
        return lines[-1][:160] if lines else ""

    if completed.returncode != 0:
        return False, f"hook '{name}' exit {completed.returncode}: {last_line(completed.stderr) or last_line(completed.stdout) or 'no output'}"
    return True, last_line(completed.stdout)
