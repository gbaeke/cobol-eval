# cobol-eval

A COBOL extractor for [graphify](https://github.com/Graphify-Labs/graphify),
which has no COBOL support upstream, plus the grammar evaluation that chose the
parser underneath it and a web app for exploring the graph it produces.

The pipeline, end to end:

```
spantree grammar.js  ->  generated parser.c  ->  pywheel/  ->  tree_sitter_cobol
                                                                    |
                              extensions/graphify-cobol/  <---------+
                                        |  (installed into a graphify checkout)
                                        v
                          AWS CardDemo  ->  graph.json  ->  webapp/
```

`extensions/graphify-cobol/` is the deliverable: a 251-line extractor that turns
COBOL into programs, paragraphs, `PERFORM`/`CALL` edges, `EXEC CICS XCTL`/`LINK`
control transfers, `COPY` includes and VSAM file assignments. On CardDemo it
yields 456 nodes across 31 programs and 30 copybooks.

## Choosing the parser

That extractor needs a grammar that can actually parse enterprise COBOL, which
is what the comparison here settled. The grammars under test:

- **yutaro** — [yutaro-sakamoto/tree-sitter-cobol](https://github.com/yutaro-sakamoto/tree-sitter-cobol), compiled from published `src/parser.c`.
- **spantree** — [Spantree/tree-sitter-cobol-enterprise](https://github.com/Spantree/tree-sitter-cobol-enterprise), generated from `grammar.js`.

`compare.py` parses every CardDemo file with both and reports the percentage of
each file covered by `ERROR` nodes. Current numbers are in `results.txt`:
spantree parses all 61 files cleanly; yutaro errors on every CICS file and
every copybook — 74.5% of the average CICS file lands inside an `ERROR` node.
Hence spantree, packaged by `pywheel/`.

## What's in the repo, and what isn't

Only hand-written sources are tracked. Everything else — the Node toolchain,
virtualenvs, the downloaded corpus, generated parsers, the compiled `.so`s and
the graph output — is gitignored and rebuilt by `run.sh`. A fresh clone is
~130 KB; a fully built tree is ~600 MB.

The two upstream clones are ignored too, because each carries its own `.git` and
git would treat it as a submodule rather than as files. Our changes to graphify
therefore live in `extensions/graphify-cobol/` as the extractor plus a wiring
patch, and `run.sh` reapplies them to a fresh clone. Nothing in this repo
modifies spantree — its local diff is npm and build artifacts only.

## Prerequisites

- `gcc`, `python3` (3.10+), `curl`, `git`
- [`uv`](https://docs.astral.sh/uv/) — creates the virtualenvs
- [`gh`](https://cli.github.com/), authenticated (`gh auth login`) — the working
  copy of the corpus used by the comparison is fetched through the GitHub API.
  The graph's copy is tracked in `carddemo-graph/`, so the graph builds without it.
- **~3 GB free RAM.** Generating the spantree parser from `grammar.js` is the
  memory hog; `run.sh` nices it and sets `oom_score_adj=1000` so the kernel kills
  that step rather than your shell. It takes several minutes.
- Nothing extra for graphify: `run.sh` clones it, installs our extractor into
  the checkout, and builds a `.venv-graphify` holding it alongside the parser.

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
`http://localhost:4000`). With no key it returns 503; with a key but no gateway
listening, 502. If `GATEWAY_API_KEY` is
unset, `app.py` falls back to reading the key from
`~/.config/agentgateway/config.yaml` — that keeps the secret off the command
line, where `ps` would show it. No credentials live in this repo.

Other knobs: `GATEWAY_MODEL` (default `anthropic-prod/fast`), `GRAPH_PATH`,
`HOST`, `PORT`.

## The corpus

`carddemo-graph/{cbl,cpy}` holds the 61 CardDemo sources the graph is built
from — 31 programs and 30 copybooks from
[aws-samples/aws-mainframe-modernization-carddemo](https://github.com/aws-samples/aws-mainframe-modernization-carddemo),
Apache-2.0, unmodified.

They're tracked rather than ignored for a non-obvious reason: graphify reads
`.gitignore` when walking a tree and only exempts paths already in git's index.
Ignoring the graph's own input makes `graphify update` report *"No code files
found"* and write a graph with zero programs — the same empty-graph symptom as a
missing parser, from the opposite cause. `run.sh` refreshes this copy from the
`gh`-downloaded `carddemo/` on every build, so the two stay identical.

## Other files

| File | What it does |
|---|---|
| `compare.py` | the ERROR-coverage comparison that writes `results.txt` |
| `cobol.py` | shared parsing/query helpers over the two grammars |
| `shapes.py`, `shapes2.py`, `shapes3.py`, `ast_survey.py` | one-off AST shape surveys used while reading the grammars |
| `pywheel/` | packages the spantree grammar as the `tree_sitter_cobol` module the extractor imports |
| `extensions/graphify-cobol/` | the COBOL extractor and its wiring patch, with its own README |

`run.sh` builds and installs `pywheel` for you (step 8c–8d), copying the
generated parser out of `spantree/src` first. To install it standalone:

```bash
cp -r spantree/src pywheel/src
pip install ./pywheel
```

Watch for the silent failure mode: if `tree_sitter_cobol` isn't importable, the
extractor returns zero nodes rather than raising, and you get a graph of bare
file nodes with no programs in it. Step 8f fails the build on exactly that,
counting `program` nodes rather than total nodes.
