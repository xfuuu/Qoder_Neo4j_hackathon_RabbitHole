"""Live multi-agent shared memory graph dashboard backed by Neo4j AuraDB.

Schema (see graph.py):
  (:Entity {key, name, sides, depth, expanded, seed})
  (:Entity)-[:RELATED {type, source_url}]->(:Entity)
side 'a' = Agent 1's frontier, side 'b' = Agent 2's frontier.
"""

import html
import os
import re
import subprocess

import httpx
import streamlit as st
import streamlit.components.v1 as components
from dotenv import load_dotenv
from neo4j import GraphDatabase, READ_ACCESS
from pyvis.network import Network

SIDE_A = "a"  # Agent 1
SIDE_B = "b"  # Agent 2

# Brutalist palette: flat ink and three saturated blocks, nothing in between.
COLOR_AGENT_1 = "#0047FF"  # Agent 1
COLOR_AGENT_2 = "#FF2200"  # Agent 2
COLOR_BRIDGE = "#FFD400"   # Shared bridge
COLOR_NEUTRAL = "#B8B8B8"  # left over from an earlier pair
COLOR_SEED_BORDER = "#000000"
COLOR_INK = "#000000"

# Every relationship, no cap. Ordered so the fetch is stable between refreshes —
# an unordered result reshuffles the layout every few seconds and nothing stays
# still enough to click.
GRAPH_QUERY = "MATCH (n:Entity)-[r]->(m:Entity) RETURN n, r, m ORDER BY elementId(r)"
REFRESH_INTERVAL_MS = 5000

# The story box above the graph
LLM_MODEL = "claude-opus-5"
PICK_PARAM = "pick"          # what the graph reports as clicked: an edge or a node
EDGE_SEP = "\u241f"           # separator inside that param
SOURCE_CHARS = 6000          # how much of a source page the model gets to read
DISPLAY_NODES = 120          # busiest nodes drawn, on top of the picked path and seeds

STORY_SYSTEM = (
    "You tell the story behind a connection in a celebrity gossip graph, from the "
    "relationships and the source pages you are given. Write it the way the scene "
    "itself would tell it: concrete, specific, the detail that makes it worth "
    "repeating — the quote, the number, the petty part. Never editorialise, never "
    "moralise, never explain why it matters. Name people exactly as the graph spells "
    "them. Say only what the sources support. No preamble, no lists, no headings, no "
    "notes about what a source leaves out."
)

# One link, so one beat.
EDGE_BRIEF = "Answer in at most two sentences, under 45 words: what happened, and how it made this link."

# A chain: every name on it has to earn its sentence, in order, or the path reads as
# a hop the story skipped.
PATH_BRIEF = (
    "Walk the chain from one end to the other, in order. Every person, company and "
    "thing named in it must appear at least once, and each link gets its own short "
    "sentence about the incident that made that link — what one of them did to or with "
    "the other, when, and the detail people remember it by. Nothing else: no birthplaces, "
    "no career summaries, no marriages, deals or children that are not links on this "
    "chain. If a source does not say what happened between two of them, say plainly that "
    "the link is recorded but unexplained rather than filling it with biography. Under "
    "110 words, and make the last sentence land."
)


