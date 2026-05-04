"""Tests for the LLM scanner: tool safety + agent loop integration."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from cobeta.llm.base import LLMMessage, ToolCall
from cobeta.scanner import llm_scan
from cobeta.scanner.llm_tools import (
    ListDirTool,
    ReadFileTool,
    SubmitProjectFingerprintTool,
    SubmitScanReportTool,
    _within_roots,
)


# ---------- a fake LLM that scripts replies ----------


@dataclass
class FakeLLM:
    replies: list[LLMMessage]
    calls: list[dict[str, Any]] = field(default_factory=list)

    def chat(self, messages, tools=None, max_tokens=2048):
        self.calls.append({"messages": messages, "tools": tools or []})
        if not self.replies:
            raise RuntimeError("FakeLLM ran out of scripted replies")
        return self.replies.pop(0)


# ---------- safety guard ----------


def test_within_roots_accepts_inside(tmp_path: Path) -> None:
    inside = tmp_path / "subdir"
    inside.mkdir()
    assert _within_roots(inside, [tmp_path])


def test_within_roots_rejects_sibling(tmp_path: Path) -> None:
    sibling = tmp_path.parent / "elsewhere-not-allowed"
    assert not _within_roots(sibling, [tmp_path])


def test_within_roots_rejects_parent(tmp_path: Path) -> None:
    assert not _within_roots(tmp_path.parent, [tmp_path])


# ---------- list_dir tool ----------


def test_list_dir_in_root(tmp_path: Path) -> None:
    (tmp_path / "a.md").write_text("x")
    (tmp_path / "subdir").mkdir()
    tool = ListDirTool([tmp_path])
    out = json.loads(tool.execute(path=str(tmp_path)))
    assert "a.md" in out["files"]
    assert "subdir" in out["dirs"]


def test_list_dir_rejects_outside(tmp_path: Path) -> None:
    other = tmp_path.parent / "nope"
    other.mkdir(exist_ok=True)
    tool = ListDirTool([tmp_path])
    out = json.loads(tool.execute(path=str(other)))
    assert "error" in out
    other.rmdir()


# ---------- read_file tool ----------


def test_read_file_in_root(tmp_path: Path) -> None:
    f = tmp_path / "README.md"
    f.write_text("# Test\nA project.\n")
    tool = ReadFileTool([tmp_path])
    out = json.loads(tool.execute(path=str(f)))
    assert out["content"].startswith("# Test")
    assert out["truncated"] is False


def test_read_file_rejects_outside(tmp_path: Path) -> None:
    other = tmp_path.parent / "elsewhere.txt"
    other.write_text("secret")
    try:
        tool = ReadFileTool([tmp_path])
        out = json.loads(tool.execute(path=str(other)))
        assert "error" in out
    finally:
        other.unlink()


def test_read_file_truncates(tmp_path: Path) -> None:
    f = tmp_path / "big.md"
    f.write_text("x" * 20000)
    tool = ReadFileTool([tmp_path])
    out = json.loads(tool.execute(path=str(f), max_bytes=1024))
    assert len(out["content"]) == 1024
    assert out["truncated"] is True


# ---------- submit tools ----------


def test_submit_project_fingerprint_accumulates() -> None:
    acc: list = []
    tool = SubmitProjectFingerprintTool(acc)
    tool.execute(path="/x/a", name="a")
    tool.execute(path="/x/b", name="b", description="hi")
    assert len(acc) == 2
    assert acc[0]["name"] == "a"
    assert acc[1]["description"] == "hi"


def test_submit_scan_report_returns_ok() -> None:
    tool = SubmitScanReportTool()
    out = json.loads(
        tool.execute(
            suggested_tags=[{"tag": "wip", "rationale": "lifecycle"}],
            inventory_summary="2 projects",
            layout_patterns={"python": ["src", "tests"]},
        )
    )
    assert out["ok"] is True
    assert out["suggested_tags"] == {"wip": "lifecycle"}
    assert out["layout_patterns"] == {"python": ["src", "tests"]}


# ---------- end-to-end with FakeLLM ----------


def test_llm_scan_completes(tmp_path: Path) -> None:
    """Script an agent that lists root, reads README, submits one fingerprint, then submits scan report."""
    proj_dir = tmp_path / "demo-proj"
    proj_dir.mkdir()
    (proj_dir / "README.md").write_text("# demo\nA test project.\n")
    (tmp_path / ".keep").write_text("")

    fake = FakeLLM(replies=[
        LLMMessage(
            role="assistant", content="",
            tool_calls=[ToolCall(id="c1", name="list_dir", arguments={"path": str(tmp_path)})],
        ),
        LLMMessage(
            role="assistant", content="",
            tool_calls=[ToolCall(id="c2", name="read_file", arguments={"path": str(proj_dir / "README.md")})],
        ),
        LLMMessage(
            role="assistant", content="",
            tool_calls=[ToolCall(
                id="c3", name="submit_project_fingerprint",
                arguments={
                    "path": str(proj_dir),
                    "name": "demo",
                    "description": "A test project.",
                    "languages": [],
                    "keywords": [],
                    "dependencies": [],
                    "top_level_dirs": [],
                    "git_remote": "",
                    "notes": "single demo project for the test",
                },
            )],
        ),
        LLMMessage(
            role="assistant", content="",
            tool_calls=[ToolCall(
                id="c4", name="submit_scan_report",
                arguments={
                    "suggested_tags": [{"tag": "wip", "rationale": "lifecycle"}],
                    "inventory_summary": "1 demo project",
                    "layout_patterns": {"python_projects": ["src", "tests"]},
                },
            )],
        ),
    ])

    report = llm_scan([tmp_path], fake, max_turns=10)
    assert len(report.projects) == 1
    assert report.projects[0]["name"] == "demo"
    assert report.suggested_tags == {"wip": "lifecycle"}
    assert report.layout_patterns == {"python_projects": ["src", "tests"]}
    assert "1 demo project" in report.inventory_summary


def test_llm_scan_raises_when_not_completed(tmp_path: Path) -> None:
    """If agent never calls submit_scan_report, llm_scan should raise."""
    fake = FakeLLM(replies=[
        LLMMessage(
            role="assistant", content="",
            tool_calls=[ToolCall(id="c1", name="list_dir", arguments={"path": str(tmp_path)})],
        ),
        # Then run out of replies — agent will fail to complete
    ])
    with pytest.raises(RuntimeError):
        llm_scan([tmp_path], fake, max_turns=2)
