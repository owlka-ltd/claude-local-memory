#!/usr/bin/env python3
"""Local persistent memory for Claude — keyword (FTS5) + SEMANTIC (sqlite-vec).

Gives Claude durable memory tools so facts survive across chats: remember
something now, recall it later. `recall` is HYBRID:

  - KEYWORD half: SQLite FTS5 (stdlib, zero extra dep), bm25-ranked. Great
    when the query shares words with the stored memory.
  - SEMANTIC half: a small LOCAL embedding (model2vec, see embedding.py),
    cosine-ranked via a sqlite-vec vector index. Catches PARAPHRASES that
    share no keywords ("trekking up hills" recalls "hiking in the
    mountains").

The two ranked lists are fused with RRF (reciprocal rank fusion) so a result
strong on either signal surfaces.

EVERYTHING IS LOCAL. There is no account, no server, and no network call at
any point in this file. Memories are rows in one SQLite file on this machine.

PARTITIONS: the store is partitioned by CLAUDE_MEMORY_PARTITION so two people
sharing one machine (or one person keeping work and personal separate) can
have independent stores. Default partition is "default".

Tools:
  remember(text, tags="")     -> stores a memory (+ embedding), returns its id
  recall(query, limit=10)     -> hybrid keyword+semantic search, best first
  list_recent(limit=10)       -> most recently stored memories
  forget(memory_id)           -> delete one memory

MIGRATION / FALLBACK: a store created by the keyword-only version keeps
working. The vector table is created lazily; old rows are backfilled the
next time they're touched, and any row without an embedding still ranks via
bm25. If the embedding backend is unavailable (model not installed yet),
recall transparently falls back to bm25-only — never a crash.

Storage: ~/.claude/local_memory/<partition>.db  (user data — an uninstaller
must print, never delete, this path).
"""
from __future__ import annotations

import os
import re
import sqlite3
import struct
import sys
import time
from pathlib import Path
from typing import List, Optional

try:
    from mcp.server.fastmcp import FastMCP
except ImportError:  # pragma: no cover
    from fastmcp import FastMCP  # fallback for older bundles

try:  # package-relative when imported as memory.server
    from . import embedding as _embedding
except ImportError:  # pragma: no cover - run as a loose script
    import embedding as _embedding  # type: ignore

_PARTITION = re.sub(
    r"[^a-zA-Z0-9_-]", "_", os.environ.get("CLAUDE_MEMORY_PARTITION", "default")
)
_DB_DIR = Path.home() / ".claude" / "local_memory"
_DB_PATH = _DB_DIR / f"{_PARTITION}.db"
_MAX_LIMIT = 50
_RRF_K = 60  # standard RRF damping constant.

# sqlite-vec is a tiny (~165 KB) loadable extension — no model server. If it
# can't be loaded we degrade to bm25-only, same as a missing embedding model.
try:
    import sqlite_vec  # type: ignore

    _HAVE_VEC = True
except ImportError:  # pragma: no cover
    sqlite_vec = None  # type: ignore
    _HAVE_VEC = False


def _pack(vec: List[float]) -> bytes:
    """Pack a float vector into sqlite-vec's little-endian float32 blob."""
    return struct.pack(f"<{len(vec)}f", *vec)


class _Conn(sqlite3.Connection):
    """sqlite3.Connection subclass we can stamp `vec_ready` onto (the base
    class forbids new attributes in some Python builds)."""

    vec_ready: bool = False


def _vec_ready(conn: sqlite3.Connection) -> bool:
    """True if this connection has the vector index available (extension
    loaded AND the vec table created)."""
    return bool(getattr(conn, "vec_ready", False))


