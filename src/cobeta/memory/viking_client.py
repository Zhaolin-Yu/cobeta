"""HTTP client for OpenViking.

OpenViking exposes a `viking://` URI namespace over HTTP. We hit a small
subset of operations: tree, find, cat, write. When the server is unreachable
or not yet supported, a local-stub fallback writes/reads from a JSON file
under a configurable directory (default `~/.cobeta/viking-stub/`) so the
rest of the system stays usable.

This is intentionally a thin wrapper. The real OpenViking API surface lives in
the `openviking` Python package; we only encode what cobeta directly needs.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import httpx


class VikingError(RuntimeError):
    """Generic OpenViking error."""


class VikingUnreachable(VikingError):
    """Raised when the viking HTTP server cannot be contacted."""


@dataclass
class VikingDocument:
    uri: str
    abstract: str = ""        # L0
    overview: str = ""        # L1
    full: Optional[str] = None  # L2 (only loaded on demand)
    metadata: dict[str, Any] = field(default_factory=dict)


_DEFAULT_STUB = Path("~/.cobeta/viking-stub").expanduser()


class VikingClient:
    """Thin HTTP client. Falls back to a local JSON stub when the server is down.

    The stub is intended for development and for nodes that are temporarily
    offline from the central node. Anything written to the stub is meant to be
    reconciled with the real server later (NOT yet implemented).

    `stub_dir` controls where the JSON store lives — pass an isolated path in
    tests so they don't pollute real user data.
    """

    def __init__(
        self,
        base_url: str,
        timeout_s: float = 10.0,
        *,
        allow_stub: bool = True,
        stub_dir: Optional[Path] = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s
        self.allow_stub = allow_stub
        self._stub_dir = (stub_dir or _DEFAULT_STUB).expanduser()
        self._client = httpx.Client(timeout=timeout_s)

    # ---- health ----
    def health(self) -> bool:
        try:
            r = self._client.get(f"{self.base_url}/health", timeout=2.0)
            return r.status_code == 200
        except Exception:
            return False

    # ---- read operations (used in bootstrap, read-only mode) ----
    def find(self, query: str, *, uri_prefix: str = "viking://", k: int = 10) -> list[VikingDocument]:
        try:
            r = self._client.get(
                f"{self.base_url}/find",
                params={"q": query, "uri": uri_prefix, "k": k},
            )
            r.raise_for_status()
            return [self._parse_doc(d) for d in r.json().get("results", [])]
        except (httpx.RequestError, httpx.HTTPStatusError):
            if not self.allow_stub:
                raise VikingUnreachable(self.base_url)
            return self._stub_find(query, uri_prefix, k)

    def tree(self, uri: str, depth: int = 1) -> list[str]:
        try:
            r = self._client.get(f"{self.base_url}/tree", params={"uri": uri, "depth": depth})
            r.raise_for_status()
            return list(r.json().get("entries", []))
        except (httpx.RequestError, httpx.HTTPStatusError):
            if not self.allow_stub:
                raise VikingUnreachable(self.base_url)
            return self._stub_tree(uri)

    def cat(self, uri: str, *, level: str = "L1") -> Optional[VikingDocument]:
        try:
            r = self._client.get(f"{self.base_url}/cat", params={"uri": uri, "level": level})
            r.raise_for_status()
            return self._parse_doc(r.json())
        except (httpx.RequestError, httpx.HTTPStatusError):
            if not self.allow_stub:
                raise VikingUnreachable(self.base_url)
            return self._stub_cat(uri)

    # ---- write operations (NOT used during bootstrap) ----
    def write(self, uri: str, content: str, metadata: Optional[dict[str, Any]] = None) -> None:
        try:
            r = self._client.post(
                f"{self.base_url}/write",
                json={"uri": uri, "content": content, "metadata": metadata or {}},
            )
            r.raise_for_status()
        except (httpx.RequestError, httpx.HTTPStatusError):
            if not self.allow_stub:
                raise VikingUnreachable(self.base_url)
            self._stub_write(uri, content, metadata or {})

    # ---- stub fallback ----
    def _stub_db_path(self) -> Path:
        self._stub_dir.mkdir(parents=True, exist_ok=True)
        return self._stub_dir / "store.json"

    def _stub_load(self) -> dict[str, dict[str, Any]]:
        p = self._stub_db_path()
        if not p.exists():
            return {}
        try:
            return json.loads(p.read_text())
        except json.JSONDecodeError:
            return {}

    def _stub_save(self, db: dict[str, dict[str, Any]]) -> None:
        self._stub_db_path().write_text(json.dumps(db, indent=2, ensure_ascii=False))

    def _stub_find(self, query: str, uri_prefix: str, k: int) -> list[VikingDocument]:
        db = self._stub_load()
        q = query.lower()
        hits = []
        for uri, rec in db.items():
            if not uri.startswith(uri_prefix):
                continue
            blob = " ".join([rec.get("abstract", ""), rec.get("overview", ""), rec.get("full", "")])
            if q in blob.lower():
                hits.append(self._parse_doc({"uri": uri, **rec}))
                if len(hits) >= k:
                    break
        return hits

    def _stub_tree(self, uri: str) -> list[str]:
        db = self._stub_load()
        return sorted(u for u in db if u.startswith(uri))

    def _stub_cat(self, uri: str) -> Optional[VikingDocument]:
        db = self._stub_load()
        if uri not in db:
            return None
        return self._parse_doc({"uri": uri, **db[uri]})

    def _stub_write(self, uri: str, content: str, metadata: dict[str, Any]) -> None:
        db = self._stub_load()
        db[uri] = {
            "abstract": content[:120],
            "overview": content[:1500],
            "full": content,
            "metadata": metadata,
        }
        self._stub_save(db)

    @staticmethod
    def _parse_doc(d: dict[str, Any]) -> VikingDocument:
        return VikingDocument(
            uri=d["uri"],
            abstract=d.get("abstract", ""),
            overview=d.get("overview", ""),
            full=d.get("full"),
            metadata=d.get("metadata", {}) or {},
        )

    def close(self) -> None:
        self._client.close()
