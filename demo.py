#!/usr/bin/env python3
"""One-command demo of the auditable graph memory for a companion agent.

Runs seven showcases against the period-partitioned library built by
build_graph.py (an index store plus one Relate store per chapter of
life — see library.py):

  1. Provenance audit  -- walk a memory's DERIVED_FROM chain back to the
     witnessed event it came from. Every claim the agent makes is auditable;
     if there is no path, there is no memory.
  2. Privacy tiers     -- the same question answered by a naive recall (leaks
     a SECRET_LOCKED memory), by the default privacy-filtered recall (safe),
     and by the explicit unlock path (passphrase-gated).
  3. Graph vs. text    -- six questions that need multi-hop relational
     reasoning, answered by Cypher traversals and by a naive keyword-overlap
     search over memory texts. Scores printed side by side.
  4. Forgetting curve  -- memory strengths decay exponentially over a
     simulated 12 weeks (in a separate library, so demos 1-3 stay pristine);
     untouched memories sink below the recall threshold — sunk, never
     deleted — while memories that life recalls again are reinforced, and
     even resurrected. The provenance chain of a sunk memory still walks.
  5. The fuzzy band   -- the same twelve weeks watched through the unified
     banded recall: CLEAR memories return in full; HAZY ones shed detail
     in their own order — lightest first, heaviest last, by detail
     weight, not by kind — until only the gist and one anchor detail
     remain; SUNK ones vanish. Emotion runs the machine: half-lives
     scale by feeling, joy outranks calm, grudges ruminate, and the
     painful vulnerable memories are not faded but BURIED — until a
     person's return, or a taste of ginger tea, brings them back up
     the ladder: affect, fragment, gist, clarity. A final act adds
     the orthogonal axis: SURPRISE. Three memories of one calm
     Friday — a grocery run, a one-day bakery stall, a sponge —
     share an emotion and differ only in how unexpected they were;
     the shocking one is still CLEAR when the others are sunk, and
     the summer itself was opened by a surprise (the chapter break).
  6. Periods          -- the top layer of autobiographical memory, and
     now the store's first-level structure: each period is its own
     partition. A cue spreads activation inside a chapter and an era
     comes back — a shard opening a season in one act, a season
     raining shards in the other; across chapters, only a damped
     echo crosses the index bridge. The order is emergent; the
     mechanism is deterministic.
  7. Habits & ceilings -- three refinements. Routine merging: six
     near-identical bookstore afternoons collapse into one Routine
     node, and daily recall answers with the habit — until a day is
     named, and the drill-down returns every afternoon. Cue dilution:
     the smell of darkroom chemicals raises a sunk memory while it is
     bound to that memory alone; after life reuses the same smell for
     three more memories, the same firing sets it down at a quarter
     of the strength and the story never assembles. Rumination: the
     grudge from Demo 5 is mentioned eight times — the first
     rehearsals pin it to its hard ceiling, then the dividend halves
     away and, still mentioned, it declines. Anger burns hot, then
     burns out.

Usage:
    python build_graph.py        # once, creates ./store (the library)
    python demo.py [--passphrase mira-unlock-demo]
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from datetime import date, timedelta
from pathlib import Path

import decay
from build_graph import build, load_dataset
from library import Library

ROOT = Path(__file__).resolve().parent
STORE = ROOT / "store"
DECAY_STORE = ROOT / "store_decay"
HAZE_STORE = ROOT / "store_haze"
PERIOD_STORE = ROOT / "store_period"
SURPRISE_STORE = ROOT / "store_surprise"
DATA = ROOT / "data" / "memories.json"

# Demo-only passphrase for the unlock path. In a real deployment this would
# be the owner's credential checked by the host application, never stored
# next to the data.
DEMO_PASSPHRASE = "mira-unlock-demo"

LINE = "=" * 74


def section(title: str) -> None:
    print(f"\n{LINE}\n{title}\n{LINE}")


def _one(lib, cypher: str, params: dict = None) -> dict:
    """First row of a fan-out query (for facts that live in one chapter)."""
    rows = lib.execute_all(cypher, params or {})
    return rows[0] if rows else {}


# --------------------------------------------------------------------------
# Demo 1: provenance audit
# --------------------------------------------------------------------------

def demo_provenance(lib) -> None:
    section("DEMO 1 — PROVENANCE AUDIT: every memory can be walked back to its source")
    mem_id = "m48"
    # The memory's chapter is located through the index; the whole
    # walk then happens inside that chapter's own store.
    db = lib.db_for_memory(mem_id)
    rows = db.execute(
        "MATCH (m:Memory {id: $id}) RETURN m.text AS text, m.kind AS kind, "
        "m.recorded_at AS recorded_at, m.confidence AS confidence",
        {"id": mem_id},
    ).data()
    mem = rows[0]
    print(f"Agent claims (memory {mem_id}, kind={mem['kind']}, "
          f"confidence={mem['confidence']}, recorded {mem['recorded_at']}):")
    print(f"  \"{mem['text']}\"")
    print("\nWalking the DERIVED_FROM chain (one Cypher hop at a time):")

    current_id, hop = mem_id, 0
    while True:
        rows = db.execute(
            "MATCH (m:Memory {id: $id})-[:DERIVED_FROM]->(src) "
            "RETURN labels(src) AS labels, src.id AS id, src.text AS text, "
            "src.title AS title, src.date AS date, src.mood AS mood",
            {"id": current_id},
        ).data()
        if not rows:
            break
        for row in rows:
            hop += 1
            label = row["labels"][0]
            if label == "Memory":
                print(f"  hop {hop}: DERIVED_FROM -> Memory {row['id']}: \"{row['text']}\"")
                current_id = row["id"]
            else:  # Event: the root of the chain — fetch the full scene
                event_id = row["id"]
                scene = db.execute(
                    "MATCH (e:Event {id: $id})-[:EXPERIENCED_AT]->(pl:Place) "
                    "RETURN pl.name AS place",
                    {"id": event_id},
                ).data()
                people = db.execute(
                    "MATCH (e:Event {id: $id})-[:WITH]->(p:Person) RETURN p.name AS name",
                    {"id": event_id},
                ).data()
                print(f"  hop {hop}: DERIVED_FROM -> Event {event_id}: \"{row['title']}\" "
                      f"({row['date']}, mood: {row['mood']})")
                print(f"           scene: place = {scene[0]['place']}, "
                      f"present = {[p['name'] for p in people]}")
                current_id = None
        if current_id is None:
            break

    # Audit sweep: follow every memory's DERIVED_FROM chain to its
    # end and confirm each one terminates at a witnessed Event. The
    # path length is unbounded — a hop cap could misclassify a long
    # legitimate chain as an orphan (B3). The sweep fans out over the
    # chapters — and every chain completes inside its own chapter,
    # which the per-partition builds guarantee.
    reachable = lib.execute_all(
        "MATCH (m:Memory)-[:DERIVED_FROM*]->(e:Event) RETURN DISTINCT m.id AS id"
    )
    total_memories = len(lib.execute_all("MATCH (m:Memory) RETURN m.id AS id"))
    orphans = total_memories - len(reachable)
    print(f"\nAudit check: {len(reachable)}/{total_memories} memories trace back to a "
          f"source event; memories with NO provenance path: {orphans}")
    if orphans == 0:
        print("Verdict: every memory in the graph is backed by a witnessed event.")
        print("If there is no path, there is no memory — the agent cannot fabricate one.")
    else:
        print(f"Verdict: AUDIT FAILED — {orphans} memor"
              f"{'y has' if orphans == 1 else 'ies have'} no provenance path. "
              "The claim above does not hold for this graph.")


# --------------------------------------------------------------------------
# Demo 2: privacy tiers
# --------------------------------------------------------------------------

def _drawer_search(lib, *, privacy_filter: bool, include_locked: bool = False):
    """Keyword recall over memories mentioning the desk drawer."""
    if include_locked:
        where = "toLower(m.text) CONTAINS 'drawer'"
    elif privacy_filter:
        where = "toLower(m.text) CONTAINS 'drawer' AND m.privacy <> 'SECRET_LOCKED'"
    else:
        where = "toLower(m.text) CONTAINS 'drawer'"
    return lib.execute_all(
        f"MATCH (m:Memory) WHERE {where} "
        "RETURN m.id AS id, m.text AS text, m.privacy AS privacy"
    )


def demo_privacy(lib, passphrase: str | None) -> None:
    section("DEMO 2 — PRIVACY TIERS: SECRET_LOCKED memories are unreachable by default")
    question = "What is Theo keeping in his desk drawer?"
    print(f"Question: {question}\n")

    print("(a) NAIVE recall — TEACHING CONTRAST, deliberately unfiltered;")
    print("    this is NOT a path the agent ever answers through. It shows")
    print("    what a careless memory layer does with the same question:")
    for row in _drawer_search(lib, privacy_filter=False):
        print(f"  [{row['privacy']}] {row['id']}: \"{row['text']}\"")
    print("  -> LEAK: a locked secret is surfaced by an ordinary question.\n")

    print("(b) DEFAULT recall (privacy hard filter — the only path normal queries get):")
    rows = _drawer_search(lib, privacy_filter=True)
    if rows:
        for row in rows:
            print(f"  [{row['privacy']}] {row['id']}: \"{row['text']}\"")
    else:
        print("  (no accessible memory mentions the drawer)")
    safe = lib.execute_all(
        "MATCH (m:Memory)-[:DERIVED_FROM]->(e:Event {id: 'e11'}) "
        "WHERE m.privacy <> 'SECRET_LOCKED' RETURN m.id AS id, m.text AS text, m.privacy AS privacy"
    )
    for row in safe:
        print(f"  closest safe memory from that night [{row['privacy']}] {row['id']}: \"{row['text']}\"")
    print("  -> The agent can acknowledge the evening without revealing its content.\n")

    print("(c) EXPLICIT UNLOCK path (passphrase-gated, auditable):")
    if passphrase is None:
        print("  (no --passphrase given: unlock not attempted)")
    elif passphrase != DEMO_PASSPHRASE:
        print("  wrong passphrase -> access denied, nothing returned.")
    else:
        rows = _drawer_search(lib, privacy_filter=True, include_locked=True)
        rows = [r for r in rows if r["privacy"] == "SECRET_LOCKED"]
        for row in rows:
            print(f"  passphrase accepted -> [{row['privacy']}] {row['id']}: \"{row['text']}\"")
        print("  -> Locked memories exist exactly for this path, and no other.")


# --------------------------------------------------------------------------
# Demo 3: graph recall vs. keyword baseline
# --------------------------------------------------------------------------

STOPWORDS = {
    "a", "an", "the", "is", "was", "did", "do", "does", "who", "what", "where",
    "when", "with", "him", "his", "he", "she", "her", "they", "them", "and",
    "at", "on", "in", "to", "for", "of", "last", "time", "usually", "still",
}


def keyword_baseline(question: str, memories: list[dict]) -> str:
    """Naive baseline: score memory texts by distinct keyword overlap.

    SECRET_LOCKED memories are excluded even here — the privacy hard rule
    applies to every recall path; this demo is about relational reasoning.
    """
    q_words = {w for w in re.findall(r"[a-z']+", question.lower()) if w not in STOPWORDS}
    best_text, best_score = "", -1
    for mem in memories:
        if mem["privacy"] == "SECRET_LOCKED":
            continue
        words = set(re.findall(r"[a-z']+", mem["text"].lower()))
        score = len(q_words & words)
        if score > best_score:
            best_text, best_score = mem["text"], score
    return best_text


def graph_answers(lib) -> list[tuple[str, str, list[str]]]:
    """Each entry: (question, graph answer, expected substrings).

    The traversals fan out over the chapters; each join resolves
    inside the chapter that holds the event, and date ordering /
    aggregation across chapters is settled over the fanned-out rows.
    """
    out = []

    # Q1 — latest 'down' event: who was with him, and where? (date order + 2 joins)
    evs = lib.execute_all(
        "MATCH (e:Event {mood: 'down'}) RETURN e.id AS id, e.date AS date")
    ev_row = max(evs, key=lambda r: r["date"])
    ev = ev_row["id"]
    ev_db = lib.partition(lib.period_for_date(ev_row["date"]))
    who = ev_db.execute(
        "MATCH (e:Event {id: $id})-[:WITH]->(p:Person) RETURN p.name AS name", {"id": ev}
    ).data()
    where = ev_db.execute(
        "MATCH (e:Event {id: $id})-[:EXPERIENCED_AT]->(pl:Place) RETURN pl.name AS name",
        {"id": ev},
    ).data()[0]["name"]
    who_txt = who[0]["name"] if who else "nobody (he was alone)"
    out.append((
        "The last time Theo felt down, who was with him and where did they go?",
        f"{who_txt}, at {where}",
        ["June", "Lakeside Park"],
    ))

    # Q2 — June's gift (the GAVE fact lives in the index) and the
    # place of the event where she gave it. The giving event is
    # identified as such — an event that involves the item *and* has
    # June present; the earliest one is the giving. (An arbitrary
    # later event that merely involves the scarf would prove nothing
    # about where it changed hands — S8.)
    item = lib.index.execute(
        "MATCH (june:Person {name: 'June'})-[:GAVE]->(i:Item) "
        "RETURN i.name AS item LIMIT 1"
    ).data()[0]["item"]
    rows = lib.execute_all(
        "MATCH (i:Item {name: $item})<-[:INVOLVES]-(e:Event)"
        "-[:EXPERIENCED_AT]->(pl:Place), "
        "(e)-[:WITH]->(:Person {name: 'June'}) "
        "RETURN pl.name AS place, e.date AS date",
        {"item": item},
    )
    place = min(rows, key=lambda r: r["date"])["place"]
    out.append((
        "What did June give Theo, and where did she give it to him?",
        f"a {item}, at the {place}",
        ["red scarf", "Night Market"],
    ))

    # Q3 — aggregation: the place Theo shares the most events with Ana
    rows = lib.execute_all(
        "MATCH (e:Event)-[:WITH]->(:Person {name: 'Ana'}), "
        "(e)-[:EXPERIENCED_AT]->(pl:Place) "
        "RETURN pl.name AS place, count(e) AS c"
    )
    totals = {}
    for r in rows:
        totals[r["place"]] = totals.get(r["place"], 0) + r["c"]
    ranked = sorted(totals.items(), key=lambda kv: kv[1], reverse=True)
    (top_place, top_c), (_, next_c) = ranked[0], ranked[1]
    out.append((
        "Which place does Theo most associate with his sister Ana?",
        f"{top_place} ({top_c} shared events, next best: {next_c})",
        ["Theo's Apartment"],
    ))

    # Q4 — the hiking plan's shopping item (event tag -> plan memory -> item)
    rows = lib.execute_all(
        "MATCH (m:Memory {kind: 'plan'})-[:DERIVED_FROM]->(e:Event), "
        "(m)-[:INVOLVES]->(i:Item) WHERE 'hiking' IN e.tags "
        "AND m.privacy <> 'SECRET_LOCKED' "
        "RETURN i.name AS item"
    )
    out.append((
        "For the hiking trip Theo planned with June, what is he planning to buy?",
        f"a {rows[0]['item']}",
        ["brass compass"],
    ))

    # Q5 — the earliest event at the chess club: who was with him?
    # (The graph proves presence at his first night there; it does
    # not prove who "introduced" him, so the question asks only what
    # the traversal can actually show — S8.)
    evs = lib.execute_all(
        "MATCH (e:Event)-[:EXPERIENCED_AT]->(:Place {name: 'Chess Club Hall'}) "
        "RETURN e.id AS id, e.date AS date")
    ev_row = min(evs, key=lambda r: r["date"])
    ev_db = lib.partition(lib.period_for_date(ev_row["date"]))
    who = ev_db.execute(
        "MATCH (e:Event {id: $id})-[:WITH]->(p:Person) RETURN p.name AS name",
        {"id": ev_row["id"]},
    ).data()[0]["name"]
    out.append((
        "Who was with Theo on his first night at the chess club?",
        who,
        ["Priya"],
    ))

    # Q6 — preference memory sourced from a Café-Lumen-with-Marco event -> item
    rows = lib.execute_all(
        "MATCH (m:Memory {kind: 'preference'})-[:DERIVED_FROM]->(e:Event), "
        "(e)-[:EXPERIENCED_AT]->(:Place {name: 'Café Lumen'}), "
        "(e)-[:WITH]->(:Person {name: 'Marco'}), (m)-[:INVOLVES]->(i:Item) "
        "WHERE m.privacy <> 'SECRET_LOCKED' "
        "RETURN i.name AS item LIMIT 1"
    )
    out.append((
        "When Theo is at Café Lumen with Marco, what does he usually order?",
        rows[0]["item"],
        ["mint tea"],
    ))

    return out


def demo_graph_vs_text(lib) -> None:
    section("DEMO 3 — GRAPH RECALL vs. KEYWORD SEARCH: questions that need relationships")
    with DATA.open(encoding="utf-8") as fh:
        memories = json.load(fh)["memories"]

    graph_score = text_score = 0
    for idx, (question, g_ans, expected) in enumerate(graph_answers(lib), 1):
        t_ans = keyword_baseline(question, memories)
        g_ok = all(s.lower() in g_ans.lower() for s in expected)
        t_ok = all(s.lower() in t_ans.lower() for s in expected)
        graph_score += g_ok
        text_score += t_ok
        print(f"Q{idx}. {question}")
        print(f"    graph : {g_ans}   [{'CORRECT' if g_ok else 'WRONG'}]")
        print(f"    text  : \"{t_ans}\"   [{'CORRECT' if t_ok else 'WRONG'}]")
    print(f"\nScore: graph recall {graph_score}/6 — keyword baseline {text_score}/6")
    print("The keyword baseline can only find what is written inside a single")
    print("memory's text. The graph answers by walking relationships: dates,")
    print("people, places, gifts and plans that no single memory spells out.")


# --------------------------------------------------------------------------
# Demo 4: forgetting curve — decay, sinking, reinforcement
# --------------------------------------------------------------------------

SIM_START = date(2026, 5, 25)   # the Monday after the dataset's last memory
SIM_WEEKS = 12

# Scripted "life happens" moments: in week N, an everyday event makes Mira
# recall one specific memory. Each target is chosen because the simulation
# lets it sink first — so the demo shows recall *resurrecting* a memory,
# not just topping up a healthy one.
REUNIONS = {
    3: ("m17", "June comes by, and the talk turns to their long walks "
               "around the lake when work went badly."),
    6: ("m29", "At the chess club's summer social, Priya quizzes Theo on "
               "the openings he studies on Sunday mornings."),
    9: ("m48", "Marco visits, and Mira makes the mint tea — with honey, "
               "never with sugar. (Yes: Demo 1's memory.)"),
}


def demo_forgetting() -> None:
    section("DEMO 4 — FORGETTING CURVE: memories fade, sink, and can be recalled back")
    print("Model (decay.py): initial strength = confidence x salience of the")
    print("source event x (1 + 0.6 x the event's surprise); exponential decay,")
    print(f"half-life {decay.HALF_LIFE_DAYS:.0f} days x the")
    print("memory's emotion_weight (emotion stretches or compresses the clock);")
    print(f"a recall reinforces s <- s + (1 - s) x {decay.REINFORCE_GAIN}; "
          f"below {decay.SUNK_THRESHOLD} a memory is SUNK.")
    print("Sunk is not deleted: the node and its provenance chain stay in the")
    print("graph — default recall just stops surfacing it. This demo runs in")
    print("its own library (./store_decay) so demos 1-3 keep a pristine graph.\n")

    if DECAY_STORE.exists():
        shutil.rmtree(DECAY_STORE)
    build(load_dataset(DATA), str(DECAY_STORE))
    lib = Library(DECAY_STORE)
    c = decay.counts(lib)
    print(f"Fresh library built: {c['total']} memories, all active "
          f"(strengths as of the day each was recorded).")

    c = decay.advance_time(lib, SIM_START.isoformat())
    print(f"\nTime catches up to {SIM_START} (the week after the last memory):")
    print(f"  active {c['active']} / sunk {c['sunk']} / buried {c['buried']} — winter's")
    print("  untouched memories have faded below recall, and the strongly-")
    print("  encoded painful ones were pushed down whole: buried, not faded.")

    rescued = {}
    for week in range(1, SIM_WEEKS + 1):
        day = SIM_START + timedelta(days=7 * week)
        before = decay.sunk_ids(lib)
        c = decay.advance_time(lib, day.isoformat())
        newly = sorted(decay.sunk_ids(lib) - before)
        line = f"  week {week:2d} ({day}): active {c['active']:2d} / sunk {c['sunk']:2d} / buried {c['buried']}"
        if newly:
            line += f"   newly sunk: {', '.join(newly)}"
        print(line)
        if week in REUNIONS:
            mem_id, story = REUNIONS[week]
            text = _one(lib,
                        "MATCH (m:Memory {id: $id}) RETURN m.text AS text",
                        {"id": mem_id})["text"]
            r = decay.reinforce(lib, mem_id, day.isoformat())
            rescued[mem_id] = r
            state = "SUNK" if r["was_sunk"] else "active"
            print(f"    ^ REUNION: {story}")
            print(f"      recalls {mem_id} \"{text}\"")
            print(f"      strength {r['pre']:.3f} ({state}) -> {r['post']:.3f} "
                  f"after recall — {'resurrected' if r['was_sunk'] else 'reinforced'}; "
                  f"active is now {decay.counts(lib)['active']}")

    c = decay.counts(lib)
    print(f"\nAfter {SIM_WEEKS} weeks: active {c['active']} / sunk {c['sunk']} / buried "
          f"{c['buried']} of {c['total']} — and the total is still {c['total']}. "
          "Nothing was deleted; things were forgotten, and a few were buried.")

    print("\nCase A — a memory that SHOULD have been remembered, and sank:")
    m10 = _one(lib,
               "MATCH (m:Memory {id: 'm10'}) RETURN m.text AS text, "
               "m.strength AS s, m.sunk AS sunk")
    print(f"  m10 \"{m10['text']}\"")
    print(f"  strength now {m10['s']:.3f}, sunk = {m10['sunk']}. Its own deadline")
    print("  ('before summer') passed while it lay below the recall threshold.")
    hits = decay.default_recall(lib, "film")
    print(f"  default recall for 'film' (privacy + sunk filters): {len(hits)} hits "
          "— the plan is unreachable by ordinary questions.")
    scene = lib.db_for_memory("m10").execute(
        "MATCH (m:Memory {id: 'm10'})-[:DERIVED_FROM]->(e:Event)"
        "-[:EXPERIENCED_AT]->(pl:Place), (e)-[:WITH]->(p:Person) "
        "RETURN e.title AS title, e.date AS date, pl.name AS place, p.name AS who"
    ).data()[0]
    print(f"  ...but the audit walk still finds it: m10 -DERIVED_FROM-> \"{scene['title']}\" "
          f"({scene['date']}, {scene['place']}, with {scene['who']}).")
    print("  SUNK, NOT DELETED: the evidence chain survives forgetting intact.")

    print("\nCase B — a memory that survived because life mentioned it again:")
    r48 = rescued["m48"]
    m48 = _one(lib,
               "MATCH (m:Memory {id: 'm48'}) RETURN m.strength AS s, "
               "m.sunk AS sunk, m.last_accessed AS last_accessed")
    hits = decay.default_recall(lib, "honey")
    w48 = _one(lib,
               "MATCH (m:Memory {id: 'm48'}) RETURN m.emotion_weight AS w, "
               "m.surprise AS sp")
    counterfactual = r48["pre"] * decay.decay_factor(
        7 * (SIM_WEEKS - 9), w48["w"], w48["sp"])
    print(f"  m48 (Demo 1's honey preference) had sunk to {r48['pre']:.3f} by week 9;")
    print(f"  one reunion recall lifted it to {r48['post']:.3f}, and it ends the summer at "
          f"{m48['s']:.3f} (sunk = {m48['sunk']}, last recalled {m48['last_accessed']}).")
    print(f"  default recall for 'honey': {[h['id'] for h in hits]} — still answerable,")
    print(f"  purely because it was lived again. Unrecalled, it would be at "
          f"{counterfactual:.3f}.")

    print("\nStacked filters — forgetting never unlocks anything:")
    locked = lib.execute_all(
        "MATCH (m:Memory) WHERE m.privacy = 'SECRET_LOCKED' "
        "RETURN m.id AS id, m.strength AS s, m.sunk AS sunk, "
        "m.buried AS buried ORDER BY m.id"
    )
    # execute_all concatenates per-chapter rows; sort the merged list.
    locked.sort(key=lambda r: r["id"])
    for row in locked:
        print(f"  {row['id']}: SECRET_LOCKED, strength {row['s']:.3f}, "
              f"sunk = {row['sunk']}, buried = {row['buried']}")
    print("  The three secrets are locked — and beyond that, sunk or buried.")
    print("  Default recall excludes them on the privacy rule alone; Demo 2's")
    print("  unlock path is unchanged. Forgetting and secrecy are separate")
    print("  axes, and neither weakens the other. (Demo 5 opens the buried box.)")
    lib.close()


# --------------------------------------------------------------------------
# Demo 5: the fuzzy band + emotion pathways + burial + sensory cues
# --------------------------------------------------------------------------

def _recall_one(lib, keyword: str, mem_id: str, **kwargs):
    """The unified banded recall, narrowed to one memory (or None)."""
    for row in decay.recall(lib, keyword, **kwargs):
        if row["id"] == mem_id:
            return row
    return None


def _print_form(row, indent: str = "      ") -> None:
    if row is None:
        print(f"{indent}(recall returns nothing — the memory has sunk)")
        return
    if row.get("stage"):
        label = row["stage"]
    else:
        label = row["band"] + (f"-{row['level']}" if row.get("level") else "")
    if row["form"] == "affect":
        # The AFFECT rung has no content at all — recall returns
        # None there, and the demo says so instead of dressing a
        # composed sentence up as the agent's answer (B4).
        print(f"{indent}[{label} · strength {row['strength']:.3f}] "
              f"(no content — only the feeling is back: "
              f"{row['emotion']}, {row['valence']})")
        return
    print(f"{indent}[{label} · strength {row['strength']:.3f}] \"{row['content']}\"")
    if row["form"] == "fragment":
        print(f"{indent}  (one isolated shard — not the story)")
    if row["form"] == "full":
        d = row["details"]
        print(f"{indent}  details: recorded {d['recorded_at']}, "
              f"source event {d['source_event']}, confidence {d['confidence']}")


def _band_line(bc) -> str:
    return (f"CLEAR {bc['clear']:2d} / HAZY {bc['hazy']:2d} / "
            f"SUNK {bc['sunk']:2d} / BURIED {bc['buried']}")


def _strength_of(lib, mem_id: str) -> float:
    return _one(lib,
                "MATCH (m:Memory {id: $id}) RETURN m.strength AS s",
                {"id": mem_id})["s"]


def _detail_spec(lib, mem_id: str) -> list:
    """A memory's stored detail inventory, in shedding order."""
    spec = _one(lib,
                "MATCH (m:Memory {id: $id}) RETURN m.detail_spec AS ds",
                {"id": mem_id})["ds"]
    return json.loads(spec)


