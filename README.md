# Local memory for Claude Code

Gives Claude Code a memory that survives across conversations, stored entirely
on your own machine.

Tell Claude to remember something. Close it. Open it again next week, in a
different folder, and ask what it knows. It remembers.

Nothing is uploaded. There is no account, no server, and no network call at
any point once it is installed. Your memories are rows in a SQLite file in
your home directory.

## Why it exists

Claude Code starts every conversation fresh. Hosted memory features solve that
by keeping your data on someone else's servers, which is a non-starter in
plenty of regulated environments.

This keeps the memory on the machine.

## What you get

Four tools, which Claude uses on its own once they are installed:

| Tool | What it does |
|---|---|
| `remember` | store a fact |
| `recall` | search stored facts |
| `list_recent` | show the most recent ones |
| `forget` | delete one |

`recall` searches two ways at once and merges the results:

- **Keyword** — exact word matching, via SQLite FTS5.
- **Meaning** — finds the right memory even when you phrase it differently.
  Ask about "trekking up hills" and it finds "hiking in the mountains".

The meaning half runs on a small static model (~30 MB) that does a table
lookup and some arithmetic. No inference server, no GPU, no API, no
per-question cost.

## Install

You need Claude Code, and Python 3.11 or newer.

**From an offline bundle** (what you hand to someone on a locked-down
machine — no network needed at all):

```bash
tar -xzf claude-local-memory-<date>.tar.gz
cd claude-local-memory-<date>
./install.sh
```

**From this repository** (fetches packages from PyPI and the model from
Hugging Face):

```bash
git clone https://github.com/owlka-ltd/claude-local-memory
cd claude-local-memory
./install.sh --allow-download
```

Then open a new terminal and run `claude` in any folder.

To check it took, ask Claude: *"what memory tools do you have?"*

## Build an offline bundle

```bash
./build.sh
```

Produces `dist/claude-local-memory-<date>.tar.gz` with every Python package
and the model inside it. Build it on the same kind of machine you will install
to (an Apple Silicon Mac builds an Apple Silicon bundle).

## Where everything lives

| What | Where |
|---|---|
| Your memories | `~/.claude/local_memory/<partition>.db` |
| The install | `~/.claude-local-memory/` |

Your memories are a plain SQLite file. Open it with any SQLite browser and
read every row. Nothing is obfuscated and nothing is encrypted, because
nothing ever leaves the machine it was written on.

## Uninstall

```bash
./uninstall.sh
```

Removes the install and unregisters it from Claude. It never deletes your
memories; it prints where they are so you can decide.

## Two people, one machine

Set `CLAUDE_MEMORY_PARTITION` to keep separate stores. Each partition gets its
own database file. Unset, everything goes to `default`.

## Integrity

The model is pinned by sha256 and verified twice: when the bundle is built and
again when it is installed. A mismatch aborts the install rather than loading
a substituted model. Every Python package is pinned by hash in
`requirements.lock.txt`.

## Licence

MIT — see [`LICENSE`](LICENSE). Third-party components and their licences are
listed in [`NOTICE.md`](NOTICE.md).

Claude Code is Anthropic's product. This project does not bundle or modify it.
