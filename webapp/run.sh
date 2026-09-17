#!/bin/bash
# Start the COBOL graph explorer.
#   GATEWAY_API_KEY=<key from http://localhost:4000/ui/> ./run.sh
# Optional: GATEWAY_MODEL (default anthropic-prod/fast), GRAPH_PATH, PORT, HOST.
#
# Needs a graph to browse: run ../run.sh first (its last step builds
# carddemo-graph/graphify-out/graph.json), or point GRAPH_PATH at your own.
set -u
cd "$(dirname "$(readlink -f "$0")")"

# Bootstrap the venv on first run; uv if present, else stdlib venv + pip.
if [ ! -x .venv/bin/uvicorn ]; then
  echo "creating webapp venv"
  if command -v uv >/dev/null; then
    uv venv -q .venv && VIRTUAL_ENV=.venv uv pip install -q -r requirements.txt
  else
    python3 -m venv .venv && .venv/bin/pip install -q -r requirements.txt
  fi
fi

export GATEWAY_URL="${GATEWAY_URL:-http://localhost:4000}"
export GATEWAY_MODEL="${GATEWAY_MODEL:-anthropic-prod/fast}"
export GRAPH_PATH="${GRAPH_PATH:-$PWD/../carddemo-graph/graphify-out/graph.json}"
[ -s "$GRAPH_PATH" ] || echo "note: no graph at $GRAPH_PATH — run ../run.sh first"
[ -z "${GATEWAY_API_KEY:-}" ] && echo "note: GATEWAY_API_KEY unset — browsing works, Ask will return 503"
exec .venv/bin/uvicorn app:app --host "${HOST:-127.0.0.1}" --port "${PORT:-8899}"
