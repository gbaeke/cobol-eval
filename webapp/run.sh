#!/bin/bash
# Start the COBOL graph explorer.
#   GATEWAY_API_KEY=<key from http://localhost:4000/ui/> ./run.sh
# Optional: GATEWAY_MODEL (default anthropic-prod/fast), GRAPH_PATH, PORT, HOST.
cd "$(dirname "$0")"
export GATEWAY_URL="${GATEWAY_URL:-http://localhost:4000}"
export GATEWAY_MODEL="${GATEWAY_MODEL:-anthropic-prod/fast}"
export GRAPH_PATH="${GRAPH_PATH:-/home/azureuser/cobol-eval/carddemo-graph/graphify-out/graph.json}"
[ -z "${GATEWAY_API_KEY:-}" ] && echo "note: GATEWAY_API_KEY unset — browsing works, Ask will return 503"
exec .venv/bin/uvicorn app:app --host "${HOST:-127.0.0.1}" --port "${PORT:-8899}"