# Brutalist: black rules, no radii, no shadows, monospace, flat colour blocks. The
# Streamlit overrides are here too — its own chrome is rounded and soft by default.
BRUTAL_CSS = """<style>
  html, body, [class*="st-"], .stApp, button, input, select, textarea {
    font-family: "Courier New", ui-monospace, monospace !important;
  }
  .stApp { background: #F2F2F2; }
  /* Streamlit's own top bar: Deploy button, running man, hamburger */
  header[data-testid="stHeader"], [data-testid="stToolbar"] { display: none !important; }
  .block-container { padding-top: 1.5rem !important; max-width: 1400px; }
  h1, h2, h3, p, div { letter-spacing: 0.01em; }

  .header-bar {
    display: flex; align-items: center; justify-content: space-between;
    padding: 18px 22px; margin-bottom: 16px;
    background: #000000; border: 4px solid #000000;
  }
  .header-title {
    font-size: 34px; font-weight: 700; color: #FFFFFF;
    text-transform: uppercase; letter-spacing: 0.06em;
  }
  .live-badge {
    color: #000000; background: #FFD400; border: 3px solid #FFFFFF;
    font-weight: 700; font-size: 13px; letter-spacing: 0.18em; padding: 4px 10px;
  }

  .stat-row { display: flex; gap: 0; margin: 0 0 16px 0; border: 4px solid #000000; }
  .stat { flex: 1; padding: 12px 16px; border-right: 4px solid #000000; }
  .stat:last-child { border-right: none; }
  .stat-label {
    font-size: 11px; font-weight: 700; letter-spacing: 0.18em; color: #000000;
  }
  .stat-value { font-size: 40px; font-weight: 700; line-height: 1.1; color: #000000; }

  .legend { display: flex; flex-wrap: wrap; gap: 18px; margin: 0 0 14px 0; }
  .legend .key {
    display: inline-flex; align-items: center; gap: 7px;
    font-size: 11px; font-weight: 700; letter-spacing: 0.12em;
  }
  .legend .swatch {
    width: 14px; height: 14px; display: inline-block; border: 3px solid #000000;
  }

  .story-box {
    border: 4px solid #000000; background: #FFFFFF; padding: 16px 18px;
    margin: 0 0 14px 0; font-size: 15px; line-height: 1.65; color: #000000;
  }
  .story-box .story-head {
    font-size: 11px; letter-spacing: 0.18em; font-weight: 700;
    text-transform: uppercase; background: #000000; color: #FFFFFF;
    display: inline-block; padding: 3px 8px; margin-bottom: 10px;
  }
  .story-box .chain { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; }
  .story-box .pill {
    border: 3px solid #000000; background: #FFD400; color: #000000;
    padding: 2px 7px; font-weight: 700; white-space: nowrap;
  }
  .story-box .rel {
    font-size: 10px; font-weight: 700; letter-spacing: 0.1em; color: #000000;
  }
  .story-box .told { margin-top: 12px; padding-top: 12px; border-top: 4px solid #000000; }
  .story-box .told .pill { background: #FFFFFF; }
  .story-box.empty { background: #FFFFFF; color: #000000; }

  .runbar {
    border: 4px solid #000000; padding: 8px 12px; margin-bottom: 14px;
    font-size: 12px; font-weight: 700; letter-spacing: 0.14em; text-transform: uppercase;
  }
  .stTextInput input {
    border: 4px solid #000000 !important; border-radius: 0 !important;
    background: #FFFFFF !important; color: #000000 !important;
    font-weight: 700 !important; padding: 10px 12px !important;
  }
  .stTextInput input:focus { box-shadow: none !important; background: #FFD400 !important; }
  [data-testid="stForm"] { border: none; padding: 0; }

  /* Streamlit's own widgets */
  .stButton > button {
    border: 4px solid #000000 !important; border-radius: 0 !important;
    background: #FFFFFF !important; color: #000000 !important;
    font-weight: 700 !important; letter-spacing: 0.12em; text-transform: uppercase;
    box-shadow: none !important;
  }
  .stButton > button:hover { background: #FFD400 !important; }
  .stButton > button[kind="primary"] { background: #000000 !important; color: #FFFFFF !important; }
  .stButton > button[kind="primary"]:hover { background: #FF2200 !important; }
  [data-testid="stAlert"] { border: 4px solid #000000; border-radius: 0; }
  iframe { border: 4px solid #000000 !important; background: #FFFFFF; }
</style>"""

# vis-network fires this inside the pyvis iframe; the only channel back to Streamlit
# is the parent page's query string, so a click navigates the top frame.
PICK_JS = r"""
<script type="text/javascript">
  // pyvis creates `network` after this script is parsed, so wait for it.
  (function wire() {
    if (typeof network === "undefined" || !network || typeof edges === "undefined") {
      setTimeout(wire, 100);
      return;
    }
    // Freeze the layout once it settles: a line that is still drifting is a line
    // nobody can click.
    network.once("stabilizationIterationsDone", function () {
      network.setOptions({physics: false});
    });
    function send(pick) {
      window.parent.postMessage({pick: pick}, "*");
    }

    // Replay: freeze every node where it already sits, empty the canvas, then put the
    // links back in the order they were written. Positions are pinned first, so what
    // moves is arrival, not the layout.
    var replaying = false;
    window.addEventListener("message", function (event) {
      if (!event.data || !event.data.replay || replaying) return;
      replaying = true;
      var allNodes = nodes.get(), allEdges = edges.get();
      var at = network.getPositions();
      allNodes.forEach(function (n) {
        if (at[n.id]) { n.x = at[n.id].x; n.y = at[n.id].y; }
        n.physics = false;
      });
      var byId = {};
      allNodes.forEach(function (n) { byId[n.id] = n; });
      network.setOptions({physics: false});
      edges.clear();
      nodes.clear();
      var i = 0;
      var timer = setInterval(function () {
        if (i >= allEdges.length) {
          clearInterval(timer);
          nodes.update(allNodes);
          replaying = false;
          return;
        }
        var e = allEdges[i++];
        [e.from, e.to].forEach(function (id) {
          if (byId[id] && !nodes.get(id)) nodes.add(byId[id]);
        });
        edges.add(e);
      }, 70);
    });
    network.on("selectEdge", function (params) {
      if (!params.nodes.length && params.edges.length === 1) {
        var e = edges.get(params.edges[0]);
        if (e) send(["edge", e.from, e.to, (e.title || "").split("\n")[0]].join("\u241f"));
      }
    });
    network.on("selectNode", function (params) {
      if (params.nodes.length === 1) send(["node", params.nodes[0]].join("\u241f"));
    });
  })();
</script>
"""