def _shed_so_far(lib, mem_id: str) -> tuple[list, list]:
    """(held, shed) detail dicts at the memory's current band/level.

    Mirrors the build's keep counts (HAZE_KEEP): CLEAR holds every
    detail, HAZY-k holds the k-deep heaviest, SUNK holds none in
    recall — though every detail, like the memory itself, stays in
    the graph. This reads the stored inventory; it invents nothing.
    """
    from build_graph import HAZE_KEEP
    spec = _detail_spec(lib, mem_id)
    s = _strength_of(lib, mem_id)
    band = decay.band_of(s)
    if band == decay.BAND_CLEAR:
        return spec, []
    if band == decay.BAND_SUNK:
        return [], spec
    keep = HAZE_KEEP[decay.haze_level(s)]
    return spec[-keep:], spec[:-keep] if keep else spec


def _print_shed(lib, mem_id: str, indent: str = "      ") -> None:
    held, shed = _shed_so_far(lib, mem_id)
    if shed:
        gone = ", ".join(f"{d['slot']}:{d['label']} ({d['weight']:.2f})"
                         for d in shed)
        print(f"{indent}shed so far: {gone}")
    kept = ", ".join(f"{d['slot']}:{d['label']} ({d['weight']:.2f})"
                     for d in held)
    print(f"{indent}still held: {kept if kept else '(nothing — only the gist)'}")


