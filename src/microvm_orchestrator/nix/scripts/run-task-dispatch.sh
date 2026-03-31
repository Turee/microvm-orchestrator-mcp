#!@bash@/bin/bash
set -euo pipefail

HARNESS="$(cat /workspace/harness 2>/dev/null || echo "claude-code")"

case "$HARNESS" in
  claude-code) exec @runClaudeTask@ ;;
  opencode)    exec @runOpenCodeTask@ ;;
  *)           echo "ERROR: Unknown harness: $HARNESS"; exit 1 ;;
esac