# vis-network renders string titles as plain text, so tooltips use \n and this
# injected stylesheet breaks the lines (and styles the popup).
TOOLTIP_CSS = """<style>
.vis-tooltip {
  white-space: pre-line;
  font-family: 'Courier New', monospace;
  font-size: 12px; line-height: 1.5;
  padding: 8px 10px; border-radius: 0;
  background: #000000 !important; color: #FFFFFF !important;
  border: 3px solid #FFD400 !important;
  box-shadow: none;
}
</style>"""


COMPONENT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "components", "graph_click")
graph_click = components.declare_component("graph_click", path=COMPONENT_DIR)


def load_config():
    """Read Neo4j credentials from the environment (.env supported)."""
    load_dotenv(override=True)  # re-read on every rerun; process env may be stale
    return {
        "NEO4J_URI": os.getenv("NEO4J_URI"),
        # Aura's own credentials file spells it NEO4J_USERNAME; accept either.
        "NEO4J_USER": os.getenv("NEO4J_USERNAME") or os.getenv("NEO4J_USER"),
        "NEO4J_PASSWORD": os.getenv("NEO4J_PASSWORD"),
    }


@st.cache_resource
def get_driver(uri, user, password):
    """Create and verify a Neo4j driver (cached across auto-refreshes)."""
    driver = GraphDatabase.driver(uri, auth=(user, password))
    driver.verify_connectivity()
    return driver


def fetch_graph(driver):
    """Fetch every connected entity and relationship, in read mode."""
    nodes, edges = {}, []
    with driver.session(default_access_mode=READ_ACCESS) as session:
        for record in session.run(GRAPH_QUERY):
            for key in ("n", "m"):
                node = record[key]
                nodes[node.element_id] = {
                    "labels": list(node.labels),
                    "props": dict(node),
                }
            rel = record["r"]
            rel_props = dict(rel)
            edges.append(
                {
                    "source": rel.start_node.element_id,
                    "target": rel.end_node.element_id,
                    "type": str(rel_props.get("type") or rel.type),
                    "source_url": rel_props.get("source_url"),
                    "snippet": rel_props.get("snippet"),
                    "id": rel.element_id,
                }
            )
    return nodes, edges


def side_set(props):
    sides = props.get("sides") or []
    if isinstance(sides, str):
        sides = [sides]
    return set(sides)


CHAIN_HOPS = 6               # longest seed-to-seed chain a node can sit on and count


def seed_path_nodes(nodes, edges, max_hops=CHAIN_HOPS):
    """Every node that sits on *some* chain between the two seeds, not just the shortest.

    `sides` says who wrote a node, which is not who the chains run through: a link can
    close through an edge an earlier run wrote. And the shortest chain alone is too
    narrow — a name one hop off it is still standing between the two seeds. So walk
    every simple path up to `max_hops`, pruned by distance to the far seed so the walk
    stays cheap.
    """
    seeds = seed_ids(nodes)
    if SIDE_A not in seeds or SIDE_B not in seeds:
        return set()
    adjacency = {}
    for edge in edges:
        adjacency.setdefault(edge["source"], set()).add(edge["target"])
        adjacency.setdefault(edge["target"], set()).add(edge["source"])

    start, goal = seeds[SIDE_A], seeds[SIDE_B]

    # hops from every node to the far seed, so a branch that cannot reach it in the
    # budget left is never walked
    to_goal, frontier, depth = {goal: 0}, [goal], 0
    while frontier:
        depth += 1
        nxt = []
        for node in frontier:
            for neighbour in adjacency.get(node, ()):
                if neighbour not in to_goal:
                    to_goal[neighbour] = depth
                    nxt.append(neighbour)
        frontier = nxt

    on_chain = set()
    stack = [(start, (start,), frozenset({start}))]
    while stack:
        node, trail, seen = stack.pop()
        left = max_hops - (len(trail) - 1)
        for neighbour in adjacency.get(node, ()):
            if neighbour in seen or to_goal.get(neighbour, 1 << 30) > left - 1:
                continue
            if neighbour == goal:
                on_chain.update(trail)
                on_chain.add(goal)
            elif left > 1:
                stack.append((neighbour, trail + (neighbour,), seen | {neighbour}))
    return on_chain


