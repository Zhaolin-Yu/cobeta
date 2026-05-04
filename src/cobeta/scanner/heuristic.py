"""Read-only filesystem scanner used at setup time to seed tag vocabulary
and OpenViking memory.

This is the **heuristic** scanner — pure code, no LLM. It walks the filesystem
shallowly (configurable depth), records a fingerprint per directory, then
clusters fingerprints into suggested tags.

What the scanner reads:
  - Directory names + os.scandir metadata
  - **Project self-description files** (README, pyproject.toml, package.json,
    Cargo.toml, .cobeta.yaml) — these are designed to describe the project
    publicly; reading them gives much richer signal than directory names alone
  - .git/config (origin URL, with credentials stripped)

What the scanner does NOT read:
  - Source code, notes, drafts, mail, anything personal
  - Any file > 1 MB
  - Any file outside the explicitly passed roots
  - Symlink targets

A future LLM mode will use the same `DirectoryFingerprint` schema and feed
the descriptions to an actual model for curated tag suggestions.
"""

from __future__ import annotations

import json
import os
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

try:
    import tomllib  # py 3.11+
except ImportError:  # pragma: no cover
    tomllib = None  # type: ignore


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

    # Rich metadata (filled by `_read_project_metadata`)
    project_name: Optional[str] = None       # from pyproject/package.json/Cargo.toml
    description: Optional[str] = None         # one-line summary
    keywords: list[str] = field(default_factory=list)  # author-curated tags
    dependencies: list[str] = field(default_factory=list)  # top-level deps
    languages: list[str] = field(default_factory=list)  # python/js/rust/go/...
    git_remote: Optional[str] = None         # anonymized

    @property
    def dominant_bucket(self) -> Optional[str]:
        if not self.extension_buckets:
            return None
        return max(self.extension_buckets, key=self.extension_buckets.get)

    @property
    def is_project_like(self) -> bool:
        # Heuristic: has git, has README, has .cobeta, or any package metadata = project-like
        return (
            self.has_git
            or self.has_readme
            or self.has_dotcobeta
            or self.project_name is not None
        )

    @property
    def label(self) -> str:
        """Best-effort short label: project_name or directory basename."""
        return self.project_name or self.path.name


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


# ---------- project metadata readers ----------

_MAX_READ_BYTES = 16 * 1024


def _read_first(path: Path, n: int = _MAX_READ_BYTES) -> str:
    try:
        if path.stat().st_size > 1024 * 1024:
            return ""  # skip huge files
        with path.open("r", encoding="utf-8", errors="replace") as f:
            return f.read(n)
    except (OSError, UnicodeError):
        return ""


_README_NAMES = ("README.md", "README.rst", "README.txt", "README", "readme.md")


def _read_readme(d: Path) -> Optional[str]:
    """First substantive paragraph of any README; ~280 chars."""
    for name in _README_NAMES:
        p = d / name
        if not p.exists() or not p.is_file():
            continue
        text = _read_first(p)
        if not text:
            continue
        paragraphs = re.split(r"\n\s*\n", text)
        for para in paragraphs:
            clean = para.strip()
            # Strip leading markdown headers / blockquote markers
            clean = re.sub(r"^[#>\s]+", "", clean)
            # Strip common badge/image markdown
            clean = re.sub(r"!?\[[^\]]*\]\([^)]*\)", "", clean)
            # Strip remaining markdown emphasis
            clean = re.sub(r"[*_`]", "", clean)
            clean = " ".join(clean.split())  # collapse whitespace
            if clean and len(clean) > 20:
                return clean[:280]
    return None


def _read_pyproject(d: Path) -> dict[str, Any]:
    p = d / "pyproject.toml"
    if not p.exists() or tomllib is None:
        return {}
    try:
        with p.open("rb") as f:
            data = tomllib.load(f)
    except Exception:
        return {}
    proj = data.get("project") or {}
    deps_raw = proj.get("dependencies") or []
    deps = []
    for d_str in deps_raw[:30]:
        # Strip version constraints
        name = re.split(r"[<>=!~\[]", str(d_str))[0].strip()
        if name:
            deps.append(name)
    return {
        "name": proj.get("name"),
        "description": proj.get("description"),
        "keywords": list(proj.get("keywords") or []),
        "dependencies": deps,
        "language": "python",
    }


