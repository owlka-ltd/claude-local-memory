#!/usr/bin/env python3
"""Verify (and if needed fetch) the LOCAL semantic-recall model.

Local memory is HYBRID by contract: keyword (FTS5) AND semantic (model2vec +
sqlite-vec). The keyword-only fail-open in server.py is a crash guard, NOT an
acceptable steady state -- so an install must actually land the embedding
model. This script is that "prove it landed" step, and install.sh runs it.

WHAT IT DOES (idempotent -- safe to re-run):
  1. Check the semantic Python deps (model2vec + sqlite-vec) are importable.
  2. Locate the model. Preference order:
       a. CLAUDE_MEMORY_EMBED_MODEL as an absolute path to a model directory
          (what install.sh sets -- fully offline, no network at all);
       b. the local Hugging Face cache;
       c. a one-time download from Hugging Face (only if --allow-download).
  3. VERIFY the weights file sha256 against embedding.PINNED_MODEL_SHA256. A
     mismatch is fatal -- refuse a tampered or substituted model.
  4. Prove it loads and reports the expected dimensionality.

NO per-call cost, NO API, NO torch -- model2vec is a static token->vector
table (numpy mean-pool). Total footprint ~46 MB (deps ~16 MB + weights ~30MB).

USAGE:
    <venv>/bin/python -m memory.provision_model [flags]

  --check-only       verify only; never download. Exit 0 if ready, 1 if not.
  --allow-download   permit a one-time fetch from Hugging Face if the model is
                     not already on disk. OFF by default so a locked-down
                     machine fails loudly instead of reaching for the network.

EXIT CODES: 0 = semantic recall is provisioned and proven.
            1 = a step failed (deps, missing model, sha mismatch, or load).
"""
from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path

# Import the pin from the embedding module (single source of truth). Works both
# package-relative and as a loose script run with the venv python.
try:  # package-relative
    from . import embedding as _embedding
except ImportError:  # pragma: no cover - loose script
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import embedding as _embedding  # type: ignore

REPO = _embedding.PINNED_MODEL_REPO
WEIGHTS = _embedding.PINNED_MODEL_WEIGHTS_FILE
WANT_SHA = _embedding.PINNED_MODEL_SHA256


def _log(msg: str) -> None:
    print(f"local-memory: {msg}", flush=True)


def _deps_present() -> bool:
    try:
        import model2vec  # noqa: F401
        import sqlite_vec  # noqa: F401
    except ImportError:
        return False
    return True


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _local_dir_weights() -> Path | None:
    """Weights inside an explicit local model directory, if configured."""
    configured = os.environ.get("CLAUDE_MEMORY_EMBED_MODEL", "")
    if not configured:
        return None
    d = Path(configured)
    if not d.is_dir():
        return None
    p = d / WEIGHTS
    return p if p.is_file() else None


def _cached_weights() -> Path | None:
    """Weights already in the local Hugging Face cache (no network)."""
    try:
        from huggingface_hub import snapshot_download
    except ImportError:
        return None
    try:
        snap = snapshot_download(REPO, local_files_only=True)
    except Exception:
        return None
    p = Path(snap) / WEIGHTS
    return p if p.is_file() else None


def _fetch_weights() -> Path | None:
    """One-time download into the Hugging Face cache. Only called when the
    caller passed --allow-download."""
    try:
        from huggingface_hub import snapshot_download
    except ImportError:
        _log("huggingface_hub not importable -- cannot fetch model")
        return None
    _log(f"fetching {REPO} (one-time)")
    try:
        snap = snapshot_download(REPO)
    except Exception as exc:  # network down, blocked, repo gone, etc.
        _log(f"model download failed: {exc}")
        return None
    p = Path(snap) / WEIGHTS
    if not p.is_file():
        _log(f"download finished but {WEIGHTS} not found under {snap}")
        return None
    return p


def _verify_sha(weights: Path) -> bool:
    got = _sha256(weights)
    if got != WANT_SHA:
        _log("SHA256 MISMATCH -- refusing this model (possible tamper)")
        _log(f"  want: {WANT_SHA}")
        _log(f"  got:  {got}")
        return False
    _log(f"sha256 OK ({got[:12]}...) for {weights.name}")
    return True


def _prove_load() -> bool:
    """Load the model and confirm a real dimensionality."""
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
    _embedding.reset_for_tests()  # drop any cached embedder so we re-probe
    dim = _embedding.embedding_dim()
    if dim <= 0:
        _log("model load FAILED -- embedding_dim() returned 0")
        return False
    _log(f"model load OK -- embedding_dim={dim}, semantic recall is live")
    return True


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    check_only = "--check-only" in argv
    allow_download = "--allow-download" in argv and not check_only

    if not _deps_present():
        _log("NOT READY -- semantic deps (model2vec/sqlite-vec) absent")
        return 1

    weights = _local_dir_weights() or _cached_weights()
    if weights is None:
        if not allow_download:
            _log(
                "NOT READY -- model not found on disk. Point "
                "CLAUDE_MEMORY_EMBED_MODEL at a model directory, or re-run "
                "with --allow-download to fetch it once."
            )
            return 1
        weights = _fetch_weights()
        if weights is None:
            return 1
    _log(f"model at {weights.parent}")

    if not _verify_sha(weights):
        return 1
    if not _prove_load():
        return 1

    _log("DONE -- keyword + semantic recall provisioned")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
