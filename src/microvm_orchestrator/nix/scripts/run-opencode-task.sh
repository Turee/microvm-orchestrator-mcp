#!@bash@/bin/bash
set -euo pipefail

# Directory structure mirrors claude harness: /workspace contains repo/, task.md,
# start-ref, result.json, .api-key, opencode-config.json
WORKSPACE="/workspace"
REPO_DIR="$WORKSPACE/repo"
TASK_FILE="$WORKSPACE/task.md"
RESULT_FILE="$WORKSPACE/result.json"
API_KEY_FILE="$WORKSPACE/.api-key"
TASK_ID_FILE="$WORKSPACE/task-id"
STREAM_LOG_FILE="$WORKSPACE/opencode-stream.jsonl"
DEBUG_LOG_FILE="$WORKSPACE/task-runner.log"
OPENCODE_CONFIG_FILE="$WORKSPACE/opencode-config.json"
RESULT_WRITTEN=0
CHOWN_TIMEOUT_SEC="${CHOWN_TIMEOUT_SEC:-120}"
OPENCODE_LAUNCH_TIMEOUT_SEC="${OPENCODE_LAUNCH_TIMEOUT_SEC:-0}"

# Read task ID if available
TASK_ID=""
if [ -f "$TASK_ID_FILE" ]; then
  TASK_ID=$(cat "$TASK_ID_FILE")
fi

# Read model selection if available
MODEL=""
MODEL_FILE="$WORKSPACE/model"
if [ -f "$MODEL_FILE" ]; then
  MODEL=$(cat "$MODEL_FILE")
fi

# Avoid writing to /root/.gitconfig (stale lockfiles can break boot).
ROOT_GIT_CONFIG="/tmp/gitconfig-root"
export GIT_CONFIG_GLOBAL="$ROOT_GIT_CONFIG"
export GIT_CONFIG_NOSYSTEM=1
mkdir -p "$(dirname "$ROOT_GIT_CONFIG")" || true
cat > "$ROOT_GIT_CONFIG" <<EOF
[safe]
	directory = $REPO_DIR
EOF
chmod 600 "$ROOT_GIT_CONFIG" 2>/dev/null || true

# Print to the serial console as well (best-effort) without double-printing.
STDOUT_TARGET="$(readlink -f /proc/self/fd/1 2>/dev/null || true)"
is_serial_stdout() {
  [ -t 1 ] && return 0
  case "$STDOUT_TARGET" in
    /dev/console|/dev/hvc0|/dev/ttyS0|/dev/ttyAMA0|/dev/tty0|/dev/tty1) return 0 ;;
    *) return 1 ;;
  esac
}
emit() {
  local line="$1"
  echo "$line"

  if [ "${FORCE_CONSOLE_MIRROR:-0}" = "1" ]; then
    if [ -w /dev/console ]; then
      echo "$line" > /dev/console 2>/dev/null || true
    fi
    return 0
  fi

  if ! is_serial_stdout && [ -w /dev/console ]; then
    echo "$line" > /dev/console 2>/dev/null || true
  fi
}

# Timestamped logging to both console + file (no token printing).
log() {
  local msg="$1"
  local ts
  ts="$(date -u '+%Y-%m-%dT%H:%M:%SZ' 2>/dev/null || echo "unknown-time")"
  echo "[$ts] $msg" | tee -a "$DEBUG_LOG_FILE" >/dev/null
  emit "[$ts] $msg"
}

# Run a command with a timeout and kill it if it exceeds the deadline.
run_with_timeout() {
  local timeout_sec="$1"
  shift
  local step_name="$1"
  shift

  if [ "$timeout_sec" -le 0 ] 2>/dev/null; then
    log "DEBUG: $step_name timeout disabled; running without watchdog"
    "$@"
    return $?
  fi

  "$@" &
  local cmd_pid="$!"
  local elapsed=0

  while kill -0 "$cmd_pid" 2>/dev/null; do
    if [ "$elapsed" -ge "$timeout_sec" ]; then
      log "ERROR: $step_name timed out after ${timeout_sec}s (pid=$cmd_pid)"
      kill -TERM "$cmd_pid" 2>/dev/null || true
      sleep 2
      kill -KILL "$cmd_pid" 2>/dev/null || true
      wait "$cmd_pid" 2>/dev/null || true
      return 124
    fi
    sleep 1
    elapsed=$((elapsed + 1))
  done

  wait "$cmd_pid"
}

# Only run the task from the serial console login shell (hvc0).
TTY_DEVICE="$(tty 2>/dev/null || true)"
case "$TTY_DEVICE" in
  /dev/hvc0)
    ;;
  *)
    exit 0
    ;;
esac

