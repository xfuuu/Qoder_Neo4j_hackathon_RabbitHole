"""MCP server for Firecrawl: search the web, scrape a page, drive it.

Two profiles over one codebase. The director gets search; the explorer gets scrape
and interact. Page bodies are reachable only from the explorer, which is what keeps
them out of the director's context.

Credits and scraped pages are filed against the active research run when there is
one (`mcp_servers.runs`); a search with no run in progress — which is how the digest
uses this server — simply goes uncounted.

    python -m mcp_servers.websearch_server --profile director
    python -m mcp_servers.websearch_server --profile explorer
"""

from __future__ import annotations

import argparse
import re

from mcp.server import MCPServer

from .. import runs
from ..lock import guard
from . import sessions
from .firecrawl import Firecrawl, Saturated

_client: Firecrawl | None = None


def client() -> Firecrawl:
    global _client
    if _client is None:
        _client = Firecrawl()
    return _client


# ---------------------------------------------------------------- director


def register_director(mcp: MCPServer) -> None:
    @mcp.tool()
    def search(
        query: str,
        limit: int = 8,
        categories: list[str] | None = None,
        include_domains: list[str] | None = None,
        exclude_domains: list[str] | None = None,
        tbs: str | None = None,
        country: str | None = None,
    ) -> str:
        """Search the web. Returns titles, URLs and snippets — never page bodies.

        Args:
            query: The search query.
            limit: Number of results (default 8).
            categories: Restrict to 'github', 'research' or 'pdf'.
            include_domains: Only these domains.
            exclude_domains: Never these domains.
            tbs: Recency filter, e.g. 'qdr:w' for the past week.
            country: ISO country code, default US.
        """
        data = client().search(
            query,
            limit=limit,
            categories=categories,
            includeDomains=include_domains,
            excludeDomains=exclude_domains,
            tbs=tbs,
            country=country,
        )
        spent = runs.spend(data.get("creditsUsed", 0))
        results = data.get("data", {}).get("web", [])
        if not results:
            return f"no results for {query!r} ({spent})"

        lines = [f"{len(results)} results for {query!r} ({spent})\n"]
        for i, r in enumerate(results, 1):
            lines.append(f"{i}. {r.get('title', '(untitled)')}\n   {r.get('url', '')}")
            if r.get("description"):
                lines.append(f"   {r['description']}")
        return "\n".join(lines)


# ---------------------------------------------------------------- explorer


def _save_page(url: str, markdown: str) -> str:
    pages = runs.run_dir() / "pages"
    pages.mkdir(exist_ok=True)
    with guard(pages / "id"):  # two parallel explorers must not claim the same id
        existing = [int(m.group(1)) for p in pages.glob("p*.md") if (m := re.fullmatch(r"p(\d+)", p.stem))]
        page_id = f"p{max(existing, default=0) + 1}"
        (pages / f"{page_id}.md").write_text(f"<!-- {url} -->\n\n{markdown}")
    return page_id


def register_explorer(mcp: MCPServer) -> None:
    @mcp.tool()
    def scrape(url: str, wait_for: int = 0) -> str:
        """Fetch one page as Markdown and open a browser session on it.

        Args:
            url: The page to fetch.
            wait_for: Milliseconds to wait for JS to settle before reading.
        """
        data = client().scrape(url, wait_for=wait_for).get("data", {})
        meta = data.get("metadata", {})
        markdown = data.get("markdown", "")
        scrape_id = meta.get("scrapeId", "")
        spent = runs.spend(meta.get("creditsUsed", 0))

        page_id = _save_page(url, markdown)
        header = (
            f"page_id: {page_id}   scrape_id: {scrape_id}   "
            f"status: {meta.get('statusCode')}   cache: {meta.get('cacheState')}   {spent}\n"
            f"title: {meta.get('title', '')}\n{'-' * 60}\n"
        )
        return header + markdown

    @mcp.tool()
    def interact(
        scrape_id: str,
        prompt: str | None = None,
        code: str | None = None,
        language: str = "node",
        timeout: int = 30,
    ) -> str:
        """Act on a scraped page in its live browser session: click, type, scroll, paginate.

        Use only when the scrape alone cannot answer the question.

        Args:
            scrape_id: From a previous scrape.
            prompt: What to do, in natural language. Either this or `code`.
            code: A Playwright/bash script. Either this or `prompt`.
            language: 'node', 'python' or 'bash'. Only with `code`.
            timeout: Seconds, capped at 60.
        """
        if not prompt and not code:
            return "error: pass either prompt or code"
        try:
            result = client().interact(
                scrape_id, prompt=prompt, code=code, language=language, timeout=min(timeout, 60)
            )
        except Saturated:
            return (
                "no browser session available — the Firecrawl account is at its concurrency "
                "limit. Do not retry. Work from the scraped markdown instead, and if the answer "
                "is genuinely only reachable by interacting, report ANSWERED: no and say why."
            )
        sessions.note_open(scrape_id, "")
        parts = [str(result.get(k)) for k in ("output", "result", "stdout") if result.get(k)]
        if result.get("stderr"):
            parts.append(f"stderr: {result['stderr']}")
        remaining = max(0, int(sessions.MAX_AGE - sessions.age(scrape_id)))
        parts.append(f"\n(session closes in ~{remaining}s — call interact_stop when done)")
        return "\n".join(parts) or "(no output)"

    @mcp.tool()
    def interact_stop(scrape_id: str) -> str:
        """Close a page's browser session. Call this as soon as you are done with it."""
        try:
            client().interact_stop(scrape_id)
        except Exception as exc:
            sessions.note_closed(scrape_id)
            return f"session already gone ({exc})"
        sessions.note_closed(scrape_id)
        return f"session {scrape_id} closed"


# ---------------------------------------------------------------- entry


def build(profile: str) -> MCPServer:
    mcp = MCPServer(name=f"websearch-{profile}")
    if profile == "director":
        register_director(mcp)
    else:
        register_explorer(mcp)
        sessions.start_reaper(client())
    return mcp


def main() -> None:
    parser = argparse.ArgumentParser(prog="websearch_server")
    parser.add_argument("--profile", choices=("director", "explorer"), required=True)
    build(parser.parse_args().profile).run("stdio")


if __name__ == "__main__":
    main()
