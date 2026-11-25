"""Smoke tests for the auditable graph memory.

Builds the library (index store + one Relate store per period) into a
temporary directory and checks node counts, the provenance chain, the
privacy hard filter, one multi-hop answer, the forgetting curve
(decay sinks memories without deleting them; sunk memories stay
traceable; recall resurrects them), the fuzzy band (detail sheds in
*weight* order — each memory loses its own lightest details first
and keeps its heaviest, its anchor, to the end), the emotion
pathways (scaled half-lives, priority ranking, the grudge that
ruminates, the buried memories only cues can raise — and then only
up a ladder: affect, fragment, gist), the period layer with
spreading activation (two cues, two opposite emergence orders, one
deterministic mechanism; across chapters only a damped echo),
surprise — the encoding factor orthogonal to emotion (a calm but
shocking memory outlives its unsurprising twins; a high-surprise
opening event is marked as its period's chapter break) — the
storage layout itself: every memory filed in exactly one chapter,
provenance chains complete inside their chapter, cross-chapter
derivations routed to the index, and cues that reach across
chapters through it — and the v1.6 trio: routine merging (repeated
days collapse into a Routine node, drill-down intact), cue dilution
(a cue's energy divided among its bindings; a shared cue only stirs
what a unique cue would raise), and the rumination ceiling (full
gains, then exhaustion, under a hard cap).

Run:  python -m unittest discover -s tests -v
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import decay  # noqa: E402
from build_graph import (  # noqa: E402
    build,
    detail_inventories,
    load_dataset,
    route_derivations,
)
from library import Library  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


class _TempLibrary(unittest.TestCase):
    """Base: a fresh library in a temporary directory per class/test."""

    data = None

    @classmethod
    def load_data(cls) -> dict:
        if cls.data is None:
            cls.data = load_dataset(ROOT / "data" / "memories.json")
        return cls.data

    def open_library(self) -> Library:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        base = str(Path(tmp.name) / "lib")
        self.counts = build(self.load_data(), base)
        lib = Library(base)
        self.addCleanup(lib.close)
        return lib

    def _strength(self, lib, mem_id: str) -> float:
        return lib.db_for_memory(mem_id).execute(
            "MATCH (m:Memory {id: $id}) RETURN m.strength AS s",
            {"id": mem_id},
        ).data()[0]["s"]

    def _one(self, lib, keyword: str, mem_id: str, **kwargs):
        for row in decay.recall(lib, keyword, **kwargs):
            if row["id"] == mem_id:
                return row
        return None


class GraphSmokeTest(_TempLibrary):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        cls.base = str(Path(cls._tmp.name) / "lib")
        cls.counts = build(cls.load_data(), cls.base)
        cls.lib = Library(cls.base)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.lib.close()
        cls._tmp.cleanup()

    def test_node_counts(self) -> None:
        self.assertEqual(self.counts["Person"], 6)
        self.assertEqual(self.counts["Place"], 8)
        self.assertEqual(self.counts["Item"], 10)
        # v1.6: +5 routine Saturday-bookstore events (e24–e28) and
        # their +5 episode memories (m57–m61) — the mergeable routine.
        self.assertEqual(self.counts["Event"], 28)
        self.assertEqual(self.counts["Memory"], 61)
        self.assertEqual(self.counts["Routine"], 1)
        self.assertEqual(self.counts["Period"], 3)
        self.assertGreaterEqual(self.counts["Cue"], 20)
        self.assertGreater(self.counts["edges"], 250)

    def test_every_memory_has_provenance_properties(self) -> None:
        rows = self.lib.execute_all(
            "MATCH (m:Memory) WHERE m.source_event IS NULL OR m.recorded_at IS NULL "
            "OR m.confidence IS NULL OR m.privacy IS NULL OR m.gist IS NULL "
            "OR m.vague IS NULL OR m.anon IS NULL OR m.shard IS NULL "
            "OR m.haze1 IS NULL OR m.haze2 IS NULL OR m.haze3 IS NULL "
            "OR m.detail_spec IS NULL "
            "OR m.emotion IS NULL OR m.valence IS NULL OR m.pathway IS NULL "
            "OR m.emotion_weight IS NULL OR m.encoded_strength IS NULL "
            "OR m.surprise IS NULL OR m.ruminate_count IS NULL "
            "RETURN m.id AS id"
        )
        self.assertEqual(rows, [])

    def test_provenance_chain_reaches_source_event(self) -> None:
        # m48 (honey, never sugar) -> m01 (mint tea preference) -> e01 (event)
        db = self.lib.db_for_memory("m48")
        rows = db.execute(
            "MATCH (m:Memory {id: 'm48'})-[:DERIVED_FROM*1..5]->(e:Event) "
            "RETURN e.id AS id"
        ).data()
        self.assertEqual([r["id"] for r in rows], ["e01"])

    def test_all_memories_trace_to_an_event(self) -> None:
        rows = self.lib.execute_all(
            "MATCH (m:Memory)-[:DERIVED_FROM*1..5]->(e:Event) "
            "RETURN DISTINCT m.id AS id"
        )
        total = len(self.lib.execute_all("MATCH (m:Memory) RETURN m.id AS id"))
        self.assertEqual(len(rows), total)

    def test_secret_locked_invisible_to_default_recall(self) -> None:
        rows = self.lib.execute_all(
            "MATCH (m:Memory) WHERE m.privacy <> 'SECRET_LOCKED' "
            "AND toLower(m.text) CONTAINS 'drawer' RETURN m.id AS id"
        )
        self.assertEqual(rows, [])

    def test_secret_exists_only_behind_unlock_path(self) -> None:
        rows = self.lib.execute_all(
            "MATCH (m:Memory) WHERE m.privacy = 'SECRET_LOCKED' RETURN m.id AS id"
        )
        self.assertEqual({r["id"] for r in rows}, {"m30", "m32", "m38"})

    def test_multihop_latest_down_event(self) -> None:
        evs = self.lib.execute_all(
            "MATCH (e:Event {mood: 'down'}) RETURN e.id AS id, e.date AS date")
        ev = max(evs, key=lambda r: r["date"])["id"]
        self.assertEqual(ev, "e05")
        db = self.lib.partition(self.lib.period_for_date("2026-02-27"))
        who = db.execute(
            "MATCH (e:Event {id: 'e05'})-[:WITH]->(p:Person) RETURN p.name AS name"
        ).data()
        self.assertEqual(who[0]["name"], "June")


class DecayTest(_TempLibrary):
    """Forgetting curve: decay sinks, never deletes; recall resurrects."""

    def test_decay_sinks_memories_but_deletes_nothing(self) -> None:
        lib = self.open_library()
        before = decay.counts(lib)
        self.assertEqual(before["sunk"], 0)
        after = decay.advance_time(lib, "2026-12-31")
        # Time passed: some memories fell below the recall threshold...
        self.assertGreater(after["sunk"], 0)
        self.assertLess(after["active"], before["active"])
        # ...but forgetting is not deletion: every node is still there.
        self.assertEqual(after["total"], before["total"])
        row = lib.db_for_memory("m01").execute(
            "MATCH (m:Memory {id: 'm01'}) RETURN m.sunk AS sunk"
        ).data()[0]
        self.assertTrue(row["sunk"])

    def test_sunk_memory_hidden_from_recall_but_still_traceable(self) -> None:
        lib = self.open_library()
        decay.advance_time(lib, "2026-12-31")
        row = lib.db_for_memory("m10").execute(
            "MATCH (m:Memory {id: 'm10'}) RETURN m.sunk AS sunk"
        ).data()[0]
        self.assertTrue(row["sunk"])
        # Default recall stacks privacy + sunk filters: m10 is unreachable.
        hits = decay.default_recall(lib, "film")
        self.assertNotIn("m10", [h["id"] for h in hits])
        # The provenance chain ignores recall filters: the audit walk
        # still reaches the witnessed event. Sunk, not deleted.
        rows = lib.db_for_memory("m10").execute(
            "MATCH (m:Memory {id: 'm10'})-[:DERIVED_FROM*1..5]->(e:Event) "
            "RETURN e.id AS id"
        ).data()
        self.assertEqual([r["id"] for r in rows], ["e03"])

    def test_reinforcement_resurrects_a_sunk_memory(self) -> None:
        lib = self.open_library()
        decay.advance_time(lib, "2026-12-31")
        row = lib.db_for_memory("m48").execute(
            "MATCH (m:Memory {id: 'm48'}) RETURN m.sunk AS sunk"
        ).data()[0]
        self.assertTrue(row["sunk"])
        result = decay.reinforce(lib, "m48", "2027-01-15")
        self.assertTrue(result["was_sunk"])
        self.assertGreater(result["post"], result["pre"])
        self.assertFalse(result["sunk"])


class HazeTest(_TempLibrary):
    """The fuzzy band: CLEAR recalls in full, HAZY in layers, SUNK not at all.

    What each layer *contains* is per-memory (detail weights, below);
    this class pins the band mechanics: forms change as strength
    crosses the floors, details are withheld in every hazy layer, and
    privacy stacks on top of the bands.
    """

    def setUp(self) -> None:
        self.lib = self.open_library()
        self.data = self.load_data()
        self.gist = {m["id"]: m["gist"] for m in self.data["memories"]}
        self.text = {m["id"]: m["text"] for m in self.data["memories"]}

    def test_hazy_layers_progress_with_strength(self) -> None:
        # m35 (the fern ritual) fades through all three haze layers.
        decay.advance_time(self.lib, "2026-05-25")
        row = self._one(self.lib, "fern", "m35")
        self.assertEqual((row["band"], row["level"], row["form"]),
                         (decay.BAND_HAZY, 1, "frayed"))
        self.assertIsNone(row["details"])
        decay.advance_time(self.lib, "2026-06-15")
        row = self._one(self.lib, "fern", "m35")
        self.assertEqual((row["band"], row["level"], row["form"]),
                         (decay.BAND_HAZY, 2, "thinned"))
        self.assertIsNone(row["details"])
        decay.advance_time(self.lib, "2026-07-06")
        row = self._one(self.lib, "fern", "m35")
        self.assertEqual((row["band"], row["level"], row["form"]),
                         (decay.BAND_HAZY, 3, "outline"))
        self.assertIn(self.gist["m35"], row["content"])
        self.assertIsNone(row["details"])

    def test_same_memory_changes_form_as_it_crosses_bands(self) -> None:
        # Fresh, m36 (the coast-trip booking) is CLEAR: full text + details.
        row = self._one(self.lib, "train", "m36")
        self.assertEqual(row["band"], decay.BAND_CLEAR)
        self.assertEqual(row["form"], "full")
        self.assertEqual(row["content"], self.text["m36"])
        self.assertIsNotNone(row["details"])
        # Down through the layers, the form changes at each floor.
        decay.advance_time(self.lib, "2026-06-15")
        row = self._one(self.lib, "train", "m36")
        self.assertEqual((row["level"], row["form"]), (1, "frayed"))
        decay.advance_time(self.lib, "2026-06-22")
        row = self._one(self.lib, "train", "m36")
        self.assertEqual((row["level"], row["form"]), (2, "thinned"))
        decay.advance_time(self.lib, "2026-07-13")
        row = self._one(self.lib, "train", "m36")
        self.assertEqual((row["level"], row["form"]), (3, "outline"))
        # A reunion recall pulls it back across the line into CLEAR.
        decay.reinforce(self.lib, "m36", "2026-07-13")
        row = self._one(self.lib, "train", "m36")
        self.assertEqual(row["band"], decay.BAND_CLEAR)
        self.assertEqual(row["form"], "full")
        # Left alone long enough it sinks: recall returns nothing, but
        # the provenance walk still reaches its source event.
        decay.advance_time(self.lib, "2026-12-31")
        self.assertIsNone(self._one(self.lib, "train", "m36"))
        rows = self.lib.db_for_memory("m36").execute(
            "MATCH (m:Memory {id: 'm36'})-[:DERIVED_FROM*1..5]->(e:Event) "
            "RETURN e.id AS id"
        ).data()
        self.assertEqual([r["id"] for r in rows], ["e14"])

    def test_secret_locked_hidden_in_every_band(self) -> None:
        # m30 starts at full strength — CLEAR band — yet default recall
        # cannot see it: privacy stacks on top of the bands.
        self.assertIsNone(self._one(self.lib, "letter", "m30"))
        row = self._one(self.lib, "letter", "m30", unlocked=True)
        self.assertEqual(row["band"], decay.BAND_CLEAR)
        self.assertEqual(row["form"], "full")
        # Even once sunk, the unlock path alone is not enough...
        decay.advance_time(self.lib, "2026-12-31")
        self.assertIsNone(self._one(self.lib, "letter", "m30", unlocked=True))
        # ...it also takes include_sunk, and then only the gist surfaces.
        row = self._one(self.lib, "letter", "m30",
                        unlocked=True, include_sunk=True)
        self.assertEqual(row["band"], decay.BAND_SUNK)
        self.assertEqual(row["form"], "gist")
        self.assertIsNone(row["details"])


class DetailWeightTest(_TempLibrary):
    """Detail weights: details fall in order of weight, not of kind."""

    def setUp(self) -> None:
        self.data = self.load_data()
        self.inventories = detail_inventories(self.data)

    def _spec(self, mem_id: str) -> list:
        return self.inventories[mem_id]

    def test_shedding_order_is_weight_ascending_for_every_memory(self) -> None:
        # The stored inventory IS the shedding sequence: ascending by
        # weight (ties broken by slot fragility, then label).
        from build_graph import SLOT_RANK
        for mem_id, spec in self.inventories.items():
            keys = [(d["weight"], SLOT_RANK[d["slot"]], d["label"])
                    for d in spec]
            self.assertEqual(keys, sorted(keys), mem_id)
            # Every memory carries at least its when and its where.
            slots = {d["slot"] for d in spec}
            self.assertIn("when", slots, mem_id)
            self.assertIn("where", slots, mem_id)

    def test_two_memories_shed_different_details_at_the_same_depth(self) -> None:
        # m12 (the scarf night): the heaviest detail is a PERSON.
        spec12 = self._spec("m12")
        self.assertEqual(spec12[-1]["slot"], "who")
        self.assertEqual(spec12[-1]["label"], "June")
        self.assertEqual([d["slot"] for d in spec12],
                         ["when", "where", "what", "who"])
        # m42 (the promotion dinner): the heaviest detail is a PLACE,
        # and the person is shed before it. Same machinery, opposite
        # order — because the weights, not the kinds, decide.
        spec42 = self._spec("m42")
        self.assertEqual(spec42[-1]["slot"], "where")
        self.assertEqual(spec42[-1]["label"], "Café Lumen")
        self.assertEqual([d["slot"] for d in spec42],
                         ["when", "what", "who", "where"])

    def test_anchor_detail_survives_into_the_deepest_haze(self) -> None:
        lib = self.open_library()
        # Drive both case memories down to HAZY-3 and read what recall
        # actually returns there: the gist plus the anchor — June for
        # m12, Café Lumen for m42 — and nothing lighter.
        decay.advance_time(lib, "2026-08-17")
        row12 = self._one(lib, "gift", "m12")
        self.assertEqual((row12["band"], row12["level"]),
                         (decay.BAND_HAZY, 3))
        self.assertIn("June", row12["content"])
        self.assertNotIn("Night Market", row12["content"])
        self.assertNotIn("red scarf", row12["content"])
        self.assertNotIn("2026-02-14", row12["content"])
        row42 = self._one(lib, "dinner", "m42")
        self.assertEqual((row42["band"], row42["level"]),
                         (decay.BAND_HAZY, 3))
        self.assertIn("Café Lumen", row42["content"])
        self.assertNotIn("Marco", row42["content"])
        self.assertNotIn("gray umbrella", row42["content"])
        self.assertNotIn("2026-04-03", row42["content"])
        # One layer up (HAZY-2), each holds exactly its two heaviest:
        # the renderings differ in kind, not just in wording.
        decay2 = self.open_library()
        decay.advance_time(decay2, "2026-07-13")
        row12 = self._one(decay2, "gift", "m12")
        self.assertEqual(row12["level"], 2)
        self.assertIn("June", row12["content"])
        self.assertIn("red scarf", row12["content"])
        self.assertNotIn("Night Market", row12["content"])
        row42 = self._one(decay2, "dinner", "m42")
        self.assertEqual(row42["level"], 2)
        self.assertIn("Café Lumen", row42["content"])
        self.assertIn("Marco", row42["content"])
        self.assertNotIn("gray umbrella", row42["content"])


class EmotionTest(_TempLibrary):
    """Emotion sets the priority: scaled half-lives, ranking, pathways."""

    def test_same_age_memories_diverge_by_emotion(self) -> None:
        lib = self.open_library()
        # m50 (tender phone call) and m51 (spare charger) were recorded
        # the same day. Ten weeks on, the tender one is still in the
        # haze; the trivial one is sunk. Same age, different clocks.
        decay.advance_time(lib, "2026-06-22")
        s50, s51 = self._strength(lib, "m50"), self._strength(lib, "m51")
        self.assertGreater(s50, s51)
        self.assertEqual(decay.band_of(s50), decay.BAND_HAZY)
        self.assertEqual(decay.band_of(s51), decay.BAND_SUNK)

    def test_recall_priority_prefers_positive(self) -> None:
        lib = self.open_library()
        # Priority is strength x emotion_weight (x1.15 on the fast
        # lane), sorted descending. 'café' also matches the grudge
        # (m49 — its held place detail is Café Lumen, and anger
        # remembers, so it tops the list). Among the pleasant hits,
        # the fast lane rises above every standard-pathway memory:
        # m26 and m42 share an event, an emotion and a date, and the
        # more confidently encoded m26 leads the lane — joy rises
        # fast, and confidence shows.
        rows = decay.recall(lib, "café")
        priorities = [r["priority"] for r in rows]
        self.assertEqual(priorities, sorted(priorities, reverse=True))
        self.assertEqual(rows[0]["id"], "m49")
        fast = [r for r in rows if r["pathway"] == "fast_lane"]
        self.assertEqual(fast[0]["id"], "m26")
        self.assertIn("m42", [r["id"] for r in fast])
        calm = [r for r in rows if r["pathway"] == "standard"]
        self.assertTrue(all(f["priority"] > r["priority"]
                            for f in fast for r in calm))

    def test_angry_memory_outlasts_neutral_peer(self) -> None:
        lib = self.open_library()
        # The grudge (m49, angry) is touched mid-summer: rumination gain
        # applies, larger than an ordinary recall's.
        decay.advance_time(lib, "2026-06-29")
        result = decay.reinforce(lib, "m49", "2026-06-29")
        self.assertEqual(result["pathway"], "grudge")
        self.assertEqual(result["gain"], decay.RUMINATION_GAIN)
        decay.advance_time(lib, "2026-08-17")
        # Still reachable and far stronger than a calm peer of similar
        # age (m41, recorded ten days later, long sunk).
        row = self._one(lib, "design", "m49")
        self.assertIsNotNone(row)
        self.assertNotEqual(row["band"], decay.BAND_SUNK)
        self.assertGreater(self._strength(lib, "m49"),
                           self._strength(lib, "m41"))
        self.assertEqual(decay.band_of(self._strength(lib, "m41")),
                         decay.BAND_SUNK)


class BuriedTest(_TempLibrary):
    """Buried pathway: pushed down whole; only cues bring it back."""

    def test_buried_hidden_everywhere_but_traceable(self) -> None:
        lib = self.open_library()
        decay.advance_time(lib, "2026-05-25")
        row = lib.db_for_memory("m43").execute(
            "MATCH (m:Memory {id: 'm43'}) RETURN m.buried AS buried"
        ).data()[0]
        self.assertTrue(row["buried"])
        # No recall path reaches it — not even include_sunk, and not
        # the low-level default_recall either.
        self.assertIsNone(self._one(lib, "honest", "m43"))
        self.assertIsNone(self._one(lib, "honest", "m43", include_sunk=True))
        hits = decay.default_recall(lib, "honest")
        self.assertNotIn("m43", [h["id"] for h in hits])
        # Buried is not deleted: the audit walk still reaches e12,
        # inside m43's own chapter.
        rows = lib.db_for_memory("m43").execute(
            "MATCH (m:Memory {id: 'm43'})-[:DERIVED_FROM*1..5]->(e:Event) "
            "RETURN e.id AS id"
        ).data()
        self.assertEqual([r["id"] for r in rows], ["e12"])

    def test_person_cue_resurfaces_buried_up_the_ladder(self) -> None:
        lib = self.open_library()
        decay.advance_time(lib, "2026-05-25")
        resurfaced = decay.cue_resurface(lib, "2026-08-18",
                                         person="June")
        self.assertIn("m43", [r["id"] for r in resurfaced])
        # First rung: AFFECT — emotion only, no content of any kind.
        row = self._one(lib, "honest", "m43")
        self.assertEqual(row["form"], "affect")
        self.assertEqual(row["emotion"], "vulnerable")
        self.assertNotIn("conversation", row["content"])
        self.assertNotIn("overwhelmed", row["content"])
        # Second rung: FRAGMENT — a single shard, not the story.
        decay.reinforce(lib, "m43", "2026-08-20")
        row = self._one(lib, "honest", "m43")
        self.assertEqual(row["form"], "fragment")
        self.assertIn("a friend's voice", row["content"])
        self.assertNotIn("overwhelmed", row["content"])
        # Third rung: the stage clears and the gist assembles (HAZY).
        decay.reinforce(lib, "m43", "2026-08-22")
        row = self._one(lib, "honest", "m43")
        self.assertEqual(row["band"], decay.BAND_HAZY)
        self.assertIn(row["form"], ("thinned", "outline", "gist"))

    def test_sense_cue_resurfaces_buried_shame_memory(self) -> None:
        lib = self.open_library()
        decay.advance_time(lib, "2026-05-25")
        row = lib.db_for_memory("m52").execute(
            "MATCH (m:Memory {id: 'm52'}) RETURN m.buried AS buried"
        ).data()[0]
        self.assertTrue(row["buried"])
        # An unknown cue fires nothing; the memory stays unreachable.
        self.assertEqual(
            decay.sense_resurface(lib, "scent:no-such-cue",
                                  "2026-08-25"), [])
        self.assertIsNone(self._one(lib, "toast", "m52"))
        self.assertIsNone(self._one(lib, "toast", "m52", include_sunk=True))
        # The ginger-tea cue bound at encoding brings it back — as
        # AFFECT only: the feeling, with no content attached. The cue
        # crossed to the winter chapter through the index (FIRES_IN).
        resurfaced = decay.sense_resurface(lib, "taste:ginger tea",
                                           "2026-08-25")
        self.assertEqual([r["id"] for r in resurfaced], ["m52"])
        self.assertTrue(resurfaced[0]["was_buried"])
        self.assertEqual(resurfaced[0]["period"], "p1")
        row = self._one(lib, "toast", "m52")
        self.assertEqual(row["form"], "affect")
        self.assertEqual(row["emotion"], "shame")
        self.assertNotIn("stumbled", row["content"])
        self.assertNotIn("toast", row["content"])


class PeriodTest(_TempLibrary):
    """Periods: the top layer, and spreading activation across it."""

    P3_IDS = {"m34", "m35", "m36", "m37", "m46", "m56"}

    def test_period_recall_returns_only_that_period(self) -> None:
        lib = self.open_library()
        rp = decay.recall_period(lib, "Quiet Winter")
        self.assertEqual(rp["name"], "The Quiet Winter")
        in_period = {
            r["id"] for r in lib.partition("p1").execute(
                "MATCH (m:Memory)-[:PART_OF]->(p:Period "
                "{name: 'The Quiet Winter'}) RETURN m.id AS id"
            ).data()
        }
        self.assertEqual(rp["memory_count"], len(in_period))
        self.assertTrue(
            {m["id"] for m in rp["memories"]} <= in_period)
        self.assertGreater(len(rp["memories"]), 0)

    def test_spreading_activation_two_opposite_orders(self) -> None:
        lib = self.open_library()
        decay.advance_time(lib, "2026-08-17")
        # Act 1: a concrete little thing lands on ONE memory — the
        # first content to surface is that memory, then the period
        # opens, then the period's gist assembles. Nothing surfaces
        # outside the summer chapter; other chapters receive only
        # sub-threshold echoes over the bridge.
        t1 = decay.spreading_activation(lib, "taste:morning coffee")
        kinds1 = [s["kind"] for s in t1["steps"]]
        self.assertEqual(t1["steps"][0]["kind"], "memory")
        self.assertEqual(t1["steps"][0]["id"], "m35")
        self.assertIn("period", kinds1)
        self.assertIn("period_gist", kinds1)
        self.assertTrue(
            {s["id"] for s in t1["steps"] if s["kind"] == "memory"}
            <= self.P3_IDS)
        self.assertTrue(t1["echoes"])
        for echo in t1["echoes"]:
            self.assertNotEqual(echo["period"], "p3")
            self.assertLess(echo["activation"], decay.ACT_AFFECT)
            self.assertLessEqual(
                echo["activation"],
                decay.SPREAD_FACTORS["about"] * decay.BRIDGE_FACTOR + 1e-9)
        # Act 2: the era's song lands on the PERIOD — the first thing
        # to surface is the period itself; the gist still assembles.
        t2 = decay.spreading_activation(
            lib, "song:the summer song from the café radio")
        self.assertEqual(t2["steps"][0]["kind"], "period")
        self.assertIn("period_gist", [s["kind"] for s in t2["steps"]])
        self.assertTrue(
            {s["id"] for s in t2["steps"] if s["kind"] == "memory"}
            <= self.P3_IDS)
        # The mechanism is deterministic: same cue, identical trace.
        t1_again = decay.spreading_activation(
            lib, "taste:morning coffee")
        self.assertEqual(t1_again["steps"], t1["steps"])
        self.assertEqual(t1_again["echoes"], t1["echoes"])


class SurpriseTest(_TempLibrary):
    """Surprise: orthogonal to emotion; the chapter break."""

    def test_every_event_and_memory_carries_surprise(self) -> None:
        lib = self.open_library()
        rows = lib.execute_all(
            "MATCH (e:Event) WHERE e.surprise IS NULL RETURN e.id AS id")
        self.assertEqual(rows, [])
        rows = lib.execute_all(
            "MATCH (m:Memory) WHERE m.surprise IS NULL RETURN m.id AS id")
        self.assertEqual(rows, [])
        # A memory's surprise is its source event's surprise — a
        # property of what happened, not of how it felt.
        rows = lib.execute_all(
            "MATCH (m:Memory)-[:DERIVED_FROM*1..5]->(e:Event) "
            "WHERE m.surprise <> e.surprise RETURN m.id AS id")
        self.assertEqual(rows, [])

    def test_surprise_outlives_unsurprising_twins(self) -> None:
        lib = self.open_library()
        # The same-day trio (recorded 2026-04-25): all calm, all on
        # the standard pathway with emotion_weight 0.7 — emotion,
        # pathway and weight identical; only surprise differs.
        trio = {}
        for mem_id in ("m53", "m54", "m55"):
            trio[mem_id] = lib.db_for_memory(mem_id).execute(
                "MATCH (m:Memory {id: $id}) RETURN m.emotion AS em, "
                "m.pathway AS pw, m.emotion_weight AS w, "
                "m.surprise AS sp, m.recorded_at AS rec",
                {"id": mem_id},
            ).data()[0]
        self.assertEqual(
            {(t["em"], t["pw"], t["w"], t["rec"]) for t in trio.values()},
            {("calm", "standard", 0.7, "2026-04-25")})
        self.assertEqual(
            (trio["m53"]["sp"], trio["m54"]["sp"], trio["m55"]["sp"]),
            (0.05, 0.95, 0.0))
        # The mechanism, in the open: at equal emotion_weight, a
        # high-surprise memory's decay factor is strictly gentler, and
        # its effective half-life strictly longer.
        self.assertGreater(decay.decay_factor(30, 0.7, 0.95),
                           decay.decay_factor(30, 0.7, 0.0))
        self.assertGreater(decay.effective_half_life(0.7, 0.95),
                           decay.effective_half_life(0.7, 0.0))
        # Eight weeks on: the grocery run and the sponge are SUNK;
        # the one-day bakery — felt about not at all, seen coming by
        # nobody — is still CLEAR.
        decay.advance_time(lib, "2026-06-29")
        s53, s54, s55 = (self._strength(lib, m) for m in ("m53", "m54", "m55"))
        self.assertGreater(s54, s53)
        self.assertGreater(s53, s55)
        self.assertEqual(decay.band_of(s54), decay.BAND_CLEAR)
        self.assertEqual(decay.band_of(s53), decay.BAND_SUNK)
        self.assertEqual(decay.band_of(s55), decay.BAND_SUNK)

    def test_chapter_break_marks_only_the_surprising_opening(self) -> None:
        lib = self.open_library()
        breaks = decay.chapter_breaks(lib)
        self.assertEqual(len(breaks), 1)
        br = breaks[0]
        self.assertEqual(br["period"], "The First Summer")
        self.assertEqual(br["event_id"], "e23")
        self.assertGreaterEqual(br["surprise"],
                                decay.CHAPTER_BREAK_THRESHOLD)
        # The marking is in the graph, both ways: STARTS edge and the
        # period's opened_by property agree — inside p3's partition.
        rows = lib.partition("p3").execute(
            "MATCH (e:Event {id: 'e23'})-[:STARTS]->(p:Period) "
            "RETURN p.id AS pid, p.opened_by AS opened_by"
        ).data()
        self.assertEqual(rows, [{"pid": "p3", "opened_by": "e23"}])
        # The most surprising event of all (e21, the one-day bakery,
        # 0.95) landed mid-chapter: it opens nothing.
        rows = lib.db_for_memory("m54").execute(
            "MATCH (e:Event {id: 'e21'}) RETURN e.surprise AS sp"
        ).data()
        self.assertEqual(rows[0]["sp"], 0.95)
        rows = lib.execute_all(
            "MATCH (e:Event {id: 'e21'})-[:STARTS]->(p:Period) "
            "RETURN p.id AS pid")
        self.assertEqual(rows, [])
        # Routine openings (the winter, the spring) are unmarked too.
        rows = lib.execute_all(
            "MATCH (p:Period) WHERE p.opened_by IS NOT NULL "
            "RETURN p.id AS pid")
        self.assertEqual([r["pid"] for r in rows], ["p3"])


class StoragePartitionTest(_TempLibrary):
    """The storage layout itself: life is filed by chapter."""

    def test_every_memory_filed_in_exactly_one_partition(self) -> None:
        lib = self.open_library()
        # Index stubs: one FILED_IN per memory, pointing at the
        # chapter the partition scan independently finds it in.
        filed = {}
        for r in lib.index.execute(
            "MATCH (r:MemoryRef)-[:FILED_IN]->(p:Period) "
            "RETURN r.id AS id, p.id AS pid"
        ).data():
            self.assertNotIn(r["id"], filed)   # exactly one chapter
            filed[r["id"]] = r["pid"]
        seen = {}
        for pid, rows in lib.execute_each("MATCH (m:Memory) RETURN m.id AS id"):
            for r in rows:
                self.assertNotIn(r["id"], seen)   # no duplicates either
                seen[r["id"]] = pid
        self.assertEqual(filed, seen)
        self.assertEqual(len(seen), 61)   # 56 + v1.6's five routine days

    def test_provenance_chains_complete_inside_their_chapter(self) -> None:
        lib = self.open_library()
        # Within each partition store alone, every memory's chain
        # reaches a witnessed event of that same chapter: no chain
        # needs to leave its partition to be audited.
        for pid in lib.period_order:
            db = lib.partition(pid)
            total = db.execute(
                "MATCH (m:Memory) RETURN count(m) AS c").data()[0]["c"]
            reached = db.execute(
                "MATCH (m:Memory)-[:DERIVED_FROM*1..5]->(e:Event) "
                "RETURN count(DISTINCT m) AS c").data()[0]["c"]
            self.assertEqual(reached, total, pid)
            # And the root events reached are this chapter's own.
            own = {r["id"] for r in db.execute(
                "MATCH (e:Event)-[:PART_OF]->(:Period {id: $pid}) "
                "RETURN e.id AS id", {"pid": pid}).data()}
            reached = {r["id"] for r in db.execute(
                "MATCH (m:Memory)-[:DERIVED_FROM*1..5]->(e:Event) "
                "RETURN DISTINCT e.id AS id").data()}
            self.assertTrue(reached <= own, pid)

    def test_cross_period_derivation_routing(self) -> None:
        # The real dataset files every derivation inside one chapter
        # (a memory is filed under its chain's root event)...
        data = self.load_data()
        from build_graph import assign_periods
        _ep, memory_period = assign_periods(data)
        intra, cross = route_derivations(data, memory_period)
        self.assertEqual(cross, [])
        self.assertEqual(len(intra), 7)
        # ...but the routing itself is general: given a (fabricated)
        # assignment that files a child in another chapter than its
        # parent, the same function routes the edge to the index.
        fake = dict(memory_period)
        fake["m48"] = "p3"          # m48 is derived from m01 (p1)
        intra2, cross2 = route_derivations(data, fake)
        self.assertEqual(cross2, [("m48", "m01")])
        self.assertNotIn(("m48", "m01"), intra2)

    def test_index_holds_the_map_not_the_life(self) -> None:
        lib = self.open_library()
        # No memories or events live in the index — only stubs and
        # the shared-entity map.
        for label in ("Memory", "Event"):
            c = lib.index.execute(
                f"MATCH (n:{label}) RETURN count(n) AS c").data()[0]["c"]
            self.assertEqual(c, 0, label)
        refs = lib.index.execute(
            "MATCH (r:MemoryRef) RETURN count(r) AS c").data()[0]["c"]
        self.assertEqual(refs, 61)   # one stub per memory, v1.6 included
        # Chronology is an index fact: p1 BEFORE p2 BEFORE p3.
        chain = lib.index.execute(
            "MATCH (a:Period)-[:BEFORE]->(b:Period) "
            "RETURN a.id AS a, b.id AS b").data()
        self.assertEqual({(r["a"], r["b"]) for r in chain},
                         {("p1", "p2"), ("p2", "p3")})
        # The GAVE facts moved here (period-independent, entity-level).
        gave = lib.index.execute(
            "MATCH (p:Person)-[:GAVE]->(i:Item) "
            "RETURN p.name AS giver, i.name AS item").data()
        self.assertEqual({(r["giver"], r["item"]) for r in gave},
                         {("June", "red scarf"), ("Ana", "fern")})

    def test_cue_reaches_across_chapters_through_the_index(self) -> None:
        # A synthetic two-chapter dataset: the same sensory cue is
        # bound to a buried memory in EACH chapter. The cue fires
        # once; the index's FIRES_IN edges carry it into both
        # chapters, and both memories resurface — each from its own
        # partition.
        data = {
            "people": [{"name": "Theo", "role": "owner", "note": ""},
                       {"name": "Ana", "role": "sister", "note": ""}],
            "places": [{"name": "Home", "kind": "apartment"},
                       {"name": "Beach", "kind": "shore"}],
            "items": [],
            "periods": [
                {"id": "pa", "name": "Chapter A",
                 "start_date": "2026-01-01", "end_date": "2026-01-31",
                 "atmosphere": "quiet", "dominant_emotion": "calm",
                 "sensory_cues": []},
                {"id": "pb", "name": "Chapter B",
                 "start_date": "2026-02-01", "end_date": "2026-02-28",
                 "atmosphere": "loud", "dominant_emotion": "joyful",
                 "sensory_cues": []},
            ],
            "events": [
                {"id": "ea", "title": "A hard night", "date": "2026-01-10",
                 "mood": "vulnerable", "surprise": 0.2, "place": "Home",
                 "with": ["Ana"], "items": [], "tags": []},
                {"id": "eb", "title": "A worse night", "date": "2026-02-10",
                 "mood": "shame", "surprise": 0.2, "place": "Beach",
                 "with": ["Ana"], "items": [], "tags": []},
            ],
            "memories": [
                {"id": "ma", "text": "Theo felt exposed at home.",
                 "vague": "Theo felt exposed.", "anon": "He felt exposed.",
                 "gist": "A hard night at home.",
                 "kind": "episode", "privacy": "PRIVATE",
                 "confidence": 0.95, "recorded_at": "2026-01-11",
                 "source_event": "ea", "about": ["Theo", "Ana"],
                 "involves": [], "sensory_cues": ["taste:ginger tea"]},
                {"id": "mb", "text": "Theo felt ashamed at the beach.",
                 "vague": "Theo felt ashamed.", "anon": "He felt ashamed.",
                 "gist": "A worse night at the beach.",
                 "kind": "episode", "privacy": "PRIVATE",
                 "confidence": 0.95, "recorded_at": "2026-02-11",
                 "source_event": "eb", "about": ["Theo", "Ana"],
                 "involves": [], "sensory_cues": ["taste:ginger tea"]},
            ],
        }
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        base = str(Path(tmp.name) / "lib")
        build(data, base)
        lib = Library(base)
        self.addCleanup(lib.close)
        self.assertEqual(lib.locate_memory("ma"), "pa")
        self.assertEqual(lib.locate_memory("mb"), "pb")
        self.assertEqual(lib.periods_for_cue("taste:ginger tea"),
                         ["pa", "pb"])
        decay.advance_time(lib, "2026-03-01")
        for mid in ("ma", "mb"):
            row = lib.db_for_memory(mid).execute(
                "MATCH (m:Memory {id: $id}) RETURN m.buried AS b",
                {"id": mid}).data()[0]
            self.assertTrue(row["b"], mid)
        resurfaced = decay.sense_resurface(lib, "taste:ginger tea",
                                           "2026-03-02")
        self.assertEqual({r["id"] for r in resurfaced}, {"ma", "mb"})
        self.assertEqual({r["period"] for r in resurfaced}, {"pa", "pb"})

    def test_bridge_echo_is_damped_below_surfacing(self) -> None:
        # On the real graph: a summer cue lights the summer chapter;
        # June- and Ana-linked memories in earlier chapters receive
        # echoes — present in the result, quantified, and strictly
        # weaker than any within-chapter surfacing.
        lib = self.open_library()
        decay.advance_time(lib, "2026-08-17")
        trace = decay.spreading_activation(lib, "taste:morning coffee")
        self.assertTrue(trace["echoes"])
        cap = decay.SPREAD_FACTORS["about"] * decay.BRIDGE_FACTOR
        for echo in trace["echoes"]:
            self.assertLessEqual(echo["activation"], cap + 1e-9)
            self.assertLess(echo["activation"], decay.ACT_AFFECT)
        # And no echoed memory appears among the surfaced steps.
        stepped = {s["id"] for s in trace["steps"] if s["kind"] == "memory"}
        self.assertTrue(stepped.isdisjoint(
            {e["id"] for e in trace["echoes"]}))


class RoutineTest(_TempLibrary):
    """Routine merging: repeated days collapse into the habit (v1.6)."""

    RID = "routine-p2-harbor-bookstore"
    MEMBERS = {"m20", "m57", "m58", "m59", "m60", "m61"}

    def test_grouping_is_deterministic_and_structural(self) -> None:
        from build_graph import assign_periods, routine_groups
        data = self.load_data()
        event_period, memory_period = assign_periods(data)
        groups = routine_groups(data, event_period, memory_period)
        self.assertEqual(set(groups), {self.RID})
        group = groups[self.RID]
        self.assertEqual(set(group["members"]), self.MEMBERS)
        self.assertEqual(group["place"], "Harbor Bookstore")
        self.assertEqual(group["period"], "p2")
        # Only episodes ever join: the bookstore preference (m21) and
        # the fact about Ana (m41) are not days and cannot merge.
        kinds = {m["id"]: m["kind"] for m in data["memories"]}
        self.assertTrue(all(kinds[m] == "episode" for m in group["members"]))

    def test_routine_node_materialised_members_untouched(self) -> None:
        lib = self.open_library()
        rows = lib.partition("p2").execute(
            "MATCH (r:Routine {id: $rid})-[:SUMMARIZES]->(m:Memory) "
            "RETURN m.id AS id, m.routine_id AS rid",
            {"rid": self.RID},
        ).data()
        self.assertEqual({r["id"] for r in rows}, self.MEMBERS)
        self.assertTrue(all(r["rid"] == self.RID for r in rows))
        # The merge deleted nothing: every member still traces to its
        # own witnessed event through the derivation chain.
        for mid in self.MEMBERS:
            ev = lib.db_for_memory(mid).execute(
                "MATCH (m:Memory {id: $id})-[:DERIVED_FROM*1..5]->(e:Event) "
                "RETURN e.id AS id", {"id": mid},
            ).data()
            self.assertEqual(len(ev), 1, mid)

    def test_recall_collapses_members_into_the_habit(self) -> None:
        lib = self.open_library()
        before = decay.recall(lib, "bookstore", routines=False)
        self.assertTrue(self.MEMBERS <= {r["id"] for r in before})
        after = decay.recall(lib, "bookstore")
        after_ids = {r["id"] for r in after}
        self.assertIn(self.RID, after_ids)
        self.assertTrue(self.MEMBERS.isdisjoint(after_ids))
        entry = next(r for r in after if r["id"] == self.RID)
        self.assertEqual(entry["form"], "routine")
        self.assertEqual(entry["routine"]["members"], 6)
        self.assertEqual(entry["routine"]["showing"], 6)
        # Non-episodes at the same place are NOT merged: the
        # preference and the fact still answer for themselves.
        self.assertIn("m21", after_ids)
        self.assertIn("m41", after_ids)

    def test_drilldown_reaches_every_day(self) -> None:
        lib = self.open_library()
        days = decay.routine_drilldown(lib, self.RID)
        self.assertEqual(
            [d["id"] for d in days],
            ["m57", "m20", "m58", "m59", "m60", "m61"],
        )
        dates = [d["date"] for d in days]
        self.assertEqual(dates, sorted(dates))
        self.assertTrue(all(d["text"] for d in days))


class DilutionTest(_TempLibrary):
    """Cue dilution: a cue's energy is divided among its bindings."""

    def _dataset(self, extra_rain: int) -> dict:
        """One buried memory on a unique cue; one sinkable memory on
        a 'rain' cue, optionally shared with `extra_rain` more."""
        events = [
            {"id": "eb", "title": "A shameful evening",
             "date": "2026-01-10", "mood": "shame", "surprise": 0.2,
             "place": "Home", "with": ["Ana"], "items": [], "tags": []},
            {"id": "es", "title": "Rain on the window",
             "date": "2026-01-12", "mood": "calm", "surprise": 0.0,
             "place": "Home", "with": [], "items": [], "tags": []},
        ]
        memories = [
            {"id": "mb", "text": "Theo froze mid-sentence at dinner.",
             "vague": "Theo froze at dinner.", "anon": "He froze.",
             "gist": "A shameful evening at home.", "kind": "episode",
             "privacy": "PRIVATE", "confidence": 0.95,
             "recorded_at": "2026-01-11", "source_event": "eb",
             "about": ["Theo", "Ana"], "involves": [],
             "sensory_cues": ["smell:bonfire"]},
            {"id": "ms",
             "text": "Theo watched the rain and thought about nothing.",
             "vague": "Theo watched the rain.", "anon": "He watched rain.",
             "gist": "Rain on the window, a quiet hour.",
             "kind": "episode", "privacy": "PUBLIC", "confidence": 0.7,
             "recorded_at": "2026-01-13", "source_event": "es",
             "about": ["Theo"], "involves": [],
             "sensory_cues": ["smell:rain"]},
        ]
        for i in range(extra_rain):
            events.append(
                {"id": f"ex{i}", "title": f"Another rainy day {i}",
                 "date": f"2026-01-{15 + i}", "mood": "calm",
                 "surprise": 0.0, "place": "Home", "with": [],
                 "items": [], "tags": []})
            memories.append(
                {"id": f"mx{i}", "text": f"Rain again, day {i}.",
                 "vague": "Rain again.", "anon": "Rain.",
                 "gist": f"Another rainy day {i}.", "kind": "episode",
                 "privacy": "PUBLIC", "confidence": 0.95,
                 "recorded_at": f"2026-01-{16 + i}",
                 "source_event": f"ex{i}", "about": ["Theo"],
                 "involves": [], "sensory_cues": ["smell:rain"]})
        return {
            "people": [{"name": "Theo", "role": "owner", "note": ""},
                       {"name": "Ana", "role": "sister", "note": ""}],
            "places": [{"name": "Home", "kind": "apartment"}],
            "items": [],
            "periods": [{"id": "pa", "name": "Chapter A",
                         "start_date": "2026-01-01",
                         "end_date": "2026-03-31", "atmosphere": "quiet",
                         "dominant_emotion": "calm", "sensory_cues": []}],
            "events": events,
            "memories": memories,
        }

    def _open_data(self, data: dict) -> Library:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        base = str(Path(tmp.name) / "lib")
        build(data, base)
        lib = Library(base)
        self.addCleanup(lib.close)
        return lib

    def _strength(self, lib: Library, mid: str) -> float:
        return lib.db_for_memory(mid).execute(
            "MATCH (m:Memory {id: $id}) RETURN m.strength AS s",
            {"id": mid},
        ).data()[0]["s"]

    def test_degree_and_energy_on_real_graph(self) -> None:
        lib = self.open_library()
        # Bound to exactly one memory (m52): full charge.
        self.assertEqual(decay.cue_degree(lib, "taste:ginger tea"), 1)
        self.assertEqual(decay.cue_energy(lib, "taste:ginger tea"), 1.0)
        # 'scent:fresh mint' is bound to two memories (m01 and m48).
        self.assertEqual(decay.cue_degree(lib, "scent:fresh mint"), 2)
        self.assertEqual(decay.cue_energy(lib, "scent:fresh mint"), 0.5)

    def test_unique_cue_raises_buried_memory(self) -> None:
        lib = self._open_data(self._dataset(extra_rain=0))
        decay.advance_time(lib, "2026-03-31")
        buried = lib.db_for_memory("mb").execute(
            "MATCH (m:Memory {id: 'mb'}) RETURN m.buried AS b").data()[0]["b"]
        self.assertTrue(buried)
        res = decay.sense_resurface(lib, "smell:bonfire", "2026-04-01")
        self.assertEqual([r["id"] for r in res], ["mb"])
        self.assertTrue(res[0]["raised"])
        self.assertEqual(res[0]["energy"], 1.0)
        self.assertEqual(res[0]["strength"], decay.RESURFACE_STRENGTH)
        row = self._one(lib, "froze", "mb")
        self.assertIsNotNone(row)
        self.assertEqual(row["form"], "affect")

    def test_shared_cue_only_stirs_a_buried_memory(self) -> None:
        # Four shameful evenings, one bonfire cue: degree 4, energy
        # 0.25 — below the threshold that breaks suppression.
        events, memories = [], []
        for i in range(4):
            events.append(
                {"id": f"eb{i}", "title": f"Shameful evening {i}",
                 "date": f"2026-01-{10 + i}", "mood": "shame",
                 "surprise": 0.2, "place": "Home", "with": ["Ana"],
                 "items": [], "tags": []})
            memories.append(
                {"id": f"mb{i}", "text": f"Theo froze, evening {i}.",
                 "vague": "Theo froze.", "anon": "He froze.",
                 "gist": f"Shameful evening {i}.", "kind": "episode",
                 "privacy": "PRIVATE", "confidence": 0.95,
                 "recorded_at": f"2026-01-{11 + i}",
                 "source_event": f"eb{i}", "about": ["Theo", "Ana"],
                 "involves": [], "sensory_cues": ["smell:bonfire"]})
        data = self._dataset(extra_rain=0)
        data["events"] = events
        data["memories"] = memories
        lib = self._open_data(data)
        self.assertEqual(decay.cue_degree(lib, "smell:bonfire"), 4)
        decay.advance_time(lib, "2026-03-31")
        res = decay.sense_resurface(lib, "smell:bonfire", "2026-04-01")
        self.assertEqual(len(res), 4)
        self.assertTrue(all(r["stirred"] and not r["raised"] for r in res))
        # Stirred, not raised: every one is still buried, and recall
        # still cannot see any of them.
        for i in range(4):
            row = lib.db_for_memory(f"mb{i}").execute(
                f"MATCH (m:Memory {{id: 'mb{i}'}}) RETURN m.buried AS b"
            ).data()[0]
            self.assertTrue(row["b"])
            self.assertIsNone(self._one(lib, "froze", f"mb{i}"))

    def test_diluted_cue_cannot_hold_a_sunk_memory(self) -> None:
        # Control: the rain cue bound to one memory only.
        lib = self._open_data(self._dataset(extra_rain=0))
        decay.advance_time(lib, "2026-03-31")
        self.assertLess(self._strength(lib, "ms"), decay.SUNK_THRESHOLD)
        res = decay.sense_resurface(lib, "smell:rain", "2026-04-01")
        hit = next(r for r in res if r["id"] == "ms")
        self.assertEqual(hit["strength"], decay.RESURFACE_STRENGTH)
        decay.reinforce(lib, "ms", "2026-04-03")   # AFFECT -> FRAGMENT
        decay.reinforce(lib, "ms", "2026-04-05")   # stage clears
        row = self._one(lib, "rain", "ms")
        self.assertIsNotNone(row)
        self.assertEqual(row["band"], decay.BAND_HAZY)

        # Same memory, same cue — but the cue now serves four
        # memories, so it can only set this one down at 0.10.
        lib = self._open_data(self._dataset(extra_rain=3))
        decay.advance_time(lib, "2026-03-31")
        res = decay.sense_resurface(lib, "smell:rain", "2026-04-01")
        hit = next(r for r in res if r["id"] == "ms")
        self.assertEqual(hit["degree"], 4)
        self.assertEqual(hit["energy"], 0.25)
        self.assertAlmostEqual(
            hit["strength"], decay.RESURFACE_STRENGTH * 0.25)
        # The feeling and the shard still surface...
        row = self._one(lib, "rain", "ms")
        self.assertIsNotNone(row)
        self.assertEqual(row["form"], "affect")
        decay.reinforce(lib, "ms", "2026-04-03")
        row = self._one(lib, "rain", "ms")
        self.assertIsNotNone(row)
        self.assertEqual(row["form"], "fragment")
        # ...but once the ladder clears, the strength the diluted cue
        # could give is below the sinking line: the story never
        # assembles, and the memory slips back under.
        decay.reinforce(lib, "ms", "2026-04-05")
        self.assertIsNone(self._one(lib, "rain", "ms"))
        self.assertLess(self._strength(lib, "ms"), decay.SUNK_THRESHOLD)


