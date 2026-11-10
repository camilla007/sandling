# Sandling

> **Small, temporary Git workspaces that know when to leave.** A Sandling is for
> debugging, building, and pushing—not becoming an archaeological layer in your home directory.

Sandling creates an isolated clone for a configured project, selects exactly the branch you ask
for, runs explicit bootstrap commands, and can remind you when the workspace is ready to retire.
It never deletes automatically. Sandboxes are disposable; your work is not.

## What it does

- Manages one or more explicit project profiles. A new install has no repository configured.
- Creates workspaces from a freshly fetched base branch or an existing remote branch unchanged.
- Runs profile bootstrap steps as argument arrays, never through a shell.
- Optionally launches your configured editor.
- Installs a local post-push reminder. It can mark a workspace ready; it cannot delete one.
- Refuses ordinary destruction when a workspace is dirty, contains untracked files, has unpushed
  commits, or lacks a usable upstream.

## Install

Sandling supports Linux and macOS with Python 3.11 or later.

```bash
pipx install sandling
sandling --help
```

Upgrade a published installation with `pipx upgrade sandling`. Sandling does not try to update
itself from a random checkout; that is how tools acquire hobbies.

For local development:

```bash
python -m pip install -e '.[dev]'
python -m pytest
python -m ruff check src tests
python -m ruff format --check src tests
```

## Configure a project

Create a profile for any Git repository:

```bash
sandling profile init demo --repository-url https://example.test/demo.git --base-branch main
```

This creates `~/.config/sandling/config.toml` (or `$XDG_CONFIG_HOME/sandling/config.toml`). Add
optional bootstrap and editor commands as shell-free argument arrays:

```toml
[profiles.demo]
repository_url = "https://example.test/demo.git"
base_branch = "main"
editor_command = ["code", "."]
bootstrap = [
  ["python3", "-m", "venv", ".venv"],
  ["{sandbox}/.venv/bin/python", "-m", "pip", "install", "-r", "requirements.txt"],
]
```

Recognised placeholders are `{sandbox}`, `{profile}`, and `{branch}`. The profile is your
configuration; review it with the same calm suspicion you reserve for shell aliases copied from
the internet.

## Start work

Create a new branch from the profile's freshly fetched base branch:

```bash
sandling start demo --new-branch fix/parse-dates
```

Or check out an existing remote branch without merging or rebasing it:

```bash
sandling start demo fix/parse-dates
```

List known workspaces and mark one after its push is accepted:

```bash
sandling list
sandling flag fix_parse_dates-20261110-090000
```

In an interactive Bash shell, source `scripts/sandling-shell.bash` if you want successful
`sandling start` calls to enter the newly created workspace. A Python process cannot move its
parent shell; physics is rude but consistent.

## Safe cleanup

Destroying requires an exact-name confirmation:

```bash
sandling destroy fix_parse_dates-20261110-090000
```

Normal destruction refuses dirty, untracked, unpushed, and upstream-less workspaces. `--force`
bypasses those Git-state checks but still requires the exact workspace name. Use it slowly, after
checking the path, and ideally after coffee.

Destroyed-workspace metadata remains for seven days. `sandling prune-metadata` removes only those
expired records; it never removes a workspace directory. There is deliberately no clean-all and no
remote-branch deletion command.

## Security and scope

Sandling runs only the commands you place in a profile and never invokes a shell for bootstrap
steps. It does not manage credentials, install system dependencies, configure project services, or
make remote Git changes other than normal user-initiated pushes from a workspace.

Report vulnerabilities through the process in [SECURITY.md](SECURITY.md). Contributions are
welcome; see [CONTRIBUTING.md](CONTRIBUTING.md).

---

Created by Camilla Buys. Build fast. Push clean. Leave no clones behind.
