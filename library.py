#!/usr/bin/env python3
"""The Library: a period-partitioned memory store, opened as one handle.

Storage layout (built by build_graph.py):

    <base>/
      index/                 the index store (one Relate store)
      periods/
        <period-slug>/       one Relate store per Period (a "chapter")

The partitions hold the life itself: each period's events, memories,
cues and the local copies of the people / places / items they touch,
with every intra-period edge. The index holds no life at all — only
the map of it: the Period nodes in chronological order (linked by
BEFORE edges), the canonical shared entities (Person / Place / Item)
with the period-independent facts between them (GAVE), an APPEARS_IN
edge from every shared entity to each period it appears in, a Cue
node with FIRES_IN edges to the periods it is bound in, and one
lightweight MemoryRef stub per memory with a FILED_IN edge to its
period — the cross-reference spine that lets any query locate any
memory's partition by its stable id.

Relate constraint this design is built around (verified against
relate-db 1.2.0): several *different* stores may be open in one
process at once, but a second connection to the *same* store is
refused ("storage locked"). So a Library opens each store exactly
once and keeps the connections; nothing else in the codebase calls
relate.connect on a library path.
"""

from __future__ import annotations

from pathlib import Path

import relate


class Library:
    """One handle over an index store plus one store per period."""

    def __init__(self, base_path: str | Path):
        base = Path(base_path)
        self.base = base
        self.index = relate.connect(str(base / "index"))
        rows = self.index.execute(
            "MATCH (p:Period) RETURN p.id AS id, p.name AS name, "
            "p.slug AS slug, p.start_date AS start_date, "
            "p.end_date AS end_date ORDER BY p.start_date"
        ).data()
        if not rows:
            raise ValueError(f"no periods in the index at {base / 'index'}")
        self.period_order = [r["id"] for r in rows]      # chronological
        self.period_meta = {r["id"]: r for r in rows}
        self.partitions = {
            r["id"]: relate.connect(str(base / "periods" / r["slug"]))
            for r in rows
        }

    # -- structure ------------------------------------------------------

    def partition(self, period_id: str):
        """The Relate connection of one period's own store."""
        return self.partitions[period_id]

    def all_partitions(self) -> list:
        """Every partition connection, in chronological period order."""
        return [self.partitions[pid] for pid in self.period_order]

    def close(self) -> None:
        for db in self.partitions.values():
            db.close()
        self.index.close()

    # -- location (the index doing its job) ------------------------------

    def locate_memory(self, mem_id: str) -> str:
        """Which period a memory is filed under, via its index stub."""
        rows = self.index.execute(
            "MATCH (r:MemoryRef {id: $id})-[:FILED_IN]->(p:Period) "
            "RETURN p.id AS pid",
            {"id": mem_id},
        ).data()
        if not rows:
            raise KeyError(f"no memory {mem_id!r} in the index")
        return rows[0]["pid"]

    def db_for_memory(self, mem_id: str):
        """The partition connection that holds one memory."""
        return self.partitions[self.locate_memory(mem_id)]

    def period_for_date(self, day: str) -> str | None:
        """The period whose date range contains `day` (index lookup)."""
        rows = self.index.execute(
            "MATCH (p:Period) WHERE p.start_date <= $d AND $d <= p.end_date "
            "RETURN p.id AS pid",
            {"d": day},
        ).data()
        return rows[0]["pid"] if rows else None

    def periods_for_entity(self, label: str, name: str) -> list:
        """Periods a shared entity (Person/Place/Item) appears in."""
        rows = self.index.execute(
            f"MATCH (n:{label} {{name: $name}})-[:APPEARS_IN]->(p:Period) "
            "RETURN p.id AS pid",
            {"name": name},
        ).data()
        found = {r["pid"] for r in rows}
        return [pid for pid in self.period_order if pid in found]

    def periods_for_cue(self, cue_label: str) -> list:
        """Periods a sensory cue is bound in (index FIRES_IN edges)."""
        rows = self.index.execute(
            "MATCH (c:Cue {label: $label})-[:FIRES_IN]->(p:Period) "
            "RETURN p.id AS pid",
            {"label": cue_label},
        ).data()
        found = {r["pid"] for r in rows}
        return [pid for pid in self.period_order if pid in found]

    # -- fan-out ----------------------------------------------------------

    def execute_all(self, cypher: str, params: dict | None = None) -> list:
        """Run a read query in every partition and concatenate the rows.

        Sound for queries whose joins resolve inside one period —
        which, by construction of the partitions, is every query about
        a period's own events, memories and their local entities. Rows
        gain no period tag; callers that need one use execute_each.
        """
        out = []
        for db in self.all_partitions():
            out.extend(db.execute(cypher, params or {}).data())
        return out

    def execute_each(self, cypher: str, params: dict | None = None) -> list:
        """Like execute_all, but returns [(period_id, rows), ...]."""
        return [
            (pid, self.partitions[pid].execute(cypher, params or {}).data())
            for pid in self.period_order
        ]
