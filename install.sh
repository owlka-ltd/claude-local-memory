#!/usr/bin/env bash
# Install local memory for Claude Code.
#
# WHAT THIS PUTS ON THE MACHINE
#   ~/.claude-local-memory/venv/     a private Python environment
#   ~/.claude-local-memory/memory/   the memory server source
#   ~/.claude-local-memory/model/    the ~30 MB embedding model
#   ~/.claude-local-memory/run.sh    the launcher Claude is pointed at
#
# WHERE THE MEMORIES THEMSELVES LIVE
#   ~/.claude/local_memory/<partition>.db
#   That is user data. This installer never deletes it, and neither does
#   uninstall.sh.
#
# OFFLINE
#   If this directory carries a `vendor/` folder (produced by build.sh), the
#   install needs NO network at all: the Python packages come from
#   vendor/wheels and the model from vendor/model. Without vendor/, it falls
#   back to downloading from PyPI and Hugging Face.
#
# USAGE
#   ./install.sh                 install (offline if vendor/ is present)
#   ./install.sh --allow-download  permit downloads when vendor/ is absent
#   ./install.sh --force         overwrite an existing "memory" MCP entry
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PREFIX="$HOME/.claude-local-memory"
VENV="$PREFIX/venv"
SRC_DIR="$PREFIX/memory"
MODEL_DIR="$PREFIX/model"
LAUNCHER="$PREFIX/run.sh"
MCP_NAME="${CLAUDE_MEMORY_MCP_NAME:-memory}"
STORE_DIR="$HOME/.claude/local_memory"

VENDOR_WHEELS="$SCRIPT_DIR/vendor/wheels"
VENDOR_MODEL="$SCRIPT_DIR/vendor/model"

ALLOW_DOWNLOAD=0
FORCE=0
for arg in "$@"; do
  case "$arg" in
    --allow-download) ALLOW_DOWNLOAD=1 ;;
    --force)          FORCE=1 ;;
    -h|--help)        sed -n '2,26p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

say() { printf '\n== %s\n' "$*"; }
die() { printf '\nFAILED: %s\n' "$*" >&2; exit 1; }

# ─────────────────────────────────────────────────────────────────────────────
# 1. Find a Python 3.11+
# ─────────────────────────────────────────────────────────────────────────────
say "Looking for Python 3.11 or newer"
PY=""
for c in python3.13 python3.12 python3.11 python3; do
  p="$(command -v "$c" 2>/dev/null || true)"
  [ -n "$p" ] || continue
  if "$p" -c 'import sys; sys.exit(0 if sys.version_info[:2] >= (3,11) else 1)' 2>/dev/null; then
    PY="$p"; break
  fi
done
[ -n "$PY" ] || die "no Python 3.11+ found. Install one (e.g. from python.org) and re-run."
echo "using $PY ($("$PY" -V 2>&1))"

# ─────────────────────────────────────────────────────────────────────────────
# 2. Private environment
# ─────────────────────────────────────────────────────────────────────────────
say "Creating a private Python environment"
mkdir -p "$PREFIX"
if [ ! -x "$VENV/bin/python3" ]; then
  "$PY" -m venv "$VENV" || die "could not create the environment at $VENV"
else
  echo "reusing $VENV"
fi
VPY="$VENV/bin/python3"
"$VPY" -m pip install --quiet --upgrade pip >/dev/null 2>&1 || true

# ─────────────────────────────────────────────────────────────────────────────
# 3. Dependencies — offline from vendor/wheels when present
# ─────────────────────────────────────────────────────────────────────────────
say "Installing the supporting packages"
LOCK="$SCRIPT_DIR/requirements.lock.txt"
[ -f "$LOCK" ] || die "requirements.lock.txt is missing next to install.sh"

if [ -d "$VENDOR_WHEELS" ]; then
  echo "offline: using the packages bundled in vendor/wheels"
  "$VPY" -m pip install --quiet --no-index --find-links "$VENDOR_WHEELS" \
      -r "$LOCK" || die "offline package install failed"
elif [ "$ALLOW_DOWNLOAD" = "1" ]; then
  echo "downloading packages (every one checked against a pinned hash)"
  "$VPY" -m pip install --quiet --require-hashes -r "$LOCK" \
      || die "package download failed"
else
  die "no vendor/wheels bundle found. Re-run with --allow-download to fetch from PyPI, or use a bundle built by build.sh."
fi

# ─────────────────────────────────────────────────────────────────────────────
# 4. The memory server source
# ─────────────────────────────────────────────────────────────────────────────
say "Installing the memory server"
rm -rf "$SRC_DIR"
mkdir -p "$SRC_DIR"
cp "$SCRIPT_DIR/memory/"*.py "$SRC_DIR/" || die "could not copy the memory source"

