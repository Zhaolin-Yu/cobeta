"""The setup wizard.

Replaces the old `cobeta install` (which assumed the user had already decided
their role). The new flow:

  1. Detect tailscale and list peers.
  2. Probe every peer + self on the viking port. Anyone responding is a
     candidate "brain".
  3. Ask the user: is this machine the brain?
     - If YES (and no brain yet): scaffold central, optionally scan filesystem
     - If YES (and a brain already exists somewhere): warn (split-brain)
     - If NO: pick which existing peer is the brain, validate connectivity,
              optionally let the brain scan THIS node
  4. Write `~/.cobeta/config.yaml`.
"""

from __future__ import annotations

import os
import socket
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import click
import httpx
from rich.console import Console
from rich.table import Table

from ..config import (
    LLMProviderConfig,
    NodeConfig,
    NodeRole,
    VikingConfig,
    save_node_config,
)
from ..config.tailscale import TailscaleStatus, status as tailscale_status
from ..memory import VikingClient, viking_client_for
from ..memory.viking_server import detect_server


@dataclass
class BrainProbe:
    hostname: str
    port: int
    reachable: bool
    is_self: bool


@dataclass
class SetupOutcome:
    config: NodeConfig
    config_path: Path
    scanned: bool = False
    suggested_tags: dict[str, str] = field(default_factory=dict)
    inventory_summary: str = ""


# ---------- brain discovery ----------

def discover_brain(
    ts: TailscaleStatus,
    *,
    port: int = 7799,
    timeout_s: float = 1.5,
) -> list[BrainProbe]:
    """Probe every tailscale peer + self for a viking server on `port`.

    Returns one BrainProbe per host attempted. Order: self first, then peers.
    """
    self_host = ts.self_hostname or socket.gethostname()
    targets = [(self_host, True)]
    for p in ts.peers:
        if p != self_host:
            targets.append((p, False))

    results: list[BrainProbe] = []
    with httpx.Client(timeout=timeout_s) as client:
        for host, is_self in targets:
            url = f"http://{host}:{port}/health"
            try:
                r = client.get(url)
                ok = r.status_code == 200
            except Exception:
                ok = False
            results.append(BrainProbe(hostname=host, port=port, reachable=ok, is_self=is_self))
    return results


# ---------- the wizard ----------