def _read_package_json(d: Path) -> dict[str, Any]:
    p = d / "package.json"
    if not p.exists():
        return {}
    text = _read_first(p)
    if not text:
        return {}
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return {}
    deps = list((data.get("dependencies") or {}).keys())[:30]
    deps += list((data.get("devDependencies") or {}).keys())[:10]
    return {
        "name": data.get("name"),
        "description": data.get("description"),
        "keywords": list(data.get("keywords") or []),
        "dependencies": deps,
        "language": "js",
    }


def _read_cargo_toml(d: Path) -> dict[str, Any]:
    p = d / "Cargo.toml"
    if not p.exists() or tomllib is None:
        return {}
    try:
        with p.open("rb") as f:
            data = tomllib.load(f)
    except Exception:
        return {}
    pkg = data.get("package") or {}
    deps = list((data.get("dependencies") or {}).keys())[:30]
    return {
        "name": pkg.get("name"),
        "description": pkg.get("description"),
        "keywords": list(pkg.get("keywords") or []),
        "dependencies": deps,
        "language": "rust",
    }


def _read_go_mod(d: Path) -> dict[str, Any]:
    p = d / "go.mod"
    if not p.exists():
        return {}
    text = _read_first(p, 4096)
    m = re.search(r"^module\s+(\S+)", text, re.MULTILINE)
    if not m:
        return {}
    full_module = m.group(1).strip()
    return {
        "name": full_module.rsplit("/", 1)[-1],
        "description": None,
        "keywords": [],
        "dependencies": [],
        "language": "go",
    }


def _read_dotcobeta(d: Path) -> dict[str, Any]:
    """Read a workspace's own .cobeta.yaml — the highest-signal source."""
    p = d / ".cobeta.yaml"
    if not p.exists():
        return {}
    text = _read_first(p)
    if not text:
        return {}
    try:
        import yaml as _yaml
        data = _yaml.safe_load(text) or {}
    except Exception:
        return {}
    spec = data.get("spec") or {}
    return {
        "name": spec.get("name"),
        "description": spec.get("intent"),
        "keywords": list(spec.get("tags") or []),
        "dependencies": [],
        "language": "cobeta-workspace",
    }


def _read_git_remote(d: Path) -> Optional[str]:
    cfg = d / ".git" / "config"
    if not cfg.exists() or not cfg.is_file():
        return None
    text = _read_first(cfg, 8192)
    m = re.search(r"url\s*=\s*(\S+)", text)
    if not m:
        return None
    url = m.group(1).strip()
    # Strip basic-auth credentials: https://user:token@host/path → https://host/path
    url = re.sub(r"(https?://)[^@/\s]+@", r"\1", url)
    # Strip ssh credentials: ssh://user@host/path → keep (no secret)
    return url


def _read_project_metadata(d: Path) -> dict[str, Any]:
    """Aggregate project self-description from all known files. Later wins.

    Order: go.mod < package.json < Cargo.toml < pyproject.toml < .cobeta.yaml.
    .cobeta.yaml has highest priority because it's our own format.
    """
    out: dict[str, Any] = {}
    out.update(_read_go_mod(d))
    out.update({k: v for k, v in _read_package_json(d).items() if v})
    out.update({k: v for k, v in _read_cargo_toml(d).items() if v})
    out.update({k: v for k, v in _read_pyproject(d).items() if v})
    out.update({k: v for k, v in _read_dotcobeta(d).items() if v})

    if not out.get("description"):
        readme = _read_readme(d)
        if readme:
            out["description"] = readme
    return out


def _fingerprint(d: Path, max_files: int = 500) -> DirectoryFingerprint:
    has_git = (d / ".git").exists()
    has_readme = any((d / name).exists() for name in _README_NAMES)
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

    fp = DirectoryFingerprint(
        path=d,
        name_tokens=_tokenize(d.name),
        has_git=has_git,
        has_readme=has_readme,
        has_dotcobeta=has_dotcobeta,
        file_count=file_count,
        extension_buckets=extension_buckets,
    )

    # Read project self-description files (the deep signal)
    meta = _read_project_metadata(d)
    if meta:
        fp.project_name = meta.get("name")
        fp.description = meta.get("description")
        fp.keywords = list(meta.get("keywords") or [])
        fp.dependencies = list(meta.get("dependencies") or [])
        if meta.get("language"):
            fp.languages = [meta["language"]]
    if has_git:
        fp.git_remote = _read_git_remote(d)

    return fp


