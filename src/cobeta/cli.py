"""cobeta CLI entry point."""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path
from typing import Optional

import click
from rich.console import Console
from rich.table import Table

from . import __version__
from .agent import bootstrap_interactive, bootstrap_with_llm
from .config import (
    LLMProviderConfig,
    NodeConfig,
    NodeRole,
    VikingConfig,
    default_config_path,
    load_node_config,
    save_node_config,
)
from .config.tailscale import status as tailscale_status, tailscale_present
from .memory import VikingClient, viking_client_for
from .memory.viking_server import detect_server
from .setup import discover_brain, run_setup_wizard
from .ssh import exec_remote, interactive_ssh, pull_path, reachable
from .workspace import inspect_existing_workspaces

console = Console()


def _safe_load_config() -> Optional[NodeConfig]:
    try:
        return load_node_config()
    except FileNotFoundError:
        return None


# ---------- top-level ----------


@click.group(context_settings={"help_option_names": ["-h", "--help"]}, invoke_without_command=True)
@click.version_option(__version__, prog_name="cobeta")
@click.pass_context
def main(ctx: click.Context) -> None:
    """cobeta — open-source agent framework for ICM workspaces."""
    if ctx.invoked_subcommand is None:
        if _safe_load_config() is None:
            console.print(
                "[yellow]·[/yellow] no config found at ~/.cobeta/config.yaml — running setup wizard\n"
            )
            ctx.invoke(setup)
        else:
            ctx.invoke(bootstrap)


# ---------- setup ----------


@main.command()
@click.option("--as", "role", type=click.Choice(["central", "node"]), default=None)
@click.option("--central", "central_hostname", default=None)
@click.option("--viking-port", default=7799, type=int)
@click.option("--viking-stub-dir", type=click.Path(file_okay=False, path_type=Path), default=None,
              help="Where the local stub fallback stores its JSON. Default ~/.cobeta/viking-stub/.")
@click.option("--llm-provider", type=click.Choice(["anthropic", "openai", "openai-compatible", "none"]), default=None)
@click.option("--llm-model", default=None, help="Override model name (e.g. mimo-v2.5-pro).")
@click.option("--llm-base-url", default=None, help="Override LLM endpoint base URL (for openai-compatible providers).")
@click.option("--workspaces-root", type=click.Path(file_okay=False, path_type=Path), default=None)
@click.option("--skip-scan", is_flag=True, default=False, help="Don't offer the read-only filesystem scan.")
def setup(
    role: Optional[str],
    central_hostname: Optional[str],
    viking_port: int,
    viking_stub_dir: Optional[Path],
    llm_provider: Optional[str],
    llm_model: Optional[str],
    llm_base_url: Optional[str],
    workspaces_root: Optional[Path],
    skip_scan: bool,
) -> None:
    """Interactive setup. Detects tailscale + brain, decides role, writes config."""

    run_setup_wizard(
        role_override=role,
        central_override=central_hostname,
        viking_port=viking_port,
        viking_stub_dir=viking_stub_dir,
        workspaces_root=workspaces_root,
        llm_provider_override=llm_provider,
        llm_model_override=llm_model,
        llm_base_url_override=llm_base_url,
        skip_scan=skip_scan,
        console=console,
    )


# ---------- status ----------