def _db() -> sqlite3.Connection:
    """Open (and on first use, create) the partition's memory store.

    Always creates the keyword (FTS5) layer. Additionally tries to load
    sqlite-vec and create the vector index sized to the active embedding's
    dimensionality; if either is unavailable the connection is keyword-only
    and `_vec_ready` stays False.
    """
    _DB_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_DB_PATH), factory=_Conn)
    conn.row_factory = sqlite3.Row
    # Content table holds the canonical row; the FTS5 table indexes it. We
    # keep them separate (not contentless) so list_recent / forget by id are
    # simple and the FTS index can be rebuilt from content if ever needed.
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            text TEXT NOT NULL,
            tags TEXT NOT NULL DEFAULT '',
            created_ts REAL NOT NULL
        );
        CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts
            USING fts5(text, tags, content='memories', content_rowid='id');
        CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
            INSERT INTO memories_fts(rowid, text, tags) VALUES (new.id, new.text, new.tags);
        END;
        CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
            INSERT INTO memories_fts(memories_fts, rowid, text, tags)
                VALUES('delete', old.id, old.text, old.tags);
        END;
        """
    )
    conn.vec_ready = False
    dim = _embedding.embedding_dim()
    if _HAVE_VEC and dim > 0:
        try:
            conn.enable_load_extension(True)
            sqlite_vec.load(conn)
            conn.enable_load_extension(False)
            # vec0 virtual table keyed by the memory rowid. Created at the
            # active model's dimensionality. If a store was built with a
            # different-dim model, a dim mismatch on insert/query is caught
            # and the row simply isn't vector-indexed (bm25 still covers it).
            conn.execute(
                f"CREATE VIRTUAL TABLE IF NOT EXISTS memories_vec "
                f"USING vec0(embedding float[{dim}])"
            )
            conn.vec_ready = True
        except Exception:
            # Extension present but load/create failed — keyword-only.
            conn.vec_ready = False
    return conn


def _store_embedding(conn: sqlite3.Connection, rowid: int, text: str) -> None:
    """Compute + upsert the embedding for one memory row. No-op if the
    vector layer or the embedding backend is unavailable."""
    if not _vec_ready(conn):
        return
    vec = _embedding.embed(text)
    if not vec:
        return
    try:
        conn.execute("DELETE FROM memories_vec WHERE rowid = ?", (rowid,))
        conn.execute(
            "INSERT INTO memories_vec(rowid, embedding) VALUES (?, ?)",
            (rowid, _pack(vec)),
        )
    except sqlite3.OperationalError:
        # e.g. dimension mismatch vs a store built with another model.
        # Leave the row to bm25; don't crash a remember/recall.
        pass


def _backfill_missing_embeddings(conn: sqlite3.Connection, limit: int = 200) -> None:
    """Lazily embed rows that have no vector yet (migration of a
    keyword-only store, or rows written while the model was absent)."""
    if not _vec_ready(conn):
        return
    try:
        rows = conn.execute(
            """
            SELECT m.id, m.text FROM memories m
            WHERE m.id NOT IN (SELECT rowid FROM memories_vec)
            ORDER BY m.created_ts DESC LIMIT ?
            """,
            (limit,),
        ).fetchall()
    except sqlite3.OperationalError:
        return
    for r in rows:
        _store_embedding(conn, r["id"], r["text"])
    if rows:
        conn.commit()


# sqlite-vec's default metric is L2. Our vectors are L2-normalised, so
# L2_dist^2 = 2*(1 - cosine). A cosine floor of ~0.20 (loosely related) maps
# to L2_dist <= sqrt(2*(1-0.20)) ~= 1.26. Beyond that the KNN is just
# returning "the nearest of several unrelated rows" — noise that would
# inflate RRF for an irrelevant memory, so we cut it. Observed: paraphrase
# cos ~0.60 (dist ~0.89) passes; unrelated cos ~0.03 (dist ~1.39) is dropped.
_SEM_MAX_DISTANCE = 1.26


def _semantic_ids(conn: sqlite3.Connection, query: str, k: int) -> List[int]:
    """Return memory ids ranked by cosine similarity (nearest first), dropping
    rows past the relevance cutoff. [] if the vector layer/embedding is
    unavailable."""
    if not _vec_ready(conn):
        return []
    qvec = _embedding.embed(query)
    if not qvec:
        return []
    try:
        rows = conn.execute(
            """
            SELECT rowid, distance FROM memories_vec
            WHERE embedding MATCH ? AND k = ?
            ORDER BY distance
            """,
            (_pack(qvec), k),
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    return [r["rowid"] for r in rows if r["distance"] <= _SEM_MAX_DISTANCE]


def _keyword_ids(conn: sqlite3.Connection, query: str, k: int) -> List[int]:
    """Return memory ids ranked by bm25 (best first)."""
    try:
        rows = conn.execute(
            """
            SELECT f.rowid AS rid
            FROM memories_fts f
            WHERE memories_fts MATCH ?
            ORDER BY bm25(memories_fts) LIMIT ?
            """,
            (_fts_query(query), k),
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    return [r["rid"] for r in rows]


def _rrf_fuse(*ranked_lists: List[int]) -> List[int]:
    """Reciprocal rank fusion: score each id by sum(1/(k+rank)) across the
    input ranked lists, return ids ordered by descending fused score."""
    scores: dict[int, float] = {}
    for ids in ranked_lists:
        for rank, mid in enumerate(ids):
            scores[mid] = scores.get(mid, 0.0) + 1.0 / (_RRF_K + rank + 1)
    return sorted(scores, key=lambda m: scores[m], reverse=True)


# Common English stopwords. Dropped from the FTS5 MATCH so a natural-language
# query doesn't keyword-match an unrelated memory purely on filler words
# ("is", "in", "my" etc.) — that noise otherwise inflates RRF for the wrong
# row. Semantic recall handles meaning; keyword should match content words.
_STOPWORDS = frozenset(
    """a an and are as at be but by for from has have i if in into is it its me
    my of on or our so that the their them then there these they this to up us
    was we were what when where which who will with you your""".split()
)


def _fts_query(raw: str) -> str:
    """Turn free text into a safe FTS5 MATCH query: OR the content terms so a
    natural-language query still matches, drop stopwords so filler words don't
    create spurious matches, and quote each term so FTS5 operators in user
    text can't break the parse."""
    terms = re.findall(r"\w+", raw.lower())
    content = [t for t in terms if t not in _STOPWORDS]
    # If the query is ALL stopwords (rare), fall back to the raw terms rather
    # than matching nothing.
    use = content or terms
    if not use:
        return '""'
    return " OR ".join(f'"{t}"' for t in use)