# Ensure we only run the task once per boot.
RUN_ONCE_LOCK_DIR="/run/opencode-task-runner.started"
if ! mkdir "$RUN_ONCE_LOCK_DIR" 2>/dev/null; then
  exit 0
fi

# Best-effort: if we crash, still write a result.json and persist logs.
on_error() {
  local exit_code="$?"
  trap - ERR
  set +e
  log "ERROR: task runner failed (exit=$exit_code)"
  log "DEBUG: last 200 lines of $DEBUG_LOG_FILE (if present):"
  tail -200 "$DEBUG_LOG_FILE" 2>/dev/null || true
  if [ -w /dev/console ]; then
    tail -200 "$DEBUG_LOG_FILE" > /dev/console 2>/dev/null || true
  fi

  if [ "$RESULT_WRITTEN" -eq 0 ]; then
    local err_tail=""
    err_tail=$(tail -200 "$DEBUG_LOG_FILE" 2>/dev/null || echo "No debug log available")
    write_result false "Task runner crashed" "$err_tail" "$exit_code"
  fi

  exit "$exit_code"
}
trap on_error ERR

# Function to write result JSON with commit info
write_result() {
  local success="$1"
  local summary="$2"
  local error="$3"
  local runner_exit_code="${4:-0}"

  cd "$REPO_DIR"
  local files_changed
  local start_ref=""
  if [ -f "$WORKSPACE/start-ref" ]; then
    start_ref=$(cat "$WORKSPACE/start-ref")
  fi

  if [ -n "$start_ref" ]; then
    files_changed=$(@git@/bin/git diff --name-only "$start_ref..HEAD" 2>/dev/null | @jq@/bin/jq -R -s -c 'split("\n") | map(select(length > 0))')
  else
    files_changed=$(@git@/bin/git status --porcelain 2>/dev/null | @gawk@/bin/awk '{print $2}' | @jq@/bin/jq -R -s -c 'split("\n") | map(select(length > 0))')
  fi

  local commit_count=0
  local commits="[]"
  if [ -n "$start_ref" ]; then
    commit_count=$(@git@/bin/git rev-list --count "$start_ref..HEAD" 2>/dev/null || echo "0")
    if [ "$commit_count" -gt 0 ]; then
      commits=$(@git@/bin/git log --oneline "$start_ref..HEAD" 2>/dev/null | @jq@/bin/jq -R -s -c 'split("\n") | map(select(length > 0))')
    fi
  fi

  @jq@/bin/jq -n \
    --argjson success "$success" \
    --arg summary "$summary" \
    --argjson files_changed "$files_changed" \
    --argjson commit_count "$commit_count" \
    --argjson commits "$commits" \
    --arg error "$error" \
    --arg stream_log_file "$STREAM_LOG_FILE" \
    --arg debug_log_file "$DEBUG_LOG_FILE" \
    --argjson runner_exit_code "$runner_exit_code" \
    '{success: $success, summary: $summary, files_changed: $files_changed, commit_count: $commit_count, commits: $commits, stream_log_file: $stream_log_file, debug_log_file: $debug_log_file, runner_exit_code: $runner_exit_code, error: (if $error == "" then null else $error end)}' \
    > "$RESULT_FILE"

  RESULT_WRITTEN=1
}

# Check for task file
if [ ! -f "$TASK_FILE" ]; then
  log "ERROR: No task file found at $TASK_FILE"
  write_result false "No task file found" "Task file not found at $TASK_FILE" 1
  exit 1
fi

# Check for repo directory
if [ ! -d "$REPO_DIR" ]; then
  log "ERROR: No repository found at $REPO_DIR"
  write_result false "No repository found" "Repository not found at $REPO_DIR" 1
  exit 1
fi

# Check for flake.nix (required for nix develop)
if [ ! -f "$REPO_DIR/flake.nix" ]; then
  log "ERROR: No flake.nix found at $REPO_DIR/flake.nix"
  write_result false "No flake.nix found" "Repository must contain flake.nix at root for nix develop environment." 1
  exit 1
fi

# Read task
TASK=$(cat "$TASK_FILE")
log "Task: $TASK"

# Debug: environment + versions (safe)
log "DEBUG: uname=$(uname -a 2>/dev/null || true)"
log "DEBUG: opencode=$(@opencode@/bin/opencode --version 2>/dev/null || echo 'missing')"
log "DEBUG: git=$(@git@/bin/git --version 2>/dev/null || echo 'missing')"
log "DEBUG: jq=$(@jq@/bin/jq --version 2>/dev/null || echo 'missing')"

log "DEBUG: Contents of $WORKSPACE:"
ls -la "$WORKSPACE/" | tee -a "$DEBUG_LOG_FILE" || log "Failed to list workspace"