@main.command()
def status() -> None:
    """Show this node's role and the health of every dependency."""

    console.rule("[bold cyan]cobeta status")

    cfg = _safe_load_config()
    if cfg is None:
        console.print("[red]✗[/red] not installed yet — run [bold]cobeta setup[/bold]")
        sys.exit(1)

    ts = tailscale_status()
    if ts.installed and ts.running:
        console.print(f"[green]✓[/green] tailscale running as [bold]{ts.self_hostname}[/bold] ({len(ts.peers)} peers)")
    elif ts.installed:
        console.print("[yellow]·[/yellow] tailscale installed but not running")
    else:
        console.print("[red]✗[/red] tailscale not installed (multi-machine features disabled)")

    console.print(f"role:            [bold]{cfg.role.value}[/bold]")
    console.print(f"machine label:   [bold]{cfg.machine_label}[/bold]")
    console.print(f"central host:    [bold]{cfg.central_hostname}[/bold]")
    console.print(f"viking endpoint: [bold]{cfg.viking.base_url}[/bold]")
    console.print(f"workspaces root: [bold]{cfg.workspaces_root}[/bold]")
    console.print(f"llm provider:    [bold]{cfg.llm.provider}[/bold] (model: {cfg.llm.model or 'n/a'})")
    if cfg.llm.base_url:
        console.print(f"  base_url:      {cfg.llm.base_url}")

    client = viking_client_for(cfg)
    if client.health():
        console.print("[green]✓[/green] viking reachable")
    else:
        console.print(
            f"[yellow]·[/yellow] viking unreachable — falling back to local stub at {cfg.viking.stub_dir}"
        )
    client.close()

    if cfg.llm.provider in ("anthropic",):
        if os.environ.get("ANTHROPIC_API_KEY"):
            console.print("[green]✓[/green] $ANTHROPIC_API_KEY set")
        else:
            console.print("[yellow]·[/yellow] $ANTHROPIC_API_KEY not set — bootstrap falls back to interactive mode")
    elif cfg.llm.provider in ("openai", "openai-compatible"):
        env_key = cfg.llm.api_key_env
        if os.environ.get(env_key):
            console.print(f"[green]✓[/green] ${env_key} set")
        else:
            console.print(f"[yellow]·[/yellow] ${env_key} not set — bootstrap falls back to interactive mode")

    summaries = inspect_existing_workspaces(cfg.workspaces_root)
    console.print(f"workspaces:      {len(summaries)} on this machine")


# ---------- bootstrap ----------


@main.command()
@click.argument("intent", required=False)
@click.option("--interactive", is_flag=True, default=False, help="Force CLI-prompt mode even if an LLM API key is set.")
def bootstrap(intent: Optional[str], interactive: bool) -> None:
    """Generate a new workspace via the bootstrap agent."""

    cfg = _safe_load_config()
    if cfg is None:
        console.print("[red]✗[/red] not installed — run [bold]cobeta setup[/bold] first")
        sys.exit(1)

    viking = viking_client_for(cfg)

    api_key_present = (
        (cfg.llm.provider == "anthropic" and os.environ.get("ANTHROPIC_API_KEY"))
        or (cfg.llm.provider in ("openai", "openai-compatible") and os.environ.get(cfg.llm.api_key_env))
    )

    use_llm = (not interactive) and cfg.llm.provider != "none" and bool(api_key_present)

    if use_llm:
        try:
            from .llm import get_provider
            llm = get_provider(
                cfg.llm.provider,
                model=cfg.llm.model,
                base_url=cfg.llm.base_url,
                api_key_env=cfg.llm.api_key_env,
            )
            result = bootstrap_with_llm(cfg, viking, llm, intent_seed=intent)
        except Exception as e:
            console.print(f"[yellow]LLM mode failed ({e}); falling back to interactive[/yellow]")
            result = bootstrap_interactive(cfg, viking, intent_seed=intent)
    else:
        result = bootstrap_interactive(cfg, viking, intent_seed=intent)

    viking.close()

    if result.error or result.workspace is None:
        console.print(f"\n[red]bootstrap failed:[/red] {result.error}")
        sys.exit(1)

    ws = result.workspace
    console.rule(f"[bold green]workspace ready: {ws.spec.name}")
    console.print(f"  path:           [bold]{ws.path}[/bold]")
    console.print(f"  viking memory:  [bold]{ws.viking_memory_uri}[/bold]")
    console.print(f"  handoff files:")
    for hp in ws.handoff_files:
        console.print(f"    - {hp.relative_to(ws.path)}")
    console.print(f"\nNow: [bold]cd {ws.path}[/bold] and start your agent CLI of choice.")


# ---------- workspaces ----------


@main.group()
def workspaces() -> None:
    """List / inspect workspaces on this machine."""


