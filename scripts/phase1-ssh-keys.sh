#!/usr/bin/env bash
# R2d2 -> joe on the three nodes. Only this project's public key is installed.
set -euo pipefail
umask 077

ssh_user="joe"
apply=false
usage() {
    echo "Usage: bash scripts/phase1-ssh-keys.sh [--user joe] [--apply]"
    echo "--apply: create/reuse the dedicated pair and add its public key to all three nodes."
    echo "Without --apply: verify the existing pair and fresh key-only logins; no public keys are installed."
}
while (($#)); do
    case "$1" in
        --user)
            if (($# < 2)); then usage >&2; exit 2; fi
            ssh_user="$2"; shift 2 ;;
        --apply) apply=true; shift ;;
        -h|--help) usage; exit 0 ;;
        *) usage >&2; exit 2 ;;
    esac
done
if [[ ! "$ssh_user" =~ ^[a-z_][a-z0-9_-]*\$?$ ]]; then echo "Invalid SSH account" >&2; exit 2; fi
if [[ ! -t 0 || "$(hostname -s)" != r2d2 || "$(id -un)" != "$ssh_user" ]]; then
    echo "Run as $ssh_user in an interactive R2d2 terminal, without sudo." >&2; exit 2
fi
for tool in ssh ssh-keygen ssh-copy-id ssh-agent ssh-add python3 tee stat; do
    command -v "$tool" >/dev/null || { echo "Required command unavailable: $tool" >&2; exit 2; }
