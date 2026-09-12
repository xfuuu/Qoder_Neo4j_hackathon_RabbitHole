"""The Cypher layer: one node label, one relationship type, one shortest path.

The schema is fixed here rather than left to the agents, because the two things the
demo turns on — "have the two frontiers met" and "what is the path" — are single
queries over a predictable shape. An agent writing its own Cypher would drift the
labels apart and the path would never resolve.

    (:Entity {key, name, sides, depth, expanded, seed})
    (:Entity)-[:RELATED {type, source_url, snippet, side, round}]->(:Entity)

`key` is the identity (lowercased, trimmed); `name` is what to show. `sides` records
which frontier has reached the node — a node holding both is where the two searches
touched. Relationship direction is whatever the source said; every read is undirected.

The path is not simply the shortest one. Among the nodes both sides reached, the junction
is the one with the *fewest* connections: a hub like Microsoft or Stanford joins any two
names in the scene and says nothing, while a rarely-connected node is the whole point of
running this.
"""

from __future__ import annotations

from neo4j import GraphDatabase

from ..env import get

MAX_HOPS = 12
STEP_BUDGET = 20  # one expansion of one term by one researcher is one step

_driver = None


def driver():
    global _driver
    if _driver is None:
        _driver = GraphDatabase.driver(
            get("NEO4J_URI"), auth=(get("NEO4J_USERNAME"), get("NEO4J_PASSWORD"))
        )
    return _driver


def query(cypher: str, **params):
    try:
        database = get("NEO4J_DATABASE")
    except RuntimeError:
        database = "neo4j"
    # A managed transaction, because Aura drops an idle connection and one server
    # process lives across a whole run: execute_write retries a defunct connection
    # instead of handing the agent a SessionExpired.
    with driver().session(database=database) as session:
        return session.execute_write(lambda tx: list(tx.run(cypher, **params)))


def key(term: str) -> str:
    return " ".join(term.lower().split())


# ---------------------------------------------------------------- writes


def clear_run_state() -> dict:
    """Hand the graph to a new run without throwing away what earlier runs learned.

    Entities and relationships accumulate across runs — that is the point: a later pair of
    seeds can find its path through edges an earlier run wrote. Only the three properties
    that belong to one run are cleared: which side reached a node, whether it has been
    expanded, and which nodes are the seeds.
    """
    query("CREATE CONSTRAINT entity_key IF NOT EXISTS FOR (e:Entity) REQUIRE e.key IS UNIQUE")
    before = stats()
    query("MATCH (e:Entity) SET e.sides = [], e.expanded = false, e.seed = null")
    return before


def seed(term_a: str, term_b: str) -> None:
    for side, term in (("a", term_a), ("b", term_b)):
        query(
            """
            MERGE (e:Entity {key: $key})
              ON CREATE SET e.name = $name
            SET e.sides = [$side], e.depth = 0, e.expanded = false, e.seed = $side
            """,
            key=key(term),
            name=term.strip(),
            side=side,
        )


def add(side: str, source_term: str, records: list[dict], round_no: int = 0) -> dict:
    """Write one term's expansion. Marks `source_term` expanded whether or not it yielded."""
    rows = query(
        "MATCH (e:Entity {key: $key}) SET e.expanded = true RETURN e.depth AS depth",
        key=key(source_term),
    )
    depth = (rows[0]["depth"] if rows else 0) + 1  # everything this term yields sits one hop out

    written, new_entities = 0, []
    for rec in records:
        subject, relation, obj = (
            str(rec.get("subject", "")).strip(),
            str(rec.get("relation", "")).strip(),
            str(rec.get("object", "")).strip(),
        )
        if not (subject and relation and obj) or key(subject) == key(obj):
            continue
        rows = query(
            """
            MERGE (s:Entity {key: $skey})
              ON CREATE SET s.name = $sname, s.depth = $depth, s.expanded = false, s.sides = []
            MERGE (o:Entity {key: $okey})
              ON CREATE SET o.name = $oname, o.depth = $depth, o.expanded = false, o.sides = []
            WITH s, o, o.sides = [] AS o_is_new
            SET s.sides = CASE WHEN $side IN s.sides THEN s.sides ELSE s.sides + $side END,
                o.sides = CASE WHEN $side IN o.sides THEN o.sides ELSE o.sides + $side END,
                o.depth = CASE WHEN o.depth > $depth THEN $depth ELSE o.depth END
            MERGE (s)-[r:RELATED {type: $type}]->(o)
              ON CREATE SET r.source_url = $url, r.snippet = $snippet,
                            r.side = $side, r.round = $round
            RETURN o_is_new, o.name AS object, o.sides AS object_sides
            """,
            skey=key(subject),
            sname=subject,
            okey=key(obj),
            oname=obj,
            type=relation,
            url=str(rec.get("source_url", "")),
            snippet=str(rec.get("snippet", ""))[:400],
            side=side,
            round=round_no,
            depth=depth,
        )
        written += 1
        row = rows[0]
        if row["o_is_new"]:
            new_entities.append(row["object"])

    return {"written": written, "new_entities": new_entities}