@workspaces.command("show")
@click.argument("name")
def workspaces_show(name: str) -> None:
    """Show one workspace's audit record (.cobeta.yaml) + handoff file paths."""
    cfg = _safe_load_config()
    if cfg is None:
        console.print("[red]✗[/red] not installed")
        sys.exit(1)
    ws_path = cfg.workspaces_root / name
    if not ws_path.is_dir():
        raise click.ClickException(f"no workspace at {ws_path}")
    audit_path = ws_path / ".cobeta.yaml"
    if not audit_path.exists():
        raise click.ClickException(f"{ws_path} is missing .cobeta.yaml — not a cobeta workspace?")
    console.print(f"[bold cyan]{name}[/bold cyan]  ({ws_path})")
    console.print(audit_path.read_text(encoding="utf-8"))
    handoffs = sorted(ws_path.glob("CLAUDE.md")) + sorted(ws_path.glob("AGENTS.md")) + \
               sorted(ws_path.glob(".cursor/rules/*.mdc")) + sorted(ws_path.glob(".opencode/*.json"))
    if handoffs:
        console.print("\n[bold]handoff files:[/bold]")
        for p in handoffs:
            console.print(f"  - {p.relative_to(ws_path)}")


@workspaces.command("list")
def workspaces_list() -> None:
    cfg = _safe_load_config()
    if cfg is None:
        console.print("[red]✗[/red] not installed")
        sys.exit(1)
    summaries = inspect_existing_workspaces(cfg.workspaces_root)
    if not summaries:
        console.print(f"(no workspaces under {cfg.workspaces_root})")
        return
    t = Table(show_header=True, header_style="bold cyan")
    t.add_column("name")
    t.add_column("intent")
    t.add_column("stages")
    t.add_column("tags")
    t.add_column("machine")
    for s in summaries:
        t.add_row(s.name, s.intent[:60], ",".join(s.stages), ",".join(s.tags), s.machine)
    console.print(t)


# ---------- viking ----------


@main.group()
def viking() -> None:
    """Inspect / interact with the OpenViking memory store."""


@viking.command("find")
@click.argument("query")
@click.option("--prefix", default="viking://", help="URI prefix to search under")
@click.option("-k", "--limit", default=5, type=int)
def viking_find(query: str, prefix: str, limit: int) -> None:
    cfg = _safe_load_config()
    if cfg is None:
        console.print("[red]✗[/red] not installed")
        sys.exit(1)
    client = viking_client_for(cfg)
    docs = client.find(query, uri_prefix=prefix, k=limit)
    client.close()
    if not docs:
        console.print(f"(no results for {query!r})")
        return
    for d in docs:
        console.print(f"[bold]{d.uri}[/bold]")
        console.print(f"  {d.abstract or d.overview[:120]}")


@viking.command("tree")
@click.argument("uri")
@click.option("--depth", default=1, type=int)
def viking_tree(uri: str, depth: int) -> None:
    cfg = _safe_load_config()
    if cfg is None:
        console.print("[red]✗[/red] not installed")
        sys.exit(1)
    client = viking_client_for(cfg)
    entries = client.tree(uri, depth=depth)
    client.close()
    if not entries:
        console.print(f"(no entries under {uri})")
        return
    for e in entries:
        console.print(e)


@viking.command("cat")
@click.argument("uri")
@click.option("--level", type=click.Choice(["L0", "L1", "L2"]), default="L2")
def viking_cat(uri: str, level: str) -> None:
    cfg = _safe_load_config()
    if cfg is None:
        console.print("[red]✗[/red] not installed")
        sys.exit(1)
    client = viking_client_for(cfg)
    doc = client.cat(uri, level=level)
    client.close()
    if doc is None:
        console.print(f"(no document at {uri})")
        return
    if level == "L0":
        console.print(doc.abstract)
    elif level == "L1":
        console.print(doc.overview)
    else:
        console.print(doc.full or doc.overview)


# ---------- machines / exec / ssh / pull ----------


