# Third-party components

This project is MIT licensed (see `LICENSE`). It bundles or depends on the
following third-party work, each under its own licence.

## Embedding model

**`minishlab/potion-base-8M`** — MIT licensed.
<https://huggingface.co/minishlab/potion-base-8M>

A static token-to-vector table used for meaning-based search. It is
redistributed inside the offline installer bundle produced by `build.sh`. The
weights file is pinned by sha256 and verified at build time and at install
time.

## Python packages

The full set is pinned with hashes in `requirements.lock.txt`. The ones doing
the substantive work:

| Package | Licence | What it does here |
|---|---|---|
| `model2vec` | MIT | loads and runs the static embedding model |
| `sqlite-vec` | MIT / Apache-2.0 | vector index inside SQLite |
| `mcp` | MIT | the protocol Claude Code uses to talk to the memory tools |
| `huggingface-hub` | Apache-2.0 | fetches the model at build time |
| `numpy` | BSD-3-Clause | vector maths |
| `tokenizers` | Apache-2.0 | text tokenisation for the embedding |
| `safetensors` | Apache-2.0 | reads the model weights file |

Keyword search uses SQLite's built-in FTS5, which is in the public domain.

## Claude Code

Claude Code is Anthropic's product and is not included, bundled or modified by
this project. This project only registers a memory server with an existing
Claude Code installation.
