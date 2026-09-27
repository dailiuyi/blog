#!/bin/bash
# Start only after the operator has checked the ready queue and active tasks.
set -eu
IFS= read -r GITHUB_TOKEN
GITHUB_TOKEN=${GITHUB_TOKEN%$'\r'}
test -n "$GITHUB_TOKEN"
export GITHUB_TOKEN
# Symphony strips tracker credentials before codex.command. This command runs
# the trusted controller; its agent/check filters remove this dedicated alias.
export SYMPHONY_ACCEPTANCE_GITHUB_TOKEN="$GITHUB_TOKEN"
acceptance_home="$HOME/.local/symphony-acceptance"
export PATH="$HOME/.local/symphony-bin:$PATH"
export PYTHONPATH="$acceptance_home/current/scripts"
config="$acceptance_home/current/config/symphony-blog.json"
runner="${SYMPHONY_BINARY:?Set SYMPHONY_BINARY to the installed native runner}"
mkdir -p "$acceptance_home/logs"
if test -f "$acceptance_home/watch.pid" && kill -0 "$(cat "$acceptance_home/watch.pid")" 2>/dev/null; then
  echo 'acceptance watcher already running' >&2
  exit 1
fi
if test -f "$acceptance_home/runner.pid" && kill -0 "$(cat "$acceptance_home/runner.pid")" 2>/dev/null; then
  echo 'acceptance runner already running' >&2
  exit 1
fi
python3 -m symphony_acceptance --config "$config" watch >>"$acceptance_home/logs/watch.log" 2>&1 &
watch_pid=$!
echo "$watch_pid" > "$acceptance_home/watch.pid"
trap 'kill "$watch_pid" 2>/dev/null || true' EXIT INT TERM
"$runner" --i-understand-that-this-will-be-running-without-the-usual-guardrails \
  --logs-root "$acceptance_home/logs" --port "${SYMPHONY_PORT:-43190}" \
  "$acceptance_home/current/WORKFLOW.md" &
runner_pid=$!
echo "$runner_pid" > "$acceptance_home/runner.pid"
cleanup() {
  kill "$runner_pid" "$watch_pid" 2>/dev/null || true
  wait "$runner_pid" "$watch_pid" 2>/dev/null || true
  for pid_file in "$acceptance_home/runner.pid" "$acceptance_home/watch.pid"; do
    if test -f "$pid_file"; then
      recorded_pid=$(cat "$pid_file")
      if test "$recorded_pid" = "$runner_pid" || test "$recorded_pid" = "$watch_pid"; then
        rm -- "$pid_file"
      fi
    fi
  done
}
trap cleanup EXIT INT TERM
# Either child dying stops the pair; never leave dispatch running without recovery.
wait -n "$runner_pid" "$watch_pid"
