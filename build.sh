#!/usr/bin/env bash
# Build the fully offline installer bundle.
#
# The result is a single .tar.gz that installs local memory on a machine with
# NO network access at all: every Python package and the embedding model are
# inside it. That is the artifact you hand someone.
#
# It is built for the machine you build it ON (macOS Apple Silicon builds an
# Apple Silicon bundle). Build on the same kind of machine you are installing
# to, or pass PIP_PLATFORM_ARGS to cross-build.
#
# USAGE
#   ./build.sh              -> dist/claude-local-memory-<date>.tar.gz
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

STAMP="$(date -u +%Y%m%d)"
NAME="claude-local-memory-$STAMP"
OUT="$SCRIPT_DIR/dist"
STAGE="$OUT/$NAME"

MODEL_REPO="minishlab/potion-base-8M"
MODEL_WEIGHTS="model.safetensors"
MODEL_SHA="f65d0f325faadc1e121c319e2faa41170d3fa07d8c89abd48ca5358d9a223de2"

say() { printf '\n== %s\n' "$*"; }
die() { printf '\nFAILED: %s\n' "$*" >&2; exit 1; }

say "Finding a Python 3.11+ to build with"
PY=""
for c in python3.13 python3.12 python3.11 python3; do
  p="$(command -v "$c" 2>/dev/null || true)"
  [ -n "$p" ] || continue
  if "$p" -c 'import sys; sys.exit(0 if sys.version_info[:2] >= (3,11) else 1)' 2>/dev/null; then
    PY="$p"; break
  fi
done
[ -n "$PY" ] || die "no Python 3.11+ found"
echo "using $PY ($("$PY" -V 2>&1))"

rm -rf "$STAGE"
mkdir -p "$STAGE/vendor/wheels" "$STAGE/vendor/model"

# ─────────────────────────────────────────────────────────────────────────────
# The Python packages, hash-verified on download.
# ─────────────────────────────────────────────────────────────────────────────
say "Downloading the Python packages (each checked against a pinned hash)"
"$PY" -m pip download --require-hashes -r requirements.lock.txt \
      -d "$STAGE/vendor/wheels" ${PIP_PLATFORM_ARGS:-} \
  || die "package download failed"
echo "$(find "$STAGE/vendor/wheels" -type f | wc -l | tr -d ' ') files, $(du -sh "$STAGE/vendor/wheels" | cut -f1)"

# ─────────────────────────────────────────────────────────────────────────────
# The embedding model, fetched into a throwaway venv then hash-verified.
# ─────────────────────────────────────────────────────────────────────────────
say "Fetching the meaning-search model"
TMPVENV="$(mktemp -d)/venv"
"$PY" -m venv "$TMPVENV" || die "could not create the build venv"
"$TMPVENV/bin/python3" -m pip install --quiet --no-index \
    --find-links "$STAGE/vendor/wheels" huggingface-hub \
  || die "could not install huggingface-hub from the vendored wheels"

SNAP="$("$TMPVENV/bin/python3" - "$MODEL_REPO" <<'PYEOF'
import sys
from huggingface_hub import snapshot_download
print(snapshot_download(sys.argv[1]))
PYEOF
)" || die "model download failed"
[ -d "$SNAP" ] || die "model snapshot not found"

# Copy only what the runtime needs — skip the onnx variant (~28 MB unused).
for f in config.json modules.json tokenizer.json tokenizer_config.json \
         special_tokens_map.json vocab.txt "$MODEL_WEIGHTS"; do
  [ -f "$SNAP/$f" ] && cp -L "$SNAP/$f" "$STAGE/vendor/model/$f"
done
[ -f "$STAGE/vendor/model/$MODEL_WEIGHTS" ] || die "model weights missing from the snapshot"

say "Verifying the model"
GOT="$(shasum -a 256 "$STAGE/vendor/model/$MODEL_WEIGHTS" | cut -d' ' -f1)"
[ "$GOT" = "$MODEL_SHA" ] || die "model sha256 mismatch: want $MODEL_SHA got $GOT"
echo "sha256 OK (${GOT:0:12}...)"

# ─────────────────────────────────────────────────────────────────────────────
# The rest of the bundle.
# ─────────────────────────────────────────────────────────────────────────────
say "Assembling"
mkdir -p "$STAGE/memory"
cp memory/*.py "$STAGE/memory/"
cp requirements.lock.txt install.sh uninstall.sh LICENSE NOTICE.md README.md "$STAGE/"
chmod +x "$STAGE/install.sh" "$STAGE/uninstall.sh"

say "Packing"
tar -czf "$OUT/$NAME.tar.gz" -C "$OUT" "$NAME"
rm -rf "$STAGE"

cat <<EOF

Done.

  $OUT/$NAME.tar.gz  ($(du -h "$OUT/$NAME.tar.gz" | cut -f1))

To install from it on a machine with no network:

  tar -xzf $NAME.tar.gz
  cd $NAME
  ./install.sh
EOF
