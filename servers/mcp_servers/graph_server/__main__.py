"""MCP server for the two-sided search graph in Neo4j.

The main agent starts a bridge and asks whether the two frontiers have met; the two
research agents write what their searches turned up and ask what to expand next. The
schema and both decisive queries live in `graph.py`, so no agent writes Cypher.

    python -m mcp_servers.graph_server
"""

from __future__ import annotations

from typing import Any

from mcp.server import MCPServer

from .. import runs
from . import graph


def render_path(result: dict) -> str:
    segments, via = result["segments"], result["via"]
    head = f"PATH FOUND — {len(segments)} hops"
    if via:
        head += f", junction: {via} (degree {result['degree']} — the least-connected node both sides reached)"
    lines = [head + "\n", segments[0]["from"]]
    for seg in segments:
        mark = "   <- junction" if seg["to"] == via else ""
        arrow = f"--[{seg['type']}]-->" if seg["forward"] else f"<--[{seg['type']}]--"
        lines.append(
            f"  {arrow}  {seg['to']}{mark}\n"
            f"      {seg['side']} side · {seg['url'] or 'no source — a guess'}"
        )
    return "\n".join(lines)


def render_status() -> str:
    s = graph.stats()
    steps = s.get("steps", 0)
    body = [
        f"steps used: {steps} of {graph.STEP_BUDGET}"
        + ("  — BUDGET SPENT, stop and report" if steps >= graph.STEP_BUDGET else ""),
        f"entities: {s.get('entities', 0)} (a: {s.get('a_side', 0)}, b: {s.get('b_side', 0)}, "
        f"reached by both: {s.get('shared', 0)}) · relationships: {s.get('rels', 0)} · "
        f"unexpanded: {s.get('unexpanded', 0)}",
    ]
    shared = graph.shared_names()
    if shared:
        body.append("both sides reached: " + ", ".join(shared))
    result = graph.path()
    body.append(render_path(result) if result else "no path between the seeds yet")
    return "\n".join(body)


def register(mcp: MCPServer) -> None:
    @mcp.tool()
    def bridge_start(term_a: str, term_b: str) -> str:
        """Begin a bridge run: seed both terms and open a run directory.

        The graph is never cleared. Everything earlier runs wrote stays, so a path may
        already exist between these two seeds before you search at all — this tool says so
        if it does.

        Args:
            term_a: The term side A expands from.
            term_b: The term side B expands from.
        """
        inherited = graph.clear_run_state()
        graph.seed(term_a, term_b)
        run_id = runs.new_run(f"bridge: {term_a} <-> {term_b}")
        lines = [
            f"run {run_id} started",
            f"directory: {runs.run_dir(run_id)}",
            f"inherited from earlier runs: {inherited.get('entities', 0)} entities, "
            f"{inherited.get('rels', 0)} relationships",
            f"side a seed: {term_a}",
            f"side b seed: {term_b}",
        ]
        path = graph.path()
        lines.append(
            render_path(path) + "\n(this path came from earlier runs — read its hops before "
            "reporting it, and keep expanding if the junction is a hub that says nothing)"
            if path
            else "(no path between these seeds yet)"
        )
        return "\n".join(lines)

    @mcp.tool()
    def graph_add(
        side: str,
        source_term: str,
        records: list[dict[str, Any]],
        round_no: int = 0,
    ) -> str:
        """Write one term's expansion into the graph, and report whether the sides have met.

        Every record is one relationship between two named things:
          {"subject": "...", "relation": "...", "object": "...",
           "source_url": "https://...", "snippet": "the words that said so"}

        `relation` is a short verb phrase — KNIFED, OUSTED, FEUDED_WITH, LIVED_WITH,
        PARTIED_WITH, SUBTWEETED. Specific beats respectable. Subjects and objects are
        proper nouns, not sentences. `source_url` may be empty when the edge is your own
        hunch; the snippet then says what made you think so.

        Call this even when a term yielded nothing (empty `records`): it marks
        `source_term` expanded so the frontier does not hand it back.

        Args:
            side: 'a' or 'b' — which frontier you are.
            source_term: The frontier term you just expanded.
            records: The relationships you extracted.
            round_no: The round the main agent gave you.
        """
        if side not in ("a", "b"):
            return "error: side must be 'a' or 'b'"
        result = graph.add(side, source_term, records, round_no)
        steps = graph.stats().get("steps", 0)
        lines = [
            f"{source_term!r} marked expanded · {result['written']} relationships written "
            f"· step {steps} of {graph.STEP_BUDGET}",
        ]
        if result["new_entities"]:
            lines.append("new entities: " + ", ".join(result["new_entities"]))
        path = graph.path()
        lines.append(render_path(path) if path else "(no path between the seeds yet)")
        return "\n".join(lines)

    @mcp.tool()
    def frontier(side: str, limit: int = 8) -> str:
        """The terms on your side that nothing has expanded yet, shallowest first.

        Args:
            side: 'a' or 'b'.
            limit: How many terms to return (default 8).
        """
        terms = graph.frontier(side, limit)
        if not terms:
            return f"side {side} has no unexpanded terms"
        return "\n".join(f"{t['name']}  (depth {t['depth']})" for t in terms)

    @mcp.tool()
    def bridge_status() -> str:
        """Steps spent, where the two frontiers stand, and the path between the seeds if one exists.

        The path runs through the least-connected node both sides reached, not simply the
        shortest chain — a hub everyone touches is a boring answer.
        """
        return render_status()


def build() -> MCPServer:
    mcp = MCPServer(name="graph")
    register(mcp)
    return mcp


def main() -> None:
    build().run("stdio")


if __name__ == "__main__":
    main()
