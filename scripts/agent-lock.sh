#!/usr/bin/env bash
# Agent lock helper for devlog-cli (multi-session coordination)
set -euo pipefail

LOCK_DIR=".agent-locks"
mkdir -p "$LOCK_DIR"
AGENT_ID="${HERMES_SESSION_ID:-$(hostname)-$$}"
TTL_MINUTES=30

claim() {
  local file="$1" desc="$2"
  local lock_file="$LOCK_DIR/${file//\//__}.lock"
  local started=$(date -Iseconds)
  local expires
  expires=$(date -d "+$TTL_MINUTES minutes" -Iseconds 2>/dev/null || date -v+${TTL_MINUTES}M -Iseconds)

  if [[ -f "$lock_file" ]]; then
    local existing_expires
    existing_expires=$(jq -r '.expires' "$lock_file" 2>/dev/null || echo "")
    if [[ -n "$existing_expires" && "$existing_expires" > "$(date -Iseconds)" ]]; then
      echo "LOCKED: $file (expires $existing_expires)" >&2
      cat "$lock_file" | jq . >&2
      return 1
    fi
  fi

  cat > "$lock_file" <<EOF
{"agent-id":"$AGENT_ID","task":"$desc","started":"$started","expires":"$expires"}
EOF
  echo "CLAIMED: $file (expires $expires)"
}

release() {
  local file="$1"
  local lock_file="$LOCK_DIR/${file//\//__}.lock"
  [[ -f "$lock_file" ]] && rm "$lock_file" && echo "RELEASED: $file" || echo "NOT LOCKED: $file"
}

check() {
  local file="${1:-}"
  if [[ -n "$file" ]]; then
    local lock_file="$LOCK_DIR/${file//\//__}.lock"
    [[ -f "$lock_file" ]] && cat "$lock_file" | jq . || echo "No lock: $file"
  else
    shopt -s nullglob
    for f in "$LOCK_DIR"/*.lock; do
      [[ -f "$f" ]] || continue
      local path_name
      path_name=$(basename "$f" .lock | sed 's/__/\//g')
      echo "=== $path_name ==="
      cat "$f" | jq .
    done
  fi
}

expire() {
  local now=$(date -Iseconds)
  shopt -s nullglob
  for f in "$LOCK_DIR"/*.lock; do
    [[ -f "$f" ]] || continue
    local exp=$(jq -r '.expires' "$f" 2>/dev/null || echo "")
    if [[ -n "$exp" && "$exp" < "$now" ]]; then
      local path_name=$(basename "$f" .lock | sed 's/__/\//g')
      echo "EXPIRED: $path_name"
      rm "$f"
    fi
  done
}

case "${1:-}" in
  claim) claim "$2" "$3" ;;
  release) release "$2" ;;
  check) check "${2:-}" ;;
  expire) expire ;;
  *) echo "Usage: agent-lock <claim|release|check|expire> [file] [desc]"; exit 1 ;;
esac