# The dual longitudinal case for the fuzzy band: two memories fading
# through the same summer, shedding in *different* orders — because
# their details carry different weights.
#   m12 — the night June gave Theo the red scarf. Its heaviest detail
#         is a person (who:June, 0.98): the date goes first, then the
#         market, then the scarf — the name is the last thing held.
#   m42 — Marco's promotion dinner at Café Lumen. Its heaviest detail
#         is the place (where:Café Lumen, 0.83): the date goes first,
#         then the umbrella, then even Marco's name — the café is
#         the last thing held.
DUAL_CASE = (
    ("m12", "gift", "June's name is the anchor — it is held to the end"),
    ("m42", "dinner", "the café is the anchor — the name goes before it"),
)


def demo_haze() -> None:
    section("DEMO 5 — THE FUZZY BAND: memory fades through a haze, not a switch")
    print("Human memory does not flip from 'known' to 'gone' at one threshold.")
    print(f"So the unified recall (decay.recall) answers in three bands:")
    print(f"  CLEAR  strength >= {decay.CLEAR_THRESHOLD}: full text, with details.")
    print(f"  HAZY   {decay.SUNK_THRESHOLD}–{decay.CLEAR_THRESHOLD}: layered — detail sheds gradually.")
    print(f"  SUNK   below {decay.SUNK_THRESHOLD}: not returned at all.")
    print("Inside the haze there is no global template for *what* goes")
    print("first. Every detail of a memory — its when, its who, its where,")
    print("its whats — carries a detail weight: the strength of its binding")
    print("to emotion (build_graph.detail_inventories). Details fall in")
    print("order of weight, not of kind:")
    print("  HAZY-1: the lightest details are gone; the 3 heaviest are held.")
    print("  HAZY-2: only the 2 heaviest details remain.")
    print("  HAZY-3: only the single heaviest detail — the anchor — remains,")
    print("         next to the bare gist.")
    print("Each layer is a rendering stored when the memory was fresh;")
    print("recall selects one, it never rewrites. A faded memory is omitted")
    print("down, detail by detail in weight order — never expanded back")
    print("into invented detail.")
    print("Emotion runs the whole machine: it scales each memory's half-life")
    print("(emotion_weight), orders recall by priority = strength x weight")
    print("(with a bonus for joy), and routes four pathways — fast_lane,")
    print("grudge, slow_fog, buried. Same twelve weeks as Demo 4, in a fresh")
    print("library (./store_haze), watched through the banded recall.\n")

    if HAZE_STORE.exists():
        shutil.rmtree(HAZE_STORE)
    build(load_dataset(DATA), str(HAZE_STORE))
    lib = Library(HAZE_STORE)

    print("Priority on the fresh graph — recall for 'café':")
    for row in decay.recall(lib, "café")[:3]:
        print(f"  {row['id']} [{row['emotion']}/{row['pathway']}] "
              f"strength {row['strength']:.3f} x weight "
              f"{row['emotion_weight']} -> priority {row['priority']:.3f}")
    print("  The grudge sits on top — anger remembers, and priority is")
    print("  strength x weight. Below it the fast lane fills the next")
    print("  places: the cheerful café memories outrank the calmer work")
    print("  chats — at comparable strength, the joyful memory surfaces")
    print("  first. The heart decides what is thought of first.\n")

    decay.advance_time(lib, SIM_START.isoformat())
    bc = decay.band_counts(lib)
    print(f"Time catches up to {SIM_START}: {_band_line(bc)} (of {bc['total']})")
    print("  Note the BURIED column: the strongly-encoded vulnerable memories")
    print("  (pathway 'buried') never entered the haze at all — they were")
    print("  pushed down whole at the first advance. Act A opens that box.")

    print("\nPrivacy still stacks on top of the bands:")
    m30 = _one(lib, "MATCH (m:Memory {id: 'm30'}) RETURN m.strength AS s")
    print(f"  m30 sits at strength {m30['s']:.3f} — squarely in the CLEAR band —")
    print("  but it is SECRET_LOCKED, so band or no band, default recall for")
    hits = decay.recall(lib, "letter")
    print(f"  'letter' returns {[h['id'] for h in hits]}. And this demo does")
    print("  not unlock it here: the only path to its text is Demo 2's")
    print("  passphrase-gated one. Band membership is not access.")

    print("\nTwo longitudinal threads run through the summer:")
    print("  (a) a dual case — m12 (the scarf June gave him) vs m42 (Marco's")
    print("      promotion dinner): same haze, opposite shedding orders,")
    print("      because their heaviest details are different *kinds* of")
    print("      thing — a person in one, a place in the other;")
    print("  (b) a same-day pair — m50 (a tender all-night phone call) vs m51")
    print("      (a spare phone charger): same age, different emotional weight.")
    for mem_id, kw, note in DUAL_CASE:
        print(f"  {mem_id} as the summer begins — {note}:")
        _print_form(_recall_one(lib, kw, mem_id))
        _print_shed(lib, mem_id)
    print(f"  pair at the start: m50 strength {_strength_of(lib, 'm50'):.3f} "
          f"[{decay.band_of(_strength_of(lib, 'm50'))}] vs "
          f"m51 {_strength_of(lib, 'm51'):.3f} "
          f"[{decay.band_of(_strength_of(lib, 'm51'))}]")

    end_day = SIM_START + timedelta(days=7 * SIM_WEEKS)
    for week in range(1, SIM_WEEKS + 1):
        day = SIM_START + timedelta(days=7 * week)
        decay.advance_time(lib, day.isoformat())
        bc = decay.band_counts(lib)
        print(f"  week {week:2d} ({day}): {_band_line(bc)}")
        if week in (4, 8, 12):
            s50, s51 = _strength_of(lib, "m50"), _strength_of(lib, "m51")
            print(f"    pair: m50 (tender, weight 1.5) {s50:.3f} "
                  f"[{decay.band_of(s50)}]  vs  m51 (calm, weight 0.7) "
                  f"{s51:.3f} [{decay.band_of(s51)}] — same age, "
                  "different clocks")
        if week in (4, 8, 12):
            for mem_id, kw, _note in DUAL_CASE:
                print(f"    dual case, week {week} — {mem_id}:")
                _print_form(_recall_one(lib, kw, mem_id))
                _print_shed(lib, mem_id)
        if week == 2:
            print("    ^ RESCUE: Ana asks after the plant she gave him, and the")
            print("      morning ritual around it comes back mid-haze. By now")
            print("      m35 has shed the fern itself and the date — but Ana")
            print("      and home, its two heaviest details, are still held:")
            _print_form(_recall_one(lib, "fern", "m35"))
            _print_shed(lib, "m35")
            r = decay.reinforce(lib, "m35", day.isoformat())
            print(f"      recall lifts m35: strength {r['pre']:.3f} -> {r['post']:.3f}")
            print("      m35 after the reunion (CLEAR again, every detail back):")
            _print_form(_recall_one(lib, "fern", "m35"))
        if week == 5:
            print("    ^ RUMINATION: at work, someone mentions Marco's promotion")
            print("      — and the old resentment is recalled, on the grudge")
            print(f"      pathway, where a recall strengthens MORE (gain "
                  f"{decay.RUMINATION_GAIN} vs {decay.REINFORCE_GAIN}):")
            r = decay.reinforce(lib, "m49", day.isoformat())
            print(f"      m49 (angry, weight 1.6): {r['pre']:.3f} -> "
                  f"{r['post']:.3f} — being touched did not soothe it;")
            print("      it rehearsed it. Anger remembers.")
        if week in REUNIONS:
            mem_id, story = REUNIONS[week]
            r = decay.reinforce(lib, mem_id, day.isoformat())
            print(f"    ^ REUNION (as in Demo 4): {mem_id} recalled "
                  f"{r['pre']:.3f} -> {r['post']:.3f}")

    print("\n  And m36, the coast-trip booking, by summer's end (SUNK):")
    _print_form(_recall_one(lib, "train", "m36"))
    row = _one(lib,
               "MATCH (m:Memory {id: 'm36'}) RETURN m.strength AS s, "
               "m.sunk AS sunk")
    scene = lib.db_for_memory("m36").execute(
        "MATCH (m:Memory {id: 'm36'})-[:DERIVED_FROM]->(e:Event)"
        "-[:EXPERIENCED_AT]->(pl:Place) "
        "RETURN e.title AS title, e.date AS date, pl.name AS place"
    ).data()[0]
    print(f"      strength {row['s']:.3f}, sunk = {row['sunk']} — it shed its")
    print("      date, then the ticket; June and home were still held when")
    print(f"      it went under. And the audit walk still reaches its source:")
    print(f"      \"{scene['title']}\" ({scene['date']}, {scene['place']}).")
    s49 = _strength_of(lib, "m49")
    print(f"  The grudge ends the summer at {s49:.3f} "
          f"[{decay.band_of(s49)}] — older than almost everything still")
    print("  standing, kept there by one act of rumination.")
    print("  Fates from one summer: the tender memory faded slowly, the")
    print("  trivial one sank in weeks, the angry one refused to fade —")
    print("  and two memories in the same haze lost different things,")
    print("  because what a memory cannot bear to lose is its own.")

    # ------------------------------------------------------------------
    print("\nACT A — THE BURIED BOX: what suppression holds down")
    m43 = _one(lib,
               "MATCH (m:Memory {id: 'm43'}) RETURN m.buried AS buried, "
               "m.strength AS s, m.encoded_strength AS s0")
    print(f"  m43 (vulnerable, encoded at {m43['s0']:.3f}) has been buried "
          f"since spring:")
    print(f"  buried = {m43['buried']}, strength frozen at {m43['s']:.3f} — "
          "from the moment")
    print("  it was pushed down, it stopped decaying altogether. No recall")
    print("  reaches it, however permissive:")
    print(f"    recall 'honest' -> "
          f"{[h['id'] for h in decay.recall(lib, 'honest')]}")
    print(f"    recall 'honest', include_sunk -> "
          f"{[h['id'] for h in decay.recall(lib, 'honest', include_sunk=True)]}")
    scene = lib.db_for_memory("m43").execute(
        "MATCH (m:Memory {id: 'm43'})-[:DERIVED_FROM*]->(e:Event) "
        "RETURN e.title AS title, e.date AS date LIMIT 1"
    ).data()[0]
    print(f"  ...but the audit walk still finds it: \"{scene['title']}\" "
          f"({scene['date']}).")
    print("  Buried is not deleted: the evidence chain is fully intact.")
    cue_day = end_day + timedelta(days=1)
    print(f"\n  {cue_day}: June comes by for dinner — a life event sharing a")
    print("  person with the buried memories' source evening (cue: person).")
    print("  The index says June appears in all three chapters; each is")
    print("  opened in turn, and the buried come back from chapter p2:")
    resurfaced = decay.cue_resurface(lib, cue_day.isoformat(), person="June")
    for r in resurfaced:
        print(f"    resurfaced {r['id']} via {r['via']} from {r['period']} "
              f"(emotion: {r['emotion']}) — enters at AFFECT")
    print("  (m32 did not come up at all. It is SECRET_LOCKED, and the")
    print("  cue path checks privacy like every other path: a locked")
    print("  memory is not raised, not moved, not even named by its")
    print("  rescue. Secrecy outranks resurfacing.)")
    print("  What recall can now say about m43 is only a feeling:")
    _print_form(_recall_one(lib, "honest", "m43"))
    for step_day, note in ((cue_day + timedelta(days=2), "a shard"),
                           (cue_day + timedelta(days=4), "the outline")):
        decay.reinforce(lib, "m43", step_day.isoformat())
        print(f"  {step_day}: mentioned again — {note} surfaces:")
        _print_form(_recall_one(lib, "honest", "m43"))

    # ------------------------------------------------------------------
    print("\nACT B — THE PROUST ACT: a taste does what people could not")
    enc52 = _one(lib,
                 "MATCH (m:Memory {id: 'm52'}) RETURN m.encoded_strength AS s"
                 )["s"]
    print("  m52 — the stumbled toast (shame, encoded "
          f"{enc52:.3f}, then buried) — never shared")
    print("  a summer cue with June, so Act A's key did not fit it. It lies")
    print("  in the winter chapter (p1); the ginger-tea cue's FIRES_IN edge")
    print("  in the index points straight at that chapter. Recall:")
    print(f"    recall 'toast' -> {[h['id'] for h in decay.recall(lib, 'toast')]}")
    print(f"    recall 'toast', include_sunk -> "
          f"{[h['id'] for h in decay.recall(lib, 'toast', include_sunk=True)]}")
    scene = lib.db_for_memory("m52").execute(
        "MATCH (m:Memory {id: 'm52'})-[:DERIVED_FROM*]->(e:Event) "
        "RETURN e.title AS title, e.date AS date LIMIT 1"
    ).data()[0]
    print(f"  The audit walk still finds it: \"{scene['title']}\" "
          f"({scene['date']}).")
    sense_day = end_day + timedelta(days=8)
    print(f"\n  {sense_day}: Ana sends over a thermos of her ginger tea.")
    print("  One taste — and the cue bound at encoding fires (sense cue:")
    print("  taste:ginger tea). Sensory cues do not decay: the tea is still")
    print("  the tea. What comes back comes back in stages:")
    res = decay.sense_resurface(lib, "taste:ginger tea", sense_day.isoformat())
    for r in res:
        print(f"    resurfaced {r['id']} from {r['period']} "
              f"(was_buried = {r['was_buried']}) — stage {r['stage']}")
    row = _recall_one(lib, "toast", "m52")
    print(f"  stage 1, AFFECT — emotion: {row['emotion']}, "
          f"valence: {row['valence']}, and nothing else:")
    _print_form(row)
    print("  It bypassed the haze on the way down; it returns through the")
    print("  stages on the way up. The way back is not the way down reversed:")
    ladder_day = sense_day
    for note in ("stage 2, FRAGMENT — one shard, unrelated to any outline",
                 "stage 3, the GIST assembles (HAZY)",
                 "stage 4, mentioned once more — CLEAR, the whole story"):
        ladder_day += timedelta(days=2)
        decay.reinforce(lib, "m52", ladder_day.isoformat())
        print(f"  {note}:")
        _print_form(_recall_one(lib, "toast", "m52"))
    print("\n  The same evening, a neighbour's hobby kit smells of darkroom")
    print("  chemicals — and Demo 4's lost film plan (m10, merely sunk) is")
    print("  hooked back by its own sensory anchor:")
    decay.sense_resurface(lib, "scent:darkroom chemicals",
                          sense_day.isoformat())
    _print_form(_recall_one(lib, "film", "m10"))
    decay.reinforce(lib, "m10", (sense_day + timedelta(days=2)).isoformat())
    _print_form(_recall_one(lib, "film", "m10"))
    bc = decay.band_counts(lib)
    print(f"\nAfter everything: {_band_line(bc)} — of {bc['total']}. Fading is")
    print("a change of form, not a loss of evidence. What the agent can no")
    print("longer say in detail, it says in outline; what it cannot even")
    print("outline, it stays silent about — and invents nothing.")
    lib.close()

    demo_surprise_act()


