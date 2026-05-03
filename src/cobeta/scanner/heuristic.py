"""Read-only filesystem scanner used at setup time to seed tag vocabulary
and OpenViking memory.

This is the **heuristic** v1 — pure code, no LLM. It walks the filesystem
shallowly (configurable depth), records a fingerprint per directory, then
clusters fingerprints into suggested tags. The user reviews the suggestions
before anything is written to viking.

A future v0.2 will offer an **agent** mode that uses the LLM with read-only
filesystem tools (`list_dir`, `cat_path`) and the same write-to-viking output.
The two modes share the same `ScanReport` schema so callers can swap them.
"""

from __future__ import annotations

import os
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional


# ---------- data ----------

# Words that are not interesting as tags (too generic or noise)
_STOPWORDS = {
    "src", "lib", "bin", "tmp", "test", "tests", "data", "docs", "doc",
    "node_modules", "venv", ".venv", "env", "build", "dist", "target",
    "old", "new", "tmp", "temp", "scratch", "misc", "stuff", "things",
    "project", "projects", "code", "work", "works", "my", "the", "a", "an",
    "to", "for", "with", "from", "into", "and", "or", "of", "in", "on",
    "main", "master", "branch", "repo", "fork", "clone", "copy",
    "draft", "drafts", "version", "versions", "v1", "v2", "v3",
    ".git", ".github", ".idea", ".vscode", ".cache", ".local",
    "applications", "downloads", "desktop", "documents", "library",
}

