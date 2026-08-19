#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")"
case "${1:-}" in
  "") docker compose down ;;
  --volumes) docker compose down --volumes ;;
  *) printf '%s\n' "Usage: ./stop.sh [--volumes]" >&2; exit 2 ;;
esac