# --------------------------------------------------------------------------
# Demo 5, Act C: surprise — the orthogonal axis, and the chapter break
# --------------------------------------------------------------------------

# Three memories of one calm Friday (2026-04-24), all recorded the next
# day, all on the standard pathway with the same emotion_weight. They
# differ in exactly one property: surprise.
SURPRISE_TRIO = (
    ("m53", "the weekly grocery run"),
    ("m54", "the bakery stall that lasted one day"),
    ("m55", "a replacement sponge"),
)
SURPRISE_START = date(2026, 5, 4)   # the Monday ten days after that Friday
SURPRISE_WEEKS = 8


def demo_surprise_act() -> None:
    print("\nACT C — SURPRISE: the axis emotion does not explain")
    print("Emotion is not the only hand on the clock. Every event also")
    print("carries `surprise` — prediction error, how far it sat from")
    print("what was expected. It is orthogonal to feeling: a calm event")
    print("can be shocking, a charged one entirely foreseen. Surprise")
    print(f"lifts the encoding (x(1 + {decay.SURPRISE_ENCODE_BOOST} x surprise)) and")
    print(f"stretches the half-life (x(1 + {decay.SURPRISE_HALFLIFE_BOOST} x surprise)) — being")
    print("unexpected mostly buys a memory time.")
    print("\nThree memories of one calm Friday, recorded the next day, all")
    print("on the standard pathway (emotion_weight 0.7). One variable")
    print("differs:")

    if SURPRISE_STORE.exists():
        shutil.rmtree(SURPRISE_STORE)
    build(load_dataset(DATA), str(SURPRISE_STORE))
    lib = Library(SURPRISE_STORE)

    for mem_id, label in SURPRISE_TRIO:
        row = _one(lib,
                   "MATCH (m:Memory {id: $id}) RETURN m.strength AS s, "
                   "m.surprise AS sp, m.emotion AS em, m.emotion_weight AS w",
                   {"id": mem_id})
        hl = decay.effective_half_life(row["w"], row["sp"])
        print(f"  {mem_id} ({label}): emotion {row['em']}, "
              f"surprise {row['sp']:.2f} -> encoded {row['s']:.3f}, "
              f"effective half-life {hl:.1f} days")

    print(f"\nTime runs from {SURPRISE_START}, eight weeks:")
    decay.advance_time(lib, SURPRISE_START.isoformat())
    for week in range(1, SURPRISE_WEEKS + 1):
        day = SURPRISE_START + timedelta(days=7 * week)
        before = decay.sunk_ids(lib)
        decay.advance_time(lib, day.isoformat())
        newly = sorted(decay.sunk_ids(lib) - before)
        cells = []
        for mem_id, _label in SURPRISE_TRIO:
            s = _strength_of(lib, mem_id)
            cells.append(f"{mem_id} {s:.3f} [{decay.band_of(s)}]")
        line = f"  week {week} ({day}): " + " · ".join(cells)
        if newly:
            line += f"   newly sunk: {', '.join(newly)}"
        print(line)
    print("  The sponge went in week 4, the grocery run in week 5. The")
    print("  one-day bakery — the same calm mood, the same age, nothing")
    print("  but surprise 0.95 — never left the CLEAR band. Nobody felt")
    print("  strongly about it. Nobody saw it coming, either.")

    print("\nAnd surprise cuts the chapters. Event segmentation theory:")
    print("the brain chapters life at surprises. In the graph, a period")
    print("whose FIRST event clears the chapter-break threshold")
    print(f"(surprise >= {decay.CHAPTER_BREAK_THRESHOLD}) is marked with a STARTS edge and an")
    print("`opened_by` property:")
    for br in decay.chapter_breaks(lib):
        print(f"  {br['period']} was opened by {br['event_id']} — "
              f"\"{br['title']}\" ({br['date']}, "
              f"surprise {br['surprise']:.2f})")
    big = max(lib.execute_all(
        "MATCH (e:Event) RETURN e.id AS id, e.title AS title, "
        "e.surprise AS sp"), key=lambda r: r["sp"])
    starts = sum(r["c"] for r in lib.execute_all(
        "MATCH (e:Event {id: $id})-[:STARTS]->(p:Period) "
        "RETURN count(p) AS c", {"id": big["id"]}))
    print(f"  The single most surprising event in the graph is "
          f"{big['id']} — \"{big['title']}\" (surprise {big['sp']:.2f}) —")
    print(f"  and it opens nothing (STARTS edges from it: {starts}). It")
    print("  landed mid-chapter, so it is simply remembered vividly")
    print("  inside one. A shock opens a chapter only where a chapter")
    print("  can begin.")
    print("\n  Emotion decides the weight of a memory; surprise decides")
    print("  how long it is carried — and where one chapter ends and")
    print("  the next begins.")
    lib.close()