# Extensions grouped by what they suggest about the directory's purpose
_EXT_BUCKETS = {
    "code-python": {".py", ".pyi", ".ipynb"},
    "code-rust": {".rs", ".toml"},
    "code-js": {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"},
    "code-go": {".go"},
    "code-cpp": {".c", ".h", ".cpp", ".hpp", ".cc", ".hh"},
    "writing": {".md", ".tex", ".rst", ".org", ".typ"},
    "data": {".csv", ".parquet", ".json", ".jsonl", ".arrow", ".feather"},
    "ml-artifact": {".pt", ".bin", ".safetensors", ".onnx", ".gguf", ".npz"},
    "config": {".yaml", ".yml", ".toml", ".ini"},
}

_KEBAB_SPLIT = re.compile(r"[\s_\-./\\]+|(?=[A-Z])")


@dataclass
class DirectoryFingerprint:
    path: Path
    name_tokens: list[str]
    has_git: bool
    has_readme: bool
    has_dotcobeta: bool
    file_count: int
    extension_buckets: dict[str, int] = field(default_factory=dict)

    @property
    def dominant_bucket(self) -> Optional[str]:
        if not self.extension_buckets:
            return None
        return max(self.extension_buckets, key=self.extension_buckets.get)

    @property
    def is_project_like(self) -> bool:
        # Heuristic: has git, has README, or has .cobeta marker = looks like a project root
        return self.has_git or self.has_readme or self.has_dotcobeta


@dataclass
class ScanReport:
    roots: list[Path]
    fingerprints: list[DirectoryFingerprint]
    suggested_tags: dict[str, str] = field(default_factory=dict)  # tag -> rationale
    user_inventory_summary: str = ""


# ---------- core walk ----------

def _tokenize(name: str) -> list[str]:
    parts = [p.lower() for p in _KEBAB_SPLIT.split(name) if p]
    return [p for p in parts if p and p not in _STOPWORDS and not p.isdigit() and len(p) > 2]


def _bucket_for(ext: str) -> Optional[str]:
    for bucket, exts in _EXT_BUCKETS.items():
        if ext in exts:
            return bucket
    return None


def _fingerprint(d: Path, max_files: int = 500) -> DirectoryFingerprint:
    has_git = (d / ".git").exists()
    has_readme = any((d / f"README{ext}").exists() for ext in ("", ".md", ".rst", ".txt"))
    has_dotcobeta = (d / ".cobeta.yaml").exists()

    extension_buckets: dict[str, int] = {}
    file_count = 0
    try:
        with os.scandir(d) as it:
            for entry in it:
                if file_count >= max_files:
                    break
                if entry.is_file(follow_symlinks=False):
                    file_count += 1
                    ext = Path(entry.name).suffix.lower()
                    bucket = _bucket_for(ext)
                    if bucket:
                        extension_buckets[bucket] = extension_buckets.get(bucket, 0) + 1
    except (PermissionError, OSError):
        pass

    return DirectoryFingerprint(
        path=d,
        name_tokens=_tokenize(d.name),
        has_git=has_git,
        has_readme=has_readme,
        has_dotcobeta=has_dotcobeta,
        file_count=file_count,
        extension_buckets=extension_buckets,
    )


def walk_filesystem_readonly(
    roots: Iterable[Path],
    *,
    max_depth: int = 2,
    skip_dot_dirs: bool = True,
    skip_dirs: Optional[set[str]] = None,
) -> list[DirectoryFingerprint]:
    """Walk `roots` shallowly, returning a fingerprint per directory.

    Pure read-only: never opens file contents, only metadata + os.scandir.
    """

    skip = (skip_dirs or set()) | {"node_modules", ".venv", "venv", ".cache", ".git", "build", "dist", "target", "__pycache__"}
    fingerprints: list[DirectoryFingerprint] = []

    def _recurse(d: Path, depth: int) -> None:
        if not d.is_dir():
            return
        try:
            fingerprints.append(_fingerprint(d))
        except Exception:
            return
        if depth >= max_depth:
            return
        try:
            with os.scandir(d) as it:
                for entry in it:
                    if not entry.is_dir(follow_symlinks=False):
                        continue
                    name = entry.name
                    if skip_dot_dirs and name.startswith("."):
                        continue
                    if name in skip:
                        continue
                    _recurse(Path(entry.path), depth + 1)
        except (PermissionError, OSError):
            return

    for r in roots:
        r = r.expanduser().resolve()
        if r.exists() and r.is_dir():
            _recurse(r, depth=0)

    return fingerprints


# ---------- tag suggestion ----------

def suggest_tags(fingerprints: list[DirectoryFingerprint], *, top_n: int = 20) -> dict[str, str]:
    """Cluster fingerprints into a suggested tag vocabulary.

    Returns `{tag: one-line-rationale}`. Caller decides which to keep.
    """

    project_fingerprints = [f for f in fingerprints if f.is_project_like]

    # Token frequency across project-like dirs
    tok_counter: Counter[str] = Counter()
    for fp in project_fingerprints:
        for tok in fp.name_tokens:
            tok_counter[tok] += 1

    suggestions: dict[str, str] = {}
    for tok, count in tok_counter.most_common(top_n):
        if count < 2:
            continue
        if not _is_valid_tag(tok):
            continue
        suggestions[tok] = f"appears in {count} project-like directories"

    # Always seed a few useful framework-managed tags
    seeded = {
        "wip": "work-in-progress; default lifecycle marker",
        "experiment": "exploratory work, expected to be discarded or promoted",
        "reference": "long-lived reference material, not actively edited",
        "shared": "intended for cross-workspace reuse",
    }
    for k, v in seeded.items():
        suggestions.setdefault(k, v)

    # Add bucket-derived tags if bucket is dominant in many dirs
    bucket_counter: Counter[str] = Counter()
    for fp in project_fingerprints:
        if fp.dominant_bucket:
            bucket_counter[fp.dominant_bucket] += 1
    for bucket, count in bucket_counter.items():
        if count >= 3:
            tag = bucket.replace("-", "-")
            suggestions.setdefault(tag, f"dominant in {count} scanned project(s)")

    return suggestions


def _is_valid_tag(s: str) -> bool:
    return bool(re.match(r"^[a-z][a-z0-9-]{1,30}$", s))


# ---------- whole-machine summary ----------

def build_inventory_summary(fingerprints: list[DirectoryFingerprint]) -> str:
    """One-paragraph summary of what was found, suitable for viking://user/inventory."""
    project_count = sum(1 for f in fingerprints if f.is_project_like)
    bucket_counts: Counter[str] = Counter()
    for f in fingerprints:
        if f.dominant_bucket:
            bucket_counts[f.dominant_bucket] += 1
    bucket_summary = ", ".join(f"{b}: {n}" for b, n in bucket_counts.most_common(8)) or "no clear pattern"
    return (
        f"Scanned {len(fingerprints)} directories, of which {project_count} look project-like "
        f"(have git/README/.cobeta). Dominant content types: {bucket_summary}."
    )
