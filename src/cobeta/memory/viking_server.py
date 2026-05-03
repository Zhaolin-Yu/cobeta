"""Wrapper to start/stop the OpenViking server on the central node.

Stub-level for now: prints the command the user should run. A future version
will support `cobeta viking start --daemon` with proper systemd / launchd /
Windows service integration. For now, we point users at the upstream
`openviking-server` binary.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass


@dataclass
class VikingServerStatus:
    binary_present: bool
    suggested_command: str


def detect_server() -> VikingServerStatus:
    binary = shutil.which("openviking-server")
    if binary:
        return VikingServerStatus(
            binary_present=True,
            suggested_command=f"{binary} --port 7799 --bind 0.0.0.0",
        )
    return VikingServerStatus(
        binary_present=False,
        suggested_command="pip install 'openviking[bot]' && openviking-server --port 7799 --bind 0.0.0.0",
    )
