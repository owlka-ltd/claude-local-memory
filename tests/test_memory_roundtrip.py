"""remember -> recall -> list_recent -> forget against a throwaway database.

Runs with keyword search only (no embedding model is downloaded) and never
touches the real ~/.claude/local_memory.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture()
def server(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_MEMORY_EMBED_MODEL", str(tmp_path / "no-such-model"))
    from memory import server as s

    monkeypatch.setattr(s, "_DB_DIR", tmp_path)
    monkeypatch.setattr(s, "_DB_PATH", tmp_path / "test.db")
    return s


def test_remember_then_recall_finds_it(server):
    out = server.remember("The board pack is due the first Tuesday of each month", "board")
    assert out.startswith("Remembered (id ")
    assert "board pack" in server.recall("board pack")


def test_recall_of_something_never_stored_does_not_return_other_memories(server):
    server.remember("The board pack is due the first Tuesday of each month")
    assert "board pack" not in server.recall("zebra")


def test_empty_text_is_refused(server):
    assert "empty" in server.remember("   ").lower()


def test_list_recent_shows_what_was_stored(server):
    server.remember("first fact")
    server.remember("second fact")
    listing = server.list_recent(5)
    assert "first fact" in listing and "second fact" in listing


def test_forget_removes_it(server):
    import re

    mid = int(re.search(r"id (\d+)", server.remember("delete me please")).group(1))
    server.forget(mid)
    assert "delete me please" not in server.list_recent(5)
    assert "delete me please" not in server.recall("delete")
