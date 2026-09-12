"""Live multi-agent shared memory graph dashboard backed by Neo4j AuraDB.

Schema (see graph.py):
  (:Entity {key, name, sides, depth, expanded, seed})
  (:Entity)-[:RELATED {type, source_url}]->(:Entity)
side 'a' = Agent 1's frontier, side 'b' = Agent 2's frontier.
"""

import html
import os

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components
from dotenv import load_dotenv
from neo4j import GraphDatabase, READ_ACCESS
from pyvis.network import Network
from streamlit_autorefresh import st_autorefresh

SIDE_A = "a"  # Agent 1
SIDE_B = "b"  # Agent 2

COLOR_AGENT_1 = "#3B82F6"  # Electric Blue
COLOR_AGENT_2 = "#EF4444"  # Vibrant Red
COLOR_BRIDGE = "#F59E0B"   # Gold / Shared Memory Bridge
COLOR_NEUTRAL = "#9CA3AF"
COLOR_SEED_BORDER = "#0F172A"

GRAPH_QUERY = "MATCH (n:Entity)-[r]->(m:Entity) RETURN n, r, m LIMIT 100"
# Strictly-side-a to strictly-side-b: any such path must cross a shared bridge.
SHORTEST_PATH_QUERY = (
    "MATCH p=shortestPath((a:Entity)-[*..6]-(b:Entity)) "
    "WHERE $sideA IN a.sides AND NOT $sideB IN a.sides "
    "AND $sideB IN b.sides AND NOT $sideA IN b.sides "
    "RETURN p LIMIT 1"
)
REFRESH_INTERVAL_MS = 2000

# vis-network renders string titles as plain text, so tooltips use \n and this
# injected stylesheet breaks the lines (and styles the popup).
TOOLTIP_CSS = """<style>
.vis-tooltip {
  white-space: pre-line;
  font-family: -apple-system, 'Helvetica Neue', sans-serif;
  font-size: 12px; line-height: 1.5;
  padding: 8px 10px; border-radius: 8px;
  background: #0F172A !important; color: #F8FAFC !important;
  border: none !important;
  box-shadow: 0 4px 14px rgba(0,0,0,0.25);
}
</style>"""


def load_config():
    """Read Neo4j credentials from the environment (.env supported)."""
    load_dotenv()
    return {
        "NEO4J_URI": os.getenv("NEO4J_URI"),
        "NEO4J_USER": os.getenv("NEO4J_USER"),
        "NEO4J_PASSWORD": os.getenv("NEO4J_PASSWORD"),
    }


@st.cache_resource
def get_driver(uri, user, password):
    """Create and verify a Neo4j driver (cached across auto-refreshes)."""
    driver = GraphDatabase.driver(uri, auth=(user, password))
    driver.verify_connectivity()
    return driver


def fetch_graph(driver):
    """Fetch up to 100 connected entities and their relationships in read mode."""
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
                }
            )
    return nodes, edges


def side_set(props):
    sides = props.get("sides") or []
    if isinstance(sides, str):
        sides = [sides]
    return set(sides)


def compute_bridge_ids(nodes):
    """Nodes touched by both agents' frontiers (sides contains 'a' and 'b')."""
    return {
        nid
        for nid, data in nodes.items()
        if {SIDE_A, SIDE_B} <= side_set(data["props"])
    }


def node_color(props):
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


