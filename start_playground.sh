#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

if [[ ! -x "$ROOT/.venv/bin/streamlit" ]]; then
  echo "Playground environment missing. Run ./setup.sh first." >&2
  exit 1
fi

export STREAMLIT_BROWSER_GATHER_USAGE_STATS=false
"$ROOT/.venv/bin/streamlit" run scada_playground/app.py \
  --server.address 127.0.0.1 --server.port 8501 --server.headless true &
APP_PID=$!
GATEWAY_PID=""
MCP_PID=""

cleanup() {
  [[ -n "$MCP_PID" ]] && kill "$MCP_PID" 2>/dev/null || true
  [[ -n "$GATEWAY_PID" ]] && kill "$GATEWAY_PID" 2>/dev/null || true
  kill "$APP_PID" 2>/dev/null || true
  [[ -n "$MCP_PID" ]] && wait "$MCP_PID" 2>/dev/null || true
  [[ -n "$GATEWAY_PID" ]] && wait "$GATEWAY_PID" 2>/dev/null || true
  wait "$APP_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

SNAPSHOT="$ROOT/scada_playground/.runtime/factory_snapshot.json"
"$ROOT/.venv/bin/python" scada_playground/mcp_server.py --transport streamable-http &
MCP_PID=$!

# Streamlit only creates the simulator session after a browser opens the UI.
# Keep the protocol gateway waiting so a clean clone can start without a stale
# runtime snapshot and without making the user race a startup timeout.
(
  trap 'exit 0' INT TERM
  while [[ ! -s "$SNAPSHOT" ]]; do sleep 0.5; done
  exec "$ROOT/.venv/bin/python" scada_playground/protocol_gateway.py
) &
GATEWAY_PID=$!

echo "SCADA playground: http://127.0.0.1:8501"
echo "Live map: http://127.0.0.1:8502/map"
echo "MCP server (simulation controls + read tools): http://127.0.0.1:8503/mcp"
echo "Stop all local services with Ctrl+C."

set +e
wait -n "$APP_PID" "$GATEWAY_PID" "$MCP_PID"
STATUS=$?
set -e
exit "$STATUS"
