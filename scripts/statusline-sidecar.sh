#!/bin/bash
# Statusline sidecar script for Claude Code.
# Reads statusline JSON from stdin, writes to $XDG_RUNTIME_DIR/claude-monitor/<session>.json,
# and outputs a summary line to stdout for Zellij status bar.
set -euo pipefail

input=$(cat)

# Validate input is valid JSON
if ! echo "$input" | jq empty 2>/dev/null; then
    echo "Error: invalid JSON input" >&2
    exit 1
fi

# Identity resolution: CWD basename -> session_id -> unknown-PID
IDENT=""
CWD_VAL=$(echo "$input" | jq -r '.cwd // empty')
if [ -n "$CWD_VAL" ]; then
    IDENT=$(basename "$CWD_VAL")
fi
if [ -z "$IDENT" ]; then
    IDENT=$(echo "$input" | jq -r '.session_id // empty')
fi
# Sanitize: alphanumeric + hyphen + underscore + dot only
IDENT=$(echo "$IDENT" | tr -cd 'a-zA-Z0-9_.-')
if [ -z "$IDENT" ]; then
    IDENT="unknown-$$"
    echo "Warning: no session identity found, using $IDENT" >&2
fi

MONITOR_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/claude-monitor"

if ! mkdir -p "$MONITOR_DIR"; then
    echo "Error: failed to create $MONITOR_DIR" >&2
    exit 1
fi
if ! chmod 700 "$MONITOR_DIR"; then
    echo "Error: failed to set permissions on $MONITOR_DIR" >&2
    exit 1
fi

# Atomic write: tmp file then mv
TMP_FILE="${MONITOR_DIR}/.${IDENT}.tmp"
if ! echo "$input" > "$TMP_FILE"; then
    echo "Error: failed to write $TMP_FILE" >&2
    exit 1
fi
if ! mv "$TMP_FILE" "${MONITOR_DIR}/${IDENT}.json"; then
    echo "Error: failed to move $TMP_FILE to ${MONITOR_DIR}/${IDENT}.json" >&2
    exit 1
fi

# Output summary for Zellij status bar
MODEL=$(echo "$input" | jq -r '.model.display_name? // "?"')
PCT=$(echo "$input" | jq -r '.context_window.used_percentage? // 0' | cut -d. -f1)
COST=$(printf '$%.2f' "$(echo "$input" | jq -r '.cost.total_cost_usd? // 0')")
echo "[${MODEL}] ${PCT}% | ${COST}"