mcp = FastMCP(
    "local-memory",
    instructions=(
        "Persistent local memory for this user. Use `remember` to store a "
        "durable fact the user will want later (a preference, a decision, a "
        "name, a project detail) — be concise and self-contained. Use "
        "`recall` to look something up before answering 'what did I say "
        "about X'. Don't store secrets or anything the user asked to keep "
        "private."
    ),
)


def _coerce_tags(tags) -> str:
    """Accept tags as a string OR a list (Claude sometimes passes a JSON
    array) and normalise to a flat comma-separated string."""
    if tags is None:
        return ""
    if isinstance(tags, (list, tuple)):
        return ", ".join(str(t).strip() for t in tags if str(t).strip())
    return str(tags).strip()


@mcp.tool()
def remember(text: str, tags: str = "") -> str:
    """Store a durable memory. Returns its id.

    Args:
        text: The fact to remember, self-contained (e.g. "The quarterly
            board pack is due the first Tuesday of each month").
        tags: Optional tags for grouping — a comma/space-separated string.
    """
    text = (text or "").strip()
    if not text:
        return "Nothing to remember (empty text)."
    conn = _db()
    try:
        stored = text[:4000]
        cur = conn.execute(
            "INSERT INTO memories(text, tags, created_ts) VALUES (?,?,?)",
            (stored, _coerce_tags(tags)[:200], time.time()),
        )
        # Embed the new row so semantic recall can find it. No-op + safe if
        # the embedding/vector layer is unavailable.
        _store_embedding(conn, cur.lastrowid, stored)
        conn.commit()
        return f"Remembered (id {cur.lastrowid})."
    finally:
        conn.close()