# ---------------------------------------------------------------- reads


def frontier(side: str, limit: int = 8) -> list[dict]:
    rows = query(
        """
        MATCH (e:Entity)
        WHERE $side IN e.sides AND e.expanded = false
        RETURN e.name AS name, e.depth AS depth
        ORDER BY e.depth, e.name
        LIMIT $limit
        """,
        side=side,
        limit=limit,
    )
    return [dict(r) for r in rows]


def stats() -> dict:
    row = query(
        """
        MATCH (e:Entity)
        WITH collect(e) AS es
        OPTIONAL MATCH ()-[r:RELATED]->()
        WITH es, count(r) AS rels
        RETURN size([e IN es WHERE 'a' IN e.sides]) AS a_side,
               size([e IN es WHERE 'b' IN e.sides]) AS b_side,
               size([e IN es WHERE 'a' IN e.sides AND 'b' IN e.sides]) AS shared,
               size([e IN es WHERE e.expanded = false]) AS unexpanded,
               size([e IN es WHERE e.expanded]) AS steps,
               size(es) AS entities, rels
        """
    )
    return dict(row[0]) if row else {}


def shared_names(limit: int = 10) -> list[str]:
    rows = query(
        """
        MATCH (e:Entity) WHERE 'a' IN e.sides AND 'b' IN e.sides
        RETURN e.name AS name ORDER BY e.depth LIMIT $limit
        """,
        limit=limit,
    )
    return [r["name"] for r in rows]


def _segments(names: list[str], rels: list[dict]) -> list[dict]:
    """Walk the chain, keeping each relationship's real direction.

    A path is read undirected, so hop i may be stored either way round. `forward` says
    which: without it, `Airbnb --[FUNDED]--> Founders Fund` reads as the opposite of what
    the source said.
    """
    return [
        {
            "from": names[i],
            "to": names[i + 1],
            "forward": rels[i]["start"] == names[i],
            **{k: v for k, v in rels[i].items() if k != "start"},
        }
        for i in range(len(rels))
    ]


def _leg(from_clause: str, to_clause: str, **params) -> dict | None:
    """One shortest path, with no predicate on it — anything else and the planner gives up.

    A filter that mentions nodes(p) cannot be pushed into shortestPath, so Neo4j falls back
    to enumerating every path and the query never returns. Hence: fetch each leg plainly and
    check the overlap in Python.
    """
    rows = query(
        f"""
        MATCH {from_clause}, {to_clause}
        MATCH p = shortestPath((x)-[:RELATED*0..{MAX_HOPS}]-(y))
        RETURN [n IN nodes(p) | n.name] AS names,
               [r IN relationships(p) | {{type: r.type, url: r.source_url, side: r.side,
                                          start: startNode(r).name}}] AS rels
        """,
        **params,
    )
    return dict(rows[0]) if rows else None


def junctions() -> list[dict]:
    """Nodes both sides reached, least-connected first — the candidates for the path's middle."""
    rows = query(
        """
        MATCH (m:Entity) WHERE 'a' IN m.sides AND 'b' IN m.sides
        RETURN m.name AS name, count { (m)-[:RELATED]-() } AS degree
        ORDER BY degree, m.name
        """
    )
    return [dict(r) for r in rows]


def path() -> dict | None:
    """The path between the seeds through the least-connected node both sides reached.

    Returns `{"segments": [...], "via": name, "degree": n}`, or None while the sides are
    still apart. Candidates are tried in ascending degree; the first whose two legs meet only
    at the junction wins. Falls back to a plain shortest path if none does.
    """
    for cand in junctions():
        a_leg = _leg("(x:Entity {seed: 'a'})", "(y:Entity {name: $via})", via=cand["name"])
        b_leg = _leg("(x:Entity {name: $via})", "(y:Entity {seed: 'b'})", via=cand["name"])
        if not (a_leg and b_leg):
            continue
        if set(a_leg["names"]) & set(b_leg["names"]) != {cand["name"]}:
            continue  # the legs double back through each other; this junction gives no simple path
        names = a_leg["names"] + b_leg["names"][1:]
        return {
            "segments": _segments(names, a_leg["rels"] + b_leg["rels"]),
            "via": cand["name"],
            "degree": cand["degree"],
        }

    plain = _leg("(x:Entity {seed: 'a'})", "(y:Entity {seed: 'b'})")
    if not plain:
        return None
    return {"segments": _segments(plain["names"], plain["rels"]), "via": None, "degree": None}