def node_color(props, on_bridge=False):
    """Colour by what this run touched; gold also marks the chain joining the seeds."""
    if on_bridge:
        return COLOR_BRIDGE
    sides = side_set(props)
    if {SIDE_A, SIDE_B} <= sides:
        return COLOR_BRIDGE
    if SIDE_A in sides:
        return COLOR_AGENT_1
    if SIDE_B in sides:
        return COLOR_AGENT_2
    return COLOR_NEUTRAL


def display_name(data):
    props, labels = data["props"], data["labels"]
    return str(
        props.get("name") or props.get("title") or props.get("key")
        or (labels[0] if labels else "node")
    )


def make_tooltip(data):
    """Plain-text tooltip; line breaks come from TOOLTIP_CSS."""
    lines = [", ".join(data["labels"]) or "Entity"]
    lines += (f"{k}: {v}" for k, v in data["props"].items() if v is not None)
    return "\n".join(lines)


def graph_stamp(nodes, edges):
    """A fingerprint of what the graph holds, not of how it was serialised.

    Neo4j returns records in no fixed order, so the generated HTML differs between
    identical fetches. Stamping the HTML would reset the layout on every refresh and
    leave nothing still enough to click.
    """
    return hash((
        tuple(sorted(nodes)),
        tuple(sorted((e["source"], e["target"], e["type"]) for e in edges)),
    ))


def build_graph_html(nodes, edges, highlight=frozenset()):
    """Render the graph with pyvis and return a self-contained HTML string."""
    net = Network(
        height="560px",
        width="100%",
        directed=True,
        bgcolor="#FFFFFF",
        font_color="#000000",
        cdn_resources="in_line",
    )
    bridge = seed_path_nodes(nodes, edges)
    for nid, data in nodes.items():
        color = node_color(data["props"], nid in bridge)
        is_seed = data["props"].get("seed") in (SIDE_A, SIDE_B)
        net.add_node(
            nid,
            label=display_name(data),
            title=make_tooltip(data),
            color={"background": color, "border": COLOR_INK},
            borderWidth=7 if is_seed else 3,
            shape="dot",
        )
    for edge in edges:
        title = edge["type"]
        if edge.get("source_url"):
            title += f"\nsource: {edge['source_url']}"
        on_path = edge.get("id") in highlight
        net.add_edge(
            edge["source"], edge["target"], title=title,
            color=COLOR_BRIDGE if on_path else None,
            width=5 if on_path else None,
        )
    net.set_options(
        """
        {
          "nodes": {"font": {"size": 13, "face": "Courier New", "bold": {"face": "Courier New"},
                              "strokeWidth": 4, "strokeColor": "#FFFFFF", "color": "#000000"}},
          "edges": {
            "smooth": false,
            "arrows": {"to": {"enabled": true, "scaleFactor": 0.5}},
            "color": {"color": "#000000", "highlight": "#FFD400", "hover": "#FF2200"},
            "width": 1.5,
            "hoverWidth": 4,
            "selectionWidth": 4
          },
          "interaction": {
            "hover": true, "dragNodes": true, "dragView": true, "zoomView": true,
            "hideEdgesOnDrag": true
          },
          "physics": {
            "enabled": true,
            "barnesHut": {
              "gravitationalConstant": -12000,
              "springLength": 200,
              "springConstant": 0.03,
              "damping": 0.12,
              "avoidOverlap": 0.4
            },
            "stabilization": {"iterations": 250}
          }
        }
        """
    )
    return (
        net.generate_html()
        .replace("</head>", TOOLTIP_CSS + "</head>")
        .replace("</body>", PICK_JS + "</body>")
    )