@mcp.tool()
def recall(query: str, limit: int = 10) -> str:
    """Search memories (best matches first). Hybrid keyword + meaning.

    Combines an exact-keyword search with a meaning-based (semantic) search
    so it finds the right memory even when you phrase it differently from
    how it was stored. Falls back to keyword-only if the semantic model
    isn't available.

    Args:
        query: What to look up — plain words.
        limit: Max results (capped at 50).
    """
    limit = max(1, min(int(limit) if str(limit).isdigit() else 10, _MAX_LIMIT))
    query = (query or "").strip()
    if not query:
        return "No matching memories."
    conn = _db()
    try:
        # Migrate/backfill any rows still missing an embedding (cheap, capped).
        _backfill_missing_embeddings(conn)
        # Over-fetch from each retriever, then fuse + trim to `limit`.
        fetch = min(_MAX_LIMIT, max(limit * 3, limit))
        kw_ids = _keyword_ids(conn, query, fetch)
        sem_ids = _semantic_ids(conn, query, fetch)
        if not kw_ids and not sem_ids:
            return "No matching memories."
        fused = _rrf_fuse(kw_ids, sem_ids)[:limit]
        if not fused:
            return "No matching memories."
        placeholders = ",".join("?" * len(fused))
        rows = conn.execute(
            f"SELECT id, text, tags FROM memories WHERE id IN ({placeholders})",
            fused,
        ).fetchall()
    except sqlite3.OperationalError as exc:
        return f"recall failed: {exc}"
    finally:
        conn.close()
    by_id = {r["id"]: r for r in rows}
    out = []
    for mid in fused:  # preserve fused ranking order
        r = by_id.get(mid)
        if r is None:
            continue
        tag = f" [{r['tags']}]" if r["tags"] else ""
        out.append(f"#{r['id']}{tag}: {r['text']}")
    return "\n".join(out) if out else "No matching memories."


@mcp.tool()
def list_recent(limit: int = 10) -> str:
    """List the most recently stored memories.

    Args:
        limit: Max results (capped at 50).
    """
    limit = max(1, min(int(limit) if str(limit).isdigit() else 10, _MAX_LIMIT))
    conn = _db()
    try:
        rows = conn.execute(
            "SELECT id, text, tags FROM memories ORDER BY created_ts DESC LIMIT ?",
            (limit,),
        ).fetchall()
    finally:
        conn.close()
    if not rows:
        return "No memories stored yet."
    return "\n".join(
        f"#{r['id']}{(' [' + r['tags'] + ']') if r['tags'] else ''}: {r['text']}"
        for r in rows
    )


@mcp.tool()
def forget(memory_id: int) -> str:
    """Delete one memory by its id.

    Args:
        memory_id: The id shown by recall / list_recent (e.g. 12).
    """
    try:
        mid = int(memory_id)
    except (TypeError, ValueError):
        return "memory_id must be a number."
    conn = _db()
    try:
        cur = conn.execute("DELETE FROM memories WHERE id = ?", (mid,))
        if _vec_ready(conn):
            try:
                conn.execute("DELETE FROM memories_vec WHERE rowid = ?", (mid,))
            except sqlite3.OperationalError:
                pass
        conn.commit()
        return f"Forgot #{mid}." if cur.rowcount else f"No memory #{mid}."
    finally:
        conn.close()


def main() -> None:
    if "--self-check" in sys.argv:
        # Prove the store opens and report what's live. Used by install.sh.
        conn = _db()
        n = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        sem = "on" if _vec_ready(conn) else "off (keyword-only)"
        dim = _embedding.embedding_dim()
        conn.close()
        print(
            f"OK — partition={_PARTITION} db={_DB_PATH} memories={n} "
            f"semantic={sem} embed_dim={dim}"
        )
        sys.exit(0)
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