def run_setup_wizard(
    *,
    role_override: Optional[str] = None,
    central_override: Optional[str] = None,
    viking_port: int = 7799,
    workspaces_root: Optional[Path] = None,
    llm_provider_override: Optional[str] = None,
    config_path: Optional[Path] = None,
    skip_scan: bool = False,
    console: Optional[Console] = None,
) -> SetupOutcome:
    """Run the interactive setup wizard. Writes ~/.cobeta/config.yaml.

    Returns a SetupOutcome describing what was written and what was scanned.
    """

    console = console or Console()
    console.rule("[bold cyan]cobeta setup")

    # ---- 1. Tailscale detection ----
    ts = tailscale_status()
    if not ts.installed:
        console.print(
            "[yellow]warning:[/yellow] tailscale CLI not found. cobeta works on a single "
            "machine without it, but multi-machine features will be unavailable."
        )
    elif not ts.running:
        console.print(
            "[yellow]warning:[/yellow] tailscale installed but not running "
            "(`sudo tailscale up`). Multi-machine features will be unavailable until you start it."
        )
    else:
        console.print(
            f"[green]✓[/green] tailscale running as [bold]{ts.self_hostname}[/bold] "
            f"with {len(ts.peers)} peer(s)"
        )

    # ---- 2. Brain discovery ----
    probes = discover_brain(ts, port=viking_port) if (ts.installed and ts.running) else []
    responding = [p for p in probes if p.reachable]
    if probes:
        _print_brain_probe_table(console, probes)

    # ---- 3. Decide role ----
    role = _decide_role(console, probes, role_override)

    # ---- 4. Decide central hostname ----
    if role == NodeRole.CENTRAL:
        central_hostname = central_override or ts.self_hostname or socket.gethostname()
    else:
        central_hostname = central_override or _pick_central_hostname(console, responding, ts)

    # ---- 5. Workspaces root ----
    if workspaces_root is None:
        default_root = Path("~/cobeta-workspaces").expanduser()
        workspaces_root = Path(
            click.prompt("Where should this node create workspaces?", default=str(default_root))
        ).expanduser()
        # Refuse to use the framework checkout dir as workspaces root
        # (heuristic: if the dir contains src/cobeta/__init__.py, it's the framework)
        if (workspaces_root / "src" / "cobeta" / "__init__.py").exists():
            console.print(
                "[yellow]warning:[/yellow] that directory looks like the cobeta framework checkout. "
                f"Using a sibling: {workspaces_root}-data"
            )
            workspaces_root = Path(str(workspaces_root) + "-data")

    # ---- 6. LLM provider ----
    llm_provider = llm_provider_override or _decide_llm_provider(console)

    # ---- 7. Build & save config ----
    cfg = NodeConfig(
        role=role,
        central_hostname=central_hostname,
        viking=VikingConfig(
            host=("localhost" if role == NodeRole.CENTRAL else central_hostname),
            port=viking_port,
        ),
        llm=LLMProviderConfig(provider=llm_provider),
        workspaces_root=workspaces_root,
        machine_label=ts.self_hostname or socket.gethostname(),
    )
    saved_path = save_node_config(cfg, config_path)
    console.print(f"[green]✓[/green] wrote {saved_path}")

    # ---- 8. Per-role finishing steps ----
    outcome = SetupOutcome(config=cfg, config_path=saved_path)
    if role == NodeRole.CENTRAL:
        _explain_central_next_steps(console)

    if not skip_scan:
        do_scan = click.confirm(
            "\nScan this machine (read-only) to seed tag vocabulary and viking memory?",
            default=False,
        )
        if do_scan:
            outcome = _do_scan(console, outcome)

    console.print(
        "\n[bold]Done.[/bold] Run [cyan]cobeta status[/cyan] to verify, then "
        "[cyan]cobeta bootstrap[/cyan] to create your first workspace."
    )
    return outcome


# ---------- helpers ----------

def _print_brain_probe_table(console: Console, probes: list[BrainProbe]) -> None:
    t = Table(show_header=True, header_style="bold cyan", title="brain probe (port :7799)")
    t.add_column("host")
    t.add_column("self?")
    t.add_column("viking?")
    for p in probes:
        t.add_row(p.hostname, "yes" if p.is_self else "", "[green]yes[/green]" if p.reachable else "no")
    console.print(t)


def _decide_role(
    console: Console, probes: list[BrainProbe], override: Optional[str]
) -> NodeRole:
    if override is not None:
        return NodeRole(override)

    self_brain = next((p for p in probes if p.is_self and p.reachable), None)
    other_brain = next((p for p in probes if not p.is_self and p.reachable), None)

    if self_brain:
        if click.confirm(
            "A viking server is already running on THIS machine. Set up as central?",
            default=True,
        ):
            return NodeRole.CENTRAL
        return NodeRole.NODE

    if other_brain:
        console.print(
            f"\nFound an existing brain at [bold]{other_brain.hostname}[/bold]. "
            "If you confirm 'no' below, this machine becomes a node pointing there."
        )
        if click.confirm("Is THIS machine the brain (no, use the existing one)?", default=False):
            console.print(
                "[yellow]warning:[/yellow] you'd have two brains. The other one is at "
                f"{other_brain.hostname}. Are you sure?"
            )
            if click.confirm("Proceed as a SECOND brain anyway?", default=False):
                return NodeRole.CENTRAL
        return NodeRole.NODE

    # No brain anywhere — somebody has to be the brain
    console.print("\nNo viking server detected on any tailscale peer. Somebody has to be the brain.")
    if click.confirm("Make THIS machine the brain?", default=True):
        return NodeRole.CENTRAL
    return NodeRole.NODE