done
repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
ssh_dir="$HOME/.ssh"
key="$ssh_dir/openstack-from-zero"
if [[ -L "$repo_root/reports" ]]; then echo "reports must be a real directory" >&2; exit 2; fi
mkdir -p -- "$repo_root/reports"
report="$(mktemp "$repo_root/reports/phase1-ssh-keys-$(date -u +%Y%m%dT%H%M%SZ)-XXXXXX.txt")"
agent_started=false
cleanup() {
    if $agent_started; then ssh-agent -k >/dev/null 2>&1 || true; fi
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
log() { printf '%s\n' "$*" | tee -a "$report"; }
stop() {
    log "RESULT: SSH_KEYS_NEED_REVIEW — $*"
    log "Existing key files and completed public-key additions are retained."
    log "Report: $report"
    printf 'Read it with: cat %q\n' "$report"
    exit 1
}
step() {
    local label="$1"
    shift
    log "$label"
    set +e
    "$@" 2>&1 | tee -a "$report"
    local statuses=("${PIPESTATUS[@]}")
    set -e
    if ((statuses[0] != 0 || statuses[1] != 0)); then stop "$label did not complete"; fi
}
log "Phase 1: dedicated SSH-key access from R2d2"
log "Apply public key: $apply"
log "Private key stays on R2d2: $key"
log "Public key installed on nodes: $key.pub"

if [[ -L "$ssh_dir" || ( -e "$ssh_dir" && ( ! -d "$ssh_dir" || ! -O "$ssh_dir" ) ) ]]; then
    stop "Unexpected SSH directory; no key files were overwritten"
fi
if [[ ! -d "$ssh_dir" ]]; then
    if ! $apply; then stop "Project key does not exist; read the apply step first"; fi
    mkdir -m 700 -- "$ssh_dir"
fi
for path in "$key" "$key.pub"; do
    if [[ -L "$path" || ( -e "$path" && ( ! -f "$path" || ! -O "$path" ) ) ]]; then
        stop "Unexpected project key file: $path"
    fi
done
if [[ -e "$key" || -e "$key.pub" ]]; then
    [[ -s "$key" && -s "$key.pub" ]] || stop "Project pair is incomplete; no file was overwritten"
    log "Reusing the existing dedicated pair; no key is regenerated."
else
    if ! $apply; then stop "Project key does not exist; read the apply step first"; fi
    log "Create one ED25519 pair. Choose a key passphrase, or press Enter twice for none."
    step "Generate private and public files together" \
        ssh-keygen -q -t ed25519 -a 64 -f "$key" -C "openstack-from-zero $ssh_user@r2d2"
fi
permissions="$(stat -c %a -- "$key")"
if (( (8#$permissions & 077) != 0 )); then stop "Private key permissions allow other accounts access; review before continuing"; fi
awk 'NF {count++; if ($1 != "ssh-ed25519" || NF < 2) bad=1} END {exit (count != 1 || bad)}' "$key.pub" \
    || stop "Expected one ED25519 public key"
# /dev/stdin prevents ssh-keygen from reading an adjacent .pub instead of the private identity.
private_fingerprint="$(ssh-keygen -lf /dev/stdin < "$key" | awk '{print $2}')" \
    || stop "Cannot identify the private key"
public_fingerprint="$(ssh-keygen -lf "$key.pub" | awk '{print $2}')" \
    || stop "Cannot identify the public key"
[[ -n "$private_fingerprint" && "$private_fingerprint" == "$public_fingerprint" ]] \
    || stop "Private/public fingerprints differ; no file was overwritten"
log "Matching pair fingerprint: $public_fingerprint"

set +e
ssh-add -l >/dev/null 2>&1
agent_status=$?
set -e
if ((agent_status == 2)); then
    log "Start a temporary local SSH agent for this script; it is stopped when the script exits."
    agent_environment="$(ssh-agent -s)" || stop "Cannot start the local SSH agent"
    eval "$agent_environment" >/dev/null
    agent_started=true
elif ((agent_status != 0 && agent_status != 1)); then
    stop "Cannot inspect the SSH agent"
fi
log "If asked by ssh-add, enter the key passphrase chosen above, not the node login password."
step "Load this key into the agent for up to four hours" ssh-add -t 4h "$key"
step "Check that the agent can sign with this public key" ssh-add -T "$key.pub"

# Hostname/account/IP/OS checks run before adding a key and again during key-only verification.
remote_source="$(cat <<'PY'
import json, os, pwd, socket, stat, subprocess, sys
from pathlib import Path
name, address, username, marker = sys.argv[1:]
if socket.gethostname().split('.')[0] != name or pwd.getpwuid(os.getuid()).pw_name != username:
    sys.exit('Unexpected node hostname/account')
release = dict(line.split('=', 1) for line in Path('/etc/os-release').read_text().splitlines() if '=' in line)
if release.get('ID', '').strip('"') != 'ubuntu' or release.get('VERSION_ID', '').strip('"') != '24.04':
    sys.exit('Expected Ubuntu 24.04')
interfaces = json.loads(subprocess.check_output(['ip', '-j', 'address', 'show'], text=True))
matches = [entry for nic in interfaces for entry in nic.get('addr_info', [])
           if entry.get('family') == 'inet' and entry.get('local') == address and entry.get('prefixlen') == 24]
if len(matches) != 1:
    sys.exit('Expected management IPv4 address was not confirmed')
directory = Path.home() / '.ssh'
authorized = directory / 'authorized_keys'
for path, kind in ((directory, 'directory'), (authorized, 'file')):
    if path.is_symlink():
        sys.exit('Review SSH path symlink before installing a public key')
    if path.exists():
        details = path.stat()
        if details.st_uid != os.getuid() or (kind == 'directory' and not stat.S_ISDIR(details.st_mode)) or (kind == 'file' and not stat.S_ISREG(details.st_mode)):
            sys.exit('Review SSH path ownership/type before installing a public key')
print(marker, name, username, address)
PY
)"
make_remote_command() {
    python3 - "$remote_source" "$1" "$2" "$ssh_user" "$3" <<'PY'
import shlex, sys
print(shlex.join(['python3', '-c', sys.argv[1], *sys.argv[2:]]))
PY
}
common_options=(-o StrictHostKeyChecking=yes -o UpdateHostKeys=no -o ConnectTimeout=10
    -o ConnectionAttempts=1 -o ControlMaster=no -o ControlPath=none -o ControlPersist=no
    -o ForwardAgent=no -o ForwardX11=no -o ClearAllForwardings=yes
    -o PermitLocalCommand=no -o RemoteCommand=none -o AddKeysToAgent=no
    -o IdentitiesOnly=yes -o NumberOfPasswordPrompts=1)
for node in node1 node2 node3; do
    case "$node" in node1) address=192.168.0.200 ;; node2) address=192.168.0.201 ;; node3) address=192.168.0.202 ;; esac
    log "Node: $node ($address)"
    if $apply; then
        remote_command="$(make_remote_command "$node" "$address" NODE_IDENTITY_OK)"
        step "Confirm $node before adding the public key; its login password may be requested" \
            ssh -F /dev/null -T "${common_options[@]}" -i "$key" \
                -o PreferredAuthentications=publickey,keyboard-interactive,password \
                -o PubkeyAuthentication=yes -o PasswordAuthentication=yes -o KbdInteractiveAuthentication=yes \
                "$ssh_user@$address" "$remote_command"
        step "Add only the selected public key on $node; its login password may be requested again" \
            ssh-copy-id -F /dev/null -i "$key.pub" "${common_options[@]}" \
                -o "IdentityFile=$key" -o PreferredAuthentications=publickey,keyboard-interactive,password \
                -o PubkeyAuthentication=yes -o PasswordAuthentication=yes -o KbdInteractiveAuthentication=yes \
                "$ssh_user@$address"
    fi
    remote_command="$(make_remote_command "$node" "$address" KEY_LOGIN_OK)"
    step "Verify a fresh login to $node using this key only" \
        ssh -F /dev/null -T "${common_options[@]}" -i "$key" \
            -o BatchMode=yes -o PreferredAuthentications=publickey -o PubkeyAuthentication=yes \
            -o PasswordAuthentication=no -o KbdInteractiveAuthentication=no \
            "$ssh_user@$address" "$remote_command"
done
log "RESULT: SSH_KEYS_READY for all"
log "Chrony, /etc/hosts, NICs and sudo policy were not changed."
log "Private combined report: $report"
printf 'Read it with: cat %q\n' "$report"
