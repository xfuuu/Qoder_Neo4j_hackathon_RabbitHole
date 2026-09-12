"""Live multi-agent shared memory graph dashboard backed by Neo4j AuraDB."""

import html
import os

import streamlit as st
import streamlit.components.v1 as components
from dotenv import load_dotenv
from neo4j import GraphDatabase, READ_ACCESS
from pyvis.network import Network
from streamlit_autorefresh import st_autorefresh

AGENT_1 = "Agent_1"
AGENT_2 = "Agent_2"

COLOR_AGENT_1 = "#3B82F6"  # Electric Blue
COLOR_AGENT_2 = "#EF4444"  # Vibrant Red
COLOR_BRIDGE = "#F59E0B"   # Gold / Shared Memory Bridge
COLOR_NEUTRAL = "#9CA3AF"

GRAPH_QUERY = "MATCH (n)-[r]->(m) RETURN n, r, m LIMIT 100"
SHORTEST_PATH_QUERY = (
    "MATCH p=shortestPath((a {created_by:'Agent_1'})-[*..6]-(b {created_by:'Agent_2'})) "
    "RETURN p LIMIT 1"
)
REFRESH_INTERVAL_MS = 2000


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
    """Fetch up to 100 connected nodes and their relationships in read mode."""
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
            edges.append(
                {
                    "source": rel.start_node.element_id,
                    "target": rel.end_node.element_id,
                    "type": rel.type,
                }
            )
    return nodes, edges


def compute_bridge_ids(nodes, edges):
    """Ids of nodes touching both agents — connected to both sides or tagged by both."""
    touched = {nid: set() for nid in nodes}
    for nid, data in nodes.items():
        creator = data["props"].get("created_by")
        if creator in (AGENT_1, AGENT_2):
            touched[nid].add(creator)
        tags = data["props"].get("tagged_by") or []
        if isinstance(tags, str):
            tags = [tags]
        touched[nid].update(t for t in tags if t in (AGENT_1, AGENT_2))
    for edge in edges:
        for nid, other in (
            (edge["source"], edge["target"]),
            (edge["target"], edge["source"]),
        ):
            creator = nodes.get(other, {}).get("props", {}).get("created_by")
            if creator in (AGENT_1, AGENT_2):
                touched[nid].add(creator)
    return {nid for nid, agents in touched.items() if agents == {AGENT_1, AGENT_2}}


def node_color(creator, is_bridge):
    if is_bridge:
        return COLOR_BRIDGE
    if creator == AGENT_1:
        return COLOR_AGENT_1
    if creator == AGENT_2:
        return COLOR_AGENT_2
    return COLOR_NEUTRAL


def display_name(data):
    props, labels = data["props"], data["labels"]
    return str(
        props.get("name") or props.get("title") or props.get("id")
        or (labels[0] if labels else "node")
    )


def make_tooltip(data):
    rows = "".join(
        f"<b>{html.escape(str(k))}:</b> {html.escape(str(v))}<br>"
        for k, v in data["props"].items()
    )
    return f"<b>{html.escape(', '.join(data['labels']))}</b><br>{rows}"


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
        net.add_node(
            nid,
            label=display_name(data),
            title=make_tooltip(data),
            color=node_color(data["props"].get("created_by"), nid in bridge_ids),
            borderWidth=2,
        )
    for edge in edges:
        net.add_edge(
            edge["source"], edge["target"],
            title=edge["type"], label=edge["type"],
        )
    net.set_options(
        """
        {
          "nodes": {"font": {"size": 14, "strokeWidth": 3, "strokeColor": "#FFFFFF"}},
          "edges": {
            "smooth": {"type": "continuous"},
            "arrows": {"to": {"enabled": true, "scaleFactor": 0.5}},
            "font": {"size": 9, "align": "middle", "strokeWidth": 0},
            "color": {"color": "#CBD5E1", "highlight": "#F59E0B"}
          },
          "interaction": {"hover": true, "dragNodes": true, "dragView": true, "zoomView": true},
          "physics": {
            "enabled": true,
            "barnesHut": {
              "gravitationalConstant": -8000,
              "springLength": 150,
              "springConstant": 0.04,
              "damping": 0.09
            },
            "stabilization": {"iterations": 200}
          }
        }
        """
    )
    return net.generate_html()


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
    agent1 = sum(1 for d in nodes.values() if d["props"].get("created_by") == AGENT_1)
    agent2 = sum(1 for d in nodes.values() if d["props"].get("created_by") == AGENT_2)
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Total Memory Nodes", len(nodes))
    col2.metric("Agent 1 Nodes", agent1)
    col3.metric("Agent 2 Nodes", agent2)
    col4.metric("Shared Memory Bridges", len(bridge_ids))


def render_legend():
    items = [
        (COLOR_AGENT_1, "Agent 1"),
        (COLOR_AGENT_2, "Agent 2"),
        (COLOR_BRIDGE, "Shared Memory Bridge"),
        (COLOR_NEUTRAL, "Other"),
    ]
    chips = "".join(
        f'<span style="margin-right:18px;"><span style="color:{color};">●</span> {label}</span>'
        for color, label in items
    )
    st.markdown(
        f'<div style="font-size:13px;color:#374151;margin:4px 0 8px 0;">{chips}</div>',
        unsafe_allow_html=True,
    )


def render_shortest_path(driver):
    st.subheader("🔗 Latest Cross-Agent Memory Connection")
    try:
        with driver.session(default_access_mode=READ_ACCESS) as session:
            record = session.run(SHORTEST_PATH_QUERY).single()
    except Exception as exc:
        st.error(f"Failed to compute the cross-agent path: {exc}")
        return
    if record is None:
        st.info(
            "No cross-agent path found yet — waiting for Agent 1 and Agent 2 "
            "memories to connect."
        )
        return
    chips = []
    for node in record["p"].nodes:
        data = {"labels": list(node.labels), "props": dict(node)}
        color = node_color(data["props"].get("created_by"), is_bridge=False)
        chips.append(
            f'<span style="background:{color};color:#fff;padding:6px 12px;'
            f'border-radius:999px;font-size:13px;font-weight:600;white-space:nowrap;">'
            f'{html.escape(display_name(data))}</span>'
        )
    chain = '<span style="color:#6B7280;margin:0 6px;">→</span>'.join(chips)
    st.markdown(
        f'<div style="border:1px solid #E5E7EB;border-left:6px solid {COLOR_BRIDGE};'
        f'border-radius:10px;padding:16px;background:#FAFAFA;overflow-x:auto;">'
        f'{chain}</div>',
        unsafe_allow_html=True,
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
        driver = get_driver(**config)
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

    bridge_ids = compute_bridge_ids(nodes, edges)
    render_metrics(nodes, bridge_ids)

    if not nodes:
        st.info(
            "The memory graph is empty. Once agents start writing to Neo4j, "
            "nodes will stream in here live."
        )
    else:
        render_legend()
        components.html(
            build_graph_html(nodes, edges, bridge_ids), height=600, scrolling=False
        )

    render_shortest_path(driver)


if __name__ == "__main__":
    main()
