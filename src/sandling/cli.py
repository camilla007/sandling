from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sandling.service import (
    BootstrapError,
    SandlingError,
    destroy_workspace,
    get_profile,
    init_profile,
    load_all_records,
    load_config,
    mark_ready,
    post_push,
    prune_metadata,
    record_git_summary,
    start_workspace,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sandling",
        description="Safe, disposable Git workspaces. Temporary by design, not by neglect.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    profile = commands.add_parser("profile", help="create a configured project profile")
    profile_commands = profile.add_subparsers(dest="profile_command", required=True)
    profile_init = profile_commands.add_parser("init", help="add a minimal project profile")
    profile_init.add_argument("name")
    profile_init.add_argument("--repository-url", required=True)
    profile_init.add_argument("--base-branch", default="main")

    start = commands.add_parser("start", help="clone, select a branch, and bootstrap a workspace")
    start.add_argument("profile", help="configured profile name")
    start.add_argument("branch", nargs="?", help="existing remote branch to check out unchanged")
    start.add_argument(
        "--new-branch", metavar="BRANCH", help="create BRANCH from the configured base branch"
    )
    start.add_argument(
        "--no-editor", action="store_true", help="do not launch the profile editor command"
    )

    listed = commands.add_parser("list", help="list known workspaces and their safety state")
    listed.add_argument("profile", nargs="?", help="show only one profile")

    flag = commands.add_parser("flag", help="mark a workspace ready for later destruction")
    flag.add_argument("name", nargs="?", help="workspace name; defaults to the current workspace")

    destroy = commands.add_parser("destroy", help="delete one safe, explicitly confirmed workspace")
    destroy.add_argument("name", help="workspace name from 'sandling list'")
    destroy.add_argument(
        "--force", action="store_true", help="bypass Git-state safeguards; still types name"
    )

    commands.add_parser(
        "prune-metadata", help="remove destroyed-workspace records older than seven days"
    )

    hook = commands.add_parser("_post-push", help=argparse.SUPPRESS)
    hook.add_argument("--workspace", required=True)
    hook.add_argument("--remote", required=True)
    return parser


def prompt_after_push() -> bool:
    message = "\nSandling: push accepted. Mark this workspace ready for destruction? [y/N] "
    try:
        with Path("/dev/tty").open("r+", encoding="utf-8") as tty:
            tty.write(message)
            tty.flush()
            return tty.readline().strip().lower() in {"y", "yes"}
    except OSError:
        print(
            "Sandling: push accepted. Run 'sandling flag' when the workspace is expendable.",
            file=sys.stderr,
        )
        return False


def print_records(profile_name: str | None) -> None:
    config = load_config()
    records = [
        record
        for record in load_all_records(config)
        if not profile_name or record.profile == profile_name
    ]
    if not records:
        print("No Sandling workspaces recorded. An empty beach is not a crisis.")
        return
    print(f"{'NAME':<42} {'PROFILE':<18} {'STATE':<12} {'BRANCH':<30} PATH")
    for record in records:
        state, _ = record_git_summary(record)
        ready = " ready" if record.ready_for_destruction_at else ""
        row = f"{record.name:<42} {record.profile:<18} {(state + ready):<12}"
        print(f"{row} {record.branch:<30} {record.path}")


def confirm_destruction(name: str) -> bool:
    return input(f"Type '{name}' to destroy that workspace directory: ").strip() == name


def handle_start(args: argparse.Namespace) -> None:
    if bool(args.branch) == bool(args.new_branch):
        raise SandlingError(
            "Use exactly one: 'sandling start PROFILE BRANCH' or '--new-branch BRANCH'."
        )
    config = load_config()
    profile = get_profile(config, args.profile)
    branch = args.new_branch or args.branch
    record = start_workspace(
        config,
        profile,
        branch=branch,
        create_new_branch=bool(args.new_branch),
        open_editor=not args.no_editor,
    )
    source = (
        f"{profile.push_remote}/{profile.base_branch}"
        if args.new_branch
        else f"{profile.push_remote}/{branch} (unchanged)"
    )
    print(f"Sandling workspace ready: {record.path}\nBranch: {branch}\nSource: {source}")


def handle_post_push(args: argparse.Namespace) -> None:
    config = load_config()
    record = post_push(
        config,
        workspace=Path(args.workspace).resolve(),
        remote=args.remote,
        pushed_refs=sys.stdin.read(),
    )
    if record and prompt_after_push():
        mark_ready(config, record.name)
        print(
            f"Sandling: {record.name} is marked ready for destruction. "
            "It will not wander off by itself."
        )


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "profile":
            path = init_profile(args.name, args.repository_url, args.base_branch)
            print(f"Sandling profile {args.name!r} added to {path}.")
        else:
            config = load_config()
            if args.command == "start":
                prune_metadata(config)
                handle_start(args)
            elif args.command == "list":
                prune_metadata(config)
                print_records(args.profile)
            elif args.command == "flag":
                prune_metadata(config)
                record = mark_ready(config, args.name)
                print(
                    f"Sandling: {record.name} is marked ready for destruction. "
                    "Nothing has been deleted."
                )
            elif args.command == "destroy":
                prune_metadata(config)
                if not confirm_destruction(args.name):
                    raise SandlingError("Destruction cancelled: workspace name did not match.")
                record = destroy_workspace(config, args.name, force=args.force)
                print(f"Sandling: destroyed {record.path}. Metadata is retained for seven days.")
            elif args.command == "prune-metadata":
                removed = prune_metadata(config)
                print(
                    f"Sandling: removed {removed} expired destroyed-workspace metadata record(s)."
                )
            elif args.command == "_post-push":
                prune_metadata(config)
                handle_post_push(args)
    except BootstrapError as error:
        print(f"Sandling workspace bootstrap failed: {error.workspace}")
        print(f"Sandling: {error}", file=sys.stderr)
        raise SystemExit(2) from error
    except SandlingError as error:
        print(f"Sandling: {error}", file=sys.stderr)
        raise SystemExit(2) from error


if __name__ == "__main__":
    main()
