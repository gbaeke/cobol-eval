# cobol-eval

Comparison of two tree-sitter COBOL grammars against the AWS CardDemo corpus,
plus a small web app for browsing the resulting knowledge graph.

The grammars under test:

- **yutaro** — [yutaro-sakamoto/tree-sitter-cobol](https://github.com/yutaro-sakamoto/tree-sitter-cobol), compiled from published `src/parser.c`.
- **spantree** — [Spantree/tree-sitter-cobol-enterprise](https://github.com/Spantree/tree-sitter-cobol-enterprise), generated from `grammar.js`.

`compare.py` parses every CardDemo file with both and reports the percentage of
each file covered by `ERROR` nodes. Current numbers are in `results.txt`:
spantree parses all 61 files cleanly; yutaro errors on every CICS file and
every copybook.

## What's in the repo, and what isn't

Only hand-written sources are tracked. Everything else — the Node toolchain,
virtualenvs, the downloaded corpus, generated parsers, the compiled `.so`s, the
graph output, and the two upstream clones (`spantree/`, `gf/`) — is gitignored
and rebuilt by `run.sh`. A fresh clone is ~100 KB; a fully built tree is ~600 MB.

## Prerequisites

- `gcc`, `python3` (3.10+), `curl`, `git`
- [`uv`](https://docs.astral.sh/uv/) — creates the virtualenvs
- [`gh`](https://cli.github.com/), authenticated (`gh auth login`) — the CardDemo
  corpus is fetched through the GitHub API
- **~3 GB free RAM.** Generating the spantree parser from `grammar.js` is the
  memory hog; `run.sh` nices it and sets `oom_score_adj=1000` so the kernel kills
  that step rather than your shell. It takes several minutes.
- Optional, for the graph and web app: `graphify` (`pip install graphifyy`, or
  have `uvx` on PATH and `run.sh` will fetch it on demand)

## Build and run the comparison

```bash
./run.sh
```

Idempotent and resumable — every step is skipped if its output already exists,
so a killed run can simply be restarted. It downloads Node 22, fetches and
compiles both grammars, pulls the corpus, builds the venv, writes `results.txt`,
and (if graphify is available) builds the knowledge graph.

## Web app

```bash
cd webapp
GATEWAY_API_KEY=<key> ./run.sh      # http://127.0.0.1:8899
```

Creates its own venv from `requirements.txt` on first run. Browsing the graph
needs no key; the **Ask** feature calls an LLM through a local
[agentgateway](https://agentgateway.dev) at `GATEWAY_URL` (default
`http://localhost:4000`) and returns 503 without one. If `GATEWAY_API_KEY` is
unset, `app.py` falls back to reading the key from
`~/.config/agentgateway/config.yaml` — that keeps the secret off the command
line, where `ps` would show it. No credentials live in this repo.

Other knobs: `GATEWAY_MODEL` (default `anthropic-prod/fast`), `GRAPH_PATH`,
`HOST`, `PORT`.

## Other files

| File | What it does |
|---|---|
| `compare.py` | the ERROR-coverage comparison that writes `results.txt` |
| `cobol.py` | shared parsing/query helpers over the two grammars |
| `shapes.py`, `shapes2.py`, `shapes3.py`, `ast_survey.py` | one-off AST shape surveys used while reading the grammars |
| `pywheel/` | packages the spantree grammar as a Python extension |

`pywheel` builds against a generated parser that isn't tracked. After `run.sh`
has produced one:

```bash
cp -r spantree/src pywheel/src
cd pywheel && pip install .
```
