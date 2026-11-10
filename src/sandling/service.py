from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tomllib
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

DEFAULT_CONFIG_DIR = Path("~/.config/sandling")
DEFAULT_STATE_ROOT = Path("~/.local/state/sandling")
DEFAULT_SANDBOX_ROOT = Path("~/.local/share/sandling/sandboxes")
METADATA_NAME = ".sandling.json"
PROFILE_NAME = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
PLACEHOLDER = re.compile(r"\{([a-z_]+)\}")
ALLOWED_PLACEHOLDERS = {"sandbox", "profile", "branch"}


class SandlingError(RuntimeError):
    """An expected, actionable Sandling failure."""


class BootstrapError(SandlingError):
    def __init__(self, workspace: Path, error: SandlingError) -> None:
        self.workspace = workspace
        super().__init__(f"Bootstrap failed for retained workspace {workspace}.\n{error}")


@dataclass(frozen=True)
class Profile:
    name: str
    repository_url: str
    base_branch: str
    sandbox_root: Path
    push_remote: str
    bootstrap: tuple[tuple[str, ...], ...]
    editor_command: tuple[str, ...] | None


@dataclass(frozen=True)
class Config:
    state_root: Path
    profiles: dict[str, Profile]


@dataclass
class WorkspaceRecord:
    name: str
    path: str
    profile: str
    branch: str
    sandbox_root: str
    push_remote: str
    created_at: str
    status: str
    ready_for_destruction_at: str | None = None
    destroyed_at: str | None = None

    @property
    def workspace_path(self) -> Path:
        return Path(self.path)


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def expand_path(value: str | Path) -> Path:
    return Path(value).expanduser().resolve()


def config_path() -> Path:
    configured = os.environ.get("SANDLING_CONFIG")
    if configured:
        return expand_path(configured)
    config_home = Path(os.environ.get("XDG_CONFIG_HOME", DEFAULT_CONFIG_DIR.parent))
    return expand_path(config_home / "sandling" / "config.toml")


