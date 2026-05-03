# Handoff files

A handoff file is a small markdown / json blob cobeta drops into a generated
workspace whose only purpose is to tell the user's agent CLI:

1. **What this directory is** ("a cobeta workspace named X for purpose Y")
2. **What each folder is for** (CONTEXT.md routing, references, output, stages)
3. **Which tags apply**
4. **Where the workspace's viking memory lives**
5. **Which machine is the default execution machine**

After cobeta writes these, our framework gets out of the way. The agent CLI
the user starts (Claude Code, Codex, Cursor, opencode) reads its own
convention file (`CLAUDE.md`, `AGENTS.md`, `.cursor/rules/cobeta.mdc`,
`.opencode/opencode.json`) and immediately knows what's going on.

## Why deterministic, not agent-written?

If the LLM writes the handoff file directly, two failure modes appear:

1. **Format drift**: a long-form Claude Code CLAUDE.md that doesn't match the
   conventions Anthropic actually parses, leaving the next session worse off
   than no handoff at all.
2. **Inconsistent semantics**: the same workspace presented differently in
   `CLAUDE.md` vs `AGENTS.md`, so switching CLIs mid-project loses context.

cobeta's handoff files are rendered by Jinja2 templates from the same
`Workspace` object. The templates live in `src/cobeta/schemas/` and are
shipped as package data — extending them or contributing better defaults is
a one-PR change.

## What cobeta does NOT write into handoff files

- The user's name, email, or other identity
- Anything from `viking://user/` (handoff files end up in workspaces that
  may or may not stay private — better to make agents pull from viking at
  runtime than embed memory snapshots)
- API keys, secrets, or paths outside the workspace
- Project-specific work content (that's the user's job; cobeta only
  generates structure and references)

## Adding a new agent CLI

If a new agent CLI emerges with its own memory-file convention:

1. Add a value to `HandoffTarget` enum in `src/cobeta/workspace/models.py`
2. Add a Jinja2 template at `src/cobeta/schemas/handoff_<name>.<ext>.j2`
3. Add an entry to `_TARGET_MAP` in `src/cobeta/workspace/handoff.py`
4. Add the matching keyword check to `_REQUIRED_KEYWORDS` in
   `src/cobeta/hooks/handoff_check.py`
5. Update CLI install detection if appropriate

Pure data-and-template change; no agent logic to touch.
