from __future__ import annotations

from pathlib import Path


def test_shell_integration_captures_exactly_one_workspace() -> None:
    script = Path(__file__).parents[1] / "scripts" / "sandling-shell.bash"
    content = script.read_text(encoding="utf-8")
    assert "Sandling workspace ready: " in content
    assert "exactly one usable workspace" in content
