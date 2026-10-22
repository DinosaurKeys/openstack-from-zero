#!/usr/bin/env bash
# R2d2 entry point: USB-only Netplan preparation, one node at a time.
set -uo pipefail
umask 077

ssh_user="joe"
selected_node="all"
apply=false
while (($#)); do
    case "$1" in
        --user|--node)
            if (($# < 2)); then echo "Missing value for $1" >&2; exit 2; fi
            if [[ "$1" == --user ]]; then ssh_user="$2"; else selected_node="$2"; fi
            shift 2 ;;
        --apply) apply=true; shift ;;
        -h|--help)
            echo "Usage: bash scripts/phase1-network.sh [--user joe] [--node all|node1|node2|node3] [--apply]"
            echo "Without --apply: inspect and show the USB candidate only."
            exit 0 ;;
        *) echo "Unknown option: $1" >&2; exit 2 ;;
    esac
done
if [[ ! "$ssh_user" =~ ^[a-z_][a-z0-9_-]*\$?$ ]]; then
    echo "Invalid Linux SSH account" >&2; exit 2
fi
case "$selected_node" in all|node1|node2|node3) ;; *) echo "Invalid node selection" >&2; exit 2 ;; esac
if [[ ! -t 0 ]]; then echo "Run from an interactive R2d2 terminal" >&2; exit 2; fi
repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ ! -f "$repo_root/scripts/phase1-network-node.py" ]]; then echo "Node helper is missing" >&2; exit 2; fi
if [[ -L "$repo_root/reports" ]]; then echo "reports must be a real directory" >&2; exit 2; fi
mkdir -p -- "$repo_root/reports" || exit 1
report_path="$(mktemp "$repo_root/reports/phase1-network-$(date -u +%Y%m%dT%H%M%SZ)-XXXXXX.txt")" || exit 1
mode_label="PREVIEW"
if $apply; then mode_label="APPLY + VERIFY"; fi
printf 'Phase 1: USB provider NIC preparation\nMode: %s\nNodes: %s\n' "$mode_label" "$selected_node" | tee -a "$report_path"

for node_name in node1 node2 node3; do
    if [[ "$selected_node" != all && "$selected_node" != "$node_name" ]]; then continue; fi
    case "$node_name" in
        node1) node_ip="192.168.0.200" ;;
        node2) node_ip="192.168.0.201" ;;
        node3) node_ip="192.168.0.202" ;;
    esac
    remote_command="$(python3 - "$repo_root/scripts/phase1-network-node.py" "$node_name" "$apply" <<'PY'
import base64, pathlib, shlex, sys
encoded=base64.b64encode(pathlib.Path(sys.argv[1]).read_bytes()).decode()
bootstrap='import base64;exec(compile(base64.b64decode('+repr(encoded)+'),"phase1-network-node.py","exec"))'
args=['sudo','/usr/bin/python3','-u','-c',bootstrap,sys.argv[2]]
if sys.argv[3]=='true': args.append('--apply')
print(shlex.join(args))
PY
)" || exit 1
    printf '\nNode: %s (%s)\nSSH and sudo may prompt; Netplan will ask to confirm its timed trial.\n' "$node_name" "$node_ip" | tee -a "$report_path"
    ssh -tt -o ConnectTimeout=10 -o ConnectionAttempts=1 -o StrictHostKeyChecking=ask \
        -o ControlMaster=no -o ControlPath=none "$ssh_user@$node_ip" "$remote_command" 2>&1 | tee -a "$report_path"
    pipe_status=("${PIPESTATUS[@]}")
    if ((pipe_status[0] != 0 || pipe_status[1] != 0)); then
        printf '\nRESULT: NETWORK_NEEDS_REVIEW at %s; completed nodes retain their settings.\nReport: %s\n' "$node_name" "$report_path" | tee -a "$report_path"
        exit 1
    fi
    if $apply; then
        printf 'Checking a fresh SSH connection to %s...\n' "$node_name" | tee -a "$report_path"
        ssh -T -o ConnectTimeout=10 -o ConnectionAttempts=1 -o StrictHostKeyChecking=ask \
            -o ControlMaster=no -o ControlPath=none "$ssh_user@$node_ip" \
            'printf "SSH_RECONNECT_OK "; hostname' 2>&1 | tee -a "$report_path"
        pipe_status=("${PIPESTATUS[@]}")
        if ((pipe_status[0] != 0 || pipe_status[1] != 0)); then
            printf '\nRESULT: NETWORK_NEEDS_REVIEW — fresh SSH failed at %s.\nUse its console and the printed backup path; other nodes have not been started.\nReport: %s\n' "$node_name" "$report_path" | tee -a "$report_path"
            exit 1
        fi
    fi
done
if $apply; then result="USB_NETWORK_READY"; else result="NETWORK_PREVIEW_ONLY"; fi
printf '\nRESULT: %s for %s\nCombined report: %s\n' "$result" "$selected_node" "$report_path" | tee -a "$report_path"
printf 'Read it with: cat %q\n' "$report_path"
