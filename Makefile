.PHONY: setup render check dashboard clean

setup: .venv render

.venv:
	python3 -m venv .venv
	./.venv/bin/pip install -q -r requirements.txt

render:
	@./scripts/render.sh

# The live view of the graph: two agents' frontiers in blue and red, the nodes both
# reached in gold, refreshing every 2s while a run digs.
dashboard:
	./.venv/bin/streamlit run app.py

# Prove both servers actually speak MCP and list the tools they expose.
check:
	@PYTHONPATH=servers ./.venv/bin/python scripts/check.py

clean:
	rm -rf .venv .mcp.json
	find . -name __pycache__ -not -path "./.venv/*" -exec rm -rf {} +
