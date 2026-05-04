# Read-only filesystem scanning

`cobeta setup` and `cobeta scan` both offer to walk your filesystem (read-only)
and use what they find to seed:

- **Tag vocabulary** → `viking://meta/tags.yaml`
- **User inventory summary** → `viking://user/inventory`

This page explains what the scanner sees, what it does NOT do, and how to
extend it.

## What the scanner reads

Two kinds of input:

### A) Directory metadata via `os.scandir`

- Directory names (used to derive candidate tags after stop-word filtering)
- Presence of `.git`, `README*`, `.cobeta.yaml`
- File extensions (bucketed into `code-python`, `code-rust`, `writing`, `data`,
  `ml-artifact`, `config`, …)
- File counts per directory (capped at 500 per dir for speed)

### B) Project self-description files (read up to 16 KB each)

These exist *to describe the project publicly* — reading them is read-only and
non-invasive:

- `README.md / .rst / .txt` — first substantive paragraph (~280 chars), used as
  the project's description
- `pyproject.toml` — `project.name`, `project.description`, `project.keywords`,
  `project.dependencies` (top 30, version constraints stripped)
- `package.json` — same fields plus dev dependencies
- `Cargo.toml` — same
- `go.mod` — module name only
- `.cobeta.yaml` — workspace audit record (highest-priority signal)
- `.git/config` — origin URL only, with basic-auth credentials stripped

These signals make tag suggestions much sharper than directory names alone:
project keywords surface directly, recurring dependencies become candidate
stack tags, and bigrams from descriptions catch compound concepts.

Hidden directories, `node_modules`, `.venv`, `build`, `dist`, `__pycache__`,
`.cache`, `.git`, `site-packages`, `pkg`, `vendor`, `target` are skipped.

By default we **do not descend** into a directory once it looks project-like
(has git/README/package metadata) — this avoids polluting results with vendored
source trees (e.g. `$GOROOT/src` exposing every Go stdlib package). Pass
`--descend-into-projects` to override.

## What the scanner does NOT do

- Does NOT open source code, notes, drafts, or any file other than the
  well-known self-description files listed above
- Does NOT walk symlinks
- Does NOT phone home; everything happens locally and only the user-confirmed
  output reaches viking
- Does NOT touch directories outside the roots you explicitly pass
- Does NOT include credentials from git config (basic-auth segments are
  regex-stripped before display)

## Interactive flow (during `cobeta setup`)

```
Scan this machine (read-only) to seed tag vocabulary and viking memory? [y/N]:
```

If yes, cobeta:
1. Picks roots: `~/projects`, `~/Projects`, `~/code`, `~/work`, `~/Documents`
   (whichever exist), or `~` as fallback
2. Walks at depth 2
3. Suggests a tag vocabulary
4. Shows the suggestions
5. Asks for permission to write to viking

You can always re-run later as `cobeta scan --root <path> --write`.

## CLI

```bash
# Dry run on default roots (~/projects, ~/code, ~/Documents if they exist)
cobeta scan

# Specify roots and depth
cobeta scan --root ~/projects --root ~/Documents/notes --depth 3

# Allow recursion into project subdirs (rarely useful — risks vendored noise)
cobeta scan --root ~/monorepo --descend-into-projects

# Persist to viking after reviewing
cobeta scan --write
```

## Seeded tags

Regardless of what's on disk, the scanner always suggests four lifecycle tags:

- `wip` — work in progress
- `experiment` — exploratory, may be discarded
- `reference` — long-lived material
- `shared` — reusable across workspaces

These are the minimum vocabulary the rest of cobeta assumes exists.

## Extending the scanner

The current implementation is heuristic-only. A future v0.2 will offer an
LLM-driven mode that uses read-only filesystem tools (`list_dir`, `cat_path`)
to make smarter suggestions. The interface (`ScanReport`) is shared so
callers can swap modes without code changes.

To add a new extension bucket, edit `_EXT_BUCKETS` in
`src/cobeta/scanner/heuristic.py`. To add new lifecycle tags, edit
`suggest_tags()`'s `seeded` dict.
