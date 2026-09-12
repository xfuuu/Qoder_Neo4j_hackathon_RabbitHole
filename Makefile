.PHONY: setup render check dashboard clean

setup: .venv render

.venv:
	python3 -m venv .venv
	./.venv/bin/pip install -q -r requirements.txt

render:
	@./scripts/render.sh

# The live view: blue is what agent 1 wrote, red agent 2, gold the chain between the
# two names. The graph refreshes every 5s on its own while a run digs.
dashboard:
	./.venv/bin/streamlit run app.py

# Prove every MCP server starts, speaks MCP, and exposes the tools it should.
check:
	@PYTHONPATH=servers ./.venv/bin/python scripts/check.py

clean:
	rm -rf .venv .mcp.json
	find . -name __pycache__ -not -path "./.venv/*" -exec rm -rf {} +
