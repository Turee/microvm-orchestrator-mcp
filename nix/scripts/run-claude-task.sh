#!@bash@/bin/bash
set -euo pipefail

# New directory structure: /workspace contains repo/, task.md, start-ref, result.json
WORKSPACE="/workspace"
REPO_DIR="$WORKSPACE/repo"
TASK_FILE="$WORKSPACE/task.md"
RESULT_FILE="$WORKSPACE/result.json"
API_KEY_FILE="$WORKSPACE/.api-key"
TASK_ID_FILE="$WORKSPACE/task-id"
STREAM_LOG_FILE="$WORKSPACE/claude-stream.jsonl"
DEBUG_LOG_FILE="$WORKSPACE/task-runner.log"
RESULT_WRITTEN=0

# Read task ID if available
TASK_ID=""
if [ -f "$TASK_ID_FILE" ]; then
  TASK_ID=$(cat "$TASK_ID_FILE")
fi

# Avoid writing to /root/.gitconfig (stale lockfiles can break boot).
# Instead, provide safe.directory via an isolated global git config file.
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
# In most microvm setups stdout is already the serial console (e.g. /dev/hvc0),
# so we only also write to /dev/console when stdout is *not* already a console.
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

  # Allow forcing mirror for unusual runner setups.
  if [ "${FORCE_CONSOLE_MIRROR:-0}" = "1" ]; then
    if [ -w /dev/console ]; then
      echo "$line" > /dev/console 2>/dev/null || true
    fi
    return 0
  fi

  # Avoid double-printing when stdout already goes to serial console.
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

# Only run the task from the serial console login shell (hvc0).
# This avoids multiple gettys (e.g. tty1) starting multiple task runners and
# ensures the task output is visible on the serial console.
TTY_DEVICE="$(tty 2>/dev/null || true)"
case "$TTY_DEVICE" in
  /dev/hvc0)
    ;;
  *)
    # Not on serial console; do nothing and let the serial getty run it.
    exit 0
    ;;
esac

# This script can still be invoked multiple times *on the serial console*.
# Ensure we only run the task once per boot.
RUN_ONCE_LOCK_DIR="/run/claude-task-runner.started"
if ! mkdir "$RUN_ONCE_LOCK_DIR" 2>/dev/null; then
  exit 0
fi