@main.command()
def machines() -> None:
    """Show registered Tailscale peers (read directly from `tailscale status`)."""
    ts = tailscale_status()
    if not ts.installed:
        console.print("[red]✗[/red] tailscale not installed")
        sys.exit(1)
    if not ts.running:
        console.print("[yellow]·[/yellow] tailscale not running")
        sys.exit(1)
    cfg = _safe_load_config()
    central = cfg.central_hostname if cfg else None
    t = Table(show_header=True, header_style="bold cyan")
    t.add_column("hostname")
    t.add_column("role")
    t.add_column("reachable?")
    if ts.self_hostname:
        role_label = "central" if ts.self_hostname == central else "node"
        t.add_row(f"{ts.self_hostname} (this)", role_label, "[green]self[/green]")
    for p in ts.peers:
        role_label = "central" if p == central else "node"
        ok = reachable(p, timeout_s=2.0)
        t.add_row(p, role_label, "[green]yes[/green]" if ok else "[red]no[/red]")
    console.print(t)


@main.command(name="exec")
@click.argument("node")
@click.argument("command", nargs=-1, required=True)
def exec_cmd(node: str, command: tuple[str, ...]) -> None:
    """Run a command on another tailnet node and print the output.

    Example: cobeta exec aim-patho -- ls ~/cobeta-workspaces
    """
    cmd_str = " ".join(command)
    res = exec_remote(node, cmd_str, capture=True, timeout_s=120)
    if res.stdout:
        click.echo(res.stdout, nl=False)
    if res.stderr:
        click.echo(res.stderr, nl=False, err=True)
    sys.exit(res.returncode)


@main.command(name="ssh")
@click.argument("node")
def ssh_cmd(node: str) -> None:
    """Drop into an interactive SSH session on `node` over Tailscale."""
    sys.exit(interactive_ssh(node))


@main.command(name="pull")
@click.argument("source")
@click.argument("dest", type=click.Path(path_type=Path))
def pull_cmd(source: str, dest: Path) -> None:
    """Pull `node:path` to local `dest` via rsync over Tailscale.

    Example: cobeta pull aim-patho:~/cobeta-workspaces/foo/output ./pulled/
    """
    if ":" not in source:
        raise click.ClickException("SOURCE must be of the form `node:remote-path`")
    node, remote_path = source.split(":", 1)
    res = pull_path(node, remote_path, dest)
    if res.stdout:
        click.echo(res.stdout, nl=False)
    if res.stderr:
        click.echo(res.stderr, nl=False, err=True)
    sys.exit(res.returncode)


# ---------- tags ----------


@main.group()
def tags() -> None:
    """Manage the controlled tag vocabulary in viking://meta/tags.yaml."""


@tags.command("list")
def tags_list() -> None:
    cfg = _safe_load_config()
    if cfg is None:
        console.print("[red]✗[/red] not installed")
        sys.exit(1)
    client = viking_client_for(cfg)
    doc = client.cat("viking://meta/tags.yaml", level="L2")
    client.close()
    if doc is None or not doc.full:
        console.print("(no tag vocabulary yet — run `cobeta scan` or `cobeta tags add <tag>`)")
        return
    import yaml as _yaml
    data = _yaml.safe_load(doc.full) or {}
    tags_map = data.get("tags") or {}
    if not tags_map:
        console.print("(empty)")
        return
    t = Table(show_header=True, header_style="bold cyan")
    t.add_column("tag")
    t.add_column("description")
    t.add_column("aliases")
    for tag, body in sorted(tags_map.items()):
        if isinstance(body, str):
            desc, aliases = body, ""
        else:
            desc = (body or {}).get("description", "")
            aliases = ",".join((body or {}).get("aliases", []) or [])
        t.add_row(tag, desc, aliases)
    console.print(t)


@tags.command("add")
@click.argument("tag")
@click.option("--description", default="", help="One-line description")
@click.option("--alias", multiple=True, help="Aliases (repeatable)")
def tags_add(tag: str, description: str, alias: tuple[str, ...]) -> None:
    cfg = _safe_load_config()
    if cfg is None:
        console.print("[red]✗[/red] not installed")
        sys.exit(1)
    import re as _re
    if not _re.match(r"^[a-z][a-z0-9-]{1,30}$", tag):
        raise click.ClickException(f"tag must be kebab-case, 2-31 chars (got {tag!r})")

    client = viking_client_for(cfg)
    import yaml as _yaml
    doc = client.cat("viking://meta/tags.yaml", level="L2")
    data = _yaml.safe_load(doc.full) if doc and doc.full else {"tags": {}}
    if not isinstance(data, dict):
        data = {"tags": {}}
    data.setdefault("tags", {})
    entry = {"description": description}
    if alias:
        entry["aliases"] = list(alias)
    data["tags"][tag] = entry
    client.write("viking://meta/tags.yaml", _yaml.safe_dump(data, sort_keys=False))
    client.close()
    console.print(f"[green]✓[/green] added tag '{tag}'")


