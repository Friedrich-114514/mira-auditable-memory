# BUILD REPORT — v1 core (2026-11-25)

Status: **v1 core complete and verified end-to-end.** Nothing published; no GitHub, no accounts.

## Environment

- Python 3.12.3, `relate-db` 1.2.0 (`pip install relate-db`, `import relate`)
- Project-local venv at `.venv/` (the `/tmp/relate-test` venv referenced in the task brief did not exist in this environment, so a fresh one was created)

## What was built

| File | Purpose |
|---|---|
| `data/memories.json` | Fully synthetic dataset: fictional agent "Mira", fictional human "Theo". 6 people, 8 places, 10 items, 15 events, **48 memories** (episodes, facts, preferences, plans, 3 secrets). No real user data anywhere. |
| `build_graph.py` | Loads the dataset into a persistent Relate store (directory mode) at `./store`. Every Memory gets `source_event`, `recorded_at`, `confidence`, `privacy` (PUBLIC / PRIVATE / SECRET_LOCKED) plus a `DERIVED_FROM` edge to its immediate source. |
| `demo.py` | One command, three showcases (provenance audit / privacy tiers / graph-vs-text). |
| `tests/test_smoke.py` | 7 unittest smoke tests. |
| `README.md` | English draft: pitch, why Relate directory mode, quick start, graph model, showcase descriptions. |
| `requirements.txt` | `relate-db>=1.2` only. |

## Actual run output (verbatim excerpts)

### Build

```
Graph built in Relate store: /home/hatch/workspace/relate-hackathon/store
  Person   nodes: 6
  Place    nodes: 8
  Item     nodes: 10
  Event    nodes: 15
  Memory   nodes: 48
  total edges: 180
```

The store persists on disk (32 KB: `manifest.json`, `nodes/`, `relationships/`, `wal/`) and is reopened by `demo.py` in a separate process.

### Demo 1 — provenance audit

```
Agent claims (memory m48, kind=preference, confidence=0.9, recorded 2026-01-07):
  "Theo takes his mint tea with honey, never with sugar."
  hop 1: DERIVED_FROM -> Memory m01: "When Theo meets Marco at Café Lumen, he always orders mint tea."
  hop 2: DERIVED_FROM -> Event e01: "First work chat of the year over tea" (2026-01-05, mood: calm)
           scene: place = Café Lumen, present = ['Marco']
Audit check: 48/48 memories trace back to a source event; memories with NO provenance path: 0
```

### Demo 2 — privacy tiers

- (a) Naive recall (no filter) **leaks** m30 `[SECRET_LOCKED]` (the unsent letter in the desk drawer).
- (b) Default recall with the privacy hard filter returns no drawer memory; the closest safe memory (m31, PRIVATE) lets the agent acknowledge the evening without content.
- (c) Unlock path with the demo passphrase returns m30; `--wrong-passphrase` run verified: `wrong passphrase -> access denied, nothing returned.`

### Demo 3 — graph vs. keyword baseline

```
Score: graph recall 6/6 — keyword baseline 3/6
```

Per question: graph correct on all six. The baseline passed exactly the three questions whose answers are fully spelled out inside a single memory's text (Q2 gift episode, Q5 introduction fact, Q6 café preference) and failed the three that need ordering/aggregation/joins (Q1 latest-by-date + companion + place, Q3 place aggregation for Ana, Q4 plan→item join). This split is honest by construction: the baseline is a plain distinct-keyword-overlap scorer over the same texts (SECRET_LOCKED excluded there too).

### Tests

```
Ran 7 tests in 0.115s — OK
```
(node counts; provenance properties present on all memories; m48 chain reaches e01; all 48 memories trace to an event; SECRET_LOCKED invisible to filtered drawer search; exactly {m30, m32, m38} locked; latest 'down' event = e05 with June.)

### Clean-room reproduction (judges' flow)

Copied the project (excluding `.venv`/`store`) to a fresh directory, created a new venv, `pip install -r requirements.txt`, then build → demo → tests: **all passed** (build counts identical, demo completed, 7/7 tests OK).

## Relate notes / pitfalls found

1. **Null semantics bite privacy filters.** `WHERE m.privacy <> 'SECRET_LOCKED'` silently drops nodes that lack the property entirely (comparison with null is not true). Every Memory must always carry `privacy`; a test enforces that no Memory has null provenance/privacy fields.
2. **Verified working in relate-db 1.2.0** (all used by the demo): parameterised queries (`db.execute(q, {...})`), `MERGE`, `OPTIONAL MATCH`, `WITH … ORDER BY … LIMIT` pipelines, `count()` aggregation with grouping, `CONTAINS`, `toLower()`, list properties with `'x' IN e.tags`, `labels()`, `DISTINCT`, and variable-length paths `-[:DERIVED_FROM*1..5]->` across mixed node labels.
3. **Provenance design choice:** `DERIVED_FROM` points only at the *immediate* source (parent memory OR event), keeping chains linear (m48 → m01 → e01). The root event id is also stored redundantly as the `source_event` property for O(1) lookups.
4. **Store layout:** directory mode produces a small inspectable store (manifest + nodes/relationships + WAL), which suits the "judges can open the graph" requirement better than `:memory:`.

## Not done (by design, this round)

- No demo video, no GitHub publishing, no Devpost registration.
- README is a draft; screenshots/CLI-inspection walkthrough can be added in the polish round.

---

# v1.1 — Forgetting curve (Demo 4) (2026-11-25)

Status: **complete and verified end-to-end.** Nothing published; no GitHub.

## What was added

| File | Change |
|---|---|
| `decay.py` | New module: the forgetting-curve model. `s0 = confidence x salience(source event mood)`; exponential decay, **half-life 45 days**; recall reinforcement `s <- s + (1 - s) x 0.6` + `last_accessed` update; **sunk threshold 0.25**. All updates/reads are real Cypher (`SET m.strength = m.strength * $f`, grouped by `strength_as_of` stamp); the module returns only values read back from the graph. |
| `build_graph.py` | Memory nodes now also carry `strength` (initial, from the model), `salience`, `last_accessed` (= `recorded_at`), `strength_as_of` (= `recorded_at`), `sunk = false`. Node/edge counts unchanged (48 memories, 180 edges). |
| `demo.py` | New **Demo 4** (runs last; builds its own `./store_decay` so demos 1–3 keep a pristine graph): time catches up to 2026-05-25, then 12 weekly steps with per-week active/sunk counts and newly-sunk ids; scripted reunions at weeks 3/6/9 recall m17/m29/m48; closing Case A (sunk plan m10) and Case B (resurrected m48); stacked-filter check on the three secrets. |
| `tests/test_smoke.py` | New `DecayTest` class, 3 tests (decay sinks but deletes nothing; sunk m10 hidden from default recall yet still traceable to e03; reinforcement resurrects sunk m48). Suite now **10 tests, all passing**. |
| `README.md` | New "Forgetting: sunk, not deleted" section (formula, half-life, threshold, stacked-filter rule), showcase list updated to four, graph model + layout updated. |

