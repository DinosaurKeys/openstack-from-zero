#!/usr/bin/env bash
# Read container names/status only. Run on R2d2; sudo is entered on each node.
set -u -o pipefail

usage() {
  printf 'Usage: bash scripts/lab-containers.sh [all|node1|node2|node3]\n'
}
if (( $# > 1 )); then usage >&2; exit 2; fi
case "${1:-all}" in
  -h|--help) usage; exit 0 ;;
  all) lab_nodes=(node1 node2 node3) ;;
  node1|node2|node3) lab_nodes=("$1") ;;
  *) usage >&2; exit 2 ;;
esac
if [[ "$(hostname -s)" != r2d2 ]]; then
  printf 'Run this view on R2d2 as joe. It connects to the OpenStack nodes.\n' >&2
  exit 2
fi

lab_result=0
printf 'OPENSTACK CONTAINERS — READ ONLY (including stopped containers)\n'
for lab_node in "${lab_nodes[@]}"; do
  case "$lab_node" in
    node1) lab_ip=192.168.0.200 ;;
    node2) lab_ip=192.168.0.201 ;;
    node3) lab_ip=192.168.0.202 ;;
  esac
  printf '\n%s (%s)\n' "$lab_node" "$lab_ip"
  printf -v lab_remote 'if [ "$(hostname -s)" != %q ]; then echo "Unexpected host identity" >&2; exit 2; fi; sudo docker ps -a --format %q' \
    "$lab_node" 'table {{.Names}}\t{{.Status}}'
  if ! ssh -t -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes -o ConnectTimeout=8 \
    -i "$HOME/.ssh/openstack-from-zero" "joe@$lab_ip" "$lab_remote"; then
    printf 'FAIL %s: container listing unavailable; see SSH/sudo error above.\n' "$lab_node" >&2
    lab_result=1
  fi
done
printf '\nContainer Up = process running; healthchecks and OpenStack heartbeats are separate checks.\n'
exit "$lab_result"