def build_graph_html(nodes, edges, bridge_ids):
    """Render the graph with pyvis and return a self-contained HTML string."""
    net = Network(
        height="560px",
        width="100%",
        directed=True,
        bgcolor="#FFFFFF",
        font_color="#1F2937",
        cdn_resources="in_line",
    )
    for nid, data in nodes.items():
        color = node_color(data["props"])
        is_seed = data["props"].get("seed") in (SIDE_A, SIDE_B)
        net.add_node(
            nid,
            label=display_name(data),
            title=make_tooltip(data),
            color=(
                {"background": color, "border": COLOR_SEED_BORDER}
                if is_seed else color
            ),
            borderWidth=4 if is_seed else 2,
        )
    for edge in edges:
        title = edge["type"]
        if edge.get("source_url"):
            title += f"\nsource: {edge['source_url']}"
        net.add_edge(edge["source"], edge["target"], title=title)
    net.set_options(
        """
        {
          "nodes": {"font": {"size": 13, "strokeWidth": 3, "strokeColor": "#FFFFFF"}},
          "edges": {
            "smooth": {"type": "continuous"},
            "arrows": {"to": {"enabled": true, "scaleFactor": 0.4}},
            "color": {"color": "#CBD5E1", "highlight": "#F59E0B", "hover": "#94A3B8"},
            "width": 1.5
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
    return net.generate_html().replace("</head>", TOOLTIP_CSS + "</head>")


def render_header():
    st.markdown(
        """
        <style>
        @keyframes livePulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.3; } }
        .header-bar {
            display: flex; align-items: center; justify-content: space-between;
            padding: 18px 24px; border-radius: 12px; margin-bottom: 12px;
            background: linear-gradient(90deg, #0F172A 0%, #1E293B 100%);
        }
        .header-title { font-size: 24px; font-weight: 800; color: #F8FAFC; }
        .live-badge {
            color: #4ADE80; font-weight: 700; font-size: 14px; letter-spacing: 1px;
            animation: livePulse 1.5s ease-in-out infinite;
        }
        </style>
        <div class="header-bar">
            <div class="header-title">🧠 Multi-Agent Knowledge &amp; Memory Graph</div>
            <div class="live-badge">● LIVE STREAMING</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_metrics(nodes, bridge_ids):
    agent1 = sum(1 for d in nodes.values() if SIDE_A in side_set(d["props"]))
    agent2 = sum(1 for d in nodes.values() if SIDE_B in side_set(d["props"]))
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Total Memory Nodes", len(nodes))
    col2.metric("Agent 1 Nodes", agent1)
    col3.metric("Agent 2 Nodes", agent2)
    col4.metric("Shared Memory Bridges", len(bridge_ids))


def render_legend():
    items = [
        (COLOR_AGENT_1, "Agent 1 (side a)"),
        (COLOR_AGENT_2, "Agent 2 (side b)"),
        (COLOR_BRIDGE, "Shared Memory Bridge"),
        (COLOR_NEUTRAL, "Other"),
    ]
    chips = "".join(
        f'<span style="margin-right:18px;"><span style="color:{color};">●</span> {label}</span>'
        for color, label in items
    )
    chips += (
        f'<span style="margin-right:18px;"><span style="color:{COLOR_SEED_BORDER};">◉</span>'
        " Seed (dark border)</span>"
    )
    st.markdown(
        f'<div style="font-size:13px;color:#374151;margin:4px 0 8px 0;">{chips}</div>',
        unsafe_allow_html=True,
    )


def render_shortest_path(driver):
    st.subheader("🔗 Latest Cross-Agent Memory Connection")
    try:
        with driver.session(default_access_mode=READ_ACCESS) as session:
            record = session.run(
                SHORTEST_PATH_QUERY, sideA=SIDE_A, sideB=SIDE_B
            ).single()
    except Exception as exc:
        st.error(f"Failed to compute the cross-agent path: {exc}")
        return
    if record is None:
        st.info(
            "No cross-agent path found yet — waiting for the two agents' "
            "frontiers to meet in shared memory."
        )
        return
    path = record["p"]
    path_nodes = list(path.nodes)
    path_rels = list(path.relationships)
    segments = []
    for i, node in enumerate(path_nodes):
        data = {"labels": list(node.labels), "props": dict(node)}
        segments.append(
            f'<span style="background:{node_color(data["props"])};color:#fff;'
            f"padding:6px 12px;border-radius:999px;font-size:13px;font-weight:600;"
            f'white-space:nowrap;">{html.escape(display_name(data))}</span>'
        )
        if i < len(path_rels):
            rtype = html.escape(
                str(dict(path_rels[i]).get("type") or path_rels[i].type)
            )
            segments.append(
                f'<span style="color:#6B7280;margin:0 6px;font-size:12px;">'
                f"—{rtype}→</span>"
            )
    st.markdown(
        f'<div style="border:1px solid #E5E7EB;border-left:6px solid {COLOR_BRIDGE};'
        f'border-radius:10px;padding:16px;background:#FAFAFA;overflow-x:auto;'
        f'display:flex;align-items:center;flex-wrap:wrap;row-gap:8px;">'
        f'{"".join(segments)}</div>',
        unsafe_allow_html=True,
    )
    with st.expander("Hop provenance · 每一跳的来源"):
        for i, rel in enumerate(path_rels):
            rel_props = dict(rel)
            rtype = str(rel_props.get("type") or rel.type)
            src = rel_props.get("source_url")
            left = display_name(
                {"labels": list(path_nodes[i].labels), "props": dict(path_nodes[i])}
            )
            right = display_name(
                {
                    "labels": list(path_nodes[i + 1].labels),
                    "props": dict(path_nodes[i + 1]),
                }
            )
            line = f"{i + 1}. **{left}** —`{rtype}`→ **{right}**"
            line += f" · [source]({src})" if src else " · no source recorded"
            st.markdown(line)


def render_inspector(nodes, edges):
    """Per-entity provenance panel: pick a node, see every link and its source."""
    st.subheader("🔍 Entity Inspector · 链路追溯")
    ordered = sorted(nodes.keys(), key=lambda n: display_name(nodes[n]).lower())
    choice = st.selectbox(
        "Select an entity to see how it connects to the graph",
        options=ordered,
        index=None,
        format_func=lambda n: display_name(nodes[n]),
        placeholder="Type to search an entity…",
    )
    if choice is None:
        st.caption(
            "Pick an entity above — every relationship it has, plus the source "
            "page each one came from, will appear here."
        )
        return

    props = nodes[choice]["props"]
    sides = side_set(props)
    badges = []
    if {SIDE_A, SIDE_B} <= sides:
        badges.append(("Shared Bridge", COLOR_BRIDGE))
    elif SIDE_A in sides:
        badges.append(("Agent 1 frontier", COLOR_AGENT_1))
    elif SIDE_B in sides:
        badges.append(("Agent 2 frontier", COLOR_AGENT_2))
    if props.get("seed") in (SIDE_A, SIDE_B):
        badges.append((f"Seed '{props['seed']}'", COLOR_SEED_BORDER))
    meta = f"depth {props.get('depth', '?')} · expanded: {props.get('expanded', '?')}"
    badge_html = "".join(
        f'<span style="background:{c};color:#fff;padding:3px 10px;border-radius:999px;'
        f'font-size:12px;font-weight:600;margin-right:8px;">{html.escape(t)}</span>'
        for t, c in badges
    )
    st.markdown(
        f'<div style="margin:6px 0 10px 0;">{badge_html}'
        f'<span style="color:#6B7280;font-size:12px;">{html.escape(meta)}</span></div>',
        unsafe_allow_html=True,
    )

    rows = []
    for edge in edges:
        if edge["source"] == choice:
            rows.append(("outgoing →", edge["type"], display_name(nodes[edge["target"]]), edge["source_url"]))
        elif edge["target"] == choice:
            rows.append(("← incoming", edge["type"], display_name(nodes[edge["source"]]), edge["source_url"]))
    if not rows:
        st.caption("No connections in the current graph window.")
        return
    df = pd.DataFrame(rows, columns=["Direction", "Relation", "Entity", "Source"])
    st.dataframe(
        df,
        hide_index=True,
        use_container_width=True,
        column_config={"Source": st.column_config.LinkColumn("Source")},
    )


def main():
    st.set_page_config(
        page_title="Multi-Agent Memory Graph", page_icon="🧠", layout="wide"
    )
    st_autorefresh(interval=REFRESH_INTERVAL_MS, limit=None, key="live-refresh")
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

    try:
        nodes, edges = fetch_graph(driver)
    except Exception as exc:
        st.error(f"Failed to fetch graph data from Neo4j: {exc}")
        return

    bridge_ids = compute_bridge_ids(nodes)
    render_metrics(nodes, bridge_ids)

    if not nodes:
        st.info(
            "The memory graph is empty. Once agents start writing to Neo4j, "
            "nodes will stream in here live."
        )
        return

    render_legend()
    components.html(
        build_graph_html(nodes, edges, bridge_ids), height=600, scrolling=False
    )
    render_shortest_path(driver)
    render_inspector(nodes, edges)


if __name__ == "__main__":
    main()