@tags.command("lint")
@click.option("--unused", is_flag=True, default=False, help="Also show declared tags that no workspace uses.")
def tags_lint(unused: bool) -> None:
    """Find tags used by workspaces but not declared in viking://meta/tags.yaml.

    With --unused, also shows declared tags that no workspace currently uses
    (candidates for retirement).
    """
    cfg = _safe_load_config()
    if cfg is None:
        console.print("[red]✗[/red] not installed")
        sys.exit(1)
    client = viking_client_for(cfg)
    import yaml as _yaml
    doc = client.cat("viking://meta/tags.yaml", level="L2")
    data = _yaml.safe_load(doc.full) if doc and doc.full else {"tags": {}}
    declared = set((data.get("tags") or {}).keys()) if isinstance(data, dict) else set()
    for body in (data.get("tags") or {}).values():
        if isinstance(body, dict):
            declared.update(body.get("aliases") or [])
    client.close()

    summaries = inspect_existing_workspaces(cfg.workspaces_root)
    used: dict[str, list[str]] = {}
    for s in summaries:
        for t in s.tags:
            used.setdefault(t, []).append(s.name)

    undeclared = {t: ws for t, ws in used.items() if t not in declared}
    declared_unused = sorted(declared - set(used.keys()))

    exit_code = 0
    if undeclared:
        console.print("[yellow]undeclared tags (used but not in vocabulary):[/yellow]")
        for t, ws in sorted(undeclared.items()):
            console.print(f"  [cyan]{t}[/cyan] (used in: {', '.join(ws)})")
        exit_code = 1
    else:
        console.print("[green]✓[/green] all used tags are declared")

    if unused:
        if declared_unused:
            console.print("\n[yellow]declared but unused:[/yellow]")
            for t in declared_unused:
                console.print(f"  [dim]{t}[/dim]")
        else:
            console.print("\n[green]✓[/green] no unused declared tags")

    sys.exit(exit_code)


# ---------- scan ----------


@main.command()
@click.argument("source")
@click.option(
    "--uri",
    default=None,
    help="Override target viking URI; default viking://resources/<workspace>/<filename>.",
)
@click.option(
    "--tag",
    "extra_tags",
    multiple=True,
    help="Additional tag(s) to attach (in addition to those inherited from the workspace).",
)
def promote(source: str, uri: Optional[str], extra_tags: tuple[str, ...]) -> None:
    """Promote a workspace output to viking long-term memory.

    SOURCE is `<workspace-name>/<rel-path-inside-workspace>`. Supports text
    files only (markdown / txt / json / yaml / csv / py / md / rst). Binaries
    are refused with a friendly error — promote a summary of them instead.

    Example: cobeta promote rope-longctx-comparison/stages/04-analysis/output/findings.md
    """
    import yaml as _yaml
    cfg = _safe_load_config()
    if cfg is None:
        console.print("[red]✗[/red] not installed")
        sys.exit(1)

    if "/" not in source:
        raise click.ClickException("SOURCE must be of the form `<workspace>/<rel-path>`")
    workspace_name, rel = source.split("/", 1)
    src_path = cfg.workspaces_root / workspace_name / rel
    if not src_path.exists():
        raise click.ClickException(f"file not found: {src_path}")
    if not src_path.is_file():
        raise click.ClickException(f"not a file: {src_path}")

    text_exts = {".md", ".txt", ".rst", ".json", ".yaml", ".yml", ".csv", ".tsv", ".py", ".org", ".tex"}
    if src_path.suffix.lower() not in text_exts:
        raise click.ClickException(
            f"refusing to promote non-text file ({src_path.suffix}). "
            "Promote a markdown summary instead."
        )

    audit_path = cfg.workspaces_root / workspace_name / ".cobeta.yaml"
    workspace_tags: list[str] = []
    if audit_path.exists():
        try:
            audit = _yaml.safe_load(audit_path.read_text(encoding="utf-8")) or {}
            workspace_tags = list((audit.get("spec") or {}).get("tags") or [])
        except _yaml.YAMLError:
            pass
    all_tags = sorted(set(workspace_tags) | set(extra_tags))

    target_uri = uri or f"viking://resources/{workspace_name}/{rel}"
    content = src_path.read_text(encoding="utf-8")

    client = viking_client_for(cfg)
    client.write(
        target_uri,
        content,
        metadata={
            "source_workspace": workspace_name,
            "source_rel_path": rel,
            "tags": all_tags,
            "promoted_at": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
        },
    )
    client.close()
    console.print(f"[green]✓[/green] promoted to [bold]{target_uri}[/bold]")
    console.print(f"  tags: {', '.join(all_tags) if all_tags else '(none)'}")
    console.print(f"  source: {src_path}")