class RuminationTest(_TempLibrary):
    """Rumination has a ceiling: full gains, then exhaustion (v1.6)."""

    DATES = ["2026-06-29", "2026-07-13", "2026-07-27", "2026-08-10",
             "2026-09-07", "2026-10-05", "2026-11-02", "2026-12-07"]

    def test_gain_schedule_and_count(self) -> None:
        lib = self.open_library()
        gains = []
        for i, day in enumerate(self.DATES):
            r = decay.reinforce(lib, "m49", day)
            gains.append(r["gain"])
            self.assertEqual(r["ruminate_count"], i + 1)
            self.assertLessEqual(r["post"], decay.RUMINATION_CAP + 1e-9)
        # Three full rehearsals, then the dividend halves to nothing.
        self.assertEqual(gains, [0.8, 0.8, 0.8, 0.4, 0.2, 0.1, 0.05, 0.025])
        # The count lives on the node, not just in the return value.
        row = lib.db_for_memory("m49").execute(
            "MATCH (m:Memory {id: 'm49'}) RETURN m.ruminate_count AS c"
        ).data()[0]
        self.assertEqual(row["c"], len(self.DATES))

    def test_rumination_burns_out(self) -> None:
        lib = self.open_library()
        posts = [decay.reinforce(lib, "m49", day)["post"]
                 for day in self.DATES]
        # It climbs to the ceiling and sits there while gains are full.
        self.assertAlmostEqual(posts[0], decay.RUMINATION_CAP)
        self.assertAlmostEqual(posts[3], decay.RUMINATION_CAP)
        # Then, still mentioned every few weeks, it falls anyway: the
        # dividend halved past usefulness and decay resumed.
        self.assertLess(posts[4], posts[3])
        self.assertLess(posts[-1], posts[4])
        self.assertTrue(all(p <= decay.RUMINATION_CAP + 1e-9 for p in posts))


if __name__ == "__main__":
    unittest.main()
