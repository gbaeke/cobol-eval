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

**Linux x86-64.** `run.sh` is not portable to macOS, in one fatal way and three
cosmetic ones:

- it downloads a `linux-x64` Node tarball, so step 1 fails outright on a Mac —
  everything after it needs `npx tree-sitter`
- it reads `/proc/meminfo` for the memory warning and writes
  `/proc/self/oom_score_adj` to steer the OOM killer; neither exists on macOS,
  so those guards quietly do nothing
- it uses GNU `stat -c%s` (BSD wants `-f%z`) and `readlink -f`, which older
  BSD/macOS `readlink` lacks

Nothing else is Linux-bound: the vendored `parser.c` is portable C, and clang
handles the `gcc -fPIC -shared` build fine. Porting is a `uname`-based tarball
selection plus those three substitutions — untried here.

- `gcc`, `python3` (3.10+), `curl`, `git`
- [`uv`](https://docs.astral.sh/uv/) — creates the virtualenvs
- [`gh`](https://cli.github.com/), authenticated (`gh auth login`) — the working
  copy of the corpus used by the comparison is fetched through the GitHub API.
  The graph's copy is tracked in `carddemo-graph/`, so the graph builds without it.
- Modest RAM — the expensive step is pre-built. `tree-sitter generate` on the
  spantree grammar peaks at **5.7 GB RSS** and takes **15m37s** (measured,
  4 vCPU), so its output is checked into `vendor/spantree-parser/` and `run.sh`
  copies it into place. You only need those 6 GB and that quarter hour if you
  run `REGENERATE=1 ./run.sh` to rebuild it from `grammar.js`, which is
  necessary only when bumping the pinned grammar commit.
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
`CORPUS_ROOT`, `HOST` (`0.0.0.0` to reach it from another machine), `PORT`.

### Ask, and Dig deeper

Two ways to question the code, and they work differently:

**Ask** (`/api/ask`) is one LLM call. `app.py` pulls the relevant slice of the
graph into the prompt and the model answers from those facts. Fast, and right
about structure — who calls whom, what a change would touch.

**Dig deeper** (`/api/investigate`) is an agent that reads the COBOL itself,
because the graph knows the call edges and nothing about what the code computes.
It lives in two modules:

| File | What it holds |
|---|---|
| `cobol_tools.py` | the six tools — `find_symbol`, `dependencies`, `read_paragraph`, `read_lines`, `read_comments`, `grep_cobol` — and the fixed-form handling that strips sequence numbers and comments before the model sees a line |
| `cobol_deep_agent.py` | the harness: [deepagents](https://docs.langchain.com/oss/python/deepagents/overview) over a `ChatOpenAI` pointed at the gateway |

The harness gives the agent a todo list (`TodoListMiddleware`, which is *not* in
the default stack), a `tracer` subagent for following a long PERFORM/CALL chain
without dragging every intermediate listing back into the main context, and a
filesystem rooted at `carddemo-graph/`. That last one is not cosmetic: the
default `StateBackend` is an empty scratchpad, so an agent that reaches for `ls`
or `read_file` first — they do — finds nothing and reports it cannot access the
COBOL. Writes are denied, and no shell tool is configured; `execute` needs a
sandbox or `LocalShellBackend`, and neither is used.

`/api/investigate/stream` runs the same thing as SSE, which is what the UI uses,
so the plan appears as the agent writes it and each item ticks off as it goes.

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
| `vendor/spantree-parser/` | the pre-generated parser that lets a clone skip the 16-minute build, with its provenance |
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
