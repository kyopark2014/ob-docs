"""Lightweight in-process code tools (NOT AgentCore Code Interpreter).

Runs in the same ECS container as ob-note. Use for computation only —
note edits must go through ``vault_write``.
"""

from __future__ import annotations

import io
import logging
import os
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path

from langchain_core.tools import tool

logger = logging.getLogger("open_agent.tools_code")

_WORKING = Path(tempfile.gettempdir()) / "ob-note-open-agent"
_exec_globals: dict = {"__name__": "__main__"}


def _ensure_workdir() -> Path:
    _WORKING.mkdir(parents=True, exist_ok=True)
    return _WORKING


@tool
def execute_code(code: str) -> str:
    """Execute Python in the Open Agent process for computation / parsing.

    Do NOT use this to save vault notes — call ``vault_write`` instead.
    Working directory is a temp folder; imports persist across calls.

    Args:
        code: Python source to exec.
    """
    work = _ensure_workdir()
    stdout_cap = io.StringIO()
    stderr_cap = io.StringIO()
    old_cwd = os.getcwd()
    old_out, old_err = sys.stdout, sys.stderr
    try:
        os.chdir(work)
        sys.stdout, sys.stderr = stdout_cap, stderr_cap
        exec(code, _exec_globals)
        out = stdout_cap.getvalue()
        err = stderr_cap.getvalue()
        parts = []
        if out:
            parts.append(out)
        if err:
            parts.append(f"[stderr]\n{err}")
        return "\n".join(parts) if parts else "Code executed successfully (no output)."
    except Exception:
        logger.exception("execute_code failed")
        return f"Error:\n{traceback.format_exc()}"
    finally:
        sys.stdout, sys.stderr = old_out, old_err
        os.chdir(old_cwd)


@tool
def bash(command: str) -> str:
    """Run a short shell command in a temp working directory.

    Do NOT use this to modify vault notes — call ``vault_write`` instead.

    Args:
        command: Shell command string.
    """
    work = _ensure_workdir()
    try:
        result = subprocess.run(
            command,
            shell=True,
            cwd=str(work),
            capture_output=True,
            text=True,
            timeout=120,
        )
    except subprocess.TimeoutExpired:
        return "Error: command timed out after 120s"
    except Exception as e:
        return f"Error: {e}"
    parts: list[str] = []
    if result.stdout:
        parts.append(result.stdout)
    if result.stderr:
        parts.append(f"STDERR:\n{result.stderr}")
    if result.returncode != 0:
        parts.append(f"Return code: {result.returncode}")
    return "\n".join(parts) if parts else "(no output)"


def get_code_tools() -> list:
    return [execute_code, bash]
