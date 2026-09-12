---
name: bridge
description: Find the funniest chain connecting two famous American names, by digging outward from both at once. Two research agents expand one name each — feuds, exes, diss tracks, unfollows, snubs, lawsuits, rumours — writing what they find into a shared Neo4j graph until it holds a path from A to B. Use when the user says "/bridge", or asks how two celebrities, founders or companies are connected, or asks for the chain between two names.
---

# Bridge

Two searches dig toward each other and meet in the middle. Side A expands from term A, side B
from term B, both writing into one Neo4j graph. A path exists the moment they touch the same
node — and Neo4j, not you, decides when that has happened.

The territory is American celebrity gossip — pop stars, rappers, actors, athletes, reality TV,
influencers, and the tech founders who became celebrities too. Feuds, exes, diss tracks,
unfollows, red-carpet snubs, lawsuits, the rumour nobody confirmed. Nothing is fact-checked and
nothing needs to be — this is a toy for finding funny chains between names. A chain through a
tabloid rumour beats a chain through a résumé.

The graph is never cleared. Every run adds to what earlier runs learned, so a new pair of
names can turn out to be connected before you search at all — `bridge_start` says so when it
happens, and those inherited hops still have to survive your reading of their sources.

You orchestrate. You do not search, and you do not read pages: `search` and the explorer
tools are reachable from here, and using them puts page bodies in the context that has to
survive the whole run. Dispatch, read reports, check the graph.

## Start

1. The arguments are two terms. If only one is given, or neither, ask for both — never invent
   the other end.
2. `bridge_start(term_a, term_b)`. It seeds both terms, opens a run directory, and reports
   what the graph already holds from earlier runs — including a path between these two seeds
   if one is already there. Note the run id.

Tell the user the two seeds and that you are starting round 1.

## Rounds

Each round is one dispatch to each side, both in the same message so they run at the same
time. Never dispatch one side and wait.

1. `frontier('a')` and `frontier('b')`. Round 1 returns just the two seeds.
2. Pick up to 3 terms per side — the ones most likely to be reachable from the other end:
   people who have been in many rooms, shows and award ceremonies everyone passed through,
   labels and franchises whose alumni scattered. Skip anything that is really a category.
3. Dispatch two `frontier-researcher` agents in one message. Each one gets:
   - its side (`a` or `b`) and the round number
   - the exact terms to expand
   - the other side's seed term, so it knows what it is digging toward
   - a one-line summary of what the graph already holds, so it does not re-tread
4. When both reports are back, call `bridge_status()`. That is the only thing that decides
   whether the run continues — a researcher claiming `MET: yes` still gets checked. It also
   reports steps spent against the 20-step budget.
5. If there is no path, start the next round from the new frontier. Say one line to the user
   about where each side is; keep it short.

Stop when any of these is true:

- `bridge_status()` returns a path — then write the report.
- **20 steps are spent.** One term expanded by one researcher is one step, and
  `bridge_status()` counts them for you. Plan the last rounds so you do not blow past it:
  with 4 steps left, send 2 terms per side, not 3.
- Both sides come back with nothing new twice in a row. The seeds are too far apart for the
  breadth you are running at; say so rather than grinding.

## Judgment

- **Hubs beat specifics when expanding, and lose when judging.** Expand the people who have
  been in many rooms — they are how the two sides reach each other. But the path itself is
  built through the *least*-connected node both sides reached, because a junction at the Met
  Gala or Saturday Night Live joins any two famous names and tells you nothing. `bridge_status()`
  already picks that way; do not argue it down to a shorter, emptier chain.
- **Watch for name drift.** `OpenAI` and `Open AI` are two nodes and will never meet. If
  `bridge_status()` shows a suspicious near-duplicate, tell the next round's researchers the
  canonical spelling to use.
- **A shared node is not yet a path.** Both sides can touch a node without an edge chain
  existing between the seeds. Only `bridge_status()` knows.
- **Do not stack the deck.** Never hand a researcher a term you picked because you already
  know it bridges — the frontier decides, from what the searches actually found. A path you
  engineered proves nothing.

## Report

Write `report.md` into the run directory, and show the path in the reply too:

- **The path** — every hop as `A --[RELATION]--> X`, with the source URL under each hop, so a
  reader can check every link in the chain. Name the junction node and its degree.
- **How it was found** — rounds and steps spent (of 20), terms expanded per side, where the
  two sides met.
- **The graph** — entity and relationship counts, and the Cypher to see it in Neo4j Browser:

  ```cypher
  MATCH p = shortestPath((a:Entity {seed:'a'})-[:RELATED*..12]-(b:Entity {seed:'b'}))
  RETURN p
  ```

  ```cypher
  MATCH (e:Entity)-[r:RELATED]-() RETURN e, r
  ```

- **The good stuff** — the juiciest edges the run turned up that the path did not use. Half
  the fun is in the graph rather than the chain.
