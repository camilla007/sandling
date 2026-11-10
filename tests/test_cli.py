from __future__ import annotations

import pytest

from sandling.cli import build_parser, main


def test_help_describes_safe_disposable_workspaces() -> None:
    assert "disposable Git workspaces" in build_parser().format_help()
    assert "update" not in build_parser().format_help()


def test_start_requires_exactly_one_branch_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sandling.cli.load_config", lambda: object())
    monkeypatch.setattr("sandling.cli.prune_metadata", lambda _: 0)
    with pytest.raises(SystemExit, match="2"):
        main(["start", "demo"])