def hop_provenance(edge, edges, nodes):
    """The relationships either end of this edge also has — how it sits in the graph."""
    ends = {edge["source"], edge["target"]}
    lines = []
    for other in edges:
        if other is edge or not ({other["source"], other["target"]} & ends):
            continue
        if other["source"] not in nodes or other["target"] not in nodes:
            continue
        lines.append(
            f'{display_name(nodes[other["source"]])} --[{other["type"]}]--> '
            f'{display_name(nodes[other["target"]])}'
        )
    return lines[:12]


@st.cache_data(show_spinner=False, ttl=3600)
def fetch_source_text(url):
    """The readable text of a source page, or "" when it cannot be read."""
    if not url:
        return ""
    try:
        response = httpx.get(
            url,
            timeout=15.0,
            follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"},
        )
        response.raise_for_status()
    except Exception:
        return ""
    body = re.sub(r"(?is)<(script|style|nav|footer|header)[^>]*>.*?</\1>", " ", response.text)
    body = re.sub(r"(?s)<[^>]+>", " ", body)
    return html.unescape(re.sub(r"\s+", " ", body)).strip()[:SOURCE_CHARS]


def call_claude(prompt, brief=EDGE_BRIEF):
    """Anthropic SDK when a key is configured; otherwise the local Claude Code CLI."""
    if os.getenv("ANTHROPIC_API_KEY"):
        import anthropic

        response = anthropic.Anthropic().messages.create(
            model=LLM_MODEL,
            max_tokens=1000,
            output_config={"effort": "low"},
            system=f"{STORY_SYSTEM}\n\n{brief}",
            messages=[{"role": "user", "content": prompt}],
        )
        return "".join(b.text for b in response.content if b.type == "text").strip()

    result = subprocess.run(
        ["claude", "-p", f"{STORY_SYSTEM}\n\n{brief}\n\n{prompt}"],
        capture_output=True,
        text=True,
        timeout=180,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip()[:300] or "claude CLI failed")
    return result.stdout.strip()


@st.cache_data(show_spinner=False, ttl=3600)
def explain_edge(headline, provenance, snippet, source_url, source_text):
    """One narrative for one relationship. Cached so the auto-refresh cannot re-ask."""
    around = [f"  {line}" for line in provenance] or ["  (none)"]
    prompt = "\n".join(
        [
            f"THE CONNECTION: {headline}",
            f"WHAT THE GRAPH SAVED WITH IT: {snippet or '(nothing)'}",
            f"SOURCE URL: {source_url or '(none recorded)'}",
            "",
            "RELATIONSHIPS AROUND IT:",
            *around,
            "",
            "TEXT OF THE SOURCE PAGE:",
            source_text or "(the page could not be read)",
        ]
    )
    return call_claude(prompt)


def pillify(text, names):
    """Escape the narrative, then wrap every graph node name in a bordered pill."""
    escaped = html.escape(text)
    candidates = sorted({html.escape(n) for n in names if len(n) > 2}, key=len, reverse=True)
    if not candidates:
        return escaped
    pattern = re.compile("|".join(re.escape(n) for n in candidates))
    return pattern.sub(lambda m: f'<span class="pill">{m.group(0)}</span>', escaped)


def current_pick(edges, nodes):
    """What the graph was last clicked on: ("edge", edge) or ("node", node id)."""
    raw = st.query_params.get(PICK_PARAM)
    if not raw:
        return None
    parts = raw.split(EDGE_SEP)
    if parts[0] == "node" and len(parts) == 2 and parts[1] in nodes:
        return "node", parts[1]
    if parts[0] == "edge" and len(parts) == 4:
        source, target, rel_type = parts[1:]
        for edge in edges:
            if (edge["source"], edge["target"], edge["type"]) == (source, target, rel_type):
                if source in nodes and target in nodes:
                    return "edge", edge
    return None


def seed_ids(nodes):
    return {
        data["props"].get("seed"): nid
        for nid, data in nodes.items()
        if data["props"].get("seed") in (SIDE_A, SIDE_B)
    }


def _bfs(adjacency, start, goal, blocked):
    """Shortest route from start to goal avoiding `blocked`, as [(node id, hop), ...]."""
    if start == goal:
        return []
    seen, queue = {start}, [(start, [])]
    while queue:
        node, trail = queue.pop(0)
        for neighbour, hop in adjacency.get(node, ()):
            if neighbour in seen or neighbour in blocked:
                continue
            step = trail + [(neighbour, hop)]
            if neighbour == goal:
                return step
            seen.add(neighbour)
            queue.append((neighbour, step))
    return None


