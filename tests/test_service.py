from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from sandling.service import (
    Config,
    Profile,
    SandlingError,
    WorkspaceRecord,
    destroy_workspace,
    expand_command,
    init_profile,
    load_config,
    load_record,
    post_push,
    prune_metadata,
    save_record,
    start_workspace,
)


def profile(tmp_path: Path) -> Profile:
    return Profile("demo", "unused", "main", tmp_path / "sandboxes", "origin", (), None)


def config(tmp_path: Path) -> Config:
    return Config(tmp_path / "state", {"demo": profile(tmp_path)})


def record(tmp_path: Path, *, name: str = "one", status: str = "active") -> WorkspaceRecord:
    workspace = tmp_path / "sandboxes" / name
    workspace.mkdir(parents=True)
    (workspace / ".sandling.json").write_text("{}", encoding="utf-8")
    return WorkspaceRecord(
        name,
        str(workspace),
        "demo",
        "main",
        str(tmp_path / "sandboxes"),
        "origin",
        "2026-01-01T00:00:00+00:00",
        status,
    )


def test_init_profile_writes_loadable_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("SANDLING_CONFIG", str(tmp_path / "config.toml"))
    init_profile("demo", "https://example.test/demo.git", "trunk")
    loaded = load_config()
    assert loaded.profiles["demo"].base_branch == "trunk"
    assert loaded.profiles["demo"].repository_url == "https://example.test/demo.git"


def test_expand_command_rejects_unknown_placeholder(tmp_path: Path) -> None:
    with pytest.raises(SandlingError, match="Unsupported placeholder"):
        expand_command(
            ("echo", "{mystery}"), workspace=tmp_path, profile=profile(tmp_path), branch="main"
        )


def test_start_new_branch_uses_fresh_remote_base(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config_value = config(tmp_path)
    calls: list[list[str]] = []

    def fake_run(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        if command[:2] == ["git", "clone"]:
            workspace = Path(command[-1])
            (workspace / ".git" / "info").mkdir(parents=True)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr("sandling.service.run", fake_run)
    monkeypatch.setattr("sandling.service.workspace_name", lambda _: "new-workspace")
    started = start_workspace(
        config_value,
        profile(tmp_path),
        branch="feature/demo",
        create_new_branch=True,
        open_editor=False,
    )
    assert started.status == "active"
    assert ["git", "fetch", "origin", "--prune"] in calls
    assert ["git", "switch", "--create", "feature/demo", "origin/main"] in calls


def test_start_uses_a_temporary_local_bare_remote(tmp_path: Path) -> None:
    remote = tmp_path / "remote.git"
    seed = tmp_path / "seed"
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
    subprocess.run(["git", "init", "-b", "main", str(seed)], check=True, capture_output=True)
    (seed / "README.md").write_text("fixture\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=seed, check=True, capture_output=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Sandling Test",
            "-c",
            "user.email=sandling@example.test",
            "commit",
            "-m",
            "fixture",
        ],
        cwd=seed,
        check=True,
        capture_output=True,
    )
    subprocess.run(["git", "remote", "add", "origin", str(remote)], cwd=seed, check=True)
    subprocess.run(
        ["git", "push", "-u", "origin", "main"], cwd=seed, check=True, capture_output=True
    )

    configured = Config(tmp_path / "state", {})
    local_profile = Profile("demo", str(remote), "main", tmp_path / "sandboxes", "origin", (), None)
    created = start_workspace(
        configured,
        local_profile,
        branch="feature/fixture",
        create_new_branch=True,
        open_editor=False,
    )
    branch = subprocess.run(
        ["git", "branch", "--show-current"],
        cwd=created.workspace_path,
        check=True,
        text=True,
        capture_output=True,
    )
    assert branch.stdout.strip() == "feature/fixture"
    assert (created.workspace_path / "README.md").read_text(encoding="utf-8") == "fixture\n"


def test_bootstrap_failure_retains_workspace(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    configured = config(tmp_path)
    failing = Profile(
        "demo", "unused", "main", tmp_path / "sandboxes", "origin", (("false",),), None
    )

    def fake_run(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        if command[:2] == ["git", "clone"]:
            workspace = Path(command[-1])
            (workspace / ".git" / "info").mkdir(parents=True)
            return subprocess.CompletedProcess(command, 0, "", "")
        if command == ["false"]:
            raise SandlingError("Command failed: false")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr("sandling.service.run", fake_run)
    monkeypatch.setattr("sandling.service.workspace_name", lambda _: "failed-workspace")
    with pytest.raises(SandlingError):
        start_workspace(
            configured, failing, branch="main", create_new_branch=False, open_editor=False
        )
    assert load_record(configured, "failed-workspace").status == "bootstrap_failed"


def test_destroy_rejects_untracked_files(tmp_path: Path) -> None:
    configured = config(tmp_path)
    unsafe = record(tmp_path)
    save_record(configured, unsafe)
    (unsafe.workspace_path / "untracked.txt").write_text("keep me", encoding="utf-8")
    subprocess.run(["git", "init"], cwd=unsafe.workspace_path, check=True, capture_output=True)
    with pytest.raises(SandlingError, match="untracked"):
        destroy_workspace(configured, unsafe.name, force=False)
    assert unsafe.workspace_path.exists()


def test_force_destroy_needs_managed_direct_child(tmp_path: Path) -> None:
    configured = config(tmp_path)
    safe = record(tmp_path)
    save_record(configured, safe)
    destroyed = destroy_workspace(configured, safe.name, force=True)
    assert destroyed.status == "destroyed"
    assert not safe.workspace_path.exists()


def test_prune_removes_only_expired_destroyed_records(tmp_path: Path) -> None:
    configured = config(tmp_path)
    old = record(tmp_path, name="old", status="destroyed")
    old.destroyed_at = (datetime.now(UTC) - timedelta(days=8)).isoformat()
    active = record(tmp_path, name="active")
    save_record(configured, old)
    save_record(configured, active)
    assert prune_metadata(configured) == 1
    assert [item.name for item in [load_record(configured, "active")]] == ["active"]


def test_post_push_only_responds_for_record_remote(tmp_path: Path) -> None:
    configured = config(tmp_path)
    workspace = record(tmp_path)
    save_record(configured, workspace)
    workspace.workspace_path.joinpath(".sandling.json").write_text(
        json.dumps(workspace.__dict__), encoding="utf-8"
    )
    assert (
        post_push(
            configured, workspace=workspace.workspace_path, remote="upstream", pushed_refs="a b c\n"
        )
        is None
    )
    assert (
        post_push(
            configured, workspace=workspace.workspace_path, remote="origin", pushed_refs="a b c\n"
        )
        == workspace
    )
