# Architecture

cobeta is built around four observations about how individuals (not teams)
actually use AI agents for serious knowledge work:

1. **The agent CLI is interchangeable.** People hop between Claude Code, Codex,
   Cursor, opencode, sometimes within the same week. Whatever organization
   layer we build must not assume a single CLI.
2. **The folder structure carries semantics that survive the conversation.**
   ICM (Interpretable Context Methodology) makes this explicit: numbered
   stages, per-stage CONTEXT.md contracts, separation of references vs outputs.
3. **Memory is the bottleneck, not compute.** What you learned three weeks ago
   should inform today's work. OpenViking's hierarchical, URI-addressable
   memory store is the cleanest implementation of this idea we've found.
4. **Determinism beats agent-creativity for structural work.** The agent should
   *propose* structure; pure code should *enforce* it. Anything that has to
   match a schema (file layout, handoff content, tag vocabulary) is generated
   by code with pydantic validation, not freehand by an LLM.

## Component map

```
┌────────────────────────────────────────────────────────────────────┐
│                         cobeta package                              │
├──────────────┬───────────────┬─────────────────┬───────────────────┤
│   cli        │   agent       │   workspace     │   memory          │
│              │               │                 │                   │
│  setup       │  bootstrap    │  models         │  viking_client    │
│  status      │  tools        │  generator      │  viking_server    │
│  bootstrap   │  prompts      │  handoff        │  (HTTP over TS)   │
│  scan        │               │  inspector      │                   │
│  workspaces  │               │                 │                   │
│  viking      │               │                 │                   │
│  machines    │               │                 │                   │
│  exec/ssh/   │               │                 │                   │
│  pull        │               │                 │                   │
│  tags        │               │                 │                   │
├──────────────┼───────────────┼─────────────────┼───────────────────┤
│   config     │   llm         │   hooks         │   schemas         │
│              │               │                 │                   │
│  models      │  base         │  tag_lint       │  handoff_*.j2     │
│  loader      │  anthropic    │  workspace_     │  workspace_       │
│  tailscale   │  openai       │    validate     │    context.j2     │
│              │  (compat.)    │  handoff_check  │  stage_           │
│              │               │                 │    context.j2     │
├──────────────┼───────────────┼─────────────────┼───────────────────┤
│   setup      │   scanner     │   ssh           │                   │
│              │               │                 │                   │
│  wizard      │  heuristic    │  exec_remote    │                   │
│  brain_      │  (LLM mode    │  interactive_   │                   │
│  discover    │   v0.2)       │    ssh          │                   │
│              │               │  pull_path      │                   │
└──────────────┴───────────────┴─────────────────┴───────────────────┘
```

## Data flow: a bootstrap session

```
                          ┌────────────────────────────────────────┐
  user types              │                                        │
  `cobeta bootstrap` ───► │  cli.bootstrap                         │
                          │                                        │
                          │     ▼                                  │
                          │  config.loader.load_node_config        │
                          │     ▼                                  │
                          │  memory.VikingClient(read-only)        │
                          │     ▼                                  │
                          │  agent.bootstrap_with_llm              │
                          │     │  ▲                               │
                          │     │  │ tool calls / tool results     │
                          │     ▼  │                               │
                          │  llm.AnthropicProvider                 │
                          │     │                                  │
                          │     ▼                                  │
                          │  agent.tools.ToolDispatcher            │
                          │     │                                  │
                          │     ▼  (terminal)                      │
                          │  hooks.lint_tags  (pre-flight)         │
                          │     ▼                                  │
                          │  workspace.generate_workspace          │
                          │     ▼                                  │
                          │  workspace.write_handoff_files         │
                          │     ▼                                  │
                          │  hooks.validate_workspace_on_disk      │
                          │  hooks.check_handoff_files             │
                          │     ▼                                  │
                          │  return Workspace to cli               │
                          └────────────────────────────────────────┘
```

## Agent class design (agno-inspired)

The agent module follows the pattern popularized by [agno](https://github.com/agno-agi/agno):

- **`Tool`** — a base class. Each tool is its own subclass declaring `name`,
  `description`, `permission` (`READ` / `WRITE` / `USER_INPUT` / `TERMINAL`),
  and `input_schema`. The tool implements `execute(**kwargs) -> str`.
- **`Agent`** — composes a model + a list of tools + an instruction string.
  `agent.run(seed)` drives the chat loop, dispatches tool calls by name, and
  returns when a `TERMINAL` tool succeeds (or `max_turns` hits, or the user
  aborts).
- **`builtin_tools.bootstrap_toolset(cfg, viking, ask_fn)`** — returns the
  canonical bootstrap toolset (read-only viking tools + `ask_user` +
  `generate_workspace`). Adding a new bootstrap-mode capability = adding one
  tool class plus listing it in this preset.

This gives us four properties:

| Property | Why it matters |
|---|---|
| Tools are testable in isolation | Mock the dependencies, call `execute()`, inspect output |
| Permission is part of the type | Read-only-during-bootstrap is enforced by *not including* write tools, not by runtime checks |
| The agent doesn't know how tools are implemented | Swap viking for a mock without touching the agent loop |
| New tool = new file | No central dispatcher to keep in sync |

The `Tool.permission` enum is metadata, not a runtime gate — runtime gating
happens by selecting which tools to pass to which Agent. For the bootstrap
loop we never include tools with `WRITE` permission, so write attempts simply
don't exist in the action space.

## Where the agent ends and code begins

The bootstrap agent has freedom to:
- Talk to the user, ask questions, iterate
- Read viking, list existing workspaces
- *Propose* a `WorkspaceSpec` (name, intent, stages, tags, handoffs)

The agent has zero freedom to:
- Skip pydantic validation on the spec
- Decide what files get written or where
- Skip post-generation hooks
- Create or modify tag vocabulary
- Write to viking memory

This boundary is the whole point. The agent does the messy human-facing part;
the code does the part that has to be perfectly reproducible.

## Multi-machine model

aim-patho-equivalent (one per user, chosen at install) runs OpenViking. Every
other node runs cobeta in `node` role, pointing its `viking_client` at the
central node's Tailscale hostname over HTTP. There is no full vault sync.
Workspaces are local to each machine; what's shared is *memory* (small,
structured, central) not *artifacts* (large, opaque, distributed).

When a workspace running on node A needs an artifact from node B (e.g., a
checkpoint produced by `experiment-x` over there), the user invokes
`cobeta exec B -- <command>` rather than blindly synchronizing.

## Why no full sync

| If we synced everything | What happens |
|---|---|
| Big checkpoints (`.pt`, `.bin`) get duplicated to every machine | Disk fills, network strained |
| Workspace outputs differ per-machine (different runs of stage 02) | Sync conflicts on every commit |
| Memory and content live in the same store | Updates to memory churn the whole tree |

We sidestep all of that by making *memory* the single shared object and
treating workspaces as machine-local with optional manual promotion.