def path_through(nodes, edges, node_id):
    """The simple chain seed A → this node → seed B, over the graph already fetched.

    Done here rather than in Cypher: a shortestPath with a predicate on its own nodes
    cannot be planned, and two unconstrained legs can overlap — which is how a chain
    ends up visiting the same person twice.
    """
    seeds = seed_ids(nodes)
    if SIDE_A not in seeds or SIDE_B not in seeds or node_id not in nodes:
        return []
    adjacency = {}
    for edge in edges:
        if edge["source"] not in nodes or edge["target"] not in nodes:
            continue
        for a, b, forward in ((edge["source"], edge["target"], True),
                              (edge["target"], edge["source"], False)):
            adjacency.setdefault(a, []).append((b, {
                "from": display_name(nodes[a]),
                "to": display_name(nodes[b]),
                "from_id": a,
                "to_id": b,
                "type": edge["type"],
                "url": edge.get("source_url"),
                "snippet": edge.get("snippet"),
                "edge": edge.get("id"),
                "forward": forward,
            }))

    first = _bfs(adjacency, seeds[SIDE_A], node_id, {seeds[SIDE_B]})
    if first is None:
        return []
    # The second leg may not re-enter the first, or the chain visits someone twice.
    used = {seeds[SIDE_A], *(nid for nid, _ in first)} - {node_id, seeds[SIDE_B]}
    second = _bfs(adjacency, node_id, seeds[SIDE_B], used)
    if second is None:
        return []
    return [hop for _, hop in first + second]


def render_chain(hops, names):
    """The path itself, drawn as pills and arrows."""
    parts = [f'<span class="pill">{html.escape(hops[0]["from"])}</span>']
    for hop in hops:
        arrow = "→" if hop["forward"] else "←"
        parts.append(
            f'<span class="rel">{arrow} {html.escape(hop["type"])} {arrow}</span>'
            f'<span class="pill">{html.escape(hop["to"])}</span>'
        )
    return f'<div class="chain">{"".join(parts)}</div>'


@st.cache_data(show_spinner=False, ttl=3600)
def explain_path(hops_key, sources):
    """The story of a whole chain, one beat per link."""
    lines = [f"  {h[0]} --[{h[1]}]--> {h[2]}   source: {h[3] or '(none)'}" for h in hops_key]
    named = []
    for hop in hops_key:
        for name in (hop[0], hop[2]):
            if name not in named:
                named.append(name)
    prompt = "\n".join(
        [
            "THE CHAIN, from one seed to the other:",
            *lines,
            "",
            "EVERY NAME THAT MUST APPEAR: " + ", ".join(named),
            "",
            "WHAT THE SOURCES SAY:",
            *sources,
        ]
    )
    return call_claude(prompt, brief=PATH_BRIEF)


def story_state():
    return st.session_state.setdefault("stories", {})


def render_story(edges, nodes):
    """The box above the graph: the picked path, and Claude's story about it."""
    pick = current_pick(edges, nodes)
    names = [display_name(data) for data in nodes.values()]

    if not pick:
        st.markdown(
            '<div class="story-box empty"><div class="story-head">How did this happen?'
            '</div>Click a gold node to see the path it bridges, or any relationship '
            'line for a single link — then load the story.</div>',
            unsafe_allow_html=True,
        )
        return EMPTY_FOCUS

    kind, value = pick
    if kind == "node":
        hops = path_through(nodes, edges, value)
        if not hops:
            st.markdown(
                f'<div class="story-box empty"><div class="story-head">'
                f'{html.escape(display_name(nodes[value]))}</div>'
                'This node does not sit on a path between the two seeds yet.</div>',
                unsafe_allow_html=True,
            )
            return {"edges": set(), "nodes": {value}}
        headline = f'{hops[0]["from"]} … {display_name(nodes[value])} … {hops[-1]["to"]}'
    else:
        edge = value
        hops = [{
            "from": display_name(nodes[edge["source"]]),
            "to": display_name(nodes[edge["target"]]),
            "type": edge["type"], "url": edge.get("source_url"),
            "snippet": edge.get("snippet"), "edge": None, "forward": True,
        }]
        headline = f'{hops[0]["from"]} --[{edge["type"]}]--> {hops[0]["to"]}'

    key = st.query_params.get(PICK_PARAM)
    stories = story_state()
    body = render_chain(hops, names)

    if key in stories:
        body += f'<div class="told">{pillify(stories[key], names)}</div>'
    st.markdown(
        f'<div class="story-box"><div class="story-head">{html.escape(headline)}</div>'
        f'{body}</div>',
        unsafe_allow_html=True,
    )

    left, right = st.columns([1, 6])
    if key not in stories:
        if left.button("Tell the story", key="tell-story", type="primary"):
            with st.spinner("Reading the sources …"):
                try:
                    hops_key = tuple(
                        (h["from"], h["type"], h["to"], h["url"] or "") for h in hops
                    )
                    sources = tuple(
                        f'{h["url"]}: {h["snippet"] or ""} {fetch_source_text(h["url"])}'[:4000]
                        for h in hops
                    )
                    stories[key] = (
                        explain_path(hops_key, sources) if len(hops) > 1
                        else explain_edge(
                            headline, tuple(hop_provenance(value, edges, nodes)),
                            hops[0]["snippet"] or "", hops[0]["url"] or "",
                            fetch_source_text(hops[0]["url"]),
                        )
                    )
                except Exception as exc:
                    stories[key] = f"Could not write the story: {str(exc)[:200]}"
            st.rerun()
    if right.button("Clear", key="clear-story"):
        del st.query_params[PICK_PARAM]
        st.rerun()

    return {
        "edges": {h["edge"] for h in hops if h["edge"]},
        "nodes": {h[k] for h in hops for k in ("from_id", "to_id") if h.get(k)},
    }


