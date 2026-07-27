#!/usr/bin/env bash
# Remove local memory for Claude Code.
#
# WHAT THIS REMOVES
#   ~/.claude-local-memory/   the environment, the server, the model
#   the "memory" entry in Claude's MCP config
#
# WHAT THIS NEVER REMOVES
#   ~/.claude/local_memory/   your actual memories. They are your data. The
#                             path is printed at the end so you can delete
#                             them yourself if you want to.
#
# USAGE
#   ./uninstall.sh              show what would be removed, then remove it
#   ./uninstall.sh --dry-run    show only, change nothing
set -euo pipefail

PREFIX="$HOME/.claude-local-memory"
STORE_DIR="$HOME/.claude/local_memory"
MCP_NAME="${CLAUDE_MEMORY_MCP_NAME:-memory}"

DRY=0
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY=1 ;;
    -h|--help) sed -n '2,17p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

say() { printf '\n== %s\n' "$*"; }

say "What is on this machine"
if [ -d "$PREFIX" ]; then
  echo "  install:  $PREFIX ($(du -sh "$PREFIX" 2>/dev/null | cut -f1))"
else
  echo "  install:  not present"
fi
if command -v claude >/dev/null 2>&1 && claude mcp list 2>/dev/null | grep -qE "^${MCP_NAME}[: ]"; then
  echo "  claude:   \"$MCP_NAME\" is registered"
  REGISTERED=1
else
  echo "  claude:   \"$MCP_NAME\" is not registered"
  REGISTERED=0
fi
if [ -d "$STORE_DIR" ]; then
  echo "  memories: $STORE_DIR ($(du -sh "$STORE_DIR" 2>/dev/null | cut -f1)) — KEPT"
else
  echo "  memories: none stored"
fi

if [ "$DRY" = "1" ]; then
  printf '\nDry run. Nothing was changed.\n'
  exit 0
fi

say "Removing"
if [ "$REGISTERED" = "1" ]; then
  claude mcp remove -s user "$MCP_NAME" >/dev/null 2>&1 \
    && echo "  unregistered \"$MCP_NAME\" from Claude" \
    || echo "  could not unregister \"$MCP_NAME\" — remove it with: claude mcp remove -s user $MCP_NAME"
fi
if [ -d "$PREFIX" ]; then
  rm -rf "$PREFIX"
  echo "  removed $PREFIX"
fi

say "Verifying"
FAIL=0
[ -d "$PREFIX" ] && { echo "  STILL PRESENT: $PREFIX"; FAIL=1; }
if command -v claude >/dev/null 2>&1 && claude mcp list 2>/dev/null | grep -qE "^${MCP_NAME}[: ]"; then
  echo "  STILL REGISTERED: $MCP_NAME"; FAIL=1
fi
[ "$FAIL" = "0" ] && echo "  clean"

cat <<EOF

Your memories were NOT deleted. They are here:

  $STORE_DIR

Delete that folder yourself if you want them gone.
EOF
exit $FAIL