# --------------------------------------------------------------------------
# Demo 6: periods + spreading activation
# --------------------------------------------------------------------------

def _print_trace(trace) -> None:
    for i, s in enumerate(trace["steps"], 1):
        a = s["activation"]
        if s["kind"] == "memory":
            if s["level"] == "affect":
                print(f"  step {i:2d}: AFFECT   · {s['id']} — a feeling "
                      f"stirs ({s['emotion']}), no content yet   [act {a:.2f}]")
            elif s["level"] == "fragment":
                print(f"  step {i:2d}: FRAGMENT · {s['id']} — "
                      f"\"{s['shard']}\"   [act {a:.2f}]")
            else:
                print(f"  step {i:2d}: GIST     · {s['id']} — "
                      f"\"{s['gist']}\"   [act {a:.2f}]")
        elif s["kind"] == "period":
            print(f"  step {i:2d}: PERIOD OPENS · {s['name']} — "
                  f"\"{s['atmosphere']}\"")
            print(f"           (dominant emotion: {s['dominant_emotion']})"
                  f"   [act {a:.2f}]")
        else:
            print(f"  step {i:2d}: PERIOD GIST ASSEMBLES · {s['name']}   "
                  f"[act {a:.2f}]")


def demo_periods() -> None:
    section("DEMO 6 — PERIODS & SPREADING ACTIVATION: sometimes what returns "
            "is an era")
    print("Autobiographical memory is layered (Conway): lifetime periods")
    print("above general events above specific details. So the library's")
    print("first-level structure is the Period layer — each period is its")
    print("own Relate store under ./store_period/periods/, with the index")
    print("beside it — and sometimes what a cue brings back is not an")
    print("event but a whole season of life.")
    print("\nThere is no fixed cascade for how things surface. A cue lands on")
    print("whichever node it is bound to, and activation spreads along the")
    print("edges (PART_OF, same-event, shared-person), decaying by edge")
    print("type, accumulating where paths meet — inside the chapter the")
    print("cue landed in. Across a chapter boundary there is no cascade at")
    print(f"all: a shared-person link transmits one damped echo (x "
          f"{decay.BRIDGE_FACTOR})")
    print("over the index bridge, capped below the surfacing threshold.")
    print("A node surfaces when its activation crosses a threshold — so")
    print("the ORDER is emergent, though the mechanism is pure arithmetic:")
    print("same graph, same cue, same trace, every run. Two acts, one")
    print("period, opposite orders.\n")

    if PERIOD_STORE.exists():
        shutil.rmtree(PERIOD_STORE)
    build(load_dataset(DATA), str(PERIOD_STORE))
    lib = Library(PERIOD_STORE)

    print("Period recall — 'what was the quiet winter like?':")
    rp = decay.recall_period(lib, "Quiet Winter")
    print(f"  {rp['name']} ({rp['start_date']} – {rp['end_date']})")
    print(f"  atmosphere: \"{rp['atmosphere']}\"")
    print(f"  dominant emotion: {rp['dominant_emotion']} · "
          f"{rp['memory_count']} reachable memories; the strongest:")
    for mem in rp["memories"][:3]:
        print(f"    - [{mem['emotion']}] \"{mem['gist']}\"")

    decay.advance_time(lib, "2026-08-17")
    bc = decay.band_counts(lib)
    print(f"\nTime runs to 2026-08-17: {_band_line(bc)} — the first summer is")
    print("over, and everything from it has sunk out of ordinary reach.\n")

    print("ACT 1 — a small concrete thing: the taste of morning coffee.")
    print("The cue is bound to one memory (m35, the fern ritual). Watch the")
    print("order the numbers produce — nobody scripted it:")
    trace1 = decay.spreading_activation(lib, "taste:morning coffee")
    _print_trace(trace1)
    again = decay.spreading_activation(lib, "taste:morning coffee")
    print(f"  (re-running the same cue reproduces the identical trace: "
          f"{again['steps'] == trace1['steps']})")
    if trace1["echoes"]:
        top = trace1["echoes"][0]
        print(f"  Across the bridge, the strongest echo reached "
              f"{top['id']} in {top['period']} at "
              f"{top['activation']:.3f} — primed by {top['via_person']}, "
              f"but below the {decay.ACT_AFFECT} surfacing threshold: "
              "an echo is not a return.")

    print("\nACT 2 — the era's own song, on a café radio.")
    print("This cue is bound to the period itself. Same summer, same graph:")
    trace2 = decay.spreading_activation(
        lib, "song:the summer song from the café radio")
    _print_trace(trace2)

    print("\nAct 1 began at a shard and opened a season; Act 2 began with")
    print("the season and rained shards. A shard opens a season; a season")
    print("rains shards — and neither order was written down anywhere in")
    print("this code. The path is emergent; the mechanism is deterministic.")
    lib.close()


