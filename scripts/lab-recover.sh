#!/usr/bin/env bash
# Run from R2d2 as joe. Checking/planning never restarts services.
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if (( $# > 1 )); then
  printf 'Usage: bash scripts/lab-recover.sh [--check|--plan|--apply]\n' >&2
  exit 2
fi
case "${1:---check}" in
  --check|--plan|--apply|-h|--help)
    exec /usr/bin/python3 "$SCRIPT_DIR/lab-recover.py" "${1:---check}"
    ;;
  *) printf 'Usage: bash scripts/lab-recover.sh [--check|--plan|--apply]\n' >&2; exit 2 ;;
esac
