#!/usr/bin/env python3
"""Pluggable local embedding source for the SEMANTIC half of local memory.

The retrieval layer (server.py) only ever talks to this module through two
functions: `embed(text) -> list[float] | None` and `embedding_dim() -> int`.
Swap the model by editing ONE place here (the `_Embedder` chosen in
`_load_embedder`) without touching any retrieval/RRF logic.

DEFAULT MODEL — model2vec `potion-base-8M`
------------------------------------------
This is a STATIC embedding: a precomputed token -> vector table. Encoding is
just "look each token up and mean-pool" — pure numpy, NO torch, NO inference
engine, NO model server, NO network call. That is what makes semantic recall
work fully offline at no per-call cost.

Footprint on a fresh machine:
  - python deps (model2vec + numpy + tokenizers + safetensors + small CLI
    deps): ~16 MB of wheels.
  - model weights (potion-base-8M, 256-dim): ~30 MB safetensors.
  Total ~46 MB. No torch (~800 MB) anywhere.

Model pin (verified on bundle build):
  repo:   minishlab/potion-base-8M
  file:   model.safetensors
  sha256: f65d0f325faadc1e121c319e2faa41170d3fa07d8c89abd48ca5358d9a223de2
  dims:   256

OFFLINE-FIRST: `CLAUDE_MEMORY_EMBED_MODEL` may be an absolute path to a local
model directory. The installer sets it to the copy it laid down on disk, so
the runtime never reaches for a network cache. Left unset, it falls back to
the pinned repo id, which requires the model to already be in the local
Hugging Face cache.

FAIL-OPEN CONTRACT
------------------
If model2vec is not installed, or the model can't be loaded, `embed()`
returns None and `embedding_dim()` returns 0. The retrieval layer treats that
as "no embeddings available" and falls back to keyword-only recall — never a
crash. This is what lets a keyword-only store keep working.
"""
from __future__ import annotations

import math
import os
import threading
from typing import List, Optional

# Canonical model pin — SINGLE SOURCE OF TRUTH for the provisioner
# (provision_model.py) and any verifier. The provisioner checks
# PINNED_MODEL_SHA256 of the weights file before declaring the model trusted;
# the runtime loads the same model.
PINNED_MODEL_REPO = "minishlab/potion-base-8M"
PINNED_MODEL_WEIGHTS_FILE = "model.safetensors"
PINNED_MODEL_SHA256 = "f65d0f325faadc1e121c319e2faa41170d3fa07d8c89abd48ca5358d9a223de2"

# An absolute path to a local model directory wins over the repo id. This is
# what the installer sets so a locked-down machine never needs the network.
_DEFAULT_MODEL = os.environ.get("CLAUDE_MEMORY_EMBED_MODEL", PINNED_MODEL_REPO)

_lock = threading.Lock()
_embedder: "Optional[_Embedder]" = None
_tried = False


class _Embedder:
    """Interface every embedding backend implements."""

    dim: int = 0

    def encode(self, text: str) -> Optional[List[float]]:  # pragma: no cover - interface
        raise NotImplementedError


class _Model2VecEmbedder(_Embedder):
    """model2vec static embeddings (default). Loads the token->vector table
    once; encoding is a numpy mean-pool, no torch."""

    def __init__(self, model_id: str) -> None:
        from model2vec import StaticModel  # imported lazily so absence => fallback

        self._model = StaticModel.from_pretrained(model_id)
        # Probe the dimensionality from a trivial encode.
        probe = self._model.encode(["x"])
        self.dim = int(probe.shape[1])

    def encode(self, text: str) -> Optional[List[float]]:
        import numpy as np

        vec = self._model.encode([text])
        if vec is None or len(vec) == 0:
            return None
        v = np.asarray(vec[0], dtype="float32")
        norm = float(np.linalg.norm(v))
        if not math.isfinite(norm) or norm == 0.0:
            return None
        # Store L2-normalised so cosine == dot product downstream.
        return (v / norm).astype("float32").tolist()


def _load_embedder() -> Optional[_Embedder]:
    """Load the embedding backend once. Returns None (and stays None) if the
    backend or its model is unavailable, so callers fall back to keyword."""
    global _embedder, _tried
    if _tried:
        return _embedder
    with _lock:
        if _tried:
            return _embedder
        _tried = True
        try:
            _embedder = _Model2VecEmbedder(_DEFAULT_MODEL)
        except Exception:
            # model2vec not installed, or weights not present.
            # Fail open: semantic layer is simply off until the model lands.
            _embedder = None
        return _embedder


def embedding_dim() -> int:
    """Dimensionality of the active embedding, or 0 if none is available."""
    emb = _load_embedder()
    return emb.dim if emb is not None else 0


def embed(text: str) -> Optional[List[float]]:
    """Return an L2-normalised embedding for `text`, or None if no embedding
    backend is available (caller then uses keyword-only)."""
    text = (text or "").strip()
    if not text:
        return None
    emb = _load_embedder()
    if emb is None:
        return None
    try:
        return emb.encode(text)
    except Exception:
        return None


def reset_for_tests() -> None:
    """Test hook: forget the cached embedder so a test can force the
    fallback path (e.g. by monkeypatching) and re-probe."""
    global _embedder, _tried
    with _lock:
        _embedder = None
        _tried = False
