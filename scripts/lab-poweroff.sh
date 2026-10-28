#!/usr/bin/env bash
# Final Ubuntu shutdown only; OpenStack services must already be stopped.
set -euo pipefail
umask 077

mode=${1:---help}
case "$mode" in
  --help|-h)
    echo 'Usage: bash scripts/lab-poweroff.sh --check | --apply'
    echo '--check checks the saved service-stop record and hosts; no shutdown.'
    echo '--apply asks for confirmation, checks each host, then schedules poweroff in one minute.'
    exit 0 ;;
  --check|--apply) ;;
  *) echo 'Use --check or --apply.' >&2; exit 2 ;;
esac
[[ $# -le 1 ]] || { echo 'Unexpected arguments.' >&2; exit 2; }
[[ $(id -un) == joe && $(hostname -s) == r2d2 ]] || {
  echo 'Run on R2d2 as joe.' >&2; exit 1;
}
repo=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
lock="$repo/private/phase5/shutdown.lock"
[[ -d ${lock%/*} && ! -L "$lock" ]] || { echo 'Missing/unsafe shutdown lock.' >&2; exit 1; }
exec 9>"$lock"
flock -n 9 || { echo 'Another shutdown operation is running.' >&2; exit 1; }

# Require a verified service shutdown, not a failed or incomplete attempt.
/usr/bin/python3 - "$repo" <<'PY'
import json, os, re, stat, sys, uuid
from pathlib import Path
root=Path(sys.argv[1])/'private/phase5'
records=[]
for path in root.glob('shutdown-*.json'):
    if not re.fullmatch(r'shutdown-\d{8}T\d{6}Z-.+\.json',path.name):continue
    info=path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid() or info.st_mode & 0o077:
        raise SystemExit('Unsafe shutdown receipt; no poweroff.')
    value=json.loads(path.read_text())
    if value.get('pending'):raise SystemExit('Service shutdown is incomplete. Use the reviewed --finish procedure; no poweroff.')
    records.append((info.st_mtime_ns,value))
if not records:raise SystemExit('No verified service-stop record. Finish service shutdown first.')
value=max(records,key=lambda r:r[0])[1]
try:uuid.UUID(value.get('cluster_uuid',''))
except (ValueError,TypeError):raise SystemExit('Invalid database cluster record; no poweroff.')
if value.get('pending') is not False or value.get('stage')!='services stopped and database verified; manual OS poweroff remains':
    raise SystemExit('Service-stop completion not confirmed; no poweroff.')
database=value.get('database',{})
if set(database)!={'node1','node2','node3'}:raise SystemExit('Database shutdown records incomplete.')
for node,r in database.items():
    if r.get('exit_code')!=0 or r.get('uuid')!=value.get('cluster_uuid') or r.get('seqno',-1)<0 or r.get('safe_to_bootstrap')!=int(node=='node3'):
        raise SystemExit('Database shutdown records need review; no poweroff.')
if database['node3']['seqno']<max(r['seqno'] for r in database.values()):
    raise SystemExit('Last database shutdown position needs review; no poweroff.')
print('Verified service-stop record: OK. Last database member: node3.')
PY

if [[ "$mode" == --apply ]]; then
  [[ -t 0 ]] || { echo 'Use an interactive terminal.' >&2; exit 1; }
  echo 'This powers off node1, node2 and node3. R2d2 stays running.'
  read -r -p 'Type POWER OFF ALL NODES to continue: ' answer
  [[ "$answer" == 'POWER OFF ALL NODES' ]] || { echo 'Cancelled. No shutdown sent.'; exit 1; }
fi

remote_code=$(cat <<'REMOTE'
set -euo pipefail
[[ $EUID -eq 0 && ${SUDO_USER:-} == joe && $(hostname -s) == "$1" ]] || exit 1
running=$(docker ps -q)
[[ -z "$running" ]] || { echo 'Containers still running; no poweroff.' >&2; exit 1; }
for service in mariadb rabbitmq; do
  [[ $(docker inspect --format '{{.State.Status}} {{.State.ExitCode}} {{.State.OOMKilled}}' "$service") == 'exited 0 false' ]] || {
    echo "$service clean shutdown not confirmed; no poweroff." >&2; exit 1;
  }
done
processes=$(ps -e -o comm=)
if awk '/^qemu-system|^qemu-kvm|^kvm$/ {found=1} END {exit !found}' <<< "$processes"; then
  echo 'VM process still present; no poweroff.' >&2; exit 1
fi
if [[ "$2" == --apply ]]; then
  [[ ! -e /run/systemd/shutdown/scheduled ]] || { echo 'A shutdown is already scheduled; no duplicate request.' >&2; exit 1; }
  /usr/sbin/shutdown -h +1
  echo "$1: POWEROFF SCHEDULED in one minute"
else
  echo "$1: READY — no shutdown requested"
fi
REMOTE
)
ssh_options=(-F /dev/null -t -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes
  -o BatchMode=yes -o ConnectTimeout=5 -o ConnectionAttempts=1
  -o ForwardAgent=no -o ClearAllForwardings=yes -i "$HOME/.ssh/openstack-from-zero")
for index in 1 2 3; do
  node="node$index"
  ip="192.168.0.$((199+index))"
  printf '\n%s (%s)\n' "$node" "$ip"
  printf -v command 'sudo bash -c %q -- %q %q' "$remote_code" "$node" "$mode"
  if ! ssh "${ssh_options[@]}" "joe@$ip" "$command"; then
    echo "$node: request/check failed or outcome unknown. No retry. Earlier hosts may already have shutdown scheduled." >&2
    exit 1
  fi
done
if [[ "$mode" == --apply ]]; then
  echo 'All three shutdown requests acknowledged. Wait until each physical host is off before removing power.'
else
  echo 'All three hosts checked. No shutdown requested.'
fi
