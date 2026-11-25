#!/usr/bin/env python3
"""Build the Mira companion-agent memory graph in Relate — filed by chapter.

Reads the synthetic dataset in data/memories.json and materialises it
as a *library* of persistent Relate stores (directory mode):

    <base>/
      index/                  the map: periods, shared entities, stubs
      periods/<period-slug>/  one store per Period — the life itself

Storage is organised the way autobiographical memory is: by period.
A period is not a tag on a memory; it is the first-level container
the memory is filed in. Each partition holds its period's Period
node, Events, Memories and Cues, plus local copies of the people /
places / items they touch, and every intra-period edge (provenance
chains included: a memory's DERIVED_FROM chain never needs to leave
its chapter, because a memory is filed under the period of the root
event its chain ends at). The index store holds no life, only the
map: Period nodes in chronological order (BEFORE edges), the
canonical shared entities with their period-independent facts (who
GAVE what), APPEARS_IN edges from each entity to the periods it
appears in, Cue nodes with FIRES_IN edges to the periods they are
bound in, one MemoryRef stub per memory (FILED_IN -> its period),
and — should a derivation ever cross a chapter boundary — the
cross-period DERIVED_FROM edge itself, registered between stubs so
it stays resolvable. (This dataset has none; the routing is general
and unit-tested.)

Graph model (per partition unless noted)
-----------------------------------------
Nodes:
  Person  {name, role, note}          (local copy; canonical in index)
  Place   {name, kind}                (local copy; canonical in index)
  Item    {name, kind}                (local copy; canonical in index)
  Event   {id, title, date, mood, surprise, tags[]}
  Period  {id, name, slug, start_date, end_date, atmosphere,
           dominant_emotion, opened_by}
  Cue     {kind, label}               -- sensory anchors; never decayed
  Memory  {id, text, vague, anon, gist, shard, haze1, haze2, haze3,
           detail_spec, kind, privacy, confidence, recorded_at,
           source_event, emotion, valence, pathway, emotion_weight,
           surprise, strength, encoded_strength, salience,
           last_accessed, strength_as_of, sunk, buried, buried_ever,
           ruminate_count, routine_id (only on routine members),
           sensory_cues[]}
  Routine {id, text, gist, place, pathway, emotion_weight,
           member_count, first_date, last_date}
           -- a materialised summary of a set of near-identical
           routine episodes (see routine_groups below); lives in the
           chapter it summarises, points at its members, deletes
           nothing

Edges:
  (Event)  -[:EXPERIENCED_AT]-> (Place)
  (Event)  -[:WITH]->           (Person)
  (Event)  -[:INVOLVES]->       (Item)
  (Memory) -[:DERIVED_FROM]->   (Event | Memory)   provenance
  (Memory) -[:ABOUT]->          (Person)
  (Memory) -[:INVOLVES]->       (Item)
  (Memory) -[:TRIGGERED_BY]->   (Cue)
  (Period) -[:TRIGGERED_BY]->   (Cue)
  (Event)  -[:PART_OF]->        (Period)
  (Memory) -[:PART_OF]->        (Period)
  (Event)  -[:STARTS]->         (Period)   chapter break (see decay.py)
  (Routine)-[:SUMMARIZES]->     (Memory)   the members it stands for
  (Routine)-[:PART_OF]->        (Period)
  (Person) -[:GAVE]->           (Item)     -- in the INDEX: a fact
                                            -- about shared entities,
                                            -- not about one chapter
  (Entity) -[:APPEARS_IN]->     (Period)   -- index only
  (Cue)    -[:FIRES_IN]->       (Period)   -- index only
  (Ref)    -[:FILED_IN]->       (Period)   -- index only (MemoryRef)
  (Period) -[:BEFORE]->         (Period)   -- index only, chronological

Detail weights and the per-memory haze renderings
-------------------------------------------------
Every memory's detail fabric — its when, its who, its where, its
whats — is inventoried at build time with a *detail weight* per
detail: how strongly that detail is bound to emotion. The derivation
(documented in README, "The fuzzy band"):

  stickiness S(entity) = 0.30 + 0.70 x (mean of the top-3
      emotion_weights of all memories binding that entity) / 1.8
  when  = 0.15 + 0.55 x the source event's surprise
          (dates are fragile — unless the day itself was a shock)
  who   = S(person) - 0.02        where = S(place) + 0.04, and at
          least 0.85 if the place hosts a chapter-break event
          (at equal charge, a place outlasts a name)
  what  = S(item) + 0.10 if the item was a gift (a given thing
          carries its giver's charge)

Details shed in ascending weight order (ties broken by slot
fragility: when < what < where < who). The three haze renderings
stored on each Memory (haze1 / haze2 / haze3) are composed at build
time from its own shedding sequence: HAZY-1 keeps the 3 heaviest
details, HAZY-2 the 2 heaviest, HAZY-3 only the single heaviest —
each rendered as the gist plus the surviving details. Recall only
ever *selects* a stored rendering; nothing is rewritten at recall
time (the anti-fabrication rule).

Each Memory also starts life with a forgetting-curve strength (see
decay.py): strength = confidence x salience(source event's mood)
x (1 + 0.6 x the event's surprise), last_accessed / strength_as_of
stamped at recorded_at, and the sunk flag set from that strength —
a memory encoded below the sinking threshold is born sunk, so the
flag and the band agree from the start.

Usage:
    python build_graph.py [--data data/memories.json] [--store store]
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
from pathlib import Path

import relate

from decay import (
    CHAPTER_BREAK_THRESHOLD,
    SUNK_THRESHOLD,
    emotion_profile,
    event_salience,
    initial_strength,
)

ROOT = Path(__file__).resolve().parent

# Slot fragility, used only to break weight ties when ordering a
# memory's shedding sequence: dates go before objects, objects
# before places, places before people.
SLOT_RANK = {"when": 0, "what": 1, "where": 2, "who": 3}

# How many of the heaviest details survive into each haze layer.
HAZE_KEEP = {1: 3, 2: 2, 3: 1}

# Routine merging: when the same unremarkable day happens again and
# again, the mind stops filing the days separately and keeps the
# habit. The trigger is deterministic and structural — no text
# similarity, no dice: episode memories filed in the same chapter,
# witnessed at the same place, on the same emotion pathway with
# emotion_weights that agree within ROUTINE_WEIGHT_SPREAD, and all
# unsurprising (surprise <= ROUTINE_MAX_SURPRISE), merge once there
# are at least ROUTINE_MIN_GROUP of them. Facts, preferences, plans
# and secrets never merge — only episodes do, and SECRET_LOCKED
# episodes are excluded twice over (kind filter is not enough: a
# secret must never be summarised into a casually returned node).
ROUTINE_MIN_GROUP = 4
ROUTINE_MAX_SURPRISE = 0.2
ROUTINE_WEIGHT_SPREAD = 0.15


def load_dataset(path: Path) -> dict:
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


# ---------------------------------------------------------------------------
# Assignment: which chapter every event and memory is filed under
# ---------------------------------------------------------------------------

def assign_periods(data: dict) -> tuple[dict, dict]:
    """Return (event_period, memory_period): id -> period id.

    An event belongs to the period whose date range contains it. A
    memory is filed under the period of the *root* event of its
    DERIVED_FROM chain (following derived_from_memory links to the
    chain's head and taking that memory's source_event) — so a
    provenance chain, by construction, lives inside one chapter.
    """
    periods = data.get("periods", [])

    def period_of_date(day: str) -> str:
        for p in periods:
            if p["start_date"] <= day <= p["end_date"]:
                return p["id"]
        raise ValueError(f"no period contains date {day}")

    event_period = {e["id"]: period_of_date(e["date"]) for e in data["events"]}
    mem_by_id = {m["id"]: m for m in data["memories"]}
    memory_period = {}
    for mem in data["memories"]:
        head = mem
        seen = set()
        while head.get("derived_from_memory"):
            if head["id"] in seen:
                raise ValueError(f"derivation cycle at {head['id']}")
            seen.add(head["id"])
            head = mem_by_id[head["derived_from_memory"]]
        memory_period[mem["id"]] = event_period[head["source_event"]]
    return event_period, memory_period


def route_derivations(data: dict, memory_period: dict) -> tuple[list, list]:
    """Split DERIVED_FROM links into (intra, cross) by partition.

    intra: [(memory_id, parent_memory_id)] filed inside one partition.
    cross: [(memory_id, parent_memory_id)] whose endpoints are filed
    in different periods — these are registered in the index between
    MemoryRef stubs instead. (A memory derived directly from its
    event is always intra: it is filed under that event's period.)
    """
    intra, cross = [], []
    for mem in data["memories"]:
        parent = mem.get("derived_from_memory")
        if not parent:
            continue
        if memory_period[mem["id"]] == memory_period[parent]:
            intra.append((mem["id"], parent))
        else:
            cross.append((mem["id"], parent))
    return intra, cross


# ---------------------------------------------------------------------------
# Routine merging: repeated days collapse into the habit they formed
# ---------------------------------------------------------------------------

def routine_groups(data: dict, event_period: dict,
                   memory_period: dict) -> dict:
    """Detect the routine groups in a dataset, deterministically.

    Returns {routine_id: group} where a group is a dict with the
    chapter (`period`), the shared `place` and `pathway`, the member
    memory ids in recorded order, the span of the members' event
    dates, and the group's pre-stored renderings (`text` / `gist`)
    composed from those facts alone — place, chapter, count, dates.
    The composer invents nothing: no activity, no weather, no company
    that the data does not name.

    A memory joins a bucket when it is an *episode* (facts,
    preferences, plans and secrets keep their own recall paths), is
    not SECRET_LOCKED, and its source event is unsurprising. Buckets
    are keyed by (chapter, place, pathway); the routine id names all
    three, so two pathways at one place can never overwrite each
    other (S4).

    Within a bucket, the group is the largest set of members whose
    emotion_weights agree within ROUTINE_WEIGHT_SPREAD — found by a
    deterministic sliding window over the weight-sorted members. A
    bucket that mixes weights (four joyful afternoons at 1.2, one
    tender one at 1.5) yields the agreeing majority as the routine
    instead of being rejected whole (S4); the odd one out simply
    stays an individual memory. Window ties break by size, then by
    the earliest first day, then by the lowest starting weight.

    The summary counts *days* (distinct event dates), not members:
    two memories of one afternoon are one day of the habit.

    One pathway is excluded outright: `buried`. A suppressed memory
    must never be summarised — a Routine node naming its days would
    leak the existence of exactly what suppression holds down, and
    the drill-down would hand the days themselves back. Burial and
    routine are opposite fates, and the grouping keeps them so.
    """
    event_by_id = {e["id"]: e for e in data["events"]}
    period_by_id = {p["id"]: p for p in data.get("periods", [])}
    buckets: dict[tuple, list] = {}
    for mem in data["memories"]:
        if mem["kind"] != "episode" or mem["privacy"] == "SECRET_LOCKED":
            continue
        event = event_by_id[mem["source_event"]]
        if event.get("surprise", 0.0) > ROUTINE_MAX_SURPRISE:
            continue
        _valence, pathway, weight = emotion_profile(event["mood"])
        if pathway == "buried":
            continue
        key = (memory_period[mem["id"]], event["place"], pathway)
        buckets.setdefault(key, []).append((mem, weight))

    groups = {}
    for (pid, place, pathway), members in sorted(buckets.items()):
        if len(members) < ROUTINE_MIN_GROUP:
            continue
        # Sliding window over the weight-sorted members: the largest
        # subset whose weights agree within the spread.
        ordered = sorted(
            members,
            key=lambda mw: (mw[1], mw[0]["recorded_at"], mw[0]["id"]))
        best = None   # (size, first_date, start_weight, window)
        for i in range(len(ordered)):
            j = i
            while (j + 1 < len(ordered)
                   and ordered[j + 1][1] - ordered[i][1]
                   <= ROUTINE_WEIGHT_SPREAD):
                j += 1
            window = ordered[i:j + 1]
            if len(window) < ROUTINE_MIN_GROUP:
                continue
            first_date = min(event_by_id[m["source_event"]]["date"]
                             for m, _w in window)
            cand = (len(window), first_date, ordered[i][1], window)
            if (best is None or cand[0] > best[0]
                    or (cand[0] == best[0] and cand[1] < best[1])
                    or (cand[0] == best[0] and cand[1] == best[1]
                        and cand[2] < best[2])):
                best = cand
        if best is None:
            continue
        chosen = best[3]
        weights = [w for _m, w in chosen]
        mems = sorted((m for m, _w in chosen),
                      key=lambda m: m["recorded_at"])
        dates = sorted(event_by_id[m["source_event"]]["date"] for m in mems)
        days = sorted(set(dates))
        rid = f"routine-{pid}-{pathway}-{slugify(place)}"
        period_name = period_by_id[pid]["name"]
        groups[rid] = {
            "id": rid,
            "period": pid,
            "place": place,
            "pathway": pathway,
            "emotion_weight": round(sum(weights) / len(weights), 3),
            "members": [m["id"] for m in mems],
            "first_date": dates[0],
            "last_date": dates[-1],
            "text": (
                f"During {period_name}, visits to {place} settled into "
                f"a routine: {len(days)} similar days between "
                f"{days[0]} and {days[-1]}, each one unremarkable on "
                f"its own, together a habit."
            ),
            "gist": f"A routine of visits to {place} during {period_name}.",
        }
    return groups


# ---------------------------------------------------------------------------
# Detail weights: how strongly each detail is bound to emotion
# ---------------------------------------------------------------------------

def _stickiness(weights: list[float]) -> float:
    """An entity's stickiness from the emotion_weights of the memories
    that bind it: the mean of its three most charged bindings."""
    if not weights:
        return 0.30
    top = sorted(weights, reverse=True)[:3]
    return _clamp01(0.30 + 0.70 * (sum(top) / len(top)) / 1.8)


def detail_inventories(data: dict) -> dict:
    """Per memory: its detail inventory, heaviest last.

    Returns {memory_id: [ {slot, label, weight}, ... ]} sorted in
    shedding order — ascending by (weight, slot fragility, label).
    The derivation rules are documented in the module docstring.
    """
    event_by_id = {e["id"]: e for e in data["events"]}
    item_by_name = {i["name"]: i for i in data["items"]}

    def mem_weight(mem) -> float:
        mood = event_by_id[mem["source_event"]]["mood"]
        return emotion_profile(mood)[2]

    # Binding weights per entity, over the whole dataset.
    person_w: dict[str, list[float]] = {}
    place_w: dict[str, list[float]] = {}
    item_w: dict[str, list[float]] = {}
    for mem in data["memories"]:
        ev = event_by_id[mem["source_event"]]
        w = mem_weight(mem)
        for name in set(ev.get("with", [])) | set(mem.get("about", [])):
            person_w.setdefault(name, []).append(w)
        place_w.setdefault(ev["place"], []).append(w)
        for name in set(mem.get("involves", [])) | set(ev.get("items", [])):
            item_w.setdefault(name, []).append(w)
    person_s = {k: _stickiness(v) for k, v in person_w.items()}
    place_s = {k: _stickiness(v) for k, v in place_w.items()}
    item_s = {k: _stickiness(v) for k, v in item_w.items()}

    # Places that host a chapter break are emotional anchors.
    _ep, _mp = assign_periods(data)
    anchor_places = set()
    for period in data.get("periods", []):
        first = min(
            (e for e in data["events"] if _ep[e["id"]] == period["id"]),
            key=lambda e: e["date"], default=None)
        if first and first.get("surprise", 0.0) >= CHAPTER_BREAK_THRESHOLD:
            anchor_places.add(first["place"])

    inventories = {}
    for mem in data["memories"]:
        ev = event_by_id[mem["source_event"]]
        details = []
        details.append({
            "slot": "when", "label": ev["date"],
            "weight": _clamp01(0.15 + 0.55 * ev.get("surprise", 0.0)),
        })
        who = (set(ev.get("with", [])) | set(mem.get("about", []))) - {"Theo"}
        for name in sorted(who):
            details.append({
                "slot": "who", "label": name,
                "weight": _clamp01(person_s.get(name, 0.30) - 0.02),
            })
        where_w = place_s.get(ev["place"], 0.30) + 0.04
        if ev["place"] in anchor_places:
            where_w = max(where_w, 0.85)
        details.append({
            "slot": "where", "label": ev["place"],
            "weight": _clamp01(where_w),
        })
        whats = set(mem.get("involves", [])) | set(ev.get("items", []))
        for name in sorted(whats):
            gift = 0.10 if item_by_name.get(name, {}).get("given_by") else 0.0
            details.append({
                "slot": "what", "label": name,
                "weight": _clamp01(item_s.get(name, 0.30) + gift),
            })
        details.sort(key=lambda d: (round(d["weight"], 3),
                                    SLOT_RANK[d["slot"]], d["label"]))
        for d in details:
            d["weight"] = round(d["weight"], 3)
        inventories[mem["id"]] = details
    return inventories


def haze_renderings(mem: dict, details: list[dict]) -> dict:
    """Compose a memory's three stored haze renderings from its own
    shedding sequence: the gist, plus the details still held at that
    depth (heaviest first). Selected at recall time, never rewritten.
    """
    out = {}
    for level in (1, 2, 3):
        keep = HAZE_KEEP[level]
        survivors = details[-keep:] if details else []
        labels = [d["label"] for d in reversed(survivors)]
        if labels:
            out[level] = f"{mem['gist']} [still held: {' · '.join(labels)}]"
        else:
            out[level] = mem["gist"]
    return out


# ---------------------------------------------------------------------------
# The build
# ---------------------------------------------------------------------------

def build(data: dict, base_path: str) -> dict:
    """Build the library at `base_path` and return counts.

    Counts: distinct entity counts (Person/Place/Item/Cue, from the
    index), totals for Event/Memory/Period, the grand edge total over
    all stores, plus per-partition and index breakdowns.
    """
    base = Path(base_path)
    event_by_id = {e["id"]: e for e in data["events"]}
    event_period, memory_period = assign_periods(data)

    # Consistency (S6): a derived memory's own `source_event` must
    # name the same event its derivation chain roots at. Filing
    # follows the chain; encoding (mood, surprise, detail inventory)
    # reads the memory's source_event — if the two disagreed, the
    # memory would silently be encoded from one event while filed
    # under another's chapter, and same-event adjacency could then
    # link across stores around the bridge. Disagreement is a data
    # error: refuse to build.
    mem_by_id = {m["id"]: m for m in data["memories"]}
    for mem in data["memories"]:
        head = mem
        seen = set()
        while head.get("derived_from_memory"):
            if head["id"] in seen:
                raise ValueError(f"derivation cycle at {head['id']}")
            seen.add(head["id"])
            head = mem_by_id[head["derived_from_memory"]]
        if mem["source_event"] != head["source_event"]:
            raise ValueError(
                f"memory {mem['id']}: source_event "
                f"{mem['source_event']!r} disagrees with its derivation "
                f"chain's root event {head['source_event']!r} "
                f"(via {head['id']})")

    intra_deriv, cross_deriv = route_derivations(data, memory_period)
    intra_by_child = dict(intra_deriv)
    inventories = detail_inventories(data)
    groups = routine_groups(data, event_period, memory_period)
    groups_by_period: dict[str, dict] = {}
    for rid, group in groups.items():
        groups_by_period.setdefault(group["period"], {})[rid] = group
    period_by_id = {p["id"]: p for p in data.get("periods", [])}
    # Period directories are named by slug; two period names that
    # slugify alike ("Chapter A" / "Chapter A!") would otherwise map
    # to one directory and the second build pass would clear the
    # first period's store (S6). Disambiguate deterministically with
    # the period id; the slug is stored on the Period nodes (index
    # and partition alike), so the Library opens exactly what was
    # built.
    slugs = {}
    _used_slugs = set()
    for p in data.get("periods", []):
        slug = slugify(p["name"])
        if slug in _used_slugs:
            slug = f"{slug}-{p['id']}"
        if slug in _used_slugs:
            raise ValueError(
                f"period slug collision cannot be resolved: {slug!r}")
        _used_slugs.add(slug)
        slugs[p["id"]] = slug

    # Chapter breaks, computed once: period id -> opening event id.
    opened_by = {}
    for period in data.get("periods", []):
        first = min(
            (e for e in data["events"] if event_period[e["id"]] == period["id"]),
            key=lambda e: e["date"], default=None)
        if first and first.get("surprise", 0.0) >= CHAPTER_BREAK_THRESHOLD:
            opened_by[period["id"]] = first["id"]

    partition_counts = {}
    membership = {}   # period id -> {"people": set, "places": set, "items": set}
    cue_periods: dict[str, set] = {}   # cue label -> period ids it fires in

    for period in data.get("periods", []):
        pid = period["id"]
        db = relate.connect(str(base / "periods" / slugs[pid]))
        db.execute("MATCH (n) DETACH DELETE n")

        events = [e for e in data["events"] if event_period[e["id"]] == pid]
        mems = [m for m in data["memories"] if memory_period[m["id"]] == pid]

        people = set()
        places = set()
        items = set()
        for e in events:
            places.add(e["place"])
            people.update(e.get("with", []))
            items.update(e.get("items", []))
        for m in mems:
            people.update(m.get("about", []))
            items.update(m.get("involves", []))
        membership[pid] = {"people": people, "places": places, "items": items}

        for person in data["people"]:
            if person["name"] in people:
                db.execute(
                    "CREATE (:Person {name: $name, role: $role, note: $note})",
                    person)
        for place in data["places"]:
            if place["name"] in places:
                db.execute("CREATE (:Place {name: $name, kind: $kind})", place)
        for item in data["items"]:
            if item["name"] in items:
                db.execute("CREATE (:Item {name: $name, kind: $kind})", item)

        db.execute(
            "CREATE (:Period {id: $id, name: $name, slug: $slug, "
            "start_date: $start, end_date: $end, atmosphere: $atmosphere, "
            "dominant_emotion: $dominant})",
            {"id": pid, "name": period["name"], "slug": slugs[pid],
             "start": period["start_date"], "end": period["end_date"],
             "atmosphere": period["atmosphere"],
             "dominant": period["dominant_emotion"]})

        for event in events:
            db.execute(
                "CREATE (:Event {id: $id, title: $title, date: $date, "
                "mood: $mood, surprise: $surprise, tags: $tags})",
                {"id": event["id"], "title": event["title"],
                 "date": event["date"], "mood": event["mood"],
                 "surprise": event.get("surprise", 0.0),
                 "tags": event.get("tags", [])})
            db.execute(
                "MATCH (e:Event {id: $id}), (pl:Place {name: $place}) "
                "CREATE (e)-[:EXPERIENCED_AT]->(pl)",
                {"id": event["id"], "place": event["place"]})
            for person in event.get("with", []):
                db.execute(
                    "MATCH (e:Event {id: $id}), (p:Person {name: $person}) "
                    "CREATE (e)-[:WITH]->(p)",
                    {"id": event["id"], "person": person})
            for item in event.get("items", []):
                db.execute(
                    "MATCH (e:Event {id: $id}), (i:Item {name: $item}) "
                    "CREATE (e)-[:INVOLVES]->(i)",
                    {"id": event["id"], "item": item})
            db.execute(
                "MATCH (e:Event {id: $id}), (p:Period {id: $pid}) "
                "CREATE (e)-[:PART_OF]->(p)",
                {"id": event["id"], "pid": pid})

        if pid in opened_by:
            db.execute(
                "MATCH (e:Event {id: $eid}), (p:Period {id: $pid}) "
                "CREATE (e)-[:STARTS]->(p) SET p.opened_by = $eid",
                {"eid": opened_by[pid], "pid": pid})

        # This partition's cues: bound to its memories, or to the
        # period itself. Cue nodes are never decayed or deleted.
        cue_labels = set(period.get("sensory_cues", []))
        for m in mems:
            cue_labels.update(m.get("sensory_cues", []))
        for label in sorted(cue_labels):
            kind, _, _ = label.partition(":")
            db.execute("CREATE (:Cue {kind: $kind, label: $label})",
                       {"kind": kind, "label": label})
            cue_periods.setdefault(label, set()).add(pid)
        for label in period.get("sensory_cues", []):
            db.execute(
                "MATCH (p:Period {id: $pid}), (c:Cue {label: $label}) "
                "CREATE (p)-[:TRIGGERED_BY]->(c)",
                {"pid": pid, "label": label})

        for mem in mems:
            mood = event_by_id[mem["source_event"]]["mood"]
            surprise = event_by_id[mem["source_event"]].get("surprise", 0.0)
            valence, pathway, weight = emotion_profile(mood)
            s0 = initial_strength(mem["confidence"], mood, surprise)
            details = inventories[mem["id"]]
            haze = haze_renderings(mem, details)
            db.execute(
                "CREATE (:Memory {id: $id, text: $text, vague: $vague, "
                "anon: $anon, gist: $gist, shard: $shard, "
                "haze1: $haze1, haze2: $haze2, haze3: $haze3, "
                "detail_spec: $detail_spec, "
                "kind: $kind, privacy: $privacy, "
                "confidence: $confidence, recorded_at: $recorded_at, "
                "source_event: $source_event, "
                "emotion: $emotion, valence: $valence, pathway: $pathway, "
                "emotion_weight: $weight, surprise: $surprise, "
                "strength: $strength, encoded_strength: $strength, "
                "salience: $salience, last_accessed: $recorded_at, "
                "strength_as_of: $recorded_at, sunk: $sunk, buried: false, "
                "buried_ever: false, ruminate_count: 0, "
                "sensory_cues: $cues})",
                {
                    "id": mem["id"],
                    "text": mem["text"],
                    "vague": mem["vague"],
                    "anon": mem["anon"],
                    "gist": mem["gist"],
                    # The FRAGMENT-stage shard: hand-written in the
                    # dataset for cue-carrying memories; the source
                    # event's place is the fallback — a lone 'where',
                    # floating free.
                    "shard": mem.get("shard")
                             or event_by_id[mem["source_event"]]["place"],
                    "haze1": haze[1], "haze2": haze[2], "haze3": haze[3],
                    "detail_spec": json.dumps(details),
                    "kind": mem["kind"],
                    "privacy": mem["privacy"],
                    "confidence": mem["confidence"],
                    "recorded_at": mem["recorded_at"],
                    "source_event": mem["source_event"],
                    "emotion": mood,
                    "valence": valence,
                    "pathway": pathway,
                    "weight": weight,
                    "surprise": surprise,
                    "strength": s0,
                    # The sunk flag agrees with the band from birth
                    # (S3): a memory encoded below the threshold is
                    # born sunk, not born "active at 0.10".
                    "sunk": s0 < SUNK_THRESHOLD,
                    "salience": event_salience(mood),
                    "cues": mem.get("sensory_cues", []),
                })
            for label in mem.get("sensory_cues", []):
                db.execute(
                    "MATCH (m:Memory {id: $id}), (c:Cue {label: $label}) "
                    "CREATE (m)-[:TRIGGERED_BY]->(c)",
                    {"id": mem["id"], "label": label})
            for person in mem.get("about", []):
                db.execute(
                    "MATCH (m:Memory {id: $id}), (p:Person {name: $person}) "
                    "CREATE (m)-[:ABOUT]->(p)",
                    {"id": mem["id"], "person": person})
            for item in mem.get("involves", []):
                db.execute(
                    "MATCH (m:Memory {id: $id}), (i:Item {name: $item}) "
                    "CREATE (m)-[:INVOLVES]->(i)",
                    {"id": mem["id"], "item": item})
            db.execute(
                "MATCH (m:Memory {id: $id}), (p:Period {id: $pid}) "
                "CREATE (m)-[:PART_OF]->(p)",
                {"id": mem["id"], "pid": pid})

        # Provenance, wired in a SECOND pass (B3): every Memory node
        # of this chapter now exists, so a child listed *before* its
        # parent in the dataset still finds the parent to point at.
        # (Wiring each edge in the creation pass silently matched
        # zero rows for an early child, and the missing edge was
        # never retried — an orphan memory with a clean conscience.)
        # The DERIVED_FROM edge points at the immediate source — the
        # witnessed event, or the parent memory in the same chapter.
        # A derivation that crossed chapters was routed to the index
        # instead (route_derivations); those children are verified
        # against the index registration below, not here.
        cross_children = {child for child, _parent in cross_deriv}
        for mem in mems:
            if mem["id"] in intra_by_child:
                db.execute(
                    "MATCH (m:Memory {id: $id}), "
                    "(parent:Memory {id: $parent}) "
                    "CREATE (m)-[:DERIVED_FROM]->(parent)",
                    {"id": mem["id"], "parent": intra_by_child[mem["id"]]})
            elif not mem.get("derived_from_memory"):
                db.execute(
                    "MATCH (m:Memory {id: $id}), (e:Event {id: $event}) "
                    "CREATE (m)-[:DERIVED_FROM]->(e)",
                    {"id": mem["id"], "event": mem["source_event"]})
        # Verification: no silent orphans. Every memory filed here
        # must carry exactly one in-partition DERIVED_FROM edge, or
        # be a registered cross-period child. Anything else is a
        # build failure, reported as one — never a graph that merely
        # *claims* every memory is backed.
        for mem in mems:
            if mem["id"] in cross_children:
                continue
            n = db.execute(
                "MATCH (m:Memory {id: $id})-[:DERIVED_FROM]->() "
                "RETURN count(*) AS c",
                {"id": mem["id"]},
            ).data()[0]["c"]
            if n != 1:
                raise RuntimeError(
                    f"build failed: memory {mem['id']} in period {pid} "
                    f"has {n} DERIVED_FROM edges (expected exactly 1)")

        # Routine nodes for this chapter: materialised summaries of
        # the repeated days. They summarise; they replace nothing —
        # every member keeps its node, its renderings and its
        # provenance chain, and gains only a routine_id back-pointer.
        for rid, group in groups_by_period.get(pid, {}).items():
            db.execute(
                "CREATE (:Routine {id: $id, text: $text, gist: $gist, "
                "place: $place, pathway: $pathway, "
                "emotion_weight: $weight, member_count: $n, "
                "first_date: $first, last_date: $last})",
                {"id": rid, "text": group["text"], "gist": group["gist"],
                 "place": group["place"], "pathway": group["pathway"],
                 "weight": group["emotion_weight"],
                 "n": len(group["members"]),
                 "first": group["first_date"],
                 "last": group["last_date"]})
            for mid in group["members"]:
                db.execute(
                    "MATCH (r:Routine {id: $rid}), (m:Memory {id: $mid}) "
                    "CREATE (r)-[:SUMMARIZES]->(m) SET m.routine_id = $rid",
                    {"rid": rid, "mid": mid})
            db.execute(
                "MATCH (r:Routine {id: $rid}), (p:Period {id: $pid}) "
                "CREATE (r)-[:PART_OF]->(p)",
                {"rid": rid, "pid": pid})

        counts = {}
        for label in ("Person", "Place", "Item", "Event", "Memory",
                      "Period", "Cue", "Routine"):
            counts[label] = db.execute(
                f"MATCH (n:{label}) RETURN count(n) AS c").data()[0]["c"]
        counts["edges"] = db.execute(
            "MATCH ()-[r]->() RETURN count(r) AS c").data()[0]["c"]
        partition_counts[pid] = counts
        db.close()

    # --- The index: the map of the life, not the life -------------------
    idx = relate.connect(str(base / "index"))
    idx.execute("MATCH (n) DETACH DELETE n")
    ordered = sorted(data.get("periods", []), key=lambda p: p["start_date"])
    for period in ordered:
        idx.execute(
            "CREATE (:Period {id: $id, name: $name, slug: $slug, "
            "start_date: $start, end_date: $end, atmosphere: $atmosphere, "
            "dominant_emotion: $dominant})",
            {"id": period["id"], "name": period["name"],
             "slug": slugs[period["id"]], "start": period["start_date"],
             "end": period["end_date"], "atmosphere": period["atmosphere"],
             "dominant": period["dominant_emotion"]})
        if period["id"] in opened_by:
            idx.execute(
                "MATCH (p:Period {id: $pid}) SET p.opened_by = $eid",
                {"pid": period["id"], "eid": opened_by[period["id"]]})
    for earlier, later in zip(ordered, ordered[1:]):
        idx.execute(
            "MATCH (a:Period {id: $a}), (b:Period {id: $b}) "
            "CREATE (a)-[:BEFORE]->(b)",
            {"a": earlier["id"], "b": later["id"]})
    for person in data["people"]:
        idx.execute(
            "CREATE (:Person {name: $name, role: $role, note: $note})", person)
    for place in data["places"]:
        idx.execute("CREATE (:Place {name: $name, kind: $kind})", place)
    for item in data["items"]:
        idx.execute("CREATE (:Item {name: $name, kind: $kind})", item)
        if item.get("given_by"):
            idx.execute(
                "MATCH (p:Person {name: $giver}), (i:Item {name: $item}) "
                "CREATE (p)-[:GAVE]->(i)",
                {"giver": item["given_by"], "item": item["name"]})
    for pid, members in membership.items():
        for label, key in (("Person", "people"), ("Place", "places"),
                           ("Item", "items")):
            for name in sorted(members[key]):
                idx.execute(
                    f"MATCH (n:{label} {{name: $name}}), "
                    "(p:Period {id: $pid}) CREATE (n)-[:APPEARS_IN]->(p)",
                    {"name": name, "pid": pid})
    all_cues = set()
    for m in data["memories"]:
        all_cues.update(m.get("sensory_cues", []))
    for p in data.get("periods", []):
        all_cues.update(p.get("sensory_cues", []))
    for label in sorted(all_cues):
        kind, _, _ = label.partition(":")
        idx.execute("CREATE (:Cue {kind: $kind, label: $label})",
                    {"kind": kind, "label": label})
        for pid in sorted(cue_periods.get(label, set())):
            idx.execute(
                "MATCH (c:Cue {label: $label}), (p:Period {id: $pid}) "
                "CREATE (c)-[:FIRES_IN]->(p)",
                {"label": label, "pid": pid})
    for mem in data["memories"]:
        idx.execute("CREATE (:MemoryRef {id: $id})", {"id": mem["id"]})
        idx.execute(
            "MATCH (r:MemoryRef {id: $id}), (p:Period {id: $pid}) "
            "CREATE (r)-[:FILED_IN]->(p)",
            {"id": mem["id"], "pid": memory_period[mem["id"]]})
    for child, parent in cross_deriv:
        idx.execute(
            "MATCH (a:MemoryRef {id: $child}), (b:MemoryRef {id: $parent}) "
            "CREATE (a)-[:DERIVED_FROM]->(b)",
            {"child": child, "parent": parent})

    index_counts = {}
    for label in ("Period", "Person", "Place", "Item", "Cue", "MemoryRef"):
        index_counts[label] = idx.execute(
            f"MATCH (n:{label}) RETURN count(n) AS c").data()[0]["c"]
    index_counts["edges"] = idx.execute(
        "MATCH ()-[r]->() RETURN count(r) AS c").data()[0]["c"]
    idx.close()

    counts = {
        "Person": index_counts["Person"],
        "Place": index_counts["Place"],
        "Item": index_counts["Item"],
        "Cue": index_counts["Cue"],
        "Event": sum(c["Event"] for c in partition_counts.values()),
        "Memory": sum(c["Memory"] for c in partition_counts.values()),
        "Routine": sum(c["Routine"] for c in partition_counts.values()),
        "Period": index_counts["Period"],
        "edges": index_counts["edges"]
                 + sum(c["edges"] for c in partition_counts.values()),
        "partitions": partition_counts,
        "index": index_counts,
        "cross_period_derivations": cross_deriv,
    }
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", default=str(ROOT / "data" / "memories.json"))
    parser.add_argument("--store", default=str(ROOT / "store"),
                        help="Library base directory (index/ + periods/).")
    parser.add_argument(
        "--fresh",
        action="store_true",
        help="Delete the library directory first (default: rebuild in place).",
    )
    args = parser.parse_args()

    if args.fresh and Path(args.store).exists():
        shutil.rmtree(args.store)

    data = load_dataset(Path(args.data))
    counts = build(data, args.store)
    print(f"Library built under: {args.store}")
    print(f"  index: {counts['index']['edges']} edges, "
          f"{counts['index']['MemoryRef']} memory refs")
    for pid, c in counts["partitions"].items():
        print(f"  period {pid}: Event {c['Event']}, Memory {c['Memory']}, "
              f"Cue {c['Cue']}, Routine {c['Routine']}, edges {c['edges']}")
    for label in ("Person", "Place", "Item", "Event", "Memory", "Period", "Cue"):
        print(f"  {label:<8} distinct/total: {counts[label]}")
    print(f"  total edges (all stores): {counts['edges']}")
    print(f"  cross-period derivations routed to index: "
          f"{len(counts['cross_period_derivations'])}")


if __name__ == "__main__":
    main()
