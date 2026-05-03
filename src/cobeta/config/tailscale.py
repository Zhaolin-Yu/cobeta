"""Thin wrapper around the `tailscale` CLI for status, hostname, and ssh."""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from typing import Optional


@dataclass
class TailscaleStatus:
    installed: bool
    running: bool
    self_hostname: Optional[str]
    peers: list[str]


def tailscale_present() -> bool:
    return shutil.which("tailscale") is not None


def status() -> TailscaleStatus:
    """Return tailscale status. Never raises — always returns a TailscaleStatus."""
    if not tailscale_present():
        return TailscaleStatus(installed=False, running=False, self_hostname=None, peers=[])
    try:
        r = subprocess.run(
            ["tailscale", "status", "--json"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception:
        return TailscaleStatus(installed=True, running=False, self_hostname=None, peers=[])
    if r.returncode != 0:
        return TailscaleStatus(installed=True, running=False, self_hostname=None, peers=[])
    try:
        data = json.loads(r.stdout)
    except json.JSONDecodeError:
        return TailscaleStatus(installed=True, running=False, self_hostname=None, peers=[])

    self_h = (data.get("Self") or {}).get("HostName")
    peers = []
    for p in (data.get("Peer") or {}).values():
        h = p.get("HostName")
        if h:
            peers.append(h)
    return TailscaleStatus(installed=True, running=True, self_hostname=self_h, peers=peers)


def ssh_command(target: str, command: str) -> list[str]:
    """Build a `tailscale ssh` command list. Caller invokes via subprocess."""
    return ["tailscale", "ssh", target, command]
