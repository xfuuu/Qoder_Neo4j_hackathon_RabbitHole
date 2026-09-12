# graph-bridge

Find how two famous American names are connected, by searching outward from both ends at once
— feuds, exes, diss tracks, unfollows, red-carpet snubs, lawsuits and whatever the internet is
saying. Pop stars, actors, athletes, reality TV, influencers, tech founders. Nothing is
fact-checked; a chain through a tabloid rumour is the point.

`/bridge <term A> <term B>` — a main agent runs rounds; each round it dispatches two
`frontier-researcher` agents, one per side. Each researcher searches its own frontier terms on
the open web and on X, reads the pages it finds through its own `page-explorer` / `pdf-reader`
subagents, and writes the named things it found into a shared Neo4j graph. The run ends when
the graph holds a path between the two seeds.

Three agent levels, and page bodies never reach the top two:

```
main agent (/bridge)            orchestrates rounds, reads graph status
├── frontier-researcher  side a   searches, extracts nouns + relations, writes graph
│   ├── page-explorer             reads one page
│   └── pdf-reader                reads one paper
└── frontier-researcher  side b   same, from the other end
```

## The dashboard

`make dashboard` opens the live view (Streamlit + pyvis, 2s auto-refresh): side A's frontier in
blue, side B's in red, the nodes both sides reached in gold, the two seeds with a dark border,
and the latest cross-agent connection spelled out hop by hop. Run it in one terminal and
`/bridge` in another to watch the two frontiers grow toward each other.

## Setup

```bash
make setup             # venv + deps, renders .mcp.json for this folder's path
cp .env.example .env   # Firecrawl key + Neo4j credentials
make check             # proves all three MCP servers speak MCP
```

Then run `claude` here once interactively and accept the trust dialog, or
`.claude/settings.json` is ignored and every tool call prompts.

If you move the folder, re-run `make render`.

## Use

```
/bridge Elon Musk | Taylor Swift
/bridge <any famous name> <any other famous name>
```

Watch it in Neo4j Browser while it runs:

```cypher
MATCH (e:Entity)-[r:RELATED]-() RETURN e, r
MATCH p = shortestPath((a:Entity {seed:'a'})-[:RELATED*..12]-(b:Entity {seed:'b'})) RETURN p
```

## The graph

One node label, one relationship type. The schema is fixed in `graph.py`, not left to the
agents, because the two queries the demo turns on — has it met, and what is the path — have
to run over a predictable shape.

```
(:Entity {key, name, sides, depth, expanded, seed})
(:Entity)-[:RELATED {type, source_url, snippet, side, round}]->(:Entity)
```

`key` is the identity (lowercased name), so `OpenAI` and `openai` are one node and
`Open AI` is a different one — name drift is the main way a run fails to meet. `sides`
records which frontier reached a node; a node holding both is where the two searches touched.

**The graph is never cleared.** Each run adds to it and resets only its own three properties
(`sides`, `expanded`, `seed`), so a later pair of names can already be connected through edges
an earlier run wrote — `bridge_start` reports that when it happens. To start over anyway:
`MATCH (n:Entity) DETACH DELETE n` in Neo4j Browser.

**The path is not simply the shortest one.** Among the nodes both sides reached, the junction
is the one with the fewest connections. A hub like Stanford or Microsoft joins any two names
in this scene and says nothing; a rarely-connected node is the whole point.

## Tools

| server | tool | who calls it |
|---|---|---|
| `graph` | `bridge_start` | main agent — clears the graph, seeds both terms |
| | `graph_add` | researchers — write one term's expansion, reports if a path now exists |
| | `frontier` | both — unexpanded terms on a side, shallowest first |
| | `bridge_status` | main agent — counts, shared nodes, the shortest path |
| `websearch` | `search` | researchers |
| `explorer` | `scrape`, `interact` | page-explorer only |
| `x` | `search` | researchers — recent terms, people, events |

## The X server

X is not in this folder. `render.sh` points both X servers at the vendored server in the
sibling `claude-toolkit/servers/x` — patched there for the director/explorer profile split,
and holding the one browser login in its `.auth`. Pass `X_ROOT=/path/to/servers/x make render`
if the toolkit lives elsewhere; `make render` fails loudly if it cannot find a built
`dist/mcp.js`.

The director profile ships every write tool X has — tweet, reply, like, retweet. The
researcher's tool list admits only `search`, and `.claude/settings.json` denies the write
tools outright. Keep both.

X reaches the graph through `x:search`, whose results carry each post's own text, author and
engagement. That text is where an X relationship comes from, and the post URL is its source.

## Cost

Firecrawl: 1 credit per search, ~2 per page read. A round is 2 researchers × up to 3 terms ×
up to 3 pages, so budget roughly 20–40 credits per round. The run stops at 20 expansion steps
— one term expanded by one researcher is one step — which is 3 to 4 rounds and, measured on
two earlier runs, 100–150 credits and under ten minutes.
