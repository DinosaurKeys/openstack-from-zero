#!/usr/bin/env bash
# Start the reviewed Hermes file-only session. Joe runs this as hermes-iac.
set -euo pipefail

stop() { printf 'STOP: %s\n' "$1" >&2; exit 1; }

case "${1:-}" in
  --help|-h)
    printf 'Usage: bash scripts/hermes-iac.sh [--check]\n'
    printf 'Run as hermes-iac inside its public working copy. No extra chat flags are accepted.\n'
    exit 0 ;;
  --check|'') ;;
  *) stop 'Use no argument to chat, or --check for local launcher checks.' ;;
esac
[[ $# -le 1 ]] || stop 'Extra arguments are not allowed.'
[[ "$(id -un)" == hermes-iac ]] || stop 'Enter the separate account first: sudo -iu hermes-iac'
[[ "$(id -u)" != 0 ]] || stop 'The Hermes account must not have root UID 0.'
for lab_group in $(id -nG); do
  case "$lab_group" in sudo|docker|lxd|incus-admin) stop 'The Hermes account has an administrative/container group; review its permissions.' ;; esac
done

lab_workspace="$HOME/work/openstack-from-zero"
lab_source="$HOME/.hermes/hermes-agent"
lab_cli="$HOME/.local/bin/hermes"
lab_pin=bf0203a794a07aac3ff764972ab9087baa4e4f4e
lab_repo="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"

[[ -d "$lab_workspace" && ! -L "$lab_workspace" ]] || stop 'The public working copy is missing or symlinked.'
[[ "$(cd -- "$lab_workspace" && pwd -P)" == "$lab_repo" ]] || stop 'Run the launcher from ~/work/openstack-from-zero.'
[[ -x "$lab_cli" && -d "$lab_source/.git" ]] || stop 'Complete the Phase 8 Hermes installation first.'
lab_actual="$(git -C "$lab_source" rev-parse HEAD)" || stop 'Cannot read the installed Hermes source revision.'
[[ "$lab_actual" == "$lab_pin" ]] || stop 'Installed Hermes differs from the reviewed v0.22.5 revision; review before changing versions.'
for lab_file in AI/HERMES-IAC-RULES.md AI/HERMES-LEARNING-GUIDE.md AI/HERMES-START.md; do
  [[ -f "$lab_workspace/$lab_file" && ! -L "$lab_workspace/$lab_file" ]] || stop 'A required AI guide is missing or symlinked.'
done
[[ ! -e "$lab_workspace/private" && ! -L "$lab_workspace/private" ]] || stop 'This must be a public-only working copy; private/ was found.'
[[ ! -e "$lab_workspace/reports" && ! -L "$lab_workspace/reports" ]] || stop 'This must be a public-only working copy; reports/ was found.'

# Remove inherited cloud/Git authentication; never print its values.
for lab_env_name in ${!OS_@}; do unset "$lab_env_name"; done
unset SSH_AUTH_SOCK SSH_AGENT_PID GH_TOKEN GITHUB_TOKEN GH_ENTERPRISE_TOKEN
unset TERMINAL_SSH_HOST TERMINAL_SSH_USER TERMINAL_SSH_KEY
export HERMES_HOME="$HOME/.hermes"
export HERMES_WRITE_SAFE_ROOT="$lab_workspace/terraform:$lab_workspace/ansible:$lab_workspace/docs:$lab_workspace/AI"
export TERMINAL_ENV=local
export TERMINAL_CWD="$lab_workspace"
export PATH="$HOME/.local/bin:$PATH"

printf 'HERMES IAC SESSION\n'
printf 'Account: hermes-iac | Installed source: v0.22.5\n'
printf 'Workspace: %s\n' "$lab_workspace"
printf 'Provider: OpenRouter | Toolsets: file, skills, clarify\n'
printf 'Infrastructure commands: Joe executes them separately.\n'

if [[ "${1:-}" == --check ]]; then
  printf 'RESULT: HERMES_LAUNCHER_CHECK_PASSED\n'
  printf 'Local paths/account/source checks only; provider login and the active runtime tool list are not tested.\n'
  exit 0
fi
[[ -t 0 && -t 1 ]] || stop 'Start chat from an interactive terminal.'
exec "$lab_cli" chat --cli --provider openrouter --toolsets file,skills,clarify \
  --in "$lab_workspace" --no-restore-cwd --query-file "$lab_workspace/AI/HERMES-START.md"