## Actual run output (verbatim excerpts)

Simulation arc (every number below was produced by Cypher updates/queries against `./store_decay` during the real run):

```
Fresh graph built: 48 memories, all active (strengths as of the day each was recorded).
Time catches up to 2026-05-25:  active 21 / sunk 27
  week  3 (2026-06-15): active 15 / sunk 33
    ^ REUNION (June visits; the lake walks come up): recalls m17
      strength 0.148 (SUNK) -> 0.659 after recall — resurrected; active is now 16
  week  6 (2026-07-06): active  8 / sunk 40
    ^ REUNION (chess club summer social): recalls m29
      strength 0.234 (SUNK) -> 0.693 after recall — resurrected; active is now 9
  week  9 (2026-07-27): active  3 / sunk 45
    ^ REUNION (Marco visits; mint tea with honey): recalls m48
      strength 0.028 (SUNK) -> 0.611 after recall — resurrected; active is now 4
  week 12 (2026-08-17): active  2 / sunk 46
After 12 weeks: active 2 / sunk 46 of 48 — and the total is still 48.
Nothing was deleted; things were forgotten.
```

Closing cases:

```
Case A: m10 "Theo wants to learn to develop film at home before summer."
  strength 0.034, sunk = True; default recall for 'film' (privacy + sunk
  filters): 0 hits — yet the audit walk still reaches its source event
  ("Ana visits with a film camera", 2026-02-02). SUNK, NOT DELETED.
Case B: m48 had sunk to 0.028 by week 9; one reunion recall lifted it to
  0.611; it ends the summer at 0.442 (sunk = False, last recalled
  2026-07-27) and default recall for 'honey' returns ['m48'].
Stacked filters: m30 / m32 / m38 end the summer SECRET_LOCKED *and* sunk
  (strengths 0.178 / 0.110 / 0.125) — excluded from default recall twice
  over; sinking never unlocks anything.
```