# Best-effort: if we crash, still write a result.json and persist logs.
on_error() {
  local exit_code="$?"
  # Avoid recursive ERR handling
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

# Function to get commit info
get_commit_info() {
  cd "$REPO_DIR"
  local start_ref=""
  if [ -f "$WORKSPACE/start-ref" ]; then
    start_ref=$(cat "$WORKSPACE/start-ref")
  fi

  # Get commit count and list
  local commit_count=0
  local commits="[]"
  if [ -n "$start_ref" ]; then
    commit_count=$(@git@/bin/git rev-list --count "$start_ref..HEAD" 2>/dev/null || echo "0")
    if [ "$commit_count" -gt 0 ]; then
      commits=$(@git@/bin/git log --oneline "$start_ref..HEAD" 2>/dev/null | @jq@/bin/jq -R -s -c 'split("\n") | map(select(length > 0))')
    fi
  fi

  echo "$commit_count"
  echo "$commits"
}

# Function to write result JSON with commit info
write_result() {
  local success="$1"
  local summary="$2"
  local error="$3"
  local runner_exit_code="${4:-0}"

  # Get list of changed files from git
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

  # Get commit info
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
  write_result false "No flake.nix found" "Repository must contain flake.nix at root for nix develop environment. See nix_flake_plan.md for requirements." 1
  exit 1
fi

# Read task
TASK=$(cat "$TASK_FILE")
log "Task: $TASK"

# Debug: environment + versions (safe)
log "DEBUG: uname=$(uname -a 2>/dev/null || true)"
log "DEBUG: node=$(@nodejs@/bin/node --version 2>/dev/null || echo 'missing')"
log "DEBUG: npx=$(@nodejs@/bin/npx --version 2>/dev/null || echo 'missing')"
log "DEBUG: git=$(@git@/bin/git --version 2>/dev/null || echo 'missing')"
log "DEBUG: jq=$(@jq@/bin/jq --version 2>/dev/null || echo 'missing')"
log "DEBUG: grep=$(@gnugrep@/bin/grep --version 2>/dev/null | head -1 || echo 'missing')"

log "DEBUG: Contents of $WORKSPACE:"
ls -la "$WORKSPACE/" | tee -a "$DEBUG_LOG_FILE" || log "Failed to list workspace"

log "DEBUG: Repo status (porcelain):"
(@git@/bin/git -C "$REPO_DIR" status --porcelain || true) | tee -a "$DEBUG_LOG_FILE"

if [ -f "$WORKSPACE/start-ref" ]; then
  log "DEBUG: start-ref=$(cat "$WORKSPACE/start-ref" 2>/dev/null || true)"
fi

if [ ! -f "$API_KEY_FILE" ]; then
  log "ERROR: Missing API token file at $API_KEY_FILE (this file is deleted after each run; re-create it to re-run this taskdir)"
  write_result false "Missing API token" "Expected API token at $API_KEY_FILE (note: it is removed after each run; re-create it to re-run this taskdir)" 1
  exit 1
fi

# Move token to /tmp for claude user (never print it).
TOKEN="$(cat "$API_KEY_FILE" || true)"
if [ -z "$TOKEN" ]; then
  log "ERROR: API token file was empty"
  write_result false "Empty API token" "API token file was empty: $API_KEY_FILE" 1
  exit 1
fi
TMP_TOKEN="/tmp/claude_api_key"
printf '%s' "$TOKEN" > "$TMP_TOKEN"
rm -f "$API_KEY_FILE"
unset TOKEN
chown claude "$TMP_TOKEN" 2>/dev/null || true
chmod 600 "$TMP_TOKEN" 2>/dev/null || true

TASK=$(cat "/workspace/task.md")

rm -f "$STREAM_LOG_FILE"
touch "$STREAM_LOG_FILE"
chmod 666 "$STREAM_LOG_FILE"
log "Saving Claude stream output to $STREAM_LOG_FILE"

log "Running Claude Code as user claude (required for --dangerously-skip-permissions)..."

# Set up XDG_RUNTIME_DIR for rootless Podman (systemd-logind doesn't create it for su sessions)
CLAUDE_UID=1000
CLAUDE_RUNTIME_DIR="/run/user/$CLAUDE_UID"
mkdir -p "$CLAUDE_RUNTIME_DIR"
chown claude:users "$CLAUDE_RUNTIME_DIR"
chmod 700 "$CLAUDE_RUNTIME_DIR"

# Create a wrapper so we can set env + run inside repo as claude.
WRAPPER="$(mktemp /tmp/claude-wrapper.XXXXXX)"
cat <<EOF > "$WRAPPER"
#!@bash@/bin/bash
set -euo pipefail

export HOME="/home/claude"
export USER="claude"
export LOGNAME="claude"
export XDG_RUNTIME_DIR="$CLAUDE_RUNTIME_DIR"
export GIT_CONFIG_NOSYSTEM=1
export GIT_CONFIG_GLOBAL="/tmp/gitconfig-claude"

# Ensure git trusts the repo without touching ~/.gitconfig (no locks/stale state).
cat > "\$GIT_CONFIG_GLOBAL" <<EOGIT
[safe]
	directory = $REPO_DIR
EOGIT
chmod 600 "\$GIT_CONFIG_GLOBAL" 2>/dev/null || true

# Token: detect type, set correct env var, and delete token file.
if [ -f "$TMP_TOKEN" ]; then
  TOKEN=\$(cat "$TMP_TOKEN" || true)
  if [ -n "\$TOKEN" ]; then
    case "\$TOKEN" in
      sk-ant-oat*)
        export CLAUDE_CODE_OAUTH_TOKEN="\$TOKEN"
        ;;
      *)
        export ANTHROPIC_API_KEY="\$TOKEN"
        ;;
    esac
  fi
  rm -f "$TMP_TOKEN"
fi

cd "$REPO_DIR"
TASK=\$(cat "/workspace/task.md")
exec @nix@/bin/nix develop . --command @nodejs@/bin/npx -y @anthropic-ai/claude-code@latest --dangerously-skip-permissions --output-format stream-json --verbose -p "\$TASK"
EOF
chmod 755 "$WRAPPER"

# Run and capture the *real* exit code of su (wrapper).
set +e
su -s @bash@/bin/bash claude -c "$WRAPPER" 2>&1 | tee "$STREAM_LOG_FILE"
CLAUDE_EXIT="${PIPESTATUS[0]}"
set -e
rm -f "$WRAPPER" 2>/dev/null || true

if [ "$CLAUDE_EXIT" -eq 0 ]; then
  # Parse stream-json output
  SUMMARY=$(@gnugrep@/bin/grep -E '^\{' "$STREAM_LOG_FILE" | @jq@/bin/jq -rs 'map(select(.type == "result")) | last | .result // empty' 2>/dev/null)
  if [ -z "$SUMMARY" ]; then
    # Fallback: get last assistant message
    SUMMARY=$(@gnugrep@/bin/grep -E '^\{' "$STREAM_LOG_FILE" | @jq@/bin/jq -rs 'map(select(.type == "assistant")) | last | .message.content // empty' 2>/dev/null)
  fi
  if [ -z "$SUMMARY" ]; then
    # Last resort: last 50 lines of output
    SUMMARY=$(tail -50 "$STREAM_LOG_FILE")
  fi
  write_result true "$SUMMARY" "" 0
  log "Task completed successfully"
else
  # On failure, capture tail of stream output
  ERROR=$(tail -200 "$STREAM_LOG_FILE" 2>/dev/null || echo "No stream output captured")
  write_result false "Task failed" "$ERROR" "$CLAUDE_EXIT"
  log "Task failed (claude exit=$CLAUDE_EXIT)"
fi
