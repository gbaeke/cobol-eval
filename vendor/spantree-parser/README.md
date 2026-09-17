# Pre-generated spantree COBOL parser

`tree-sitter generate` on the spantree grammar peaks at **5.7 GB RSS** and takes
**15m37s** (measured, 4 vCPU). Upstream ships `src/scanner.c` but not the
generated `src/parser.c`, so without this directory every clone pays that cost
before it can parse a single line of COBOL.

These files are the output of that step, checked in so `run.sh` can skip it.
9 MB raw, ~440 KB in git.

| Source | |
|---|---|
| Grammar | [Spantree/tree-sitter-cobol-enterprise](https://github.com/Spantree/tree-sitter-cobol-enterprise) |
| Commit | `86a2c479cb7e299e8dc3bb9db7a346502335d21e` (2026-04-24) |
| Generated with | `tree-sitter-cli` 0.25.9, ABI 14 |
| License | MIT, © 2026 Spantree Technology Group, LLC |

`run.sh` clones spantree **pinned to that exact commit**, because `parser.c` and
the repo's hand-written `scanner.c` share an ABI — a drifting `--depth 1` clone
of the default branch could pair this parser with an incompatible scanner.

## Regenerating

Needed only when bumping the pinned commit. Wants ~6 GB free and a quarter hour:

```bash
REGENERATE=1 ./run.sh                          # ignores this directory
cp spantree/src/parser.c spantree/src/node-types.json vendor/spantree-parser/
cp spantree/src/tree_sitter/*.h vendor/spantree-parser/tree_sitter/
```

Then update the commit above, and `SPANTREE_COMMIT` in `run.sh`. Generation is
deterministic — regenerating the same commit reproduces `parser.c` byte for byte,
so a non-empty diff means the grammar actually changed.