def _as_string_list(value: Any, field: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value or not all(isinstance(item, str) for item in value):
        raise SandlingError(f"{field} must be a non-empty array of strings.")
    return tuple(value)


def _bootstrap_steps(value: Any, profile_name: str) -> tuple[tuple[str, ...], ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise SandlingError(f"profiles.{profile_name}.bootstrap must be an array of argv arrays.")
    return tuple(
        _as_string_list(step, f"profiles.{profile_name}.bootstrap[{index}]")
        for index, step in enumerate(value)
    )


def load_config() -> Config:
    path = config_path()
    if not path.exists():
        raise SandlingError(
            f"No Sandling configuration at {path}. "
            "Run 'sandling profile init NAME --repository-url URL'."
        )
    try:
        with path.open("rb") as handle:
            raw = tomllib.load(handle)
    except tomllib.TOMLDecodeError as error:
        raise SandlingError(f"Invalid Sandling configuration at {path}: {error}") from error

    settings = raw.get("settings", {})
    if not isinstance(settings, dict):
        raise SandlingError("settings must be a TOML table.")
    xdg_state_root = Path(os.environ.get("XDG_STATE_HOME", DEFAULT_STATE_ROOT.parent)) / "sandling"
    state_root = expand_path(settings.get("state_root", xdg_state_root))
    default_sandbox_root = expand_path(settings.get("sandbox_root", DEFAULT_SANDBOX_ROOT))
    raw_profiles = raw.get("profiles", {})
    if not isinstance(raw_profiles, dict):
        raise SandlingError("profiles must be a TOML table.")

    profiles: dict[str, Profile] = {}
    for name, value in raw_profiles.items():
        if not isinstance(name, str) or not PROFILE_NAME.fullmatch(name):
            raise SandlingError(
                "Profile names must start with a lowercase letter and contain letters, "
                "digits, _ or -."
            )
        if not isinstance(value, dict):
            raise SandlingError(f"profiles.{name} must be a TOML table.")
        repository_url = value.get("repository_url")
        if not isinstance(repository_url, str) or not repository_url.strip():
            raise SandlingError(f"profiles.{name}.repository_url must be a non-empty string.")
        base_branch = value.get("base_branch", "main")
        push_remote = value.get("push_remote", "origin")
        if not isinstance(base_branch, str) or not base_branch:
            raise SandlingError(f"profiles.{name}.base_branch must be a non-empty string.")
        if not isinstance(push_remote, str) or not push_remote:
            raise SandlingError(f"profiles.{name}.push_remote must be a non-empty string.")
        editor_value = value.get("editor_command")
        profiles[name] = Profile(
            name=name,
            repository_url=repository_url,
            base_branch=base_branch,
            sandbox_root=expand_path(value.get("sandbox_root", default_sandbox_root / name)),
            push_remote=push_remote,
            bootstrap=_bootstrap_steps(value.get("bootstrap"), name),
            editor_command=(
                _as_string_list(editor_value, f"profiles.{name}.editor_command")
                if editor_value is not None
                else None
            ),
        )
    return Config(state_root=state_root, profiles=profiles)


def init_profile(name: str, repository_url: str, base_branch: str) -> Path:
    if not PROFILE_NAME.fullmatch(name):
        raise SandlingError(
            "Profile names must start with a lowercase letter and contain letters, digits, _ or -."
        )
    if not repository_url.strip():
        raise SandlingError("repository URL must not be empty.")
    if not base_branch.strip():
        raise SandlingError("base branch must not be empty.")
    path = config_path()
    if path.exists():
        config = load_config()
        if name in config.profiles:
            raise SandlingError(f"Profile {name!r} already exists in {path}.")
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "# Sandling profiles. Bootstrap commands are argv arrays; no shell is involved.\n",
            encoding="utf-8",
        )
    with path.open("a", encoding="utf-8") as handle:
        handle.write(
            f"\n[profiles.{name}]\n"
            f"repository_url = {json.dumps(repository_url)}\n"
            f"base_branch = {json.dumps(base_branch)}\n"
        )
    return path


def get_profile(config: Config, name: str) -> Profile:
    try:
        return config.profiles[name]
    except KeyError as error:
        known = ", ".join(sorted(config.profiles)) or "none"
        raise SandlingError(f"Unknown profile {name!r}. Configured profiles: {known}.") from error


def state_path(config: Config, name: str) -> Path:
    return config.state_root / "workspaces" / f"{name}.json"


def save_record(config: Config, record: WorkspaceRecord) -> None:
    path = state_path(config, record.name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(record), indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _record_from_path(path: Path) -> WorkspaceRecord:
    try:
        return WorkspaceRecord(**json.loads(path.read_text(encoding="utf-8")))
    except (OSError, TypeError, json.JSONDecodeError) as error:
        raise SandlingError(f"Invalid Sandling metadata at {path}: {error}") from error


def load_record(config: Config, name: str) -> WorkspaceRecord:
    path = state_path(config, name)
    if not path.exists():
        raise SandlingError(
            f"No Sandling workspace named {name!r}. Run 'sandling list' to see known workspaces."
        )
    return _record_from_path(path)


def load_all_records(config: Config) -> list[WorkspaceRecord]:
    records_dir = config.state_root / "workspaces"
    if not records_dir.exists():
        return []
    return [_record_from_path(path) for path in sorted(records_dir.glob("*.json"))]


def run(
    command: list[str], *, cwd: Path | None = None, capture: bool = False
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(command, cwd=cwd, check=True, text=True, capture_output=capture)
    except FileNotFoundError as error:
        raise SandlingError(f"Required command not found: {command[0]}") from error
    except subprocess.CalledProcessError as error:
        detail = (error.stderr or error.stdout or "command failed").strip()
        raise SandlingError(f"Command failed: {' '.join(command)}\n{detail}") from error


def branch_slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


def workspace_name(branch: str) -> str:
    slug = branch_slug(branch)
    if not slug:
        raise SandlingError("Branch name must contain at least one letter or number.")
    return f"{slug}-{datetime.now().strftime('%Y%m%d-%H%M%S')}"


def metadata_path(workspace: Path) -> Path:
    return workspace / METADATA_NAME


def write_workspace_metadata(workspace: Path, record: WorkspaceRecord) -> None:
    metadata_path(workspace).write_text(
        json.dumps(asdict(record), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def install_local_exclude(workspace: Path) -> None:
    exclude = workspace / ".git" / "info" / "exclude"
    current = exclude.read_text(encoding="utf-8") if exclude.exists() else ""
    if METADATA_NAME not in current.splitlines():
        exclude.parent.mkdir(parents=True, exist_ok=True)
        with exclude.open("a", encoding="utf-8") as handle:
            if current and not current.endswith("\n"):
                handle.write("\n")
            handle.write(f"{METADATA_NAME}\n")


def install_post_push_hook(workspace: Path) -> None:
    hook = workspace / ".git" / "hooks" / "post-push"
    python = str(Path(sys.executable).resolve())
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text(
        "#!/bin/sh\n"
        "# Installed by Sandling. It reminds; it never deletes.\n"
        'workspace="$(git rev-parse --show-toplevel 2>/dev/null)" || exit 0\n'
        f'exec "{python}" -m sandling _post-push --workspace "$workspace" --remote "$1"\n',
        encoding="utf-8",
    )
    hook.chmod(0o755)


def expand_command(
    command: tuple[str, ...], *, workspace: Path, profile: Profile, branch: str
) -> list[str]:
    variables = {"sandbox": str(workspace), "profile": profile.name, "branch": branch}
    expanded: list[str] = []
    for argument in command:
        unknown = set(PLACEHOLDER.findall(argument)) - ALLOWED_PLACEHOLDERS
        if unknown:
            raise SandlingError(f"Unsupported placeholder(s): {', '.join(sorted(unknown))}.")
        for key, value in variables.items():
            argument = argument.replace("{" + key + "}", value)
        expanded.append(argument)
    return expanded


def start_workspace(
    config: Config,
    profile: Profile,
    *,
    branch: str,
    create_new_branch: bool,
    open_editor: bool = True,
) -> WorkspaceRecord:
    name = workspace_name(branch)
    workspace = profile.sandbox_root / name
    if workspace.exists():
        raise SandlingError(f"Workspace path already exists: {workspace}")
    profile.sandbox_root.mkdir(parents=True, exist_ok=True)
    record = WorkspaceRecord(
        name=name,
        path=str(workspace),
        profile=profile.name,
        branch=branch,
        sandbox_root=str(profile.sandbox_root),
        push_remote=profile.push_remote,
        created_at=utc_now(),
        status="creating",
    )
    save_record(config, record)
    try:
        run(
            [
                "git",
                "clone",
                "--origin",
                profile.push_remote,
                profile.repository_url,
                str(workspace),
            ]
        )
        run(["git", "fetch", profile.push_remote, "--prune"], cwd=workspace)
        if create_new_branch:
            run(
                [
                    "git",
                    "switch",
                    "--create",
                    branch,
                    f"{profile.push_remote}/{profile.base_branch}",
                ],
                cwd=workspace,
            )
        elif branch == profile.base_branch:
            run(["git", "switch", branch], cwd=workspace)
        else:
            run(
                ["git", "switch", "--track", "--create", branch, f"{profile.push_remote}/{branch}"],
                cwd=workspace,
            )
        install_local_exclude(workspace)
        install_post_push_hook(workspace)
        write_workspace_metadata(workspace, record)
        for command in profile.bootstrap:
            try:
                run(
                    expand_command(command, workspace=workspace, profile=profile, branch=branch),
                    cwd=workspace,
                )
            except SandlingError as error:
                raise BootstrapError(workspace, error) from error
    except SandlingError:
        record.status = "bootstrap_failed"
        save_record(config, record)
        if workspace.exists():
            write_workspace_metadata(workspace, record)
        raise
    record.status = "active"
    save_record(config, record)
    write_workspace_metadata(workspace, record)
    if open_editor and profile.editor_command:
        command = expand_command(
            profile.editor_command, workspace=workspace, profile=profile, branch=branch
        )
        try:
            subprocess.Popen(command, cwd=workspace, start_new_session=True)
        except FileNotFoundError as error:
            raise SandlingError(
                f"Workspace is ready, but editor command was not found: {command[0]}"
            ) from error
    return record


def find_record_for_current_directory(config: Config) -> WorkspaceRecord:
    current = Path.cwd().resolve()
    for candidate in [current, *current.parents]:
        metadata = metadata_path(candidate)
        if metadata.exists():
            return load_record(config, _record_from_path(metadata).name)
    raise SandlingError(
        "Current directory is not inside a Sandling workspace. Pass a workspace name explicitly."
    )


def mark_ready(config: Config, name: str | None = None) -> WorkspaceRecord:
    record = load_record(config, name) if name else find_record_for_current_directory(config)
    if record.status == "destroyed":
        raise SandlingError(f"Workspace {record.name!r} was already destroyed.")
    record.ready_for_destruction_at = utc_now()
    save_record(config, record)
    if record.workspace_path.exists():
        write_workspace_metadata(record.workspace_path, record)
    return record


def record_git_summary(record: WorkspaceRecord) -> tuple[str, str]:
    workspace = record.workspace_path
    if not workspace.exists():
        return "missing", ""
    if record.status == "destroyed":
        return "destroyed", ""
    try:
        dirty = run(["git", "status", "--porcelain"], cwd=workspace, capture=True).stdout.strip()
        return ("dirty" if dirty else record.status), dirty
    except SandlingError:
        return "invalid", ""


def require_safe_to_destroy(record: WorkspaceRecord) -> None:
    workspace = record.workspace_path
    if not workspace.exists():
        raise SandlingError(f"Workspace directory no longer exists: {workspace}")
    status = run(
        ["git", "status", "--porcelain", "--untracked-files=all"], cwd=workspace, capture=True
    )
    if status.stdout.strip():
        raise SandlingError(
            "Refusing destruction: working tree contains tracked, staged, or untracked changes."
        )
    try:
        ahead = run(
            ["git", "rev-list", "--count", "@{upstream}..HEAD"], cwd=workspace, capture=True
        )
    except SandlingError as error:
        raise SandlingError(
            "Refusing destruction: branch has no usable upstream. Push it first or use --force."
        ) from error
    if int(ahead.stdout.strip()) > 0:
        raise SandlingError(
            "Refusing destruction: local commits have not been pushed to the upstream branch."
        )


def destroy_workspace(config: Config, name: str, *, force: bool) -> WorkspaceRecord:
    record = load_record(config, name)
    if record.status == "destroyed":
        raise SandlingError(f"Workspace {name!r} was already destroyed on {record.destroyed_at}.")
    if not force:
        require_safe_to_destroy(record)
    workspace = record.workspace_path.resolve()
    root = expand_path(record.sandbox_root)
    if workspace.parent != root or not metadata_path(workspace).exists():
        raise SandlingError(
            "Refusing destruction: target is not a direct, managed Sandling workspace."
        )
    shutil.rmtree(workspace)
    record.status = "destroyed"
    record.destroyed_at = utc_now()
    save_record(config, record)
    return record


def prune_metadata(config: Config, *, retention_days: int = 7) -> int:
    cutoff = datetime.now(UTC) - timedelta(days=retention_days)
    removed = 0
    for record in load_all_records(config):
        if (
            record.status == "destroyed"
            and record.destroyed_at
            and datetime.fromisoformat(record.destroyed_at) <= cutoff
        ):
            state_path(config, record.name).unlink(missing_ok=True)
            removed += 1
    return removed


def post_push(
    config: Config, *, workspace: Path, remote: str, pushed_refs: str
) -> WorkspaceRecord | None:
    if not pushed_refs.strip() or not metadata_path(workspace).exists():
        return None
    record = _record_from_path(metadata_path(workspace))
    if remote != record.push_remote:
        return None
    return load_record(config, record.name)