def _pick_central_hostname(
    console: Console, responding: list[BrainProbe], ts: TailscaleStatus
) -> str:
    if responding:
        candidates = [p.hostname for p in responding if not p.is_self] or [responding[0].hostname]
        if len(candidates) == 1:
            return candidates[0]
        console.print("Multiple brains responded:")
        for i, c in enumerate(candidates, 1):
            console.print(f"  {i}. {c}")
        choice = click.prompt(
            "Pick which one is yours (number)",
            type=click.IntRange(1, len(candidates)),
            default=1,
        )
        return candidates[choice - 1]

    return click.prompt(
        "No brain auto-detected. Tailscale hostname of the brain you want to point at"
    )


def _decide_llm_provider(console: Console) -> str:
    guess = "none"
    if os.environ.get("ANTHROPIC_API_KEY"):
        guess = "anthropic"
    elif os.environ.get("OPENAI_API_KEY"):
        guess = "openai"
    return click.prompt(
        "Default LLM provider for the bootstrap agent",
        type=click.Choice(["anthropic", "openai", "none"]),
        default=guess,
    )


def _explain_central_next_steps(console: Console) -> None:
    srv = detect_server()
    if srv.binary_present:
        console.print(
            f"\nNext: start the OpenViking server (you control how — systemd, tmux, docker, …):"
        )
        console.print(f"  [bold]{srv.suggested_command}[/bold]")
    else:
        console.print(
            f"\nNext: install OpenViking and start the server:\n  [bold]{srv.suggested_command}[/bold]"
        )


def _do_scan(console: Console, outcome: SetupOutcome) -> SetupOutcome:
    """Run the heuristic scanner, present suggestions, write to viking on confirmation."""

    from ..scanner import build_inventory_summary, suggest_tags, walk_filesystem_readonly  # local import to keep import graph small

    home = Path("~").expanduser()
    candidate_roots: list[Path] = []
    for sub in ("projects", "Projects", "code", "work", "Documents"):
        if (home / sub).is_dir():
            candidate_roots.append(home / sub)
    if not candidate_roots:
        candidate_roots = [home]

    console.print(
        f"\nScanning these roots (read-only, depth 2): "
        + ", ".join(str(r) for r in candidate_roots)
    )
    fingerprints = walk_filesystem_readonly(candidate_roots)
    console.print(f"  → {len(fingerprints)} directories fingerprinted")

    suggestions = suggest_tags(fingerprints)
    summary = build_inventory_summary(fingerprints)
    console.print(f"\n[bold]Inventory:[/bold] {summary}")

    console.print("\n[bold]Suggested tag vocabulary:[/bold]")
    for tag, why in sorted(suggestions.items()):
        console.print(f"  [cyan]{tag:<22}[/cyan] {why}")

    if not click.confirm(
        "\nWrite these to viking://meta/tags.yaml + viking://user/inventory?",
        default=True,
    ):
        console.print("(skipped)")
        outcome.scanned = False
        return outcome

    cfg = outcome.config
    client = viking_client_for(cfg)
    try:
        import yaml as _yaml
        existing_doc = client.cat("viking://meta/tags.yaml", level="L2")
        existing = _yaml.safe_load(existing_doc.full) if existing_doc and existing_doc.full else {"tags": {}}
        if existing is None or not isinstance(existing, dict):
            existing = {"tags": {}}
        for tag, why in suggestions.items():
            existing.setdefault("tags", {}).setdefault(tag, {"description": why})
        client.write("viking://meta/tags.yaml", _yaml.safe_dump(existing, sort_keys=False))
        client.write(
            "viking://user/inventory",
            summary,
            metadata={"scanned_roots": [str(r) for r in candidate_roots]},
        )
    finally:
        client.close()

    outcome.scanned = True
    outcome.suggested_tags = suggestions
    outcome.inventory_summary = summary
    console.print("[green]✓[/green] wrote tag vocabulary and inventory to viking")
    return outcome