log "DEBUG: Repo status (porcelain):"
(@git@/bin/git -C "$REPO_DIR" status --porcelain || true) | tee -a "$DEBUG_LOG_FILE"

if [ -f "$WORKSPACE/start-ref" ]; then
  log "DEBUG: start-ref=$(cat "$WORKSPACE/start-ref" 2>/dev/null || true)"
fi

if [ ! -f "$API_KEY_FILE" ]; then
  log "ERROR: Missing API token file at $API_KEY_FILE"
  write_result false "Missing API token" "Expected API token at $API_KEY_FILE" 1
  exit 1
fi

# Move token to /tmp for claude user (never print it).
TOKEN="$(cat "$API_KEY_FILE" || true)"
if [ -z "$TOKEN" ]; then
  log "ERROR: API token file was empty"
  write_result false "Empty API token" "API token file was empty: $API_KEY_FILE" 1
  exit 1
fi
TMP_TOKEN="/tmp/opencode_api_key"
printf '%s' "$TOKEN" > "$TMP_TOKEN"
rm -f "$API_KEY_FILE"
unset TOKEN
chown claude "$TMP_TOKEN" 2>/dev/null || true
chmod 600 "$TMP_TOKEN" 2>/dev/null || true

TASK=$(cat "/workspace/task.md")

rm -f "$STREAM_LOG_FILE"
touch "$STREAM_LOG_FILE"
chmod 666 "$STREAM_LOG_FILE"
log "Saving opencode output to $STREAM_LOG_FILE"

log "Running opencode as user claude..."

CLAUDE_UID=1000
CLAUDE_RUNTIME_DIR="/run/user/$CLAUDE_UID"
mkdir -p "$CLAUDE_RUNTIME_DIR"
chown claude:users "$CLAUDE_RUNTIME_DIR"
chmod 700 "$CLAUDE_RUNTIME_DIR"

# Chown repo root + .git first, escalate if write probe fails.
log "DEBUG: ownership stage start"
if ! run_with_timeout "$CHOWN_TIMEOUT_SEC" "chown repo root" chown claude:users "$REPO_DIR"; then
  write_result false "Task launch failed" "Timed out or failed while chowning repo root ($REPO_DIR)" 1
  exit 1
fi
if [ -d "$REPO_DIR/.git" ]; then
  if ! run_with_timeout "$CHOWN_TIMEOUT_SEC" "chown .git metadata" chown -R claude:users "$REPO_DIR/.git"; then
    write_result false "Task launch failed" "Timed out or failed while chowning $REPO_DIR/.git" 1
    exit 1
  fi
fi
log "DEBUG: ownership stage complete (root + .git)"

if ! su -s @bash@/bin/bash claude -c "cd \"$REPO_DIR\" && test -w . && test -w .git && touch .microvm-write-probe.$$ && rm -f .microvm-write-probe.$$"; then
  log "DEBUG: ownership probe failed; escalating to recursive chown"
  if ! run_with_timeout "$CHOWN_TIMEOUT_SEC" "recursive repo chown fallback" chown -R claude:users "$REPO_DIR"; then
    write_result false "Task launch failed" "Timed out or failed while recursively chowning repo ($REPO_DIR)" 1
    exit 1
  fi
fi
log "DEBUG: ownership verification complete"

# Probe native devShell up front.
NIX_PROBE_STDERR="/tmp/nix-develop-test.err"
NIX_PROBE_STDOUT="/tmp/nix-develop-test.out"
DEV_SHELL_TARGET="path:."
log "DEBUG: native devShell probe start"
if su -s @bash@/bin/bash claude -c "cd \"$REPO_DIR\" && @nix@/bin/nix develop path:. --command true" >"$NIX_PROBE_STDOUT" 2>"$NIX_PROBE_STDERR"; then
  log "DEBUG: native devShell probe succeeded"
else
  log "DEBUG: native devShell probe failed; falling back to x86_64-linux"
  if [ -f "$NIX_PROBE_STDERR" ]; then
    log "DEBUG: native probe stderr tail:"
    tail -50 "$NIX_PROBE_STDERR" | tee -a "$DEBUG_LOG_FILE" >/dev/null || true
  fi
  DEV_SHELL_TARGET="path:.#devShells.x86_64-linux.default"
fi

# Create a wrapper to set env variables and run opencode inside repo as claude.
WRAPPER="$(mktemp /tmp/opencode-wrapper.XXXXXX)"
cat <<EOF > "$WRAPPER"
#!@bash@/bin/bash
set -euo pipefail

export HOME="/home/claude"
export USER="claude"
export LOGNAME="claude"
export XDG_RUNTIME_DIR="$CLAUDE_RUNTIME_DIR"

