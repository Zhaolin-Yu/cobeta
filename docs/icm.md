# How cobeta uses ICM

ICM (Interpretable Context Methodology) is a workflow-organization pattern
described at [txmyer-dev/icm](https://github.com/txmyer-dev/icm). Its central
claim: *folder structure is agent architecture*. cobeta builds on this by
generating ICM-shaped workspaces deterministically.

## The five layers (per ICM)

| Layer | What it is | Where in a cobeta workspace |
|---|---|---|
| 0 | "Where am I?" | Handoff file (`CLAUDE.md`, `AGENTS.md`, …) at workspace root |
| 1 | "Where do I go?" | `CONTEXT.md` at workspace root |
| 2 | "What do I do?" | `stages/<NN-name>/CONTEXT.md` |
| 3 | "What rules apply?" | `references/` (workspace-wide) and `stages/<n>/references/` (stage-local) |
| 4 | "What am I working with?" | `stages/<n>/output/` |

cobeta's generator writes Layers 0, 1, and 2 directly. Layers 3 and 4 are
empty directories at generation time — the user (or their agent CLI) fills
them as work proceeds.

## Stage contracts

Every stage's `CONTEXT.md` follows the ICM contract template:

- **Inputs** — table of `(source, location, why)` rows
- **Process** — numbered steps
- **Outputs** — what artifacts the stage produces
- **Audit** — pre-exit checklist

cobeta's `Stage` pydantic model captures these as `ContextContract`. The
bootstrap agent fills in inputs/process/outputs/audit when generating a
spec — though leaving them blank for the user to fill in later is also
allowed (the stage CONTEXT.md template renders explicit "_define this_"
placeholders).

## What cobeta adds to ICM

- **Determinism**: pydantic-validated `WorkspaceSpec`, deterministic generator,
  post-generation hooks. No agent freedom past the spec.
- **Cross-CLI handoff**: the same workspace is usable from Claude Code, Codex,
  Cursor, opencode — cobeta writes the right memory file for each CLI the
  user opted into.
- **Memory layer** (OpenViking): Layer 3 references can point into
  `viking://resources/`, and per-workspace memory lives at
  `viking://agent/memories/<name>/`.
- **Multi-machine awareness**: every workspace records its `default_machine`
  in `.cobeta.yaml`, and the handoff files mention how to use
  `cobeta exec <other-machine>` for cross-node artifact pulls.

## What cobeta does NOT do

- We do **not** invent new workspace shapes. ICM defines `pipeline` /
  `monitoring` / `creation` style narratives; cobeta uses those as agent
  prompt material (in `examples/workspace-examples/`) but doesn't ship
  fixed templates that agents must pick from. The bootstrap agent is
  expected to *propose* a workspace shape based on the user's intent.
- We do **not** automate stage transitions. Walking from stage 01 to 02 is
  the user's job (or their agent CLI's job once they `cd` into the workspace).
  cobeta exits after generation.
