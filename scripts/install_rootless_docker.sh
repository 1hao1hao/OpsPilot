#!/usr/bin/env bash
set -euo pipefail

account_name="$(id -un)"
account_id="$(id -u)"

has_large_enough_range() {
    awk -F: -v name="$account_name" -v uid="$account_id" \
        '($1 == name || $1 == uid) && $3 >= 65536 { found = 1 } END { exit !found }' "$1"
}

if ! has_large_enough_range /etc/subuid; then
    printf '%s\n' "Missing /etc/subuid allocation for $account_name (minimum 65536 IDs)." >&2
    printf '%s\n' "Ask an administrator to allocate an unused range, then rerun this script." >&2
    exit 2
fi
if ! has_large_enough_range /etc/subgid; then
    printf '%s\n' "Missing /etc/subgid allocation for $account_name (minimum 65536 IDs)." >&2
    printf '%s\n' "Ask an administrator to allocate an unused range, then rerun this script." >&2
    exit 2
fi

installer="$(mktemp /tmp/docker-rootless-install.XXXXXX)"
trap 'rm -f "$installer"' EXIT
curl -fsSL https://get.docker.com/rootless -o "$installer"
if ! lsmod | grep -q '^ip_tables'; then
    printf '%s\n' "ip_tables is not loaded; continuing with SKIP_IPTABLES=1 for this unprivileged install." >&2
    SKIP_IPTABLES=1 sh "$installer"
else
    sh "$installer"
fi

export PATH="$HOME/bin:$PATH"
export DOCKER_HOST="unix:///run/user/$account_id/docker.sock"
docker info
docker-compose version
