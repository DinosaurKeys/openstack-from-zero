#!/usr/bin/env bash
# R2d2 entry point: inspect only the identified node3 Cinder disk.
set -uo pipefail
umask 077

ssh_user="joe"
while (($#)); do
    case "$1" in
        --user)
            if (($# < 2)); then echo "Missing value for --user" >&2; exit 2; fi
            ssh_user="$2"; shift 2 ;;
        -h|--help)
            echo "Usage: bash scripts/phase1-storage-check.sh [--user joe]"
            echo "Read node3 disk identity, usage, signatures and available SMART data. No disk changes."
            exit 0 ;;
        *) echo "Unknown option: $1" >&2; exit 2 ;;
    esac
done
if [[ ! "$ssh_user" =~ ^[a-z_][a-z0-9_-]*\$?$ ]]; then
    echo "Invalid Linux SSH account" >&2; exit 2
fi
if [[ ! -t 0 ]]; then echo "Run from an interactive R2d2 terminal" >&2; exit 2; fi
repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ -L "$repo_root/reports" ]]; then echo "reports must be a real directory" >&2; exit 2; fi
remote_command="$(python3 - "$repo_root/scripts/phase1-storage-node.py" <<'PY'
import base64, pathlib, shlex, sys
encoded = base64.b64encode(pathlib.Path(sys.argv[1]).read_bytes()).decode()
bootstrap = 'import base64;exec(compile(base64.b64decode('+repr(encoded)+'),"phase1-storage-node.py","exec"))'
print(shlex.join(['sudo', '/usr/bin/python3', '-u', '-c', bootstrap]))
PY
)" || exit 1
mkdir -p -- "$repo_root/reports" || exit 1
report_path="$(mktemp "$repo_root/reports/phase1-storage-$(date -u +%Y%m%dT%H%M%SZ)-XXXXXX.txt")" || exit 1
printf 'Phase 1: node3 Cinder disk inspection\nMode: READ ONLY\nNode: node3 (192.168.0.202)\nSSH and sudo may prompt. No packages are installed.\n' | tee -a "$report_path"
ssh -tt -o ConnectTimeout=10 -o ConnectionAttempts=1 -o StrictHostKeyChecking=ask \
    -o ControlMaster=no -o ControlPath=none "$ssh_user@192.168.0.202" "$remote_command" 2>&1 | tee -a "$report_path"
pipe_status=("${PIPESTATUS[@]}")
result="STORAGE_INSPECTION_COLLECTED"
exit_code=0
if ((pipe_status[0] != 0 || pipe_status[1] != 0)); then
    result="STORAGE_INSPECTION_NEEDS_REVIEW"
    exit_code=1
fi
printf '\nRESULT: %s\nReview this evidence before any disk preparation.\nReport: %s\n' "$result" "$report_path" | tee -a "$report_path"
printf 'Read it with: cat %q\n' "$report_path"
exit "$exit_code"
