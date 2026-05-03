# cobeta

> An open-source agent framework that organizes your work into ICM workspaces
> and shares memory across Tailscale-linked machines via OpenViking.

cobeta is **not a vault for your content**. It is a *framework* you install on
a few of your machines. It does one thing well: when you start a new piece of
work, a small bootstrap agent reads what it knows about you (read-only),
talks to you briefly, then deterministically generates an ICM-shaped workspace
on disk and writes a handoff file (`CLAUDE.md`, `AGENTS.md`, …) that any agent
CLI can pick up. After that, cobeta gets out of the way.

## Three external dependencies

| | Why | Where it runs |
|---|---|---|
| [Tailscale](https://tailscale.com) | Private mesh network so all your machines see each other without VPN headaches | Every machine |
| [OpenViking](https://github.com/volcengine/OpenViking) | Hierarchical memory store accessed via `viking://` URIs (L0/L1/L2 tiered loading) | One **central** machine; others connect over Tailscale |
| An LLM API (Anthropic, OpenAI, …) | Powers the bootstrap agent | Every machine, via `$ANTHROPIC_API_KEY` etc. |

## Architecture in one picture

```
   ┌─────────────────────────┐
   │  central node           │
   │  (you pick which one)   │
   │                         │
   │  cobeta + viking-server │◄──── HTTP over Tailscale ────┐
   └─────────────────────────┘                              │
                                                            │
   ┌─────────────────────────┐                              │
   │  node A                 │                              │
   │  cobeta (client mode)   │──────── viking_client ───────┤
   └─────────────────────────┘                              │
                                                            │
   ┌─────────────────────────┐                              │
   │  node B                 │                              │
   │  cobeta (client mode)   │──────── viking_client ───────┘
   └─────────────────────────┘

   Big files stay on the machine that produced them.
   Only memory (small structured records) is centralized.
```

## What you do with it

```bash
# Once, on every machine
uv tool install 'cobeta[all]'       # cobeta itself
cobeta setup                        # wizard: detects tailscale + brain, asks the right questions

# Whenever you start something new (on any node)
cobeta bootstrap
# → talks to you for ~30 seconds (LLM-driven if you have an API key, plain CLI if not)
# → generates ~/cobeta-workspaces/<name>/ with stages, references, output dirs
# → writes handoff files (CLAUDE.md, AGENTS.md, …) so any agent CLI picks it up
# → done; cobeta exits

cd ~/cobeta-workspaces/<name> && claude
# now you're working — claude reads CLAUDE.md, knows the workspace shape, the tags, viking URIs
```

## Cross-machine = ssh, not sync

cobeta does **not** synchronize workspaces. When you need to look at or run
something on another node, go there directly:

```bash
cobeta machines                                  # who's on the tailnet
cobeta exec aim-patho -- ls ~/cobeta-workspaces  # run on a peer
cobeta ssh aim-patho                             # interactive ssh
cobeta pull aim-patho:~/output ./pulled/         # rsync back
```

All over Tailscale. Memory is centralized (one OpenViking on the brain);
artifacts stay on whichever machine produced them.

## What lives where

| Thing | Location | Synced? |
|---|---|---|
| Framework binary | `~/.local/bin/cobeta` (via `uv tool install`) | n/a — `uv tool install --upgrade cobeta` |
| Per-machine config | `~/.cobeta/config.yaml` | No — each machine has its own role |
| Tag vocabulary | `viking://meta/tags.yaml` | Yes (lives on the brain) |
| Workspaces | `~/cobeta-workspaces/<name>/` (configurable) | No — local to the machine that created them |
| Long-term memory | `viking://user/`, `viking://agent/memories/<workspace>/` | Yes (brain) |
| Big artifacts (checkpoints, datasets) | Wherever they were produced | No — pull on demand via `cobeta exec/pull` |

## See also

- [`INSTALL.md`](INSTALL.md) — full setup walk-through
- [`docs/architecture.md`](docs/architecture.md) — design rationale
- [`docs/icm.md`](docs/icm.md) — how cobeta uses ICM (Interpretable Context Methodology)
- [`docs/handoff-files.md`](docs/handoff-files.md) — what gets written into `CLAUDE.md` / `AGENTS.md` / `.cursor/rules/` and why
- [`docs/viking.md`](docs/viking.md) — OpenViking integration

## License

MIT. Free to use, fork, share. Your content is yours; this framework never
touches it beyond the `~/cobeta/<workspace>/` paths you create with it.
