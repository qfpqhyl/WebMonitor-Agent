#!/bin/bash
set -euo pipefail
unbound -d -c /etc/unbound/webmonitor.conf &
resolver=$!
squid -N -f /etc/squid/squid.conf &
proxy=$!
cleanup() {
    kill "$resolver" "$proxy" 2>/dev/null || true
    wait "$resolver" "$proxy" 2>/dev/null || true
}
trap cleanup EXIT TERM INT
# If either real service exits, stop its peer and let Compose restart the pair.
wait -n "$resolver" "$proxy"
exit 1
