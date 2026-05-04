# Installing cobeta

Three external prerequisites, then cobeta itself via `uv`, then `cobeta setup`.
Repeat per machine. The setup wizard auto-detects whether you're the brain or
a node — you don't have to decide in advance.

## 1. Tailscale (every machine)

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
tailscale status                    # confirm all your machines appear
```

If they don't show up, fix tailnet membership before proceeding. cobeta works
without Tailscale on a single machine but multi-machine features depend on it.

## 2. uv (every machine)

cobeta is distributed as a `uv tool` — installed in its own isolated env so it
never collides with other Python work. `uv` itself is a single fast binary.

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
uv --version
```

## 3. Install cobeta (every machine)

```bash
uv tool install 'cobeta[all]'    # all = anthropic + openai + openai-compatible
# Or scope down:
uv tool install 'cobeta[openai]'
uv tool install 'cobeta[anthropic]'
```

The `cobeta` binary lands on your `$PATH` automatically. Verify:

```bash
cobeta --version
```

## 4. Set your LLM API key (every machine, REQUIRED)

cobeta's primary surfaces (the bootstrap agent, the LLM scanner) need an
OpenAI-compatible endpoint. Any provider speaking the OpenAI chat-completions
protocol works:

```bash
export OPENAI_API_KEY=sk-...
# Optional: only set if your endpoint isn't api.openai.com
export OPENAI_BASE_URL=https://your-provider/v1
```

Concrete examples:

| Provider | base URL | model name example |
|---|---|---|
| OpenAI | `https://api.openai.com/v1` | `gpt-4o-mini` |
| Xiaomi MiMo | `https://token-plan-sgp.xiaomimimo.com/v1` | `mimo-v2.5-pro` |
| Together AI | `https://api.together.xyz/v1` | `meta-llama/Llama-3-70b-chat-hf` |
| Groq | `https://api.groq.com/openai/v1` | `llama3-70b-8192` |
| Ollama (local) | `http://localhost:11434/v1` | `llama3:8b` |
| OpenRouter | `https://openrouter.ai/api/v1` | `anthropic/claude-sonnet-4` |
| LM Studio (local) | `http://localhost:1234/v1` | (whatever you loaded) |

Without a key, you can still use `cobeta scan --heuristic` and `cobeta
bootstrap --interactive`, but those are degraded-UX fallbacks — the framework
is designed around the agent-driven path.

## 5. Run the setup wizard

```bash
cobeta setup
```

The wizard:

1. **Detects Tailscale** and lists your peers
2. **Probes every peer** on the OpenViking port (default `:7799`) to find an
   existing brain
3. **Asks you**: is this machine the brain, or do you want to point at the one
   it found?
4. **Asks for your OpenAI-compatible LLM endpoint**: base URL, model, key env var
5. **Validates the LLM endpoint** by hitting `/v1/models` once. Refuses to
   declare setup successful if the URL+key combination doesn't work
   (override with `--skip-llm-validation` if you know what you're doing)
6. **Optionally scans** your filesystem (read-only) to seed a tag vocabulary
   and a `viking://user/inventory` summary

The wizard writes `~/.cobeta/config.yaml`. Verify with:

```bash
cobeta status
```

Non-interactive setup (good for shell scripts and provisioning):

```bash
cobeta setup --as central \
  --llm-base-url https://api.openai.com/v1 \
  --llm-model gpt-4o-mini \
  --llm-api-key-env OPENAI_API_KEY \
  --skip-scan
```

## 6. Start the OpenViking server (brain only)

If your machine became the brain, you need to actually start the memory server.
cobeta does not run this for you (so you stay in control of how it's
daemonized).

```bash
# On the brain machine
uv tool install 'openviking[bot]'      # whatever the upstream package name is
openviking-server --port 7799 --bind 0.0.0.0

# Daemonize via your tool of choice — systemd, tmux, docker, launchd
```

Verify from any node:

```bash
curl http://<brain-tailscale-hostname>:7799/health
# → {"status":"ok"}
```

If the server is unreachable, cobeta degrades gracefully to a local JSON stub
at `~/.cobeta/viking-stub/` so you can keep working offline.

## 7. Create your first workspace

```bash
cobeta bootstrap
# or with a seed intent:
cobeta bootstrap "compare RoPE long-context variants"
```

The bootstrap agent reads viking (read-only), proposes a workspace shape, lets
you iterate, then deterministically generates the directory tree + handoff
files (`CLAUDE.md`, `AGENTS.md`, …) so any agent CLI you `cd` into picks it up.

## Updating

```bash
uv tool install --upgrade cobeta
# Your ~/.cobeta/config.yaml is preserved.
```

## Uninstalling

```bash
uv tool uninstall cobeta
rm -rf ~/.cobeta                    # config + local stub data
# Workspaces under ~/cobeta-workspaces/ are yours — keep or delete as you like
```

## Cross-machine operations

cobeta intentionally does NOT sync workspaces. To look at or run something on
another node, go there:

```bash
cobeta machines                                  # list peers + reachability
cobeta exec laptop-xps -- ls ~/cobeta-workspaces # run a command on a peer
cobeta ssh laptop-xps                            # interactive ssh
cobeta pull laptop-xps:~/output ./pulled/        # rsync a path back
```

All over Tailscale; falls back to plain `ssh` if Tailscale isn't installed.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `cobeta status` says viking unreachable | On the brain: `curl localhost:7799/health`. From a node: `tailscale ping <brain-hostname>`. Check the server is running. |
| `cobeta scan` says `$OPENAI_API_KEY not set` | Either `export OPENAI_API_KEY=...` then re-run, OR pass `--heuristic` to use the free deterministic scanner. |
| `cobeta setup` LLM validation failed | Run `curl -H "Authorization: Bearer $OPENAI_API_KEY" $OPENAI_BASE_URL/models` directly to debug. Most common: wrong URL (missing `/v1` suffix) or expired key. |
| `cobeta setup` can't find `tailscale` | Install Tailscale first (step 1). cobeta will run single-machine-only without it. |
| Want to switch endpoints later | Edit `~/.cobeta/config.yaml` directly (under `llm:`) or re-run `cobeta setup` with new flags. |