def walk_filesystem_readonly(
    roots: Iterable[Path],
    *,
    max_depth: int = 2,
    skip_dot_dirs: bool = True,
    skip_dirs: Optional[set[str]] = None,
    descend_into_projects: bool = False,
) -> list[DirectoryFingerprint]:
    """Walk `roots` shallowly, returning a fingerprint per directory.

    Read-only: only `os.scandir` for traversal, plus reading well-known
    project self-description files (README, pyproject.toml, package.json,
    Cargo.toml, go.mod, .git/config, .cobeta.yaml).

    By default, when a directory looks project-like (has git/README/package
    metadata), we do NOT descend into it. This avoids polluting results with
    a project's own subfolders or vendored source trees (e.g. $GOROOT/src
    showing every Go stdlib package as a separate entry).
    """

    skip = (skip_dirs or set()) | {
        "node_modules", ".venv", "venv", ".cache", ".git", "build", "dist",
        "target", "__pycache__", "site-packages", "pkg", "vendor",
    }
    fingerprints: list[DirectoryFingerprint] = []

    def _recurse(d: Path, depth: int) -> None:
        if not d.is_dir():
            return
        try:
            fp = _fingerprint(d)
            fingerprints.append(fp)
        except Exception:
            return
        if depth >= max_depth:
            return
        # Don't descend into project-like dirs unless explicitly told to.
        # (A project's subfolders are rarely separately-tagable.)
        if not descend_into_projects and fp.is_project_like and depth > 0:
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

def suggest_tags(fingerprints: list[DirectoryFingerprint], *, top_n: int = 30) -> dict[str, str]:
    """Cluster fingerprints into a suggested tag vocabulary.

    Returns `{tag: one-line-rationale}`. Caller decides which to keep. Sources
    in priority order:

    1. Author-curated keywords from pyproject/package.json/Cargo.toml — these
       were declared by the user themselves, highest signal
    2. Common dependencies across multiple projects — recurring stack items
    3. Compound terms (bigrams) extracted from README descriptions
    4. Single tokens from project names
    5. Bucket-dominant tags (writing, code-python, ...)
    6. Always-seeded lifecycle tags (wip, experiment, reference, shared)
    """

    project_fingerprints = [f for f in fingerprints if f.is_project_like]
    suggestions: dict[str, str] = {}

    # ---- 1. Author-declared keywords (highest priority) ----
    keyword_counter: Counter[str] = Counter()
    for fp in project_fingerprints:
        for kw in fp.keywords:
            kw = str(kw).lower().replace("_", "-").replace(" ", "-")
            if _is_valid_tag(kw):
                keyword_counter[kw] += 1
    for kw, count in keyword_counter.most_common(top_n):
        if count >= 1:  # author-declared even once is meaningful
            suggestions[kw] = (
                f"declared as keyword in {count} project(s)"
                if count > 1
                else "declared as keyword in 1 project"
            )

    # ---- 2. Recurring dependencies (cross-project stack signals) ----
    dep_counter: Counter[str] = Counter()
    for fp in project_fingerprints:
        for dep in fp.dependencies:
            dep_norm = re.sub(r"[^a-z0-9-]", "-", dep.lower()).strip("-")
            if _is_valid_tag(dep_norm):
                dep_counter[dep_norm] += 1
    for dep, count in dep_counter.most_common(top_n):
        if count >= 2:
            suggestions.setdefault(dep, f"shared dependency across {count} project(s)")

    # ---- 3. Compound terms (bigrams) from descriptions ----
    bigrams: Counter[str] = Counter()
    bigram_stopwords = {  # README boilerplate that always shows up
        "this-directory", "directory-contains", "directory-are", "this-package",
        "this-repository", "this-project", "the-following", "see-also",
        "high-throughput", "memory-efficient",  # too generic when paired
    }
    for fp in project_fingerprints:
        if not fp.description:
            continue
        # Tokenize description: lowercase words, drop punctuation
        words = [w for w in re.findall(r"[a-zA-Z][a-zA-Z0-9]*", fp.description.lower())
                 if len(w) > 2 and w not in _STOPWORDS]
        for i in range(len(words) - 1):
            bigram = f"{words[i]}-{words[i + 1]}"
            if _is_valid_tag(bigram) and bigram not in bigram_stopwords:
                bigrams[bigram] += 1
    for bg, count in bigrams.most_common(top_n):
        if count >= 2:
            suggestions.setdefault(bg, f"appears as compound in {count} description(s)")

    # ---- 4. Single tokens from project / dir names ----
    tok_counter: Counter[str] = Counter()
    for fp in project_fingerprints:
        for tok in fp.name_tokens:
            tok_counter[tok] += 1
        if fp.project_name:
            for tok in _tokenize(fp.project_name):
                tok_counter[tok] += 1
    for tok, count in tok_counter.most_common(top_n):
        if count < 2 or not _is_valid_tag(tok):
            continue
        suggestions.setdefault(tok, f"appears in {count} project name(s)")

    # ---- 5. Bucket-derived tags ----
    bucket_counter: Counter[str] = Counter()
    for fp in project_fingerprints:
        if fp.dominant_bucket:
            bucket_counter[fp.dominant_bucket] += 1
    for bucket, count in bucket_counter.items():
        if count >= 3:
            suggestions.setdefault(bucket, f"dominant in {count} scanned project(s)")

    # ---- 6. Always-seeded lifecycle tags ----
    seeded = {
        "wip": "work-in-progress; default lifecycle marker",
        "experiment": "exploratory work, expected to be discarded or promoted",
        "reference": "long-lived reference material, not actively edited",
        "shared": "intended for cross-workspace reuse",
    }
    for k, v in seeded.items():
        suggestions.setdefault(k, v)

    return suggestions