# ─────────────────────────────────────────────────────────────────────────────
# 5. The embedding model
# ─────────────────────────────────────────────────────────────────────────────
say "Installing the meaning-search model"
if [ -d "$VENDOR_MODEL" ]; then
  rm -rf "$MODEL_DIR"
  mkdir -p "$MODEL_DIR"
  cp -R "$VENDOR_MODEL/." "$MODEL_DIR/" || die "could not copy the bundled model"
  echo "offline: model installed from vendor/model"
  MODEL_ARG="$MODEL_DIR"
elif [ -d "$MODEL_DIR" ] && [ -f "$MODEL_DIR/model.safetensors" ]; then
  echo "reusing the model already at $MODEL_DIR"
  MODEL_ARG="$MODEL_DIR"
elif [ "$ALLOW_DOWNLOAD" = "1" ]; then
  echo "no bundled model; it will be fetched once and checked against its pinned hash"
  MODEL_ARG=""
else
  die "no vendor/model bundle found. Re-run with --allow-download, or use a bundle built by build.sh."
fi

# ─────────────────────────────────────────────────────────────────────────────
# 6. The launcher Claude runs
# ─────────────────────────────────────────────────────────────────────────────
say "Writing the launcher"
{
  echo '#!/usr/bin/env bash'
  echo '# Launcher for local Claude memory. Written by install.sh — edit the'
  echo '# repo and re-run the installer rather than editing this file.'
  echo 'set -euo pipefail'
  printf 'export CLAUDE_MEMORY_EMBED_MODEL=%q\n' "$MODEL_DIR"
  echo '# Never reach for the network at runtime.'
  echo 'export HF_HUB_OFFLINE=1'
  echo 'export TRANSFORMERS_OFFLINE=1'
  printf 'cd %q\n' "$PREFIX"
  printf 'exec %q -m memory.server "$@"\n' "$VPY"
} > "$LAUNCHER"
chmod +x "$LAUNCHER"

# `python -m memory.server` needs memory/ to be an importable package.
[ -f "$SRC_DIR/__init__.py" ] || : > "$SRC_DIR/__init__.py"

# ─────────────────────────────────────────────────────────────────────────────
# 7. Prove the model is real and loads
# ─────────────────────────────────────────────────────────────────────────────
say "Checking the model"
PROVISION_FLAGS=()
[ "$ALLOW_DOWNLOAD" = "1" ] && PROVISION_FLAGS+=(--allow-download)
(
  cd "$PREFIX"
  CLAUDE_MEMORY_EMBED_MODEL="$MODEL_ARG" \
    "$VPY" -m memory.provision_model "${PROVISION_FLAGS[@]+"${PROVISION_FLAGS[@]}"}"
) || die "the meaning-search model could not be verified"

# If the model was downloaded rather than bundled, copy it into the install so
# the launcher's offline path finds it next time.
if [ -z "$MODEL_ARG" ]; then
  CACHED="$("$VPY" - <<'PYEOF'
from huggingface_hub import snapshot_download
try:
    print(snapshot_download("minishlab/potion-base-8M", local_files_only=True))
except Exception:
    print("")
PYEOF
)"
  if [ -n "$CACHED" ] && [ -d "$CACHED" ]; then
    rm -rf "$MODEL_DIR"; mkdir -p "$MODEL_DIR"
    cp -RL "$CACHED/." "$MODEL_DIR/" 2>/dev/null || true
    echo "model copied into $MODEL_DIR (offline from now on)"
  fi
fi

# ─────────────────────────────────────────────────────────────────────────────
# 8. Tell Claude about it
# ─────────────────────────────────────────────────────────────────────────────
say "Registering with Claude Code"
if ! command -v claude >/dev/null 2>&1; then
  cat <<EOF
Claude Code was not found on this machine, so the memory could not be
registered automatically. Install Claude Code, then run:

  claude mcp add -s user $MCP_NAME $LAUNCHER

EOF
else
  if claude mcp list 2>/dev/null | grep -qE "^${MCP_NAME}[: ]"; then
    if [ "$FORCE" = "1" ]; then
      claude mcp remove -s user "$MCP_NAME" >/dev/null 2>&1 || true
      claude mcp add -s user "$MCP_NAME" "$LAUNCHER" || die "could not register with Claude"
      echo "replaced the existing \"$MCP_NAME\" entry"
    else
      cat <<EOF
An MCP server called "$MCP_NAME" is already registered, so nothing was changed.
Re-run with --force to replace it, or pick a different name:

  CLAUDE_MEMORY_MCP_NAME=local-memory ./install.sh

EOF
    fi
  else
    claude mcp add -s user "$MCP_NAME" "$LAUNCHER" || die "could not register with Claude"
    echo "registered as \"$MCP_NAME\""
  fi
fi

# ─────────────────────────────────────────────────────────────────────────────
# 9. End-to-end check
# ─────────────────────────────────────────────────────────────────────────────
say "Final check"
"$LAUNCHER" --self-check || die "the memory server did not start cleanly"

cat <<EOF

Done.

  Installed to:   $PREFIX
  Your memories:  $STORE_DIR
                  (plain SQLite files on this machine, nothing else)

Open a new terminal, run "claude" in any folder, and ask it to remember
something. Nothing about this leaves the machine.
EOF
