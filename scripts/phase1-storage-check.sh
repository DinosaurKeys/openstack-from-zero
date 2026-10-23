#!/usr/bin/env bash
# R2d2 entry point: inspect SATA disks on the selected lab nodes.
set -uo pipefail
umask 077

ssh_user="joe"
selected_node="all"
while (($#)); do
    case "$1" in
        --user)
            if (($# < 2)); then echo "Missing value for --user" >&2; exit 2; fi
            ssh_user="$2"; shift 2 ;;
        --node)
            if (($# < 2)); then echo "Missing value for --node" >&2; exit 2; fi
            selected_node="$2"; shift 2 ;;
        -h|--help)
            echo "Usage: bash scripts/phase1-storage-check.sh [--user joe] [--node all|node1|node2|node3]"
            echo "Default: inspect SATA disks on all three nodes. No disk changes."
            exit 0 ;;
        *) echo "Unknown option: $1" >&2; exit 2 ;;
    esac
done
case "$selected_node" in
    all) nodes=(node1 node2 node3) ;;
    node1|node2|node3) nodes=("$selected_node") ;;
    *) echo "Choose --node all, node1, node2 or node3" >&2; exit 2 ;;
esac
if [[ ! "$ssh_user" =~ ^[a-z_][a-z0-9_-]*\$?$ ]]; then
    echo "Invalid Linux SSH account" >&2; exit 2
fi
if [[ ! -t 0 ]]; then echo "Run from an interactive R2d2 terminal" >&2; exit 2; fi
identity_file="$HOME/.ssh/openstack-from-zero"
identity_options=()
if [[ -e "$identity_file" || -L "$identity_file" ]]; then
    if [[ -L "$identity_file" || ! -f "$identity_file" || ! -r "$identity_file" ]]; then
        echo "Project SSH private-key file needs review; no node connection made" >&2; exit 2
    fi
    identity_options=(-i "$identity_file" -o IdentitiesOnly=yes)
fi
repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ -L "$repo_root/reports" ]]; then echo "reports must be a real directory" >&2; exit 2; fi
mkdir -p -- "$repo_root/reports" || exit 1
report_path="$(mktemp "$repo_root/reports/phase1-storage-$(date -u +%Y%m%dT%H%M%SZ)-XXXXXX.txt")" || exit 1
printf 'Phase 1: SATA disk inspection\nMode: READ ONLY\nNodes: %s\nSSH and sudo may prompt. No packages are installed.\n' "$selected_node" | tee -a "$report_path"
if ((${#identity_options[@]})); then
    printf 'SSH identity: %s (private key stays on R2d2)\nKey-unlock prompt: key passphrase; sudo prompt: joe password on the current node.\n' "$identity_file" | tee -a "$report_path"
else
    printf 'Project key absent: use existing SSH authentication; node login passwords may be requested.\n' | tee -a "$report_path"
fi
exit_code=0
for node in "${nodes[@]}"; do
    case "$node" in
        node1) address="192.168.0.200" ;;
        node2) address="192.168.0.201" ;;
        node3) address="192.168.0.202" ;;
    esac
    remote_command="$(python3 - "$repo_root/scripts/phase1-storage-node.py" "$node" <<'PY'
import base64, pathlib, shlex, sys
encoded = base64.b64encode(pathlib.Path(sys.argv[1]).read_bytes()).decode()
bootstrap = ('import base64,sys;scope={"__name__":"storage_collector"};'
             'exec(compile(base64.b64decode('+repr(encoded)+'),"phase1-storage-node.py","exec"),scope);'
             'sys.exit(scope["main"]('+repr(sys.argv[2])+'))')
print(shlex.join(['sudo', '/usr/bin/python3', '-u', '-c', bootstrap]))
PY
)" || { exit_code=1; break; }
    printf '\nNode: %s (%s)\n' "$node" "$address" | tee -a "$report_path"
    ssh -tt -o ConnectTimeout=10 -o ConnectionAttempts=1 -o StrictHostKeyChecking=ask \
        -o ControlMaster=no -o ControlPath=none "${identity_options[@]}" "$ssh_user@$address" "$remote_command" 2>&1 | tee -a "$report_path"
    pipe_status=("${PIPESTATUS[@]}")
    if ((pipe_status[0] != 0 || pipe_status[1] != 0)); then
        printf 'Node result: %s NEEDS_REVIEW\n' "$node" | tee -a "$report_path"
        exit_code=1
    else
        printf 'Node result: %s COLLECTED\n' "$node" | tee -a "$report_path"
    fi
done
result="STORAGE_INSPECTION_COLLECTED"
if ((exit_code != 0)); then
    result="STORAGE_INSPECTION_NEEDS_REVIEW"
fi
printf '\nRESULT: %s\nReview this evidence before any disk preparation.\nReport: %s\n' "$result" "$report_path" | tee -a "$report_path"
printf 'Read it with: cat %q\n' "$report_path"
exit "$exit_code"
