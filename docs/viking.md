# OpenViking integration

[OpenViking](https://github.com/volcengine/OpenViking) is the memory backend.
This doc explains what cobeta puts where, how the read-only-during-bootstrap
rule is enforced, and what the local-stub fallback does.

## URI namespaces cobeta uses

| URI prefix | What lives here | Who writes |
|---|---|---|
| `viking://user/preferences/` | Long-lived facts about the user (work style, tooling preferences, habits). | Workspace agents during stage 03 (integrate); never bootstrap. |
| `viking://user/memories/` | Episodic things the user told the agent. | Same as above. |
| `viking://agent/memories/<workspace>/` | Per-workspace accumulated learnings — what worked, what didn't. | The agent inside that workspace; cobeta itself never writes here. |
| `viking://agent/skills/` | Registered skills for downstream agents. | Out of scope for the framework; user-managed. |
| `viking://resources/concepts/` | Cross-workspace reusable knowledge (paper notes, atomic concepts). | User-managed; cobeta only references. |
| `viking://meta/tags.yaml` | Controlled tag vocabulary. | User-managed; cobeta validates against. |

## Bootstrap is read-only

The `viking-introspect` rules are enforced two ways:

1. **Procedurally** — the bootstrap agent's system prompt forbids write
   operations and the toolset exposed to it (`viking_find`, `viking_tree`,
   `viking_cat`) is read-only. There's no `viking_write` tool.
2. **By convention** — written into the system prompt. If a future tool ever
   exposes write to bootstrap, this rule is the canonical source of truth.

## The local-stub fallback

When the central viking is unreachable (Tailscale down, central node off, or
on the very first install before viking is started), `VikingClient` falls back
to a simple JSON-on-disk store at `~/.cobeta/viking-stub/store.json`.

This keeps `cobeta bootstrap` usable in all situations, but **the stub is
per-machine**. Whatever you write while disconnected is local; reconciling
back to the real viking is on the roadmap. For now, treat the stub as
ephemeral scratch space.

## Schema for `viking://meta/tags.yaml`

The tag vocabulary is the only viking content cobeta has an opinion about.
Format:

```yaml
tags:
  long-context:
    description: Techniques for extending transformer context windows
    aliases: [longctx, long_ctx]
  rope:
    description: Rotary position embedding and variants
  transformer:
    description: Transformer architecture and components
```

Any tag in a `WorkspaceSpec.tags` that isn't a key (or alias) under `tags:` is
flagged by `hooks.lint_tags`. The lint runs both pre-flight (before generating
a workspace) and on demand (`cobeta tags lint`, planned).
