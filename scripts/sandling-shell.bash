#!/usr/bin/env bash
# Source from an interactive POSIX Bash shell. A child process cannot move its parent.

sandling() {
    if [[ "${1:-}" != "start" ]]; then
        command sandling "$@"
        return $?
    fi

    local output status workspace
    output=$(mktemp "${TMPDIR:-/tmp}/sandling-start.XXXXXX") || return 1
    command sandling "$@" | tee "$output"
    status=${PIPESTATUS[0]}
    if ((status != 0)); then
        rm -f -- "$output"
        return "$status"
    fi
    mapfile -t workspaces < <(sed -n 's/^Sandling workspace ready: //p' "$output")
    rm -f -- "$output"
    if ((${#workspaces[@]} != 1)) || [[ ! -d "${workspaces[0]:-}" ]]; then
        printf 'Sandling: successful start did not report exactly one usable workspace; shell was not changed.\n' >&2
        return 1
    fi
    workspace=${workspaces[0]}
    cd -- "$workspace" || return 1
}