@main.command()
@click.option("--root", "roots", multiple=True, type=click.Path(exists=True, file_okay=False, path_type=Path), help="Root(s) to scan; default: ~/projects, ~/code, ~/Documents (whichever exist).")
@click.option("--depth", default=2, type=int)
@click.option("--descend-into-projects", is_flag=True, default=False,
              help="By default, don't recurse into dirs that already look like a project (avoids vendored stdlib pollution). Pass this to override.")
@click.option("--write", is_flag=True, default=False, help="After review, write to viking. Without this flag, dry-run.")
def scan(roots: tuple[Path, ...], depth: int, descend_into_projects: bool, write: bool) -> None:
    """Read-only filesystem scan that suggests a tag vocabulary.

    Pass --write to actually persist suggestions to viking://meta/tags.yaml.
    """
    cfg = _safe_load_config()
    if cfg is None:
        console.print("[red]✗[/red] not installed")
        sys.exit(1)

    from .scanner import (
        build_inventory_summary,
        render_per_project_table,
        suggest_tags,
        walk_filesystem_readonly,
    )

    if not roots:
        home = Path("~").expanduser()
        candidates: list[Path] = []
        for sub in ("projects", "Projects", "code", "work", "Documents"):
            if (home / sub).is_dir():
                candidates.append(home / sub)
        roots = tuple(candidates) or (home,)

    console.print(f"scanning (depth {depth}, reading project metadata): " + ", ".join(str(r) for r in roots))
    fingerprints = walk_filesystem_readonly(
        list(roots),
        max_depth=depth,
        descend_into_projects=descend_into_projects,
    )
    console.print(f"  → {len(fingerprints)} directories fingerprinted")

    suggestions = suggest_tags(fingerprints)
    summary = build_inventory_summary(fingerprints)

    console.print(f"\n[bold]inventory:[/bold] {summary}")
    console.print("\n[bold]projects found:[/bold]")
    console.print(render_per_project_table(fingerprints))
    console.print("\n[bold]suggested tag vocabulary:[/bold]")
    for tag, why in sorted(suggestions.items()):
        console.print(f"  [cyan]{tag:<24}[/cyan] {why}")

    if not write:
        console.print("\n(dry run — pass --write to persist to viking)")
        return

    client = viking_client_for(cfg)
    import yaml as _yaml
    doc = client.cat("viking://meta/tags.yaml", level="L2")
    existing = _yaml.safe_load(doc.full) if doc and doc.full else {"tags": {}}
    if not isinstance(existing, dict):
        existing = {"tags": {}}
    existing.setdefault("tags", {})
    for tag, why in suggestions.items():
        existing["tags"].setdefault(tag, {"description": why})
    client.write("viking://meta/tags.yaml", _yaml.safe_dump(existing, sort_keys=False))
    client.write(
        "viking://user/inventory",
        summary,
        metadata={"scanned_roots": [str(r) for r in roots]},
    )
    client.close()
    console.print(f"[green]✓[/green] wrote tag vocabulary and inventory to viking")


if __name__ == "__main__":
    main()
