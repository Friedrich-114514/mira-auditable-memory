# Auditable Graph Memory for a Companion Agent

**An agent memory layer in Relate where every memory is a node in a graph of witnessed events — so anything the agent "remembers" can be audited back to its source, secrets stay locked by construction, recall works by walking relationships instead of guessing at text, and memories fade the way a person's do: through a haze, at a speed set by emotion and surprise — sunk or buried, never deleted, and sometimes brought back by a taste, a song, or a person walking through the door.**

Built for the Relate by Neo4j Hackathon ("Context, Connected"). The companion agent (**Mira**) and her human (**Theo**) are fictional; the dataset in `data/memories.json` is fully synthetic and contains no real person's data.

## Why Relate — and why directory mode

Companion agents live or die by their memory, and today's default memory layer — a vector store over text chunks — has three structural problems:

1. **No provenance.** A retrieved chunk cannot say *which lived event* it came from, so the agent cannot distinguish something that happened from something it hallucinated.
2. **No real privacy.** "Secret" memories sit in the same index as everything else; one careless similarity search leaks them.
3. **No relationships.** "Who was with him the last time he felt down, and where did they go?" is a join over people, dates and places — not a text search.

Relate fixes all three by making memory a property graph queried in Cypher: provenance is an edge you can walk, privacy is a property every recall query must filter on, and multi-hop questions are traversals.

We use Relate in **directory mode** rather than `:memory:` or a single `.rl` file because the store is the deliverable: it persists on disk, judges can open and inspect the exact graph the demo queries, and it survives restarts of the agent — which is the whole point of a memory layer. It still runs fully in-process: no server, no signup, one `pip install`. And the store is not one graph but a small library of them — one per chapter of life, plus an index (next section).

## Quick start

```bash
pip install -r requirements.txt   # just relate-db
python build_graph.py             # builds the persistent graph into ./store
python demo.py                    # runs all seven showcases
python -m unittest discover -s tests -v   # smoke tests
```

## Storage: life is filed by chapter

A period is not a tag on a memory. It is the **first-level container** the memory is stored in — the way a life is actually filed. The build therefore produces not one Relate store but a small library (`library.py` opens and routes across all of them):

```
store/
  index/                       one Relate store: the map, not the life
    Period nodes (the chapters themselves)
    canonical Person / Place / Item / Cue nodes
    MemoryRef stubs — one per memory, with a FILED_IN edge to its chapter
    APPEARS_IN   person/place/item -> the chapters they appear in
    FIRES_IN     cue -> the chapters it is bound in
    GAVE         person -> item (period-independent facts)
    BEFORE       chapter -> next chapter (the chronology)
    DERIVED_FROM between MemoryRef stubs — only for the rare derivation
                 whose parent was filed in another chapter
  periods/
    the-quiet-winter/          one Relate store per chapter
    the-restless-spring/       that chapter's Events, Memories and Cues,
    the-first-summer/          its local copies of the people, places and
                               items in its scenes, and every edge between
                               them — including the full DERIVED_FROM
                               provenance chains
```