def render_header():
    st.markdown(BRUTAL_CSS, unsafe_allow_html=True)
    st.markdown(
        """
        <div class="header-bar">
            <div class="header-title">brain&nbsp;rotter</div>
            <div class="live-badge">● LIVE</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def run_state():
    return st.session_state.get("run")


def start_search(name_a, name_b):
    """Kick off `/bridge A | B` in this folder, detached, and watch it from here."""
    proc = subprocess.Popen(
        ["claude", "-p", f"/bridge {name_a} | {name_b}"],
        cwd=os.path.dirname(os.path.abspath(__file__)),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    st.session_state["run"] = {"proc": proc, "pair": (name_a, name_b)}
    st.session_state.pop("stories", None)
    st.query_params.clear()


def render_search():
    """Two names in, a bridge run out."""
    run = run_state()
    running = bool(run and run["proc"].poll() is None)

    with st.form("search", border=False):
        left, mid, right = st.columns([3, 3, 2])
        name_a = left.text_input("NAME A", placeholder="Elon Musk", label_visibility="collapsed")
        name_b = mid.text_input("NAME B", placeholder="Taylor Swift", label_visibility="collapsed")
        go = right.form_submit_button(
            "SEARCHING…" if running else "START SEARCH",
            type="primary",
            disabled=running,
            use_container_width=True,
        )
    if go and name_a.strip() and name_b.strip():
        start_search(name_a.strip(), name_b.strip())
        st.rerun()

    if run:
        pair = " ↔ ".join(run["pair"])
        state = "DIGGING" if running else "FINISHED"
        colour = COLOR_BRIDGE if running else "#FFFFFF"
        cols = st.columns([6, 1])
        cols[0].markdown(
            f'<div class="runbar" style="background:{colour};">{state}: {html.escape(pair)}</div>',
            unsafe_allow_html=True,
        )
        if running and cols[1].button("STOP", key="stop-run"):
            run["proc"].terminate()
            st.rerun()


EMPTY_FOCUS = {"edges": frozenset(), "nodes": frozenset()}


def visible_subgraph(nodes, edges, focus):
    """Trim the view to the nodes worth drawing: the picked path, the seeds, the hubs.

    Everything is fetched — the graph keeps growing across runs — but past a few
    hundred nodes pyvis draws a hairball nothing can be clicked out of. Degree is the
    ranking: the nodes with the most connections are the ones a path runs through.
    """
    degree = {}
    for edge in edges:
        for end in (edge["source"], edge["target"]):
            degree[end] = degree.get(end, 0) + 1

    must_keep = (
        set(focus.get("nodes") or ())
        | seed_path_nodes(nodes, edges)
        | {nid for nid, data in nodes.items() if data["props"].get("seed")}
    )
    ranked = sorted(nodes, key=lambda nid: (-degree.get(nid, 0), display_name(nodes[nid])))
    keep = must_keep | set(ranked[:DISPLAY_NODES])

    kept_nodes = {nid: data for nid, data in nodes.items() if nid in keep}
    kept_edges = [
        edge for edge in edges
        if edge["source"] in kept_nodes and edge["target"] in kept_nodes
    ]
    return kept_nodes, kept_edges


def render_metrics(nodes, edges):
    sides = [side_set(data["props"]) for data in nodes.values()]
    gold = seed_path_nodes(nodes, edges) | {
        nid for nid, data in nodes.items()
        if {SIDE_A, SIDE_B} <= side_set(data["props"])
    }
    cells = [
        ("NODES", len(nodes), "#FFFFFF"),
        ("AGENT 1", sum(1 for s in sides if SIDE_A in s), COLOR_AGENT_1),
        ("AGENT 2", sum(1 for s in sides if SIDE_B in s), COLOR_AGENT_2),
        ("BRIDGE CHAIN", len(gold), COLOR_BRIDGE),
    ]
    st.markdown(
        '<div class="stat-row">'
        + "".join(
            f'<div class="stat" style="background:{bg};">'
            f'<div class="stat-label">{label}</div>'
            f'<div class="stat-value">{value}</div></div>'
            for label, value, bg in cells
        )
        + "</div>",
        unsafe_allow_html=True,
    )


def render_legend():
    items = [
        (COLOR_AGENT_1, "AGENT 1 / SIDE A"),
        (COLOR_AGENT_2, "AGENT 2 / SIDE B"),
        (COLOR_BRIDGE, "ON THE CHAIN / BOTH"),
        (COLOR_NEUTRAL, "NOT TOUCHED THIS RUN"),
    ]
    chips = "".join(
        f'<span class="key"><span class="swatch" style="background:{color};"></span>{label}</span>'
        for color, label in items
    )
    st.markdown(f'<div class="legend">{chips}</div>', unsafe_allow_html=True)


@st.fragment(run_every=REFRESH_INTERVAL_MS / 1000)
def live_graph(driver):
    """Metrics and the graph, refreshed on their own.

    A fragment reruns by itself, so the story above it survives — a whole-page refresh
    every few seconds would cancel the model call mid-sentence and wipe what it wrote.
    """
    try:
        nodes, edges = fetch_graph(driver)
    except Exception as exc:
        st.error(f"Failed to fetch graph data from Neo4j: {exc}")
        return

    st.session_state["graph"] = (nodes, edges)
    render_metrics(nodes, edges)
    if not nodes:
        st.info(
            "The memory graph is empty. Once agents start writing to Neo4j, "
            "nodes will stream in here live."
        )
        return

    render_legend()
    focus = st.session_state.get("focus", EMPTY_FOCUS)
    shown_nodes, shown_edges = visible_subgraph(nodes, edges, focus)
    if len(shown_nodes) < len(nodes):
        st.markdown(
            f'<div class="runbar" style="background:#FFFFFF;">DRAWING THE {len(shown_nodes)} '
            f'BUSIEST OF {len(nodes)} NODES · {len(shown_edges)} OF {len(edges)} LINKS</div>',
            unsafe_allow_html=True,
        )
    clicked = graph_click(
        graph_html=build_graph_html(
            shown_nodes, shown_edges, focus["edges"]
        ),
        stamp=graph_stamp(shown_nodes, shown_edges),
        play=st.session_state.get("play", 0),
        height=600,
        default=None,
    )
    if st.button("▶ REPLAY HOW IT GREW", key="replay", use_container_width=True):
        st.session_state["play"] = st.session_state.get("play", 0) + 1
        st.rerun(scope="fragment")

    if clicked and st.query_params.get(PICK_PARAM) != clicked:
        st.query_params[PICK_PARAM] = clicked
        st.rerun(scope="app")  # the story box is outside this fragment


def main():
    st.set_page_config(
        page_title="brain rotter", layout="wide"
    )
    render_header()

    config = load_config()
    missing = [key for key, value in config.items() if not value]
    if missing:
        st.warning(
            "Missing environment variables: "
            + ", ".join(missing)
            + ". Add them to a `.env` file next to `app.py`."
        )
        return

    try:
        driver = get_driver(
            config["NEO4J_URI"], config["NEO4J_USER"], config["NEO4J_PASSWORD"]
        )
    except Exception as exc:
        st.error(
            f"Could not connect to Neo4j ({exc}). "
            "Check your `NEO4J_URI`, `NEO4J_USER`, and `NEO4J_PASSWORD`."
        )
        return

    if "graph" not in st.session_state:
        try:
            st.session_state["graph"] = fetch_graph(driver)
        except Exception as exc:
            st.error(f"Failed to fetch graph data from Neo4j: {exc}")
            return

    render_search()

    nodes, edges = st.session_state["graph"]
    st.session_state["focus"] = render_story(edges, nodes)
    live_graph(driver)


if __name__ == "__main__":
    main()
