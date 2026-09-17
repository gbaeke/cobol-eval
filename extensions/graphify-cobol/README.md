# graphify COBOL extractor

Adds IBM Enterprise COBOL (CICS/SQL-aware) support to
[graphify](https://github.com/Graphify-Labs/graphify), which has no COBOL
extractor upstream. This is the point of the repo: the grammar comparison in
`../../results.txt` is what picked the parser underneath it.

**What it extracts:** programs and paragraphs as nodes; `PERFORM` and `CALL`
edges; `EXEC CICS XCTL`/`LINK` as control-transfer edges (XCTL doesn't return —
the pseudo-conversational screen flow); `COPY` includes; and VSAM file
assignments from the CICS file commands. A second pass adds `INFERRED` call
edges, following the bespoke-extractor pattern of graphify's `fortran.py`.

## Install

```bash
./install.sh [path-to-graphify-checkout]     # default ../../gf
```

Copies `cobol.py` into `graphify/extractors/` and applies `wiring.patch`, which
registers the extensions in three places: `CODE_EXTENSIONS` in `detect.py`,
`_DISPATCH` in `extract.py`, and `LANGUAGE_EXTRACTORS` in `extractors/__init__.py`.

## The parser it needs

`cobol.py` does `import tree_sitter_cobol` — the Spantree enterprise grammar
built as a Python extension by `../../pywheel`. **Without that module installed
the extractor returns no nodes and no edges**, with `error: "tree-sitter-cobol
not installed"` tucked in the result, so the symptom is an empty graph rather
than a crash. `../../run.sh` builds and installs it before building the graph.

## Keeping it in sync

`wiring.patch` is a plain `git diff` against graphify 0.9.61. If upstream moves
and the patch stops applying, `install.sh` prints the three edits to redo by
hand. Re-cut it with `cd gf && git diff > ../extensions/graphify-cobol/wiring.patch`.
