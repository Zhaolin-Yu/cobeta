# Read-only filesystem scanning

`cobeta setup` and `cobeta scan` both offer to walk your filesystem (read-only)
and use what they find to seed:

- **Tag vocabulary** → `viking://meta/tags.yaml`
- **User inventory summary** → `viking://user/inventory`

This page explains what the scanner sees, what it does NOT do, and how to
extend it.

## What the scanner reads

Only directory metadata via `os.scandir` — never file contents. Specifically:

- Directory names (used to derive candidate tags after stop-word filtering)
- Presence of `.git`, `README*`, `.cobeta.yaml`
- File extensions (bucketed into `code-python`, `code-rust`, `writing`, `data`,
  `ml-artifact`, `config`, …)
- File counts per directory (capped at 500 per dir for speed)

Hidden directories, `node_modules`, `.venv`, `build`, `dist`, `__pycache__`,
`.cache` and `.git` are skipped.

## What the scanner does NOT do

- Does NOT open any file
- Does NOT walk symlinks
- Does NOT phone home; everything happens locally and only the user-confirmed
  output reaches viking
- Does NOT touch directories outside the roots you explicitly pass

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
# Dry run; just print what would be suggested
cobeta scan

# Specify roots and depth
cobeta scan --root ~/projects --root ~/Documents/notes --depth 3

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
