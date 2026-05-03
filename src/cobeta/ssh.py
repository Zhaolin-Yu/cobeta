"""Cross-node SSH primitives over Tailscale.

cobeta's "multi-machine" story is intentionally minimal: it does NOT sync
workspaces, register them centrally, or otherwise track what lives where.
When you want to look at or run something on another node, you go there.

These helpers wrap `tailscale ssh` and `rsync` so you don't have to remember
the flag soup. Everything is a thin shell out — no daemons, no sync state.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass
class ExecResult:
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


def _ssh_binary() -> tuple[str, list[str]]:
    """Return (binary, base_args) preferring `tailscale ssh`, falling back to plain `ssh`."""
    if shutil.which("tailscale"):
        return "tailscale", ["ssh"]
    if shutil.which("ssh"):
        return "ssh", []
    raise RuntimeError("neither tailscale nor ssh found on PATH")


def exec_remote(node: str, command: str, *, capture: bool = True, timeout_s: float = 60.0) -> ExecResult:
    """Run a shell `command` on `node`, return the result.

    For interactive use (TTY required) prefer `interactive_ssh` instead.
    """
    binary, base = _ssh_binary()
    cmd = [binary, *base, node, command]
    try:
        r = subprocess.run(
            cmd, capture_output=capture, text=True, timeout=timeout_s, check=False
        )
    except subprocess.TimeoutExpired as e:
        return ExecResult(returncode=124, stdout=e.stdout or "", stderr=f"timeout after {timeout_s}s")
    return ExecResult(returncode=r.returncode, stdout=r.stdout or "", stderr=r.stderr or "")


def interactive_ssh(node: str) -> int:
    """Drop the user into an interactive ssh session. Returns the exit code."""
    binary, base = _ssh_binary()
    cmd = [binary, *base, node]
    return subprocess.call(cmd)


def pull_path(node: str, remote_path: str, local_dest: Path, *, recursive: bool = True) -> ExecResult:
    """Rsync `node:remote_path` to `local_dest`. Uses tailscale ssh as transport when available."""
    rsync = shutil.which("rsync")
    if not rsync:
        return ExecResult(returncode=127, stdout="", stderr="rsync not installed")

    args = ["rsync"]
    if recursive:
        args.append("-a")
    else:
        args.append("-t")
    args.append("--info=stats1")
    binary, base = _ssh_binary()
    if binary == "tailscale":
        args += ["-e", "tailscale ssh"]
    args.append(f"{node}:{remote_path}")
    args.append(str(local_dest))

    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=600, check=False)
    except subprocess.TimeoutExpired as e:
        return ExecResult(returncode=124, stdout=e.stdout or "", stderr="rsync timeout")
    return ExecResult(returncode=r.returncode, stdout=r.stdout or "", stderr=r.stderr or "")


def reachable(node: str, *, timeout_s: float = 5.0) -> bool:
    """Quick aliveness probe: try `tailscale ssh node true` (or `ssh node true`)."""
    res = exec_remote(node, "true", capture=True, timeout_s=timeout_s)
    return res.ok
