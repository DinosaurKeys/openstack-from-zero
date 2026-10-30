#!/usr/bin/env bash
# Read host capacity and Heat/RabbitMQ inventory. No service or queue mutations.
set -u
set -o pipefail

case "${1:---check}" in
  --help|-h)
    echo 'Usage: bash scripts/lab-resource-check.sh --check'
    echo 'Read CPU, memory, Heat services and RabbitMQ queues; change nothing.'
    exit 0 ;;
  --check)
    if (( $# > 1 )); then echo 'Use --check only.' >&2; exit 2; fi ;;
  *) echo 'Use --check only. This checker has no repair or delete mode.' >&2; exit 2 ;;
esac

lab_check=$(cat <<'CHECK'
set -eu
if [ "$(hostname -s)" != "$lab_expected" ]; then
  echo 'BLOCKED: management address has an unexpected hostname.' >&2
  exit 1
fi
printf '\n== %s ==\n' "$(hostname -s)"
printf 'CPU threads: '; getconf _NPROCESSORS_ONLN
printf 'CPU load averages (1/5/15 minutes): '; cut -d ' ' -f 1-3 /proc/loadavg
free -h
sudo -v
printf '\nHeat containers:\n'
lab_heat=$(sudo -n docker ps -a --filter 'name=^/heat_' --format '{{.Names}} | {{.Status}}')
if [ -n "$lab_heat" ]; then printf '%s\n' "$lab_heat"; else echo 'NONE found'; fi
printf '\nHeat startup units:\n'
# A systemctl pattern with no matches exits nonzero. Read the service list
# successfully first, then filter optional Heat units without hiding errors.
lab_units=$(systemctl list-unit-files --type=service --no-legend --no-pager)
lab_heat_units=$(printf '%s\n' "$lab_units" | awk '$1 ~ /^kolla-heat_.*-container[.]service$/ { print }')
if [ -n "$lab_heat_units" ]; then printf '%s\n' "$lab_heat_units"; else echo 'NONE found'; fi
printf '\nRabbitMQ container resource usage:\n'
sudo -n docker stats --no-stream --format 'CPU={{.CPUPerc}} RAM={{.MemUsage}}' rabbitmq
if [ "$(hostname -s)" = node1 ]; then
  printf '\nRabbitMQ version:\n'
  sudo -n docker exec rabbitmq rabbitmqctl version
  printf '\nRabbitMQ vhosts:\n'
  sudo -n docker exec rabbitmq rabbitmqctl -q list_vhosts name
  lab_queues=$(sudo -n docker exec rabbitmq rabbitmqctl -q list_queues -p / name type messages consumers --formatter json --timeout 60)
  printf '%s\n' "$lab_queues" | python3 -c '
import json,re,sys
try: q=json.load(sys.stdin)
except ValueError: raise SystemExit("BLOCKED: queue output was not valid JSON; counts unknown")
if not isinstance(q,list): raise SystemExit("BLOCKED: invalid queue response")
for x in q:
    if not isinstance(x,dict) or not isinstance(x.get("name"),str) or not isinstance(x.get("type"),str) or any(type(x.get(k)) is not int or x[k]<0 for k in ("messages","consumers")):
        raise SystemExit("BLOCKED: incomplete queue response")
h=[x for x in q if re.fullmatch(r"(?:heat-engine-listener|engine_worker)\.[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}",x["name"])]
c=[x for x in h if x["type"]=="quorum" and x["messages"]==0 and x["consumers"]==0]
print("Queue inventory for vhost / (cluster-wide; counted once):")
print("  Total queues:",len(q))
print("  Heat-named queues:",len(h))
print("  Empty Heat quorum queues with no consumers:",len(c))
print("  Review candidates only; nothing deleted.")
'
fi
CHECK
)

echo 'CPU / MEMORY / HEAT / RABBITMQ — CHECK ONLY'
echo 'No service stop, restart, retirement or queue deletion is requested.'
printf 'Time (UTC): %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
lab_failed=0
for lab_entry in node1:192.168.0.200 node2:192.168.0.201 node3:192.168.0.202; do
  lab_node=${lab_entry%%:*}
  lab_ip=${lab_entry#*:}
  printf '\nReading %s; enter its sudo password if asked.\n' "$lab_ip"
  if ! ssh -t -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes \
    -o ConnectTimeout=10 -o ServerAliveInterval=10 -o ServerAliveCountMax=3 \
    -i "$HOME/.ssh/openstack-from-zero" joe@"$lab_ip" "lab_expected=$lab_node; $lab_check"; then
    printf 'BLOCKED: %s check incomplete; missing values are unknown.\n' "$lab_ip" >&2
    lab_failed=1
  fi
done
echo
echo 'RAM available is usable host memory now; CPU threads are not unallocated vCPUs.'
echo 'Queue counts cover vhost / only. Other listed vhosts need separate inspection.'
echo 'Empty/no-consumer queues are candidates for review, not proof of staleness.'
if (( lab_failed )); then
  echo 'RESULT: RESOURCE_AND_HEAT_CHECK_INCOMPLETE'
  exit 1
fi
echo 'RESULT: RESOURCE_AND_HEAT_CHECK_COLLECTED'
echo 'Return this output for review before retirement or cleanup.'