Honest wrinkle, kept in the output rather than smoothed over: m17, resurrected at week 3, sinks *again* by week 12 (it appears in week 12's newly-sunk list). One recall buys time, not immortality — which is exactly how the curve is supposed to behave.

Demos 1–3 regression-checked in the same full run: provenance audit still 48/48, privacy demo still leaks under naive recall and blocks under the filter, graph-vs-text still 6/6 vs 3/6. Tests: `Ran 10 tests — OK`.

## Relate notes (v1.1 additions)

1. **Arithmetic in SET works** in relate-db 1.2.0, including parameterised factors (`SET m.strength = m.strength * $f`) and compound expressions (`m.strength + (1.0 - m.strength) * $g`), plus boolean literals in SET and `>=` / `<` float comparisons in WHERE — the whole decay engine runs as native Cypher updates, no read-modify-write round trips except where a per-memory anchor date is needed.
2. **Grouped decay trick:** because every memory carries `strength_as_of`, one `MATCH ... WHERE m.strength_as_of = $from SET ...` per distinct stamp advances the whole graph; after the first advance all stamps coincide, so each weekly step is a single grouped update plus the two sunk-flag passes.
3. Demo 4 deliberately uses a **separate store** (`./store_decay`, rebuilt fresh each run): the forgetting simulation is destructive to *strengths* (by design), and judges should be able to re-run demos 1–3 against the untouched `./store`.

---

# v1.2 — The fuzzy band, layered; emotion runs the machine (2026-11-25)

Status: **complete and verified end-to-end.** Nothing published; no GitHub. This round merges nine design refinements from Fri, in the order they arrived: (1) fuzzy band, (2) haze layers shed detail gradually, (3) emotion-weighted priority, (4) valence asymmetry, (5) emotion-specific pathways (correcting 4: anger does *not* fade quietly), (6) sensory cues / Proust effect, (7) staged resurfacing (the way back is not the way down reversed), (8) period layer (Conway), (9) fixed cascade replaced by spreading activation (correcting 8's ordering). Demos 1–3 regression-checked in the same run: audit **52/52**, privacy leak/filter/unlock unchanged, graph-vs-text still **6/6 vs 3/6**.

## Dataset changes

- Every memory gained `gist` (round 1), then `vague` + `anon` renderings (round 2), `sensory_cues` (round 6), `shard` for 18 memories (round 7; build falls back to the source event's place).
- 4 new events + 4 new memories, all fictional: **e16 angry / m49** (Marco takes credit — the grudge case), **e17 tender / m50** + **e18 calm / m51** (same-day contrast pair, both recorded 2026-04-13), **e19 shame / m52** (the stumbled toast — the buried/Proust case, cue `taste:ginger tea`).
- 3 Periods by date: The Quiet Winter (24 memories), The Restless Spring (23), The First Summer (5).
- Totals now: 6 people / 8 places / 10 items / **19 events / 52 memories / 3 periods / 24 cues / 296 edges**.

## What was added

| File | Change |
|---|---|
| `decay.py` | Band model (`band_of`, `haze_level`, `band_counts`); unified `recall` (band-shaped forms, priority-sorted); `EMOTION_PROFILE` (valence / pathway / emotion_weight per specific emotion); per-weight grouped decay in `advance_time`; bury pass (`pathway='buried'` + `encoded_strength ≥ 0.6` → `buried`, strength frozen, `buried_ever` guard against re-burial); pathway-aware `reinforce` (grudge rumination gain 0.8; staged memories advance one rung instead); `cue_resurface` (person/place/item), `sense_resurface` (TRIGGERED_BY); `recall_period`; `spreading_activation` (deterministic, soft-accumulating, edge factors PART_OF 0.85 / same-event 0.7 / ABOUT 0.55 period-local, self excluded). |
| `build_graph.py` | Memory nodes carry `vague/anon/gist/shard`, `emotion/valence/pathway/emotion_weight`, `encoded_strength`, `buried/buried_ever`, `sensory_cues`; Cue nodes + TRIGGERED_BY edges (memories and periods); Period nodes + PART_OF edges (events by date, memories via root event). |
| `demo.py` | Demo 4 updated for the emotion model (buried column; m48 counterfactual now computed, not hardcoded — it prints 0.005). New **Demo 5** (fuzzy band + pathways + Act A buried/person-cue + Act B Proust ladder) and **Demo 6** (period recall + two spreading-activation acts) in their own stores (`store_haze`, `store_period`). |
| `tests/test_smoke.py` | 21 tests (was 10): layered-haze sequence on m35 and m36, secret-in-every-band, same-age divergence (m50 vs m51), priority prefers positive (m42 first), grudge outlasts neutral peer, buried hidden everywhere but traceable, person-cue ladder (AFFECT→FRAGMENT→HAZY), sense-cue AFFECT-only first hit, period recall containment, spreading activation's two opposite orders + determinism. **21/21 passing.** |
| `README.md` | Forgetting section rewritten: band/layer table, "Emotion sets the priority", "Emotion-specific pathways" with text state machine, "Sensory cues", "Periods, not just events — and spreading activation". |

## Actual run output (verbatim excerpts)

Fuzzy-band longitudinal case (Demo 5) — one memory, four forms, all selected by real queries:

```
[CLEAR  · 0.687] "Theo booked a train ticket for a weekend coast trip with June in June."
[HAZY-1 · 0.497] "Theo booked a train ticket for a coast trip with June. (hazy — exact times and numbers have faded)"
[HAZY-2 · 0.447] "Theo booked a ticket for a coast trip with a close friend. (hazy — names have faded too)"
[HAZY-3 · 0.323] "Theo booked a trip with a friend for later in the year. (hazy — only the outline remains)"
(SUNK · 0.189)  recall returns nothing — audit walk still reaches "Booking the coast trip tickets"
```

Emotion machinery (Demo 5):

```
Joy rises fast — recall 'café':  m42 [cheerful/fast_lane] 0.722 x 1.1 -> priority 0.914
                                 m01 [calm/standard]     0.665 x 0.7 -> priority 0.465
                                 m03 [calm/standard]     0.630 x 0.7 -> priority 0.441
Same-day pair: start  m50 (tender, w1.5) 0.555 [CLEAR] vs m51 (calm, w0.7) 0.194 [SUNK]
               week4  m50 0.417 [HAZY]  vs m51 0.105 [SUNK]
               week8  m50 0.313 [HAZY]  vs m51 0.057 [SUNK]
               week12 m50 0.234 [SUNK]  vs m51 0.031 [SUNK]   (m50 spent the summer
                     in the haze; m51 was gone before it began)
Rumination (week 5): m49 (angry, w1.6) 0.283 -> 0.857 with gain 0.8; ends summer 0.534 [HAZY-1]
Catch-up counts: CLEAR 7 / HAZY 18 / SUNK 23 / BURIED 4 (of 52)
```

Burial and the two keys (Demo 5, Acts A–B):

```
m43: buried = True, strength frozen at 0.495 (encoded 0.800)
  recall 'honest' -> []        recall 'honest', include_sunk -> []
  audit walk -> "Heart-to-heart by the lake" (2026-03-29)   [chain intact]
Cue (person: June, 2026-08-18): resurfaced m32, m33, m43 — m32 stays invisible (SECRET_LOCKED)
  m43 ladder: [AFFECT] "(something about this stirs a feeling — the feeling is back, the story is not)"
              [FRAGMENT] "a friend's voice, steady, saying very little (a shard, not the story)"
              [HAZY-2] "When Theo is overwhelmed, he prefers one honest conversation…"
m52 (shame, encoded 0.855): recall 'toast' -> [] even with include_sunk; audit -> e19 celebration
  sense cue taste:ginger tea -> resurfaced (was_buried = True), then:
  [AFFECT] feeling only (emotion: shame) -> [FRAGMENT] "a raised glass, and his own voice faltering"
  -> [HAZY-2] anon rendering -> [CLEAR 0.757] full text + details
Demo 4's lost film plan (m10, merely sunk) is hooked by scent:darkroom chemicals the same evening.
Final counts after both acts: CLEAR 1 / HAZY 10 / SUNK 41 / BURIED 0 — of 52.
```

Spreading activation (Demo 6) — same period, opposite orders, real traces:

```
ACT 1 (cue taste:morning coffee, lands on m35):
  step 1-2:  AFFECT m35 -> FRAGMENT m35 "coffee steam, and talking to a plant…"
  step 4:    PERIOD OPENS · The First Summer [act 0.47]
  steps 5-16: m34/m36/m37/m46 climb AFFECT -> FRAGMENT -> GIST (shard rain)
  step 17:   PERIOD GIST ASSEMBLES [act 0.99]
  (re-running the same cue reproduces the identical trace: True)
ACT 2 (cue song:the summer song from the café radio, lands on the period):
  step 1:    PERIOD OPENS · The First Summer [act 0.55]
  steps 2-6: AFFECT for all five summer memories
  steps 7-16: FRAGMENT -> GIST, one memory at a time
  step 17:   PERIOD GIST ASSEMBLES [act 0.96]
```

Demo 4 under the emotion model (numbers moved, honestly): catch-up `active 25 / sunk 23 / buried 4`; week-3 reunion m17 resurrected 0.206 → 0.682; week-6 m29 now *reinforced* rather than resurrected (0.260 — the proud pathway's longer clock kept it just above the line); m48 resurrected 0.008 → 0.603, ends at 0.380, computed counterfactual 0.005; secrets end locked + sunk-or-buried (m32 is both SECRET_LOCKED and buried, strength frozen 0.588).

## Design decisions & pitfalls (v1.2)

1. **Layered renderings are stored, never generated.** `vague`/`anon`/`gist`/`shard` were all written into the dataset when the memories were "fresh". Recall selects; it does not rewrite. This keeps the anti-fabrication rule mechanical rather than promissory — there is no code path that can produce a detail the stored data does not contain.
2. **Burial keys on `encoded_strength`, not current strength.** Suppression is a decision about how a memory was *formed*; keying on current strength would let slow decay quietly un-bury things, and fast decay bury things that were never suppressed. Buried strengths are frozen (advance skips buried), and `buried_ever` prevents the clock from re-burying a memory that has been resurfaced and faced.
3. **ABOUT spread is period-local and never routes through the self.** Unrestricted shared-person spread floods the graph through Theo (he is in nearly every memory's ABOUT set) and through June/Ana across chapters. Links are therefore built only between same-period memories and skip Theo entirely — documented in code and README as a modelling choice: the self is the medium, not a cue.
4. **Spreading activation is synchronous and sorted.** Each round computes all contributions from round-start values (soft accumulation `1 − Π(1 − c)`), applied together; iteration order is sorted by node key. Traces are byte-identical across runs — the demo asserts it live.
5. **`EXISTS { … }` subqueries work in relate-db 1.2.0** (used by `cue_resurface`'s item path), as do boolean property filters in WHERE (`m.buried = false`) and SET-to-null for clearing `resurface_stage`.
6. Demo 5's week-12 pair line shows *both* memories SUNK (m50 at 0.234, just under the line) — kept as printed rather than tuned away: the divergence story is in the trajectory (CLEAR→HAZY all summer vs gone by May), not in a rigged endpoint.

# v1.3 — Surprise: the orthogonal axis and the chapter break (2026-11-25)

Status: **complete and verified end-to-end.** Nothing published; no GitHub. This round adds one dimension only — **surprise** — on top of the v1.2 model; all six existing demos regression-checked in the same run: audit **56/56**, privacy leak/filter/unlock unchanged, graph-vs-text still **6/6 vs 3/6**, tests **24/24** (21 prior + 3 new).

## Design

- **Surprise is a property of the event, inherited by its memories** — like emotion, but on its own axis. `surprise` ∈ [0, 1] is prediction error: how far the event sat from what was expected. It is stored on the Event node and copied onto every Memory distilled from it. It maps to **no pathway and no valence**: a joyful event can be fully foreseen, a calm one can be a total shock.
- **Two asymmetric boosts** (constants in `decay.py`): encoding `s0 × (1 + 0.6 · surprise)` (clamped to 1.0) and half-life `× (1 + 1.8 · surprise)`. Surprise is deliberately given the stronger hand on the clock, not on the encoding — being unexpected mostly buys a memory *time*. `advance_time` now groups by `(strength_as_of, emotion_weight, surprise)`; `reinforce`'s pre-boost decay uses the same three-factor clock; Demo 4's m48 counterfactual is computed with it too.
- **Chapter breaks**: at build time, if a Period's *earliest* event has `surprise ≥ 0.8` (`CHAPTER_BREAK_THRESHOLD`), the build writes an `(Event)-[:STARTS]->(Period)` edge and sets the period's `opened_by`. `decay.chapter_breaks(db)` reads the marks back. The rule keys on the period's first event specifically — a shock landing mid-chapter opens nothing (see e21 below), because a chapter can only break where a chapter can begin.

## Dataset changes

- All 19 existing events gained a `surprise` value (0.0–0.60). Four were kept deliberately low for documented reasons, not narrative ones: e13 = 0.00 and e14 = 0.00 (they carry the layered-haze longitudinal cases m35/m36, whose band-crossing dates are asserted in tests — and both events genuinely were planned/expected), e05 = 0.05 (keeps Demo 4's week-3 reunion a true resurrection of m17), e17 = 0.10 (keeps m50 in the HAZY band, not CLEAR, at the same-age test date).
- 4 new events + 4 new memories, all fictional: **e20 grocery run / m53** (calm, surprise 0.05), **e21 one-day bakery stall / m54** (calm, surprise **0.95** — the most surprising event in the graph), **e22 replacement sponge / m55** (calm, surprise 0.00) — a same-day trio recorded 2026-04-25, identical emotion/pathway/weight (calm / standard / 0.7), differing only in surprise; and **e23 busker under the open window / m56** (joyful, surprise 0.90, 2026-05-01 — the First Summer's first event, its chapter break).
- Totals now: 6 people / 8 places / 10 items / **23 events / 56 memories / 3 periods / 26 cues / 319 edges**. Period membership: Quiet Winter 24, Restless Spring 26 (trio added), First Summer 6 (m56 added).

## What was added

| File | Change |
|---|---|
| `decay.py` | `SURPRISE_ENCODE_BOOST` / `SURPRISE_HALFLIFE_BOOST` / `CHAPTER_BREAK_THRESHOLD` constants; `initial_strength` takes surprise (clamped); new `effective_half_life(w, surprise)`; `decay_factor` three-factor; `advance_time` groups by surprise too; `recall` rows carry `surprise`; new `chapter_breaks(db)`. |
| `build_graph.py` | Event nodes store `surprise`; Memory nodes inherit it from the source event; Period build marks the chapter break (STARTS edge + `opened_by`) when the period's earliest event clears the threshold. |
| `demo.py` | Demo 5 gains **Act C — Surprise** (own store `./store_surprise`): the trio's encodings/effective half-lives, an 8-week trajectory printout, the chapter-break query, and the e21 counterexample. Demo 4's counterfactual passes surprise. One prose fix: the grudge's closing line no longer says "top of the haze" — with credit-theft rated surprise 0.55 it now ends CLEAR (0.722), and the line says so. |
| `tests/test_smoke.py` | Counts updated (23 events / 56 memories); provenance-properties check now includes `surprise`; P3 id set gains m56; new **SurpriseTest** (3 tests): surprise present everywhere and inherited unchanged from the root event; the trio diverges by surprise alone (mechanism asserted via `decay_factor`/`effective_half_life`, outcome via bands at 2026-06-29); chapter break marks only the surprising opening (e23→p3 via STARTS + `opened_by`; e21 at 0.95 has no STARTS edge; exactly one period is marked). **24/24 passing.** |
| `README.md` | New section **"Surprise: the chapter break"** (orthogonality, the two boosts and their asymmetry, event segmentation theory, the mid-chapter counterexample); formulas, graph model, dataset counts and layout updated. |

## Actual run output (verbatim excerpts)

Build:

```
Graph built in Relate store: .../store
  Person   nodes: 6     Place  nodes: 8     Item   nodes: 10
  Event    nodes: 23    Memory nodes: 56    Period nodes: 3
  Cue      nodes: 26    total edges: 319
chapter_breaks: [{'period_id': 'p3', 'period': 'The First Summer',
  'event_id': 'e23', 'title': 'A busker under the open window',
  'date': '2026-05-01', 'surprise': 0.9}]
periods opened_by: Quiet Winter None · Restless Spring None · First Summer 'e23'
```

Demo 5, Act C — the controlled experiment (same day, same emotion, only surprise differs):

```
  m53 (the weekly grocery run): emotion calm, surprise 0.05 -> encoded 0.577, effective half-life 34.3 days
  m54 (the bakery stall that lasted one day): emotion calm, surprise 0.95 -> encoded 0.989, effective half-life 85.4 days
  m55 (a replacement sponge): emotion calm, surprise 0.00 -> encoded 0.490, effective half-life 31.5 days

  week 1 (2026-05-11): m53 0.418 [HAZY] · m54 0.869 [CLEAR] · m55 0.345 [HAZY]
  week 3 (2026-05-25): m53 0.315 [HAZY] · m54 0.775 [CLEAR] · m55 0.253 [HAZY]
  week 4 (2026-06-01): m53 0.273 [HAZY] · m54 0.732 [CLEAR] · m55 0.217 [SUNK]   newly sunk: m55
  week 5 (2026-06-08): m53 0.237 [SUNK] · m54 0.692 [CLEAR] · m55 0.186 [SUNK]   newly sunk: m53
  week 8 (2026-06-29): m53 0.155 [SUNK] · m54 0.583 [CLEAR] · m55 0.117 [SUNK]

  The First Summer was opened by e23 — "A busker under the open window" (2026-05-01, surprise 0.90)
  The single most surprising event in the graph is e21 — "The bakery stall that
  lasted one day" (surprise 0.95) — and it opens nothing (STARTS edges from it: 0).
```

Regression notes (numbers that moved, honestly):

- Demo 4: m17 resurrected 0.236 → 0.694 (as v1.2); m29 reinforced 0.507 → 0.803; m48 resurrected 0.016 → 0.606, counterfactual now 0.011 (computed on the three-factor clock); totals `active 19 / sunk 33 / buried 4 of 56`.
- Demo 5: catch-up counts `CLEAR 15 / HAZY 28 / SUNK 9 / BURIED 4`; the same-day pair's tender half now ends the summer still in the haze (m50 0.303 [HAZY] vs m51 0.031 [SUNK] — in v1.2 it had just slipped under the line); rumination m49 0.573 → 0.915, ending CLEAR at 0.722; m36's layered sequence is **unchanged** (0.447 HAZY-2, 0.323 HAZY-3) because e14's surprise is 0.00; m52's ladder ends CLEAR 0.759 (was 0.757).
- Demo 6: both spreading-activation traces now run 20 steps and include the summer's new opening memory m56 (AFFECT at step 11 in Act 1, GIST by step 19–20); period gist assembles in both acts; the determinism re-run still reproduces the trace exactly (`True`).

## Design decisions & pitfalls (v1.3)

1. **Surprise lives on the event, not the memory.** Like emotion, it is a fact about what happened; memories inherit it through their root event (a test asserts the inheritance holds along every DERIVED_FROM chain). This keeps one source of truth and makes "how surprising was that day?" a graph question.
2. **The opener rule is "first event, if surprising" — not "most surprising event".** The naive alternative (earliest event above threshold) would have marked e21, the one-day bakery, as the Restless Spring's opener even though it happened seven weeks into that chapter. Segmentation cuts a stream at boundaries; mid-chapter, a shock is content, not a cut. The dataset keeps e21 as the standing counterexample, and a test pins both directions.
3. **Clamping s0 at 1.0 is a real modelling choice.** With three multiplicative factors, strongly-felt *and* shocking memories (m12, m49, m52…) would otherwise encode above 1.0 and break the reinforcement formula's "distance to 1.0" semantics. The cost — the very top of the encoding range compresses — is accepted and documented in `initial_strength`.
4. **In this Cypher dialect, `ORDER BY` cannot reference a node variable across an aggregating `RETURN`** (a scratch query grouping by period and ordering by `p.start_date` failed with "variable 'p' not defined", while the same ordering in `chapter_breaks`' non-aggregating query works). Worth remembering for future queries: order before you aggregate, or carry the sort key through the projection.


# v1.4 — Storage by chapter: periods as containers, not tags (2026-11-25)

Fri's direction: normal memory storage is organised *by time period* — a period should not be a label stuck on a memory, it should be the first-level container the memory is stored in. v1.2 had Period nodes and PART_OF edges inside one monolithic store; v1.4 makes "stored by period" true physically and at the query entry point.

## What changed

- **Physical partitioning.** `build_graph.build` now produces a *library* of Relate stores instead of one store:
  - `store/periods/<period-slug>/` — one directory store per period, holding that period's Events, Memories, Cues, its local copies of the Person/Place/Item nodes appearing in its scenes, and **all** intra-period edges, including complete DERIVED_FROM provenance chains. A memory is filed under the period of its chain's *root event*, so no chain ever dangles across stores.
  - `store/index/` — one index store holding only: Period nodes, canonical Person/Place/Item/Cue nodes, one `MemoryRef` stub per memory with a `FILED_IN` edge to its chapter, `APPEARS_IN` (entity → chapters), `FIRES_IN` (cue → chapters it is bound in), `BEFORE` chronology edges, `GAVE` edges (moved here: who-gave-what is a fact about people, not about any one chapter), and `DERIVED_FROM` edges between MemoryRef stubs for cross-period derivations (see routing below).
- **`library.py` (new).** A `Library` class opens the index plus one connection per period store and routes everything: `locate_memory` / `locate_periods` (by date, cue or entity, via the index), `db_for_memory`, fan-out helpers (`execute_all`, `execute_each`). All of `decay.py` and `demo.py` now speak to a `Library`, never to a bare store.
- **Period-first query entry.** Recall/decay/resurfacing first locate the chapter through the index, then work inside that chapter's store. `decay.recall` fans out over chapters and merges by priority; `cue_resurface` uses `APPEARS_IN` to open only the chapters where the person appears; `sense_resurface` uses `FIRES_IN` to go straight to the chapter(s) a cue is bound in. Cross-period movement is always explicit, over the index bridge.
- **Cross-period derivations.** The dataset's seven `derived_from_memory` pairs all turn out to be intra-period (a memory is filed with its root event's chapter, and every derivation chain in the data stays inside one chapter) — so zero derivation edges land in the index for this dataset. The routing is nevertheless general: `build_graph.route_derivations` classifies each pair by the two filing chapters, and a unit test feeds it a fabricated filing assignment to prove a cross-period pair would be routed to the index stubs. Recorded honestly rather than engineered into the fiction.
- **Demo 6 bridge damping.** Spreading activation now runs *inside* the chapter the cue landed in. Cross-period ABOUT edges no longer enter the diffusion graph at all (see pitfall 2); instead, after the within-chapter cascade settles, each shared-person link to another chapter transmits one **damped echo** = source activation × 0.55 (shared-person factor) × **0.30 (BRIDGE_FACTOR)**, capped at 0.165 — strictly below the 0.30 AFFECT threshold. Echoes prime; they never surface, and they do not spread onward. `spreading_activation` returns an `echoes` list, printed by the demo and asserted by tests.

## Distribution: before / after

Before (v1.3): one store, 319 edges total. After (v1.4): the same life, filed.

| Store | Events | Memories | Cues | Edges |
|---|---|---|---|---|
| periods/the-quiet-winter (p1) | 7 | 24 | 11 | 130 |
| periods/the-restless-spring (p2) | 13 | 26 | 9 | 143 |
| periods/the-first-summer (p3) | 3 | 6 | 6 | 44 |
| index | — | 56 MemoryRef stubs | 26 canonical | 124 |
| **total** | **23** | **56** | — | **441** |

Reconciliation against the monolith: partition edges 317 + the 2 GAVE edges that moved to the index = 319, exactly the v1.3 total. The remaining index edges (122) are the new map structure itself: FILED_IN stubs, APPEARS_IN, FIRES_IN, BEFORE, canonical-entity TRIGGERED_BY for period cues.

## Actual run output (verbatim excerpts)

Build:

```
{'Person': 6, 'Place': 8, 'Item': 10, 'Cue': 26, 'Event': 23, 'Memory': 56,
 'Period': 3, 'edges': 441,
 'partitions': {'p1': {... 'Event': 7, 'Memory': 24, 'Cue': 11, 'edges': 130},
                'p2': {... 'Event': 13, 'Memory': 26, 'Cue': 9, 'edges': 143},
                'p3': {... 'Event': 3, 'Memory': 6, 'Cue': 6, 'edges': 44}},
 'index': {'Period': 3, 'Person': 6, 'Place': 8, 'Item': 10, 'Cue': 26,
           'MemoryRef': 56, 'edges': 124},
 'cross_period_derivations': []}
```

Demo 6, Act 1 — the bridge, quantified (the within-chapter trace is unchanged: 20 steps, memory steps all inside p3, re-run reproduces it exactly):

```
  (re-running the same cue reproduces the identical trace: True)
  Across the bridge, the strongest echo reached m11 in p1 at 0.165 — primed by June, but below the 0.3 surfacing threshold: an echo is not a return.
```

Regression notes (v1.3 numbers, re-verified on the new layout — none moved):

- Demo 3: graph 6/6, keyword baseline 3/6. Demo 4: catch-up `active 43 / sunk 9 / buried 4`; 12-week totals `active 19 / sunk 33 / buried 4 of 56`; reunions m17 0.236 → 0.694, m29 0.507 → 0.803, m48 0.016 → 0.606.
- Demo 5: catch-up `CLEAR 15 / HAZY 28 / SUNK 9 / BURIED 4`; rumination m49 0.573 → 0.915, ending CLEAR 0.722; m52's ladder ends CLEAR 0.759. Surprise trio (Act C): m54 ends week 8 at **0.583 [CLEAR]**, m53 sunk week 5, m55 sunk week 4 — identical to v1.3.
- Demo 1 audit on the partitioned layout: 56/56 memories trace to a source event, each chain walked inside its own chapter.

## Design decisions & pitfalls (v1.4)

1. **Relate multi-store behaviour, measured (scratch experiments, 2026-11-25).** Distinct stores can be opened simultaneously and interleaved freely. The *same* store refuses a second simultaneous connection ("storage locked"). So `Library` holds exactly one connection per store (4 total) and never re-opens; there is no workaround and none was faked — the limitation is documented in the README's storage section.
2. **First bridge design was wrong, and visibly so.** The initial implementation put cross-period ABOUT edges into the diffusion graph with factor 0.55 × 0.30 = 0.165 per hop. Result: activation saturated the whole graph — 165 steps, all 56 memories surfaced. A per-hop factor below threshold does not stay below threshold when hundreds of paths accumulate. Replaced by the echo model: bridge links are not diffusion edges at all; each transmits one capped echo after the cascade settles. Demo 6 went back to exactly 20 steps per act, with echoes reported separately.
3. **Filing rule: a memory belongs to its root event's chapter.** Filing by `recorded_at` instead would have split derivation chains whose parent was recorded in an earlier chapter than its child. Root-event filing keeps every provenance chain whole inside one store — the invariant the audit demo depends on — at the cost of a memory occasionally being *recorded* a few days into the next chapter while filed in the previous one. Accepted and documented.
4. **Entity nodes are duplicated per chapter on purpose.** Each partition carries local copies of the people/places/items in its scenes (same stable ids); the index carries the canonical set. The copies are what make a chapter self-contained and auditable in isolation; the index copies are the join points. No write path updates one without the other — the dataset is static after build, and decay only ever writes Memory state inside the owning chapter.

# v1.5 — Detail weights: the haze sheds by weight, not by kind (2026-11-25)

Fri's second correction, landed on top of v1.4 in the same round: the v1.2 haze used one global template (times die first, then names, then only the gist). Real forgetting doesn't work by category. Each detail has its own **detail weight** — how strongly that detail is bound to emotion — and details fall in ascending weight order. So one memory loses its date and keeps a name to the end (the person was the point), while another loses the name and keeps the place (the place was the anchor).

## What was added

- **Detail inventories (`build_graph.detail_inventories`).** For every memory, its structured details — `when` (the source event's date), `who` (scene people, Theo excluded), `where` (the scene's place), `what` (involved items) — get a weight:
  - entity stickiness `S = 0.30 + 0.70 × (mean of the top-3 emotion_weights among memories bound to that entity) / 1.8`;
  - `when = 0.15 + 0.55 × surprise` of the source event;
  - `who = S(person) − 0.02`; `where = S(place) + 0.04`, floored at 0.85 if the place hosted a chapter-opening (STARTS) event; `what = S(item) + 0.10 if the item was given as a gift`.
  - The inventory is sorted ascending by weight (ties: slot fragility `when < what < where < who`, then label) — that sorted list *is* the shedding sequence. It is stored on the Memory node as `detail_spec` (JSON), in the memory's own chapter store, alongside the renderings it generates.
- **Per-memory haze renderings.** Following the standing rule (renderings are pre-stored at build; recall selects, never rewrites), each memory now carries its own `haze1/haze2/haze3`: the gist plus a `[still held: …]` list of its 3 / 2 / 1 heaviest details, heaviest last in the shedding order and first in the list. `decay.recall` returns these for the HAZY layers instead of the old global `vague`/`anon`/`gist` progression. The `vague`/`anon` renderings remain stored and are still used by the resurfacing ladder and period gists. Because renderings are properties on the Memory node, they automatically live in the memory's own partition (v1.4 interaction, as specified: renderings are filed with their memory).
- **Demo 5 dual case.** The longitudinal thread is now two memories in parallel — m12 (the red scarf) vs m42 (the promotion dinner) — with the shed/held lists printed at each stage.
- **Tests.** `DetailWeightTest` pins: every memory's inventory is weight-ascending; m12's and m42's shedding orders are opposite in kind (person-anchor vs place-anchor); at HAZY-3 each memory's recall content contains exactly its anchor and none of its shed details; at HAZY-2 exactly its two heaviest.

## The dual case, from the data (detail_spec, ascending = shedding order)

```
m12 (June's red scarf, recorded 2026-02-15):
  when 2026-02-14 (0.480) → where Night Market (0.807) → what red scarf (0.867) → who June (0.980)
m42 (Marco's promotion dinner, recorded 2026-04-04):
  when 2026-04-03 (0.343) → what gray umbrella (0.728) → who Marco (0.773) → where Café Lumen (0.833)
```

## Actual run output (verbatim excerpts)

Demo 5, the dual case across the summer:

```
  m12 as the summer begins — June's name is the anchor — it is held to the end:
      [HAZY-1 · strength 0.543] "A friend once surprised Theo with a thoughtful winter gift. [still held: June · red scarf · Night Market] (hazy — the lightest details have faded)"
      shed so far: when:2026-02-14 (0.48)
      still held: where:Night Market (0.81), what:red scarf (0.87), who:June (0.98)
  m42 as the summer begins — the café is the anchor — the name goes before it:
      [CLEAR · strength 0.564] "Marco treated Theo to dinner at Café Lumen to celebrate his promotion."

    dual case, week 8 — m12:
      [HAZY-2 · strength 0.384] "A friend once surprised Theo with a thoughtful winter gift. [still held: June · red scarf] (hazy — only the heaviest details remain)"
      shed so far: when:2026-02-14 (0.48), where:Night Market (0.81)
    dual case, week 8 — m42:
      [HAZY-3 · strength 0.349] "A coworker once treated Theo to a meal to celebrate good news. [still held: Café Lumen] (hazy — only the outline and its anchor detail remain)"
      shed so far: when:2026-04-03 (0.34), what:gray umbrella (0.73), who:Marco (0.77)
      still held: where:Café Lumen (0.83)

    dual case, week 12 — m12:
      [HAZY-3 · strength 0.323] "A friend once surprised Theo with a thoughtful winter gift. [still held: June] (hazy — only the outline and its anchor detail remain)"
      shed so far: when:2026-02-14 (0.48), where:Night Market (0.81), what:red scarf (0.87)
      still held: who:June (0.98)
```

## Behaviour change, recorded honestly

- **Keyword recall now also matches the haze renderings** (recall matches text/vague/anon/gist and now haze1–3). One visible consequence: `recall("café")` now returns m49 (the grudge) at the top — its held place detail is Café Lumen, and its priority (strength 1.0 × weight 1.6) outranks the pleasant café memories. The priority mechanism is unchanged; the match surface grew. Demo 5's opening narration and the corresponding test were updated to say so, rather than quietly re-tuned.
- Haze *strength trajectories* are untouched: every band crossing date asserted by the v1.2/v1.3 tests (m35's three layers, m36's descent, the m50/m51 divergence, the surprise trio) still occurs on the same dates. Only the *content* of the hazy layers changed — from a global template to each memory's own weight order.
- Test count: 24 (v1.3) → **33**, all passing: the nine additions are the three DetailWeightTest cases and the six StoragePartitionTest cases (single-filing invariant, intra-chapter chain completeness, cross-period derivation routing incl. a fabricated-assignment unit test, index-contents, cross-chapter cue reach on a synthetic two-chapter dataset, bridge-echo damping bound).

# v1.6 — Habits & ceilings: routine merging, cue dilution, rumination exhausts (2026-11-25)

Source: Fri approved three of Gemini's critique-round suggestions on
2026-11-25 ("按建议补三项"): repeated days should collapse into a
habit; a cue reused too often should blunt; a grudge rehearsed
forever should tire. All three are implemented in code and Cypher,
tested, and demoed as the new Demo 7. Nothing here is documentation
only.

## 1. Routine merging (ROUTINE_SUMMARY)

**Grouping (build time, deterministic).** `build_graph.routine_groups`
buckets memories by (chapter, source place, emotion pathway). A
memory joins a bucket only if it is an episode, is not SECRET_LOCKED,
and its source event's surprise ≤ 0.2. A bucket becomes a routine at
≥ 4 members whose emotion weights agree within 0.15. One pathway is
excluded outright — `buried`: a suppressed memory must never be
summarised, because a summary naming its days would leak exactly
what suppression holds down (and the drill-down would hand the days
back). Facts, preferences, plans and secrets are not episodes and
never merge.

**Materialisation.** Each routine becomes a `Routine` node in its
chapter's store: `{id, place, pathway, text, gist, member_count,
first_date, last_date}`, with `(Routine)-[:SUMMARIZES]->(Memory)`
edges, `(Routine)-[:PART_OF]->(Period)`, and `routine_id` set on
each member. The summary text is a pre-stored rendering composed
from the data — the same zero-fabrication rule as every other
rendering in the system: recall selects it, never rewrites it.
Originals are untouched: text, strength, provenance chains all stay.

**Recall.** `decay.recall(..., routines=True)` (the default) runs
the normal band/privacy pipeline, then collapses: when ≥ 2 unstaged
members of one routine survive, they are replaced by a single
`form: "routine"` entry carrying the stored summary, inheriting the
strongest member's band/strength/priority. A lone surviving member
answers individually. `routines=False` returns the un-merged members
(the demo's "before"). `decay.routine_drilldown(lib, routine_id)`
walks the SUMMARIZES edges and returns every member in event-date
order with full text, strength and band — the name-a-day path.

**Dataset addition （备案）.** The shipped dataset had no group of
≥ 4 similar low-surprise days at one place, so five were added:
Saturday reading visits to Harbor Bookstore (events e24–e28,
2026-03-07 → 2026-04-25, calm, surprise 0.05; memories m57–m61).
Together with the pre-existing m20 (2026-03-15, same place, calm,
surprise 0.1) they form the dataset's single routine:
`routine-p2-harbor-bookstore`, 6 members. Dataset is now 28 events /
61 memories. The new memories bind only Harbor Bookstore + Theo with
detail weight 0.7, so no existing detail_spec, haze layer, or
stickiness value changed (verified: Harbor Bookstore stickiness
0.572 before and after; all v1.2–v1.5 haze assertions pass
unchanged).

## 2. Cue dilution

Cue nodes still never decay. What changed is what a firing delivers:
`decay.cue_energy(lib, label) = 1.0 / degree`, degree = the number
of TRIGGERED_BY bindings to that cue label across the whole library
(memories and periods, read live via the partitions). Three landing
spots:

- **Sensory resurfacing** (`sense_resurface`): entry strength is now
  `RESURFACE_STRENGTH × energy` (0.40 at degree 1). A *sunk* memory
  is raised at any energy — but a diluted entry (0.10 at degree 4)
  sits below the sinking line, so once its AFFECT→FRAGMENT ladder
  clears, the story never assembles and it slips back under. A
  *buried* memory additionally requires `energy ≥ 0.5`
  (`BURY_MIN_ENERGY`) to be raised at all; below that the result is
  reported as stirred (`raised: False`) and the graph is untouched.
- **Spreading activation**: the injection is `SPREAD_INJECT ×
  energy`. Both Demo 6 cues have degree 1, so Demo 6's traces are
  bit-identical to v1.2 (verified by re-running them).
- **Life-cue resurfacing** (a person returning) is deliberately NOT
  diluted: a person is not a smell, and her return does not divide.

Real-data degrees: `taste:ginger tea` = 1, `scent:darkroom
chemicals` = 1, `scent:fresh mint` = 2 (bound to m01 and m48).

## 3. Rumination ceiling & exhaustion

`Memory.ruminate_count` (initialised 0 at build) counts grudge
rehearsals. `decay.rumination_gain(count)`: rehearsals 1–3 gain the
full 0.8; afterwards the gain halves per rehearsal (0.4, 0.2, 0.1,
0.05, … → 0). After any grudge boost, strength is clamped to
`RUMINATION_CAP = 0.85` — a grudge can be kept hot but never again
at the 1.00 of its first burn. `reinforce` returns `ruminate_count`
and `capped` alongside the existing fields. Non-grudge pathways are
unchanged; the v1.2 assertion "first rumination gains the full 0.8"
still holds by construction.

Measured trajectory (Demo 7, Act 3; m49 mentioned eight times):

```
2026-06-29  mention #1: 0.573 + gain 0.800 → 0.850  ← pinned at the ceiling
2026-07-13  mention #2: 0.794 + gain 0.800 → 0.850  ← pinned at the ceiling
2026-07-27  mention #3: 0.794 + gain 0.800 → 0.850  ← pinned at the ceiling
2026-08-10  mention #4: 0.794 + gain 0.400 → 0.850  ← pinned at the ceiling
2026-09-07  mention #5: 0.742 + gain 0.200 → 0.794
2026-10-05  mention #6: 0.693 + gain 0.100 → 0.724
2026-11-02  mention #7: 0.632 + gain 0.050 → 0.651
2026-12-07  mention #8: 0.549 + gain 0.025 → 0.561
```

## Actual run output (verbatim excerpts, Demo 7)

```
ACT 1 — six afternoons that were all the same afternoon.
  un-merged recall: 9 answers
    m20   [full] Ana and Theo spent a slow afternoon at Harbor Bookstore; she tal
    m57   [full] Theo spent Saturday afternoon reading at Harbor Bookstore, worki
    ... (m58, m60, m59, m61 likewise; m21, m41, m22 also answer)
    ROUTINE [CLEAR] During The Restless Spring, visits to Harbor Bookstore settled into a routine: 6 similar days between 2026-03-07 and 2026-04-25, each one unremarkable on its own, together a habit.
      (6 of 6 days standing behind this one answer, 2026-03-07 → 2026-04-25)
  Name a day, though, and the habit opens back up:
    2026-03-07  m57   [CLEAR] ...
    2026-03-15  m20   [CLEAR] ...
    2026-03-21  m58   [CLEAR] ...
    2026-04-04  m59   [CLEAR] ...
    2026-04-11  m60   [CLEAR] ...
    2026-04-25  m61   [CLEAR] ...

ACT 2 — the smell that meant one thing, then four.
  By 2026-08-17 the film memory (m10) has sunk to 0.159.
  The cue 'scent:darkroom chemicals' is bound to 1 memory — energy 1.00, undiluted. It fires:
    m10 raised at 0.40 [AFFECT] — the feeling first, as always.
  Two mentions climb the ladder, and the story assembles: [thinned] at 0.40.
  By 2027-01-15 it has sunk again (0.117). ...
    degree 4 → energy 0.25. The same cue fires again:
    m10 raised at 0.10 — a quarter of the strength (and 3 other sunk memories lifted just as weakly; dilution is indiscriminate).
  A mention brings the shard: [fragment]. One more —
  and the ladder clears onto a strength of 0.10, below the 0.25 sinking line: recall returns nothing.
```

## Assertion changes, each with its reason （备案）

- `test_node_counts`: Event 23 → 28, Memory 56 → 61 (the five added
  routine days, above); added `Routine == 1`.
- `test_every_memory_filed_in_exactly_one_partition`: 56 → 61 filed
  memories; `test_index_holds_the_map_not_the_life`: MemoryRef count
  56 → 61. Same reason — dataset grew, invariant unchanged.
- `test_every_memory_has_provenance_properties`: the NULL-check now
  also covers `ruminate_count` (new build-time property on every
  Memory).
- No other existing assertion changed. All six prior demos run
  end-to-end unmodified in narration; Demo 5's grudge now ends the
  summer at 0.671 [CLEAR] instead of v1.3's 0.722 — the expected,
  mechanical consequence of the 0.85 cap on its week-5 rumination
  (0.915 uncapped → 0.850 capped, then the same decay). Demo 4/5
  weekly band counts shift by the five new calm memories sinking on
  schedule; all printed numbers are computed live.
- Graph totals: edges 441 → 478 (+10 event edges, +15 memory edges,
  +6 SUMMARIZES, +1 routine PART_OF, +5 FILED_IN, reconciles
  exactly). Partitions: p1 Event 7 / Memory 24 / Cue 11; p2 Event 18
  / Memory 31 / Cue 9 / Routine 1; p3 Event 3 / Memory 6 / Cue 6.

## Tests

33 → **43, all passing**. New: RoutineTest (4 — deterministic
grouping, node materialised with members' provenance intact, recall
collapse with non-episodes unmerged, drill-down in date order),
DilutionTest (4 — degree/energy on the real graph, degree-1 cue
raises a buried memory, degree-4 cue only stirs buried memories and
leaves them buried, diluted sunk resurface slips back after the
ladder while the degree-1 control holds at HAZY), RuminationTest
(2 — exact gain schedule + persisted count + cap never exceeded,
and the burn-out: pinned at the ceiling for four mentions, then
declining despite continued mentions).

## Design decisions & pitfalls (v1.6)

- **Merging happens at build time, collapsing at recall time.** The
  Routine node is a graph fact (auditable, inspectable in the
  store); the collapse is a recall policy (routines=False shows the
  members). Putting the merge in the graph keeps the zero-fabrication
  audit story intact — the summary is data, not a runtime invention.
- **Collapse threshold ≥ 2 surviving members.** If only one day of a
  habit is still above the haze, that day answers as itself; the
  habit summary would claim a pattern the recall can no longer show.
- **Staged members never collapse.** A memory mid-resurfacing
  (AFFECT/FRAGMENT) is an event in progress, not a day in a habit;
  it keeps its staged form until the ladder resolves.
- **The buried-pathway exclusion was a real catch, not decoration.**
  Without it, four shameful evenings at one place satisfy every
  grouping rule, and the drill-down would have handed suppressed
  memories back through a side door. Burial and routine are opposite
  fates; the grouping now keeps them so, and a test dataset with
  four buried memories on one cue verifies they stay buried.
- **Dilution divides energy, not the cue.** The cue node and its
  edges are untouched by firings — degree changes only when life
  binds the cue to something new. That keeps the mechanism
  compatible with the v1.4 index (FIRES_IN unchanged) and with the
  "cues persist" claim: what persists is the key; what fades is how
  much of it is left for any one lock.
