"""LLM-driven filesystem scanner.

The agent walks the user's project roots itself (with safety guards), reads
self-description files, and produces a structured ScanReport. Compared to
the heuristic scanner, the LLM version is:

- **Slower and costs tokens** — pay-per-scan instead of free
- **Smarter at interpretation** — can infer what a project actually IS,
  not just what files it contains
- **Better at cross-project pattern detection** — explicitly observes
  layout patterns by language / domain
- **Limited by prompt budget** — bounded by max_turns

Use heuristic mode by default; reach for LLM mode when the heuristic gives
poor signal (e.g. too generic tag suggestions, missed semantic similarities).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..agent.agent import Agent
from ..llm.base import LLMProvider
from .llm_tools import (
    ListDirTool,
    ReadFileTool,
    SubmitProjectFingerprintTool,
    SubmitScanReportTool,
)


@dataclass
class LLMScanReport:
    """Structured output from the LLM scanner. Same fields as heuristic mode
    writes to viking (so the CLI can persist either source identically).
    """

    projects: list[dict[str, Any]] = field(default_factory=list)
    suggested_tags: dict[str, str] = field(default_factory=dict)
    inventory_summary: str = ""
    layout_patterns: dict[str, list[str]] = field(default_factory=dict)


_SCAN_SYSTEM_PROMPT = """\
You are a filesystem-scanning agent. Your job: walk the user's project
directories (read-only) and produce a structured ScanReport with per-project
fingerprints + suggested tag vocabulary + cross-project layout patterns.

# Hard rules

- READ-ONLY. Never modify anything. (Your tools don't allow writes.)
- Only access paths inside the allowed roots — the framework enforces this
  and your reads will be rejected if you stray. Don't try.
- Be efficient. Aim for under ~30 file reads and ~50 list_dir calls total.
- Don't read source code. Prefer self-description files: README.md, README.rst,
  pyproject.toml, package.json, Cargo.toml, go.mod, .git/config, .cobeta.yaml.
  Reading code wastes tokens and rarely helps tag/layout inference.

# Process

1. For each root in the seed message, call `list_dir(root)` to see what's at
   top level.
2. For each subdir at top level, call `list_dir(<root>/<subdir>)` to see if
   it's project-like (has git/README/package metadata files).
3. For each project-like subdir, read its self-description files (start with
   README and pyproject/package.json/Cargo.toml).
4. Call `submit_project_fingerprint` once per project with what you learned.
   Be honest about uncertainty — leave fields blank/empty if you don't know.
5. AFTER all per-project fingerprints, call `submit_scan_report` with:
   - Curated tag vocabulary suggestions (kebab-case, with rationale)
   - Cross-project layout patterns grouped by language or domain
   - One-paragraph inventory summary
6. Stop.

# Tag vocabulary suggestions

Sources to draw from, in priority order:
1. Author-declared `keywords` in pyproject/package.json/Cargo.toml — highest signal
2. Dependencies that appear in multiple projects (shared stack signal)
3. Project name tokens appearing in 2+ projects (e.g. "agent", "proxy")
4. Always include the four lifecycle tags: `wip`, `experiment`, `reference`, `shared`

Tags must be kebab-case (a-z, 0-9, hyphens). Avoid project-specific singletons
that won't reuse across the user's body of work.

# Layout patterns

Cluster projects by language or type, then list the top-level directory names
that recur across each cluster. Example:

```
python_projects: src, tests, docs, examples
ml_projects: data, experiments, notebooks
js_projects: src, dist, public
go_projects: cmd, internal, pkg
```

These patterns become a prior the bootstrap agent uses later when designing
new workspace layouts. Make them concrete and useful — only include dirs you
actually saw across multiple projects in that cluster.

# Style

Quiet. The framework prints your tool calls; your text output goes to the user.
Keep text to short progress notes. The structured submissions are what matter.
"""


def llm_scan(
    roots: list[Path],
    llm: LLMProvider,
    *,
    max_turns: int = 40,
    echo_text: bool = False,
) -> LLMScanReport:
    """Run the LLM scanner agent. Returns a populated LLMScanReport.

    Raises RuntimeError if the agent fails to complete (e.g. exhausts max_turns
    without calling submit_scan_report).
    """

    projects: list[dict[str, Any]] = []
    tools = [
        ListDirTool(roots),
        ReadFileTool(roots),
        SubmitProjectFingerprintTool(projects),
        SubmitScanReportTool(),
    ]

    agent = Agent(
        model=llm,
        tools=tools,
        instructions=_SCAN_SYSTEM_PROMPT,
        max_turns=max_turns,
        echo_text=echo_text,
        # Override user-prompt fallback: in scan mode, model emitting bare text
        # without a tool call is a sign of trouble — return a structured nudge
        # instead of asking the user.
        user_prompt_fn=lambda q: "(no human in the loop — call your next tool)",
    )

    seed = (
        "Scan these roots and produce a ScanReport: "
        + ", ".join(str(r.expanduser()) for r in roots)
        + "\n\nStart by listing the contents of each root."
    )
    result = agent.run(seed)

    if not result.completed or result.terminal_payload is None:
        raise RuntimeError(
            f"llm_scan failed: {result.error or 'no terminal payload'}"
        )

    payload = result.terminal_payload
    return LLMScanReport(
        projects=projects,
        suggested_tags=payload.get("suggested_tags", {}) or {},
        inventory_summary=payload.get("inventory_summary", "") or "",
        layout_patterns=payload.get("layout_patterns", {}) or {},
    )
