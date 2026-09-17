#!/bin/bash
# Idempotent, resumable COBOL grammar comparison. Safe to re-run; each step
# is skipped if its output already exists.
set -u
cd "$(dirname "$(readlink -f "$0")")"
ROOT=$PWD
export PATH="$ROOT/node-v22.14.0-linux-x64/bin:$PATH"
log() { echo "[$(date +%H:%M:%S)] $*"; }

# 1. node
if [ ! -x node-v22.14.0-linux-x64/bin/node ]; then
  log "downloading node"
  curl -sL -o node.tar.xz https://nodejs.org/dist/v22.14.0/node-v22.14.0-linux-x64.tar.xz && tar xf node.tar.xz && rm node.tar.xz
fi

# 2. yutaro grammar sources + build
if [ ! -f cobol/cobol.so ]; then
  log "fetching yutaro grammar"
  mkdir -p cobol/src/tree_sitter
  B=https://raw.githubusercontent.com/yutaro-sakamoto/tree-sitter-cobol/main
  [ -f cobol/src/parser.c ]  || curl -sfL $B/src/parser.c -o cobol/src/parser.c
  [ -f cobol/src/scanner.c ] || curl -sfL $B/src/scanner.c -o cobol/src/scanner.c
  [ -f cobol/src/tree_sitter/parser.h ] || curl -sfL $B/src/tree_sitter/parser.h -o cobol/src/tree_sitter/parser.h
  log "compiling yutaro .so"
  gcc -O0 -fPIC -shared -Icobol/src cobol/src/parser.c cobol/src/scanner.c -o cobol/cobol.so || exit 1
fi

# 3. CardDemo corpus
if [ ! -d carddemo/cbl ] || [ "$(ls carddemo/cbl 2>/dev/null | wc -l)" -lt 31 ]; then
  log "downloading CardDemo corpus"
  mkdir -p carddemo/cbl carddemo/cpy
  for d in cbl cpy; do
    gh api repos/aws-samples/aws-mainframe-modernization-carddemo/contents/app/$d \
      --jq '.[]|"\(.name)\t\(.download_url)"' | while IFS=$'\t' read -r n u; do
        [ -s "carddemo/$d/$n" ] || curl -sL "$u" -o "carddemo/$d/$n"
      done
  done
fi

# 4. python env
if [ ! -x .venv/bin/python ]; then
  log "creating venv"
  uv venv -q .venv && VIRTUAL_ENV=.venv uv pip install -q "tree-sitter>=0.23,<0.26"
fi

# 5. spantree clone + generate (the memory hog: capped at 2.5 GB virtual so the
#    OOM killer takes THIS process, not the parent session)
[ -d spantree ] || { log "cloning spantree"; git clone --depth 1 -q https://github.com/Spantree/tree-sitter-cobol-enterprise.git spantree; }
if [ ! -f spantree/src/parser.c ]; then
  cd spantree
  [ -d node_modules/tree-sitter-cli ] || { log "installing tree-sitter-cli"; npm install --silent tree-sitter-cli@0.25.9 >>../gen.log 2>&1; }
  log "generating spantree parser (capped 2.5G, niced)"
  # oom_score_adj=1000 makes the kernel pick THIS process first under pressure,
  # without capping its address space (ulimit -v kills the Rust CLI instantly).
  ( echo 1000 > /proc/self/oom_score_adj; exec nice -n 19 npx tree-sitter generate ) >>../gen.log 2>&1
  rc=$?
  cd ..
  log "generate exit=$rc"
  [ -f spantree/src/parser.c ] || { log "FAILED: no parser.c"; exit 1; }
fi
log "parser.c = $(stat -c%s spantree/src/parser.c) bytes"

# 6. build spantree .so
if [ ! -f spantree/cobol_ent.so ]; then
  log "compiling spantree .so"
  gcc -O0 -fPIC -shared -Ispantree/src spantree/src/parser.c spantree/src/scanner.c -o spantree/cobol_ent.so || exit 1
fi
nm -D --defined-only spantree/cobol_ent.so | grep -i "tree_sitter_" | head -3

# 7. compare
log "running comparison"
.venv/bin/python compare.py > results.txt 2>compare.err
log "done -> results.txt"
tail -12 results.txt

# 8. COBOL knowledge graph — the actual point of this repo.
#    graphify has no COBOL extractor upstream, so this installs ours
#    (extensions/graphify-cobol) plus the parser it imports, then builds the
#    graph the webapp browses. `update` re-extracts without an LLM: no API key.
if [ ! -s carddemo-graph/graphify-out/graph.json ]; then
  # 8a. graphify checkout
  [ -d gf ] || { log "cloning graphify"; git clone --depth 1 -q https://github.com/Graphify-Labs/graphify.git gf; }

  # 8b. our COBOL extractor, wired into graphify's dispatch tables
  log "installing COBOL extractor into graphify"
  extensions/graphify-cobol/install.sh "$ROOT/gf" || exit 1

  # 8c. the parser that extractor imports. Without it the extractor returns
  #     zero nodes with an error string, i.e. an EMPTY graph, not a crash.
  [ -d pywheel/src ] || cp -r spantree/src pywheel/src

  # 8d. one venv holding graphify (from the patched checkout) + the parser
  if [ ! -x .venv-graphify/bin/python ]; then
    log "creating graphify venv"
    uv venv -q .venv-graphify
    VIRTUAL_ENV=.venv-graphify uv pip install -q -e ./gf ./pywheel || exit 1
  fi

  # 8e. build it
  log "building carddemo graph (several minutes)"
  mkdir -p carddemo-graph/cbl carddemo-graph/cpy
  cp carddemo/cbl/* carddemo-graph/cbl/
  cp carddemo/cpy/* carddemo-graph/cpy/
  .venv-graphify/bin/python -m graphify update carddemo-graph --no-cluster >>gen.log 2>&1

  # 8f. Count PROGRAM nodes, not all nodes: graphify emits a node per file
  #     regardless, so a total count still looks healthy when the extractor
  #     silently returned nothing. Programs exist only if it actually parsed.
  N=$(.venv-graphify/bin/python -c "import json; g=json.load(open('carddemo-graph/graphify-out/graph.json')); print(sum(1 for n in g.get('nodes',[]) if n.get('kind')=='program'))" 2>/dev/null || echo 0)
  if [ "${N:-0}" -lt 25 ]; then
    log "FAILED: only $N program nodes (expected 31) — is tree_sitter_cobol"
    log "        importable in .venv-graphify? see gen.log"
    exit 1
  fi
  log "graph = $N programs -> carddemo-graph/graphify-out/graph.json"
fi