**The index holds the map; the chapters hold the life.** Inside a chapter's store, everything is complete and self-contained: a memory's provenance chain can be audited end-to-end without ever leaving its chapter (a memory is filed under the chapter of its chain's root event, so chains never dangle). The index knows *where* everything is and how the chapters relate — chronology, who appears where, which cues fire where, who gave what — but it contains no memory text and no events.

**Queries enter period-first.** Recall, decay and resurfacing all start by locating the chapter — by date, by cue, by entity — through the index, and only then open that chapter's store and work inside it. Crossing chapters is possible, but it is never free or silent: it goes **over the bridge**, through the index, explicitly. That is the shape of real recall. Inside one season of life, one thing leads to the next effortlessly; reaching *across* seasons takes a cue, a person, a deliberate effort — you have to turn the page.

The bridge has a number on it. In spreading activation (Demo 6), activation cascades freely *inside* a chapter, but a shared-person link across a chapter boundary transmits only a **damped echo**: source activation × 0.55 (the shared-person factor) × **0.30 (the bridge factor)** — a maximum of 0.165, deliberately below the 0.30 surfacing threshold. An echo can prime a memory in another chapter; it can never, by itself, bring it back. Cross-period recall is harder than within-period recall, and now that is not a metaphor — it is a coefficient.

One honest limitation, recorded rather than worked around: Relate refuses a second simultaneous connection to the *same* store ("storage locked"), so the `Library` class holds exactly one connection per store — four in total (index + three chapters) — and routes every query through them. Distinct stores can be open side by side without restriction, which is what makes the layout possible at all.

## The graph model

```
— inside each chapter's store (the life itself):
(Event)  -[:EXPERIENCED_AT]-> (Place)
(Event)  -[:WITH]-> (Person)
(Event)  -[:INVOLVES]-> (Item)
(Memory) -[:DERIVED_FROM]-> (Event)    provenance: the witnessed source
(Memory) -[:DERIVED_FROM]-> (Memory)   provenance: generalised from an earlier memory
(Memory) -[:ABOUT]-> (Person)
(Memory) -[:INVOLVES]-> (Item)
(Memory) -[:TRIGGERED_BY]-> (Cue)      sensory anchors, bound at encoding time
(Period) -[:TRIGGERED_BY]-> (Cue)      an era has its signature senses too
(Event)  -[:PART_OF]-> (Period)        Conway: periods above events
(Memory) -[:PART_OF]-> (Period)
(Event)  -[:STARTS]-> (Period)         a high-surprise opening event:
                                        the chapter break
(Routine) -[:SUMMARIZES]-> (Memory)    the habit standing over its days
(Routine) -[:PART_OF]-> (Period)       a routine belongs to one chapter

— in the index store (the map between chapters):
(Person) -[:GAVE]-> (Item)             who gave what: a fact about people,
                                        not about any one chapter
(Person|Place|Item) -[:APPEARS_IN]-> (Period)
(Cue)    -[:FIRES_IN]-> (Period)       where a cue can reach
(MemoryRef) -[:FILED_IN]-> (Period)    where each memory is filed
(Period) -[:BEFORE]-> (Period)         the chronology
(MemoryRef) -[:DERIVED_FROM]-> (MemoryRef)
                                        only when a derivation crosses
                                        chapters (none in this dataset —
                                        the routing exists and is tested)
```

- **Person** `{name, role, note}` — Theo and the people around him.
- **Place** `{name, kind}` — café, chess club, lakeside park, …
- **Item** `{name, kind}` — a red scarf, a brass compass, an unsent letter, …
- **Event** `{id, title, date, mood, surprise, tags[]}` — things that actually happened, witnessed by the agent. `surprise` ∈ [0, 1] is how far the event sat from what was expected — prediction error, recorded independently of how it felt.
- **Period** `{id, name, start_date, end_date, atmosphere, dominant_emotion, opened_by}` — a chapter of life ("The Quiet Winter", "The Restless Spring", "The First Summer"). Every event and memory belongs to exactly one, by date — and is physically stored in that chapter's own store. `opened_by` names the event that broke the chapter open, when there was one (below).
- **Cue** `{kind, label}` — a sensory anchor (`taste:ginger tea`, `song:…`, `scent:…`), shared by the memories and periods bound to it. Cue nodes are never decayed or deleted. A cue lives in the chapter(s) where it is bound, and in the index, whose `FIRES_IN` edges say which chapters it can reach into.
- **Memory** `{id, text, vague, anon, gist, shard, haze1, haze2, haze3, detail_spec, kind, privacy, confidence, recorded_at, source_event, emotion, valence, pathway, emotion_weight, surprise, strength, encoded_strength, salience, last_accessed, strength_as_of, sunk, buried, ruminate_count, routine_id, sensory_cues[]}` — what the agent distilled from events, stored at **four resolutions** (full text, `vague`, `anon`, `gist`) plus one isolated `shard`, plus its three **per-memory haze renderings** (`haze1`–`haze3`) and the `detail_spec` they were generated from (below). All renderings live in the same chapter store as the memory itself. **Every** Memory carries provenance properties (`source_event`, `recorded_at`, `confidence`), a privacy level (`PUBLIC`, `PRIVATE`, or `SECRET_LOCKED`), an emotion profile inherited from its source event, that event's `surprise`, and the forgetting state described below. Two properties belong to the v1.6 mechanisms: `ruminate_count` (how many times a grudge has been rehearsed — the rumination ceiling below) and `routine_id` (set only on memories a Routine summarises).
- **Routine** `{id, place, pathway, text, gist, member_count, first_date, last_date}` — a habit, materialised at build time over a group of near-identical days (below). Its `text` and `gist` are pre-stored renderings composed from the dataset, like every other rendering in this system: recall selects them, never rewrites them. A Routine lives in its chapter's store, summarises its member memories there, and deletes nothing.

The dataset holds 6 people, 8 places, 10 items, 28 events, 3 periods, 26 cues and 61 memories spanning five months of fictional everyday life: work, family, a chess club, gifts, plans — and three secrets.

## The seven showcases (`demo.py`)

1. **Provenance audit.** The agent claims *"Theo takes his mint tea with honey, never with sugar."* The demo walks its `DERIVED_FROM` chain — memory → earlier memory → the witnessed café event, complete with date, place and who was present — then audits the whole graph: 61/61 memories trace back to a source event. If there is no path, there is no memory.

2. **Privacy tiers.** *"What is Theo keeping in his desk drawer?"*
   - A **naive** recall (no filter) leaks the `SECRET_LOCKED` memory — this is what an unguarded memory layer does.
   - The **default** recall hard-filters `SECRET_LOCKED`; the agent can acknowledge the evening ("he stayed up late writing") without revealing its content.
   - The **unlock path** (demo passphrase `mira-unlock-demo`, `--wrong-passphrase` shows rejection) is the only route to locked content.

3. **Graph recall vs. keyword search.** Six questions that need multi-hop reasoning — latest-by-date ordering, gift chains, place aggregation, plan→item joins — are answered by Cypher traversals and by a naive keyword-overlap baseline over the same memory texts. The graph answers 6/6; the baseline only finds what a single memory's text happens to spell out.

4. **Forgetting curve.** In its own store (`./store_decay`, so showcases 1–3 stay pristine), time advances 12 weeks. Untouched memories decay and sink below the recall threshold; three "reunion" moments recall specific memories back — each one already sunk, each one resurrected by being lived again. A plan whose deadline passes while sunk is the closing cautionary tale; Demo 1's honey preference, saved by a single reunion, is the counterpoint.

5. **The fuzzy band & the emotion pathways.** The same twelve weeks in `./store_haze`, watched through the unified banded recall: two memories fade side by side and shed *different* details in *opposite* orders — one holds a person's name to the deepest layer, the other holds a place and lets the name go — because details fall in order of their own emotional weight, not of their kind; a same-day pair — a tender phone call vs. a spare charger — diverges purely by emotional weight; a grudge is ruminated back to the top of the haze. Then the buried box opens: the vulnerable memories that were pushed down whole are raised by a person's return (the index says which chapters she appears in; each is opened in turn), and a shame memory that no person could reach is brought back by the taste of ginger tea — up the ladder, one rung per mention: affect, fragment, gist, clarity. A final act (in `./store_surprise`) isolates the orthogonal axis: three memories of one calm Friday — a grocery run, a bakery stall that existed for exactly one day, a replacement sponge — identical in emotion, differing only in surprise; eight weeks on, the shocking one has never left CLEAR while the other two are sunk. And the summer itself turns out to have been opened by a surprise: the chapter break, marked in the graph.

6. **Periods & spreading activation.** In `./store_period`, a period recall answers "what was the quiet winter like?" with atmosphere and strongest gists. Then two cues hit the same summer: a taste of morning coffee lands on one memory (a shard first, the era opening around it), and the summer's song lands on the period itself (the atmosphere first, shards raining after). Activation cascades inside the summer chapter only; across the bridge to earlier chapters, June's and Ana's links carry nothing but damped echoes (≤ 0.165, below the surfacing threshold) — printed, quantified, and never enough to surface anything. Both traces print every threshold crossing with its activation value, and the demo verifies that re-running a cue reproduces its trace exactly.

7. **Habits & ceilings.** Three acts on the v1.6 mechanisms. *The routine:* asked about the bookstore, un-merged recall returns nine answers, six of them near-identical Saturday afternoons; merged recall returns the Routine's one stored summary instead — and the drill-down then lists every afternoon by date, each still chained to its own event. *The blunted key* (in `./store_dilution`): the smell of darkroom chemicals, bound to one memory, raises it whole up the ladder; months later the same smell has been reused for three more memories, and the same firing sets the film memory down at 0.10 — the feeling and the shard surface, the story never assembles. *The tired grudge* (in `./store_rumination`): the angry memory from showcase 5 is mentioned eight times; the first four mentions pin it to its 0.85 ceiling, then the rehearsal dividend halves away and — still mentioned — it declines. Anger burns hot, then burns out.

## Forgetting: sunk, not deleted — and faded through a haze

Memory strength is an ordinary property on every Memory node, updated by ordinary Cypher — so even forgetting stays auditable (`decay.py`):

- **Initial strength:** `s0 = confidence × salience(source event) × (1 + 0.6 · surprise)`, clamped to 1.0, where salience scores how emotionally charged the witnessed event was (a heavy or vulnerable moment starts at 1.0; a calm one at 0.7) and `surprise` is the event's prediction error (the "Surprise" section below). The encoding strength is kept separately as `encoded_strength`; some later decisions key on how strongly a memory was *formed*, not how strong it happens to be now.
- **Decay:** exponential — `s(t) = s_anchor · 2^(−Δdays / (45 · emotion_weight · (1 + 1.8 · surprise)))`, where the anchor is the strength's `strength_as_of` date and `emotion_weight` and `surprise` each stretch or compress the clock on their own axis.
- **Reinforcement:** recalling a memory (it is used, mentioned, lived again) sets `s ← s + (1 − s) · 0.6` and updates `last_accessed`. One recall can resurrect a sunk memory.
- **Sinking:** below strength **0.25** a memory is flagged `sunk`. Default recall then stops surfacing it — the filter stacks on top of the privacy filter (default recall excludes `SECRET_LOCKED` *and* sunk; reaching a forgotten secret takes the unlock path *plus* an explicit `include_sunk`). But nothing is ever deleted: the node, its strength, and its full `DERIVED_FROM` provenance chain remain in the graph, and the provenance audit — which ignores recall filters by design — can still walk a sunk memory back to its witnessed event.

That is the base philosophy in one line: **sunk, not deleted** — forgetting changes what the agent can recall, never what it can prove.

### The fuzzy band: details fall in order of weight, not of kind

Recall is not binary above the sinking line either. The unified recall (`decay.recall`) answers in three bands, and the middle band is *layered* — because human memory does not switch off at a threshold, it fades through a haze. Fuzzy-trace theory (Reyna & Brainerd) says verbatim traces decay fast while gist traces endure. But *which* details go first is not a property of their kind. There is no global template that says dates die before names: one memory loses its date and keeps a name to the very end, because the person mattered; another loses the name early and keeps the place, because the place was the anchor. **Details fall in order of weight, not of kind.**

Every structured detail of a memory — its `when`, its `who` (the people in its scene, Theo excluded: the self is not a detail), its `where`, its `what`s (the items it involves) — carries a **detail weight**: the strength of that detail's binding to emotion, derived at build time (`build_graph.detail_inventories`) and stored on the node as `detail_spec`:

- an entity's **stickiness** is `0.30 + 0.70 × (mean of the three largest emotion_weights among memories bound to that entity) / 1.8` — a person or place is as heavy as the most emotional memories tied to them;
- `when` is light and mostly surprise-driven: `0.15 + 0.55 × surprise` (an unexpected day is itself memorable; an ordinary date is the first thing to go);
- `who` = the person's stickiness − 0.02; `where` = the place's stickiness + 0.04 — and a place where a chapter *broke open* (a `STARTS` event) is floored at 0.85: the scene of a turning point is an anchor by construction;
- `what` = the item's stickiness, + 0.10 if the item was a gift (things given carry the giver).

Inside the haze, details shed in ascending weight order (ties broken by a fixed slot fragility: `when` < `what` < `where` < `who`):

| Strength | Band / layer | What recall returns |
|---|---|---|
| ≥ 0.55 | **CLEAR** | the full text, plus detail fields (recorded_at, source_event, confidence) |
| 0.45 – 0.55 | **HAZY-1** | the memory's own `haze1` rendering: the gist plus its **3 heaviest** details, named |
| 0.35 – 0.45 | **HAZY-2** | its `haze2` rendering: the gist plus its **2 heaviest** details |
| 0.25 – 0.35 | **HAZY-3** | its `haze3` rendering: the gist plus its single heaviest detail — **the anchor** |
| < 0.25 | **SUNK** | nothing (provenance audit still reaches it) |

Demo 5 runs the dual case side by side. The night June gave Theo the red scarf sheds *date → market → scarf* and holds **June** to the deepest layer — the person is the anchor. Marco's promotion dinner sheds *date → umbrella → Marco himself* and holds **Café Lumen** — the place is the anchor. Same haze, same machinery, opposite orders, each explained by its own weights.

Every layer is a rendering stored on the node when the memory was fresh, generated from that memory's own weight order. Recall *selects* a version; it never rewrites text at recall time — blurring on the fly would mean inventing judgements about what is and isn't still known. A faded memory may be **omitted down, layer by layer — never expanded back** into detail the agent no longer holds. That is the anti-fabrication rule applied to forgetting. (The older `vague`/`anon` renderings are still stored on every node; the resurfacing ladder and the period gist use them. The haze itself is rendered per memory now.)

### Emotion sets the priority

Emotion is the master switch. Each memory inherits an explicit `emotion` from its source event, mapped to a `valence` (kept for statistics) and a `pathway` with an `emotion_weight`:

- **Half-life scaling:** effective half-life = 45 days × `emotion_weight`. Trivial things (calm, ×0.7) sink in weeks; a tender night (×1.5) is still in the haze months later. The demo runs a same-day pair — a tender all-night phone call and a spare phone charger, recorded the same day — and prints them diverging week by week: *same age, different clocks.*
- **Recall ranking:** results are ordered by `priority = strength × emotion_weight`, with a ×1.15 bonus for the positive fast lane. At equal strength, the joyful memory surfaces before the neutral one — *the heart decides what is thought of first.*

### Emotion-specific pathways

"Negative" is not one thing, so the machinery keys on the **specific emotion**, not the coarse valence:

| Pathway | Emotions | Weight | Behaviour |
|---|---|---|---|
| **fast_lane** | joyful, warm, tender, excited, cheerful, proud, pleased, eager, amused | 1.0–1.5 | recall-priority bonus; joy rises fast |
| **grudge** | angry, resentful | 1.6 | encoded strong, decays slowly — and being touched does not soothe it: a recall **ruminates**, with a larger gain (0.8 vs 0.6). The longer it is rehearsed, the clearer it stays |
| **slow_fog** | down, grief, sad, heavy | 1.25–1.3 | fades through the normal haze layers, just more slowly |
| **buried** | vulnerable, shame, fear | 1.8 | encoded the strongest of all — then **actively pushed down** (below) |
| **standard** | calm, quiet | 0.7–0.8 | small things sink in weeks |

**Burial (protective forgetting / motivated suppression).** A buried-pathway memory encoded at strength ≥ 0.6 does not fade through the haze. At the first advance of time it is marked `buried`: its strength freezes, and *no* recall path reaches it — not default recall, not `include_sunk`, nothing. It bypassed the haze on the way down. But **buried ≠ deleted**: the node, its frozen strength and its full provenance chain remain, and the audit walk finds it instantly. Only two keys fit a buried memory: a **life cue** (a new event sharing a person, place or item with its source event) or a **sensory cue** (below). `SECRET_LOCKED` still outranks everything: a buried secret that resurfaces stays invisible without the unlock path.

The state machine, in text:

```
                 encode
                   │
                   ▼
        ┌────────────────────┐   decay (rate set by emotion_weight)
        │       CLEAR        │ ──────────────┐
        └────────────────────┘               ▼
                   ▲                  HAZY-1 → HAZY-2 → HAZY-3
                   │                         │ decay
              recall /                       ▼
              reunion                     SUNK ──recall──► back up the bands
                   │                         │
                   │              life cue / sensory cue
                   │                         ▼
                   │                    (resurface ladder)
                   │
   buried pathway, encoded ≥ 0.6:
        encode ──first advance──► BURIED  (strength frozen, no recall reaches it,
                   ▲                audit still can; bypassed the haze going down)
                   │ life cue / sensory cue
                   ▼
        AFFECT ──mention──► FRAGMENT ──mention──► HAZY ──mention──► CLEAR
        (feeling only)      (one shard)          (gist)            (full text)
```

One line for the four fates: **anger remembers, shame hides, grief fades slowly, joy rises fast.**

### Surprise: the chapter break

Emotion is not the only encoding signal — and it is not even the oldest one. Every event also carries `surprise` ∈ [0, 1]: its **prediction error**, the distance between what happened and what the agent's model of the world expected. Surprise is *orthogonal* to emotion by construction. It is not a feeling and it does not map to any pathway: a joyful event can be entirely foreseen (a birthday party planned for months), and a perfectly calm one can be a total shock — the retired baker who sells bread from a stall by the market gate for exactly one day, and is gone the next morning. Nothing about that morning was emotional. Nobody who lived it forgot it.

Surprise acts at two strengths, deliberately asymmetric:

- **Encoding:** `s0` is multiplied by `(1 + 0.6 · surprise)` — the unexpected is written down a little more firmly.
- **Half-life:** the effective half-life is multiplied by `(1 + 1.8 · surprise)` — the unexpected is given much more *time*. Being surprising mostly buys a memory duration, not intensity: the one-day bakery runs its clock at barely a third of the speed of the grocery run Theo did the same afternoon, on the same errand, in the same calm mood.

Demo 5's final act runs that controlled experiment: three memories of one Friday, same emotion (calm), same pathway, same weight, same recorded date — surprise 0.05, 0.95 and 0.00. Eight weeks later the grocery run and the sponge are sunk; the bakery stall has never left the CLEAR band.

And surprise does one more job, at the top of the hierarchy. Event segmentation theory (Zacks and colleagues) holds that the brain does not record life as a continuous stream: it cuts experience into chapters, and **the cuts land where prediction fails — the brain chapters life at surprises**. So a period whose *first* event carries `surprise ≥ 0.8` is marked at build time as broken open by that event: an `(Event)-[:STARTS]->(Period)` edge, and the period's `opened_by` property naming it (`decay.chapter_breaks` reads them back). The First Summer begins the morning a busker starts rehearsing under Theo's open window (surprise 0.90) — the season did not fade in, it started. Note the asymmetry the graph keeps honest: the single most surprising event in the dataset is the one-day bakery (0.95), and it opens *nothing*. It landed mid-chapter, so it is merely remembered vividly inside one. A shock can only break a chapter where a chapter can begin.

### Sensory cues: memories fade; cues persist

At encoding time, memories bind **sensory anchors** — `sensory_cues` on the node, and `(Memory)-[:TRIGGERED_BY]->(Cue)` edges in the graph (periods have their own signature cues). The asymmetry is the point: strengths decay, sink and freeze — **cue edges do not decay at all**. The song is still the song; the tea is still the tea. That is the physical basis on which anything can ever come back (the Proust effect).

A fired cue — a person returning, a taste, a smell — resurfaces sunk and even buried memories. But the way back is **not** the way down reversed. A resurfaced memory climbs a ladder, one rung per subsequent mention:

1. **AFFECT** — only the feeling returns: recall yields the emotion and valence tags and a marker ("something about this stirs a feeling"), *no content whatsoever*;
2. **FRAGMENT** — one isolated shard surfaces (the stored `shard`: a place, an object, half a moment), unrelated to any outline — *a shard, not the story*;
3. **HAZY** — the gist assembles; the memory re-enters the normal banded life;
4. **CLEAR** — one more mention, and the whole story is back.

### Routine merging: nobody remembers six identical afternoons

Conway's middle layer — *general events* — is where repeated days live, and until v1.6 this graph skipped it: every afternoon answered for itself. Now, at build time, a deterministic grouping pass (`build_graph.routine_groups`) looks for days that have collapsed into a habit. A memory joins a candidate bucket when it is an **episode** (facts, preferences, plans and secrets are not days and keep their own recall paths), is not `SECRET_LOCKED`, and its source event is **unsurprising** (`surprise ≤ 0.2` — a surprising day is an event, not a routine). Buckets are keyed by *(chapter, place, pathway)*; a bucket becomes a routine at **≥ 4 members** whose emotion weights agree within 0.15. One pathway is excluded outright: `buried` — a suppressed memory must never be summarised, because a summary naming its days would leak exactly what suppression holds down.

A qualifying bucket materialises as a **Routine node** in its chapter's store, with `SUMMARIZES` edges to every member and the members' `routine_id` set. Its summary is a pre-stored rendering composed from the data — *"During The Restless Spring, visits to Harbor Bookstore settled into a routine: 6 similar days between 2026-03-07 and 2026-04-25, each one unremarkable on its own, together a habit."* Nothing is deleted: every member keeps its text, its strength, and its full provenance chain.

Recall changes shape, not content. When two or more members of a routine survive the usual band and privacy filters, `decay.recall` replaces them with a single `routine` entry carrying the stored summary (a lone surviving member still answers individually — the last afternoon standing stands on its own). And the habit is not a wall: `decay.routine_drilldown` walks the `SUMMARIZES` edges and returns every member in date order, with its full text and current band. Merged for remembering, never for auditing. Demo 7, Act 1 shows the before/after on the same question.

### Cue dilution: the key is blunted by reuse

Earlier versions of this README said cue edges do not decay — true, and incomplete. A cue itself never fades, but the energy it can deliver to *one* memory is divided among everything it has been bound to since: **`energy = 1 / degree`**, where degree counts the cue's `TRIGGERED_BY` bindings across the whole library (memories and periods alike, read live from the graph — `decay.cue_energy`). Hear a song once in three years and it is a lightning strike; set it as your morning alarm and it is just a sound.

Dilution lands in two places. In **sensory resurfacing**, a fired cue raises each sunk memory at entry strength `0.40 × energy` instead of a flat 0.40: a cue shared four ways sets its memories down at 0.10, below the sinking line — the feeling and the shard still climb their ladder, but when the ladder clears there is not enough strength left for the story to assemble, and the memory slips back under. A **buried** memory is stricter: suppression only breaks at `energy ≥ 0.5`; below that the cue reaches it but cannot raise it — the firing reports it as *stirred* (`raised = False`) and the graph is left untouched, still buried, still frozen. In **spreading activation**, the injected activation is likewise scaled by the cue's energy. Life-cue resurfacing (a person returning) is *not* diluted: June is not a smell, and her return does not divide. Demo 7, Act 2 runs the controlled comparison — the same cue, the same memory, degree 1 versus degree 4.

### Rumination has a ceiling

Demo 5's grudge rehearsed itself stronger on every mention, without limit — dramatically satisfying, mechanically false. Real rumination exhausts: the same thought, rehearsed, buys less each time, until rehearsing is just remembering. So the grudge pathway's reinforce gain is now a **finite dividend**, scheduled by the `ruminate_count` stored on the memory node: rehearsals 1–3 gain the full 0.8; from the fourth on, the gain halves each time (0.4, 0.2, 0.1, …) towards zero, after which the memory simply decays like anything else. And no rehearsal — at any count — can restore the strength past a **hard cap of 0.85**: below the 1.00 of its first burn, so a grudge can be kept hot, but never again at the temperature of the day it happened. Demo 7, Act 3 mentions Marco eight times across six months: four mentions pin the memory to the ceiling, and then, still mentioned every few weeks, it falls — 0.794, 0.724, 0.651, 0.561. Anger burns hot, then burns out.

### Periods, not just events — and spreading activation

Autobiographical memory is hierarchical (Conway): **lifetime periods → general events → specific details**. So the graph's top layer is `Period` nodes, and there is a period mode of recall (`decay.recall_period`): ask what a season of life was *like*, and you get its atmosphere, its dominant emotion, and the gists of its emotionally strongest memories — not one event.

And sometimes what a cue brings back is not an event but an era. There is **no fixed cascade** for that. `decay.spreading_activation` models it directly: a cue injects activation (0.55) at whichever node it is bound to — a memory, a period — and activation spreads along `PART_OF`, same-event and shared-person edges, decaying by edge type (0.85 / 0.7 / 0.55), accumulating where paths converge — **inside the chapter the cue landed in**, because that chapter's store is where the cascade runs. A memory surfaces as its activation crosses 0.30 (affect), 0.50 (fragment), 0.72 (gist); a period opens at 0.30 and its gist assembles at 0.60 once three of its memories are up. The surfacing *order* is therefore emergent — a concrete little object produces *shard → season opens → shard rain → period gist*, while the era's own song produces *season → shards falling → one memory taking shape* — yet the mechanism is pure deterministic arithmetic: same graph, same cue, same trace, every run. (Shared-person spread is built period-locally, and never runs through Theo himself: the self is the medium, not a cue.) Across a chapter boundary the cascade does not continue: each shared-person link to another chapter transmits a single **damped echo** (× 0.55 × 0.30 bridge factor, capped at 0.165 < the 0.30 affect threshold) — enough to prime, never to surface, and echoes do not spread onward. **A shard opens a season; a season rains shards; another season only hears an echo.**

## Layout

```
build_graph.py        dataset -> persistent Relate library (./store):
                      one store per period + the index store; also
                      derives every memory's detail weights and its
                      per-memory haze renderings
library.py            opens a built library (index + one connection per
                      chapter) and routes queries: period-first location,
                      fan-out, provenance routing
demo.py               the seven showcases, one command
decay.py              the forgetting, emotion & surprise engine (bands,
                      pathways, burial, resurfacing, periods, spreading
                      activation, chapter breaks, routine collapsing,
                      cue dilution, the rumination ceiling) — all
                      period-first: locate the chapter, then work
                      inside it
data/memories.json    the synthetic dataset (people, places, items, events,
                      periods, memories with cues and shards)
tests/test_smoke.py   build + provenance + privacy + multi-hop + decay +
                      haze + detail weights + emotion + burial + period +
                      surprise + storage-partition invariants + routine
                      merging + cue dilution + rumination (43 tests)
store/                the persistent library: store/index/ plus
                      store/periods/<chapter-slug>/ (inspect any of them
                      with the relate CLI)
store_decay/          demo 4's own library, rebuilt fresh on every demo run
store_haze/           demo 5's own library, rebuilt fresh on every demo run
store_period/         demo 6's own library, rebuilt fresh on every demo run
store_surprise/       demo 5 act C's own library, rebuilt fresh on every run
store_dilution/       demo 7 act 2's own library, rebuilt fresh on every run
store_rumination/     demo 7 act 3's own library, rebuilt fresh on every run
```

## Notes

- Rebuilding is reproducible: `python build_graph.py --fresh` recreates the store from the dataset.
- The unlock passphrase is a demo stand-in for the owner's credential, which a host application would check outside the database.
- License: MIT (hackathon project).
