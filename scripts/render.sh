#!/bin/bash
# Render .mcp.json, the one file that needs an absolute path: Claude Code resolves
# neither ~ nor ${HOME} in an MCP command. Re-run this after moving the folder.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# X is not ours: it is the vendored server in the sibling toolkit, patched there for the
# director/explorer profile split and holding the one browser login. Override with
# X_ROOT=... if the toolkit lives somewhere else.
X_ROOT="${X_ROOT:-$ROOT/../claude-toolkit/servers/x}"
if [ ! -f "$X_ROOT/dist/mcp.js" ]; then
  echo "ERROR: no X server at $X_ROOT/dist/mcp.js"
  echo "       build it there (cd \"$X_ROOT/..\"/.. && make build) or pass X_ROOT=..."
  exit 1
fi
X_ROOT="$(cd "$X_ROOT" && pwd)"

sed -e "s|@@ROOT@@|$ROOT|g" -e "s|@@X_ROOT@@|$X_ROOT|g" "$ROOT/.mcp.json.in" > "$ROOT/.mcp.json"
echo "    rendered .mcp.json for $ROOT"
echo "    X server: $X_ROOT"