export GIT_CONFIG_COUNT=1
export GIT_CONFIG_KEY_0="safe.directory"
export GIT_CONFIG_VALUE_0="*"
export GIT_CONFIG_NOSYSTEM=1
export GIT_CONFIG_GLOBAL="/tmp/gitconfig-claude"

cat > "\$GIT_CONFIG_GLOBAL" <<EOGIT
[safe]
	directory = *
EOGIT
chmod 600 "\$GIT_CONFIG_GLOBAL" 2>/dev/null || true

# Token: detect type and set the appropriate env var.
if [ -f "$TMP_TOKEN" ]; then
  TOKEN=\$(cat "$TMP_TOKEN" || true)
  if [ -n "\$TOKEN" ]; then
    case "\$TOKEN" in
      sk-ant-oat*)
        export ANTHROPIC_API_KEY="\$TOKEN"
        ;;
      sk-ant-*)
        export ANTHROPIC_API_KEY="\$TOKEN"
        ;;
      sk-*)
        export OPENAI_API_KEY="\$TOKEN"
        ;;
      *)
        export ANTHROPIC_API_KEY="\$TOKEN"
        ;;
    esac
  fi
  rm -f "$TMP_TOKEN"
fi

# Install opencode config for the claude user.
if [ -f "$OPENCODE_CONFIG_FILE" ]; then
  cp "$OPENCODE_CONFIG_FILE" "\$HOME/.opencode.json"
fi

cd "$REPO_DIR"
TASK=\$(cat "/workspace/task.md")

echo "Using devShell target: $DEV_SHELL_TARGET" >&2
exec @nix@/bin/nix develop $DEV_SHELL_TARGET --command @opencode@/bin/opencode -p "\$TASK" -f json -q
EOF
chmod 755 "$WRAPPER"

LAUNCH_SCRIPT="$(mktemp /tmp/opencode-launch.XXXXXX)"
LAUNCH_EXIT_FILE="$(mktemp /tmp/opencode-launch-exit.XXXXXX)"
cat <<EOF > "$LAUNCH_SCRIPT"
#!@bash@/bin/bash
set -o pipefail
su -s @bash@/bin/bash claude -c "$WRAPPER" 2>&1 | tee "$STREAM_LOG_FILE"
echo "\${PIPESTATUS[0]}" > "$LAUNCH_EXIT_FILE"
EOF
chmod 755 "$LAUNCH_SCRIPT"

log "DEBUG: opencode launch stage start (timeout=${OPENCODE_LAUNCH_TIMEOUT_SEC}s)"
if run_with_timeout "$OPENCODE_LAUNCH_TIMEOUT_SEC" "opencode launch pipeline" @bash@/bin/bash "$LAUNCH_SCRIPT"; then
  launch_rc=0
else
  launch_rc="$?"
fi
if [ "$launch_rc" -eq 124 ]; then
  rm -f "$WRAPPER" "$LAUNCH_SCRIPT" "$LAUNCH_EXIT_FILE" 2>/dev/null || true
  write_result false "Task launch timeout" "Timed out running opencode launch pipeline after ${OPENCODE_LAUNCH_TIMEOUT_SEC}s" 124
  exit 124
fi
log "DEBUG: opencode launch stage complete"

OPENCODE_EXIT="$(cat "$LAUNCH_EXIT_FILE" 2>/dev/null || echo "1")"
rm -f "$WRAPPER" "$LAUNCH_SCRIPT" "$LAUNCH_EXIT_FILE" 2>/dev/null || true

if [ "$OPENCODE_EXIT" -eq 0 ]; then
  # Parse JSON output from opencode (-f json mode).
  # opencode outputs a JSON object at the end; extract the last assistant message or summary.
  SUMMARY=$(@jq@/bin/jq -rs 'map(select(.role == "assistant")) | last | .content // empty' "$STREAM_LOG_FILE" 2>/dev/null || true)
  if [ -z "$SUMMARY" ]; then
    # Fallback: try result field
    SUMMARY=$(@jq@/bin/jq -rs 'map(select(.type == "result")) | last | .result // empty' "$STREAM_LOG_FILE" 2>/dev/null || true)
  fi
  if [ -z "$SUMMARY" ]; then
    SUMMARY=$(tail -50 "$STREAM_LOG_FILE")
  fi
  write_result true "$SUMMARY" "" 0
  log "Task completed successfully"
else
  ERROR=$(tail -200 "$STREAM_LOG_FILE" 2>/dev/null || echo "No stream output captured")
  write_result false "Task failed" "$ERROR" "$OPENCODE_EXIT"
  log "Task failed (opencode exit=$OPENCODE_EXIT)"
fi