def _is_valid_tag(s: str) -> bool:
    return bool(re.match(r"^[a-z][a-z0-9-]{1,30}$", s))


# ---------- whole-machine summary ----------

def build_inventory_summary(fingerprints: list[DirectoryFingerprint]) -> str:
    """Multi-line summary of what was found, suitable for viking://user/inventory."""
    project_fps = [f for f in fingerprints if f.is_project_like]
    bucket_counts: Counter[str] = Counter()
    lang_counts: Counter[str] = Counter()
    for f in project_fps:
        if f.dominant_bucket:
            bucket_counts[f.dominant_bucket] += 1
        for lang in f.languages:
            lang_counts[lang] += 1
    bucket_summary = ", ".join(f"{b}: {n}" for b, n in bucket_counts.most_common(8)) or "no clear pattern"
    lang_summary = ", ".join(f"{l}: {n}" for l, n in lang_counts.most_common(8)) or "no declared language"

    described = sum(1 for f in project_fps if f.description)
    git_repos = sum(1 for f in project_fps if f.git_remote)

    return (
        f"Scanned {len(fingerprints)} directories. "
        f"{len(project_fps)} are project-like; "
        f"{described} have a description in README/pyproject/etc; "
        f"{git_repos} have a git remote. "
        f"Content buckets: {bucket_summary}. "
        f"Declared languages: {lang_summary}."
    )


def render_per_project_table(fingerprints: list[DirectoryFingerprint]) -> str:
    """Format project-like fingerprints as a multi-line listing for the user."""
    project_fps = [f for f in fingerprints if f.is_project_like]
    if not project_fps:
        return "(no project-like directories found)"
    lines: list[str] = []
    for fp in project_fps:
        head = f"{fp.path}"
        if fp.project_name and fp.project_name != fp.path.name:
            head += f"   (name: {fp.project_name})"
        lines.append(head)
        if fp.description:
            desc = fp.description[:140] + ("…" if len(fp.description) > 140 else "")
            lines.append(f"  └─ {desc}")
        meta_bits = []
        if fp.languages:
            meta_bits.append("/".join(fp.languages))
        if fp.keywords:
            meta_bits.append(f"keywords: {', '.join(fp.keywords[:6])}" + ("…" if len(fp.keywords) > 6 else ""))
        if fp.dependencies:
            meta_bits.append(f"deps: {', '.join(fp.dependencies[:4])}" + ("…" if len(fp.dependencies) > 4 else ""))
        if fp.git_remote:
            meta_bits.append(f"remote: {fp.git_remote}")
        if meta_bits:
            lines.append("     " + " · ".join(meta_bits))
    return "\n".join(lines)