# --------------------------------------------------------------------------
# Demo 7: routine merging, cue dilution, the rumination ceiling
# --------------------------------------------------------------------------

def demo_habits_and_ceilings() -> None:
    section("DEMO 7 — HABITS & CEILINGS: the routine, the blunted key, "
            "the tired grudge")

    # -- Act 1: routine merging, on the pristine library ---------------
    print("ACT 1 — six afternoons that were all the same afternoon.")
    print("That spring, Theo kept drifting to Harbor Bookstore. Ask the")
    print("un-merged recall about the bookstore and every day answers")
    print("for itself:")
    lib = Library(STORE)
    before = decay.recall(lib, "bookstore", routines=False)
    print(f"  un-merged recall: {len(before)} answers")
    for row in before:
        print(f"    {row['id']:<5} [{row['form']}] {row['content'][:64]}")
    print("\n  But nobody remembers six identical afternoons as six")
    print("  memories. At build time they were merged into a Routine —")
    print("  originals untouched, provenance chains intact — and daily")
    print("  recall now answers with the habit:")
    after = decay.recall(lib, "bookstore")
    routine_id = None
    for row in after:
        if row["form"] == "routine":
            # The drill-down below opens the very group this answer
            # collapsed into — its id is read from the answer, never
            # hardcoded, so a regrouped build cannot strand it.
            routine_id = row["id"]
            info = row["routine"]
            print(f"    ROUTINE [{row['band']}] {row['content']}")
            print(f"      ({info['showing']} of {info['members']} days "
                  f"standing behind this one answer, "
                  f"{info['span'][0]} → {info['span'][1]})")
        else:
            print(f"    {row['id']:<5} [{row['form']}] "
                  f"{row['content'][:64]}")
    print("  The preference (m21) and the fact about Ana (m41) did NOT")
    print("  merge — only episodes are days, and only days become habit.")
    print("\n  Name a day, though, and the habit opens back up:")
    for day in decay.routine_drilldown(lib, routine_id):
        print(f"    {day['date']}  {day['id']:<5} [{day['band']}] "
              f"{day['text'][:64]}")
    print("  Merged for remembering, never for auditing: every afternoon")
    print("  is still in the graph, still chained to its own event.")
    lib.close()

    # -- Act 2: cue dilution, in a throwaway library -------------------
    print("\nACT 2 — the smell that meant one thing, then four.")
    data = load_dataset(DATA)
    build(data, ROOT / "store_dilution")
    lib = Library(ROOT / "store_dilution")
    cue = "scent:darkroom chemicals"
    decay.advance_time(lib, "2026-08-17")
    sunk = lib.db_for_memory("m10").execute(
        "MATCH (m:Memory {id: 'm10'}) RETURN m.strength AS s").data()[0]["s"]
    print(f"  By 2026-08-17 the film memory (m10) has sunk to {sunk:.3f}.")
    print(f"  The cue '{cue}' is bound to "
          f"{decay.cue_degree(lib, cue)} memory — energy "
          f"{decay.cue_energy(lib, cue):.2f}, undiluted. It fires:")
    hit = decay.sense_resurface(lib, cue, "2026-08-17")[0]
    print(f"    m10 raised at {hit['strength']:.2f} "
          f"[{hit['stage']}] — the feeling first, as always.")
    decay.reinforce(lib, "m10", "2026-08-19")
    decay.reinforce(lib, "m10", "2026-08-21")
    row = next(r for r in decay.recall(lib, "film") if r["id"] == "m10")
    print(f"  Two mentions climb the ladder, and the story assembles: "
          f"[{row['form']}] at {row['strength']:.2f}.")
    decay.advance_time(lib, "2027-01-15")
    sunk = lib.db_for_memory("m10").execute(
        "MATCH (m:Memory {id: 'm10'}) RETURN m.strength AS s").data()[0]["s"]
    print(f"\n  By 2027-01-15 it has sunk again ({sunk:.3f}). But life has")
    print("  been reusing the smell — it now also clings to the chess")
    print("  memories and the camera Ana lent him:")
    db = lib.partition("p1")
    for mid in ("m05", "m08", "m45"):
        db.execute(
            "MATCH (m:Memory {id: $id}), "
            "(c:Cue {label: 'scent:darkroom chemicals'}) "
            "MERGE (m)-[:TRIGGERED_BY]->(c)", {"id": mid})
    print(f"    degree {decay.cue_degree(lib, cue)} → energy "
          f"{decay.cue_energy(lib, cue):.2f}. The same cue fires again:")
    hits = decay.sense_resurface(lib, cue, "2027-01-15")
    hit = next(r for r in hits if r["id"] == "m10")
    print(f"    m10 raised at {hit['strength']:.2f} — a quarter of the "
          f"strength (and {len(hits) - 1} other sunk memories lifted "
          f"just as weakly; dilution is indiscriminate).")
    decay.reinforce(lib, "m10", "2027-01-17")
    row = next((r for r in decay.recall(lib, "film") if r["id"] == "m10"),
               None)
    print(f"  A mention brings the shard: [{row['form']}]. One more —")
    decay.reinforce(lib, "m10", "2027-01-19")
    row = next((r for r in decay.recall(lib, "film") if r["id"] == "m10"),
               None)
    print(f"  and the ladder clears onto a strength of 0.10, below the "
          f"{decay.SUNK_THRESHOLD} sinking line: recall returns "
          f"{'nothing' if row is None else row['form']}.")
    print("  The cue never faded — cues don't. It was *spent*: divided")
    print("  among everything it had been asked to mean.")
    lib.close()

    # -- Act 3: the rumination ceiling ---------------------------------
    print("\nACT 3 — the grudge, mentioned eight times.")
    print("Demo 5 showed anger rehearsing itself stronger. It does —")
    print(f"for a while. The dividend is finite: "
          f"{decay.RUMINATION_FULL_COUNT} full rehearsals, then it")
    print(f"halves each time, and no rehearsal can restore the memory")
    print(f"past {decay.RUMINATION_CAP} — below the 1.00 of its first")
    print("burn. Theo keeps bringing Marco up, roughly every few weeks:")
    build(data, ROOT / "store_rumination")
    lib = Library(ROOT / "store_rumination")
    days = ["2026-06-29", "2026-07-13", "2026-07-27", "2026-08-10",
            "2026-09-07", "2026-10-05", "2026-11-02", "2026-12-07"]
    for day in days:
        r = decay.reinforce(lib, "m49", day)
        mark = "  ← pinned at the ceiling" if r["capped"] else ""
        print(f"    {day}  mention #{r['ruminate_count']}: "
              f"{r['pre']:.3f} + gain {r['gain']:.3f} → {r['post']:.3f}"
              f"{mark}")
    print("  Four mentions hold it at the ceiling. Then the mentions")
    print("  keep coming — and stop mattering: the gain halves away,")
    print("  ordinary decay resumes, and the strength falls anyway.")
    print("  Anger burns hot, then burns out. The memory is not deleted")
    print("  and the wrong is not forgiven; it is *tired* — which is")
    print("  what time does to grudges that life stops feeding.")
    lib.close()


# --------------------------------------------------------------------------

def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--passphrase", default=None,
        help="Passphrase for the demo-2 unlock path. Opt-in: without "
             "it the unlock is not attempted at all — the demo never "
             "unlocks silently on a built-in default (B1).")
    parser.add_argument("--wrong-passphrase", action="store_true",
                        help="Demonstrate that a wrong passphrase is rejected.")
    return parser


def main() -> None:
    args = _parser().parse_args()

    if not (STORE / "index").exists():
        print("Relate library not found. Run `python build_graph.py` first.",
              file=sys.stderr)
        sys.exit(1)

    lib = Library(STORE)
    print("Auditable Graph Memory for a Companion Agent — demo")
    print("Agent: Mira (fictional) · Human: Theo (fictional) · Engine: Relate (Cypher 25)")

    demo_provenance(lib)
    passphrase = "wrong-guess" if args.wrong_passphrase else args.passphrase
    demo_privacy(lib, passphrase)
    demo_graph_vs_text(lib)
    lib.close()
    demo_forgetting()
    demo_haze()
    demo_periods()
    demo_habits_and_ceilings()
    print(f"\n{LINE}\nDemo complete.\n{LINE}")


if __name__ == "__main__":
    main()
