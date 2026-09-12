"""Speak MCP to both servers over stdio and print the tools each one exposes."""

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = str(ROOT / ".venv" / "bin" / "python")
X = json.loads((ROOT / ".mcp.json").read_text())["mcpServers"]

SERVERS = {
    "graph": [PY, "-m", "mcp_servers.graph_server"],
    "websearch (director)": [PY, "-m", "mcp_servers.websearch_server", "--profile", "director"],
    "explorer": [PY, "-m", "mcp_servers.websearch_server", "--profile", "explorer"],
    "x (director)": [X["x"]["command"], *X["x"]["args"]],
}


def tools(cmd):
    requests = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                    "clientInfo": {"name": "check", "version": "0"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
    ]
    out = subprocess.run(
        cmd, cwd=ROOT,
        env={"PYTHONPATH": str(ROOT / "servers"), "PATH": os.environ["PATH"],
             "AUTH_DIR": X["x"]["env"]["AUTH_DIR"], "HOME": os.environ["HOME"]},
        input="\n".join(json.dumps(r) for r in requests) + "\n",
        capture_output=True, text=True, timeout=60,
    ).stdout
    for line in out.splitlines():
        msg = json.loads(line)
        if msg.get("id") == 2:
            return [t["name"] for t in msg["result"]["tools"]]
    raise RuntimeError(f"no tools/list response:\n{out}")


failed = False
for name, cmd in SERVERS.items():
    try:
        names = tools(cmd)
    except Exception:
        names = None
    if names is None:  # a server can hit stdin EOF before it answers; ask once more
        try:
            names = tools(cmd)
        except Exception as exc:
            failed = True
            print(f"  {name}: FAILED — {exc}")
            continue
    print(f"  {name}: {', '.join(names)}")
sys.exit(1 if failed else 0)
