#!/usr/bin/env python3
"""Forgetting-curve model for the auditable graph memory.

The engine. Every Memory carries a *strength* that fades with time
and is refreshed when the memory is recalled — the classic Ebbinghaus
forgetting curve, stored as ordinary graph properties and updated
with ordinary Cypher, so the forgetting process itself stays
auditable.

The engine works on a *Library* (library.py): the period-partitioned
store. Every operation is period-first — it locates the chapter a
memory is filed in (via the index) and works inside that chapter's
own store; operations that span chapters (global recall, counts)
iterate the chapters in the index's chronological order, which is
the only bridge between them. That is not an implementation detail:
inside one chapter of life, one thing leads to another easily;
across chapters you have to turn the page, and the page-turning goes
through the index.

Model
-----
Initial strength (set at build time, see build_graph.py):

    s0 = confidence x salience(source event) x (1 + 0.6 x surprise)

  `confidence` is how sure the agent is of the memory; `salience`
  scores how emotionally charged the witnessed event was (a heavy or
  vulnerable moment starts stronger than a calm one). See
  EVENT_SALIENCE below.

Surprise (SURPRISE_ENCODE_BOOST / SURPRISE_HALFLIFE_BOOST):

  Every event also carries `surprise` in [0, 1] — how far it sat from
  what the agent expected (prediction error). Surprise is ORTHOGONAL
  to emotion: it is not a feeling, it is the size of the mismatch
  between the world and the model of it. A perfectly calm event that
  nobody saw coming (a bakery stall that exists for exactly one day)
  is encoded almost as strongly as a charged one, and its clock runs
  slow:

    s0 multiplier:      (1 + 0.6 x surprise)     [clamped: s0 <= 1.0]
    half-life scaling:  (1 + 1.8 x surprise)

  Surprise is given the stronger hand on the clock, not on the
  encoding: being unexpected mostly buys a memory *time*. And at the
  top of the graph, surprise does one more job — a high-surprise
  event (surprise >= CHAPTER_BREAK_THRESHOLD = 0.8) that opens a
  Period is recorded as that chapter's break (see chapter_breaks):
  event segmentation theory says the brain chapters life at
  surprises.

Decay (exponential, base half-life HALF_LIFE_DAYS = 45 days):

    s(t) = s_anchor * 2 ** (-(days since anchor) / effective half-life)
    effective half-life = 45 x emotion_weight x (1 + 1.8 x surprise)

  The graph stores `strength` together with `strength_as_of` (the
  date that strength is current as of); advancing time multiplies
  each memory by the decay factor for the elapsed days and
  re-stamps the date.

Reinforcement (a recall — the memory is used, mentioned, lived again):

    s <- s + (1 - s) * REINFORCE_GAIN        (REINFORCE_GAIN = 0.6)

  and `last_accessed` is set to the recall date. Recall moves strength
  60% of the way back toward 1.0: being remembered is what keeps a
  memory alive, and a single recall can resurrect a sunk one.

Sinking (SUNK_THRESHOLD = 0.25):

  A memory whose strength falls below the threshold is flagged
  `sunk = true`. **Sunk is not deleted.** The node, its strength and
  its whole DERIVED_FROM provenance chain stay in the graph, inside
  its own chapter's partition; default recall simply stops surfacing
  it — exactly like a person who has forgotten something they could
  still be reminded of. The sunk filter stacks on top of the privacy
  filter: default recall excludes SECRET_LOCKED *and* sunk memories;
  only an explicit unlock + include_sunk path (or a provenance audit,
  which ignores recall filters by design) can reach a sunk memory.

The fuzzy band (CLEAR_THRESHOLD = 0.55):

  Human memory does not switch off at a threshold — it fades through
  a haze. So recall (see `recall` below) is three-banded:

    strength >= 0.55          CLEAR  full text, with details
    0.25 <= strength < 0.55   HAZY   layered, see below
    strength <  0.25          SUNK   not returned at all

  And the haze itself is layered — but the layers are NOT a global
  template. What a memory sheds, and in what order, is decided by its
  own *detail weights* (build_graph.detail_inventories): every detail
  of the memory — its when, its who, its where, its whats — carries a
  weight, the strength of its binding to emotion. Details fall in
  order of weight, not of kind:

    HAZY-1  the lightest details are gone; the 3 heaviest are held
    HAZY-2  only the 2 heaviest details remain
    HAZY-3  only the single heaviest detail — the anchor — remains,
            next to the bare gist

  So one memory forgets its date first and keeps a name to the very
  end (because that person matters), while another loses the name
  early and keeps the place (because the place is the anchor). Every
  layer is a rendering stored on the node at build time, composed
  from that memory's own shedding sequence. Recall *selects* a
  version; it never rewrites text at recall time — blurring a memory
  on the fly would mean inventing judgements about what is and isn't
  still known. That is the anti-fabrication rule applied to
  forgetting: a faded memory may be *omitted down*, detail by detail
  in weight order, but never *expanded back* into detail the agent no
  longer holds. Privacy stacks on top of the bands exactly as before:
  a SECRET_LOCKED memory is invisible in every band and every layer
  unless the caller is on the unlock path.

Three later mechanisms complete the picture (v1.6):

  Routine merging (see recall / _collapse_routines): repeated,
  unsurprising days at the same place collapse — at build time —
  into a Routine node that summarises them. Daily recall answers
  with the habit; the days themselves are never deleted, and a
  drill-down (routine_drilldown) still reaches every one.

  Cue dilution (see cue_energy): a sensory cue never decays, but
  the energy it delivers to any one memory is divided by the number
  of nodes it has been bound to. Reuse blunts the key.

  Rumination ceiling (see rumination_gain / RUMINATION_CAP): a
  grudge's rehearsals gain in full only a few times; the dividend
  then halves towards zero, and no rehearsal can restore the
  strength past a hard cap below its first burn. Anger burns hot,
  then burns out.

All numbers this module returns are read back from the graph with
Cypher after Cypher updates — nothing is computed off-graph and
merely printed.
"""

from __future__ import annotations

from datetime import date

# --- Model parameters (also documented in README, "Forgetting") -----------
HALF_LIFE_DAYS = 45.0      # exponential decay half-life of memory strength
CLEAR_THRESHOLD = 0.55     # strength at/above this -> CLEAR band (full recall)
SUNK_THRESHOLD = 0.25      # strength below this -> SUNK band; between the two: HAZY
HAZE1_FLOOR = 0.45         # HAZY level boundaries (which stored rendering
HAZE2_FLOOR = 0.35         # recall selects); what each layer *contains*
                           # is per-memory — see detail weights, above
REINFORCE_GAIN = 0.6       # recall moves strength 60% of the way to 1.0

# Surprise: an encoding factor ORTHOGONAL to emotion (prediction error,
# not feeling). It lifts the initial encoding modestly and stretches
# the half-life strongly — the unexpected is mostly given *time*.
SURPRISE_ENCODE_BOOST = 0.6    # s0 multiplier: (1 + 0.6 x surprise)
SURPRISE_HALFLIFE_BOOST = 1.8  # half-life multiplier: (1 + 1.8 x surprise)
CHAPTER_BREAK_THRESHOLD = 0.8  # an event at/above this surprise that
                               # opens a Period is its chapter break

BAND_CLEAR = "CLEAR"
BAND_HAZY = "HAZY"
BAND_SUNK = "SUNK"

# How emotionally charged a witnessed event is, by its mood. Charged
# moments (heavy, vulnerable, down) form stronger memories than calm ones.
EVENT_SALIENCE = {
    "heavy": 1.0,
    "vulnerable": 1.0,
    "fear": 1.0,
    "down": 0.95,
    "angry": 0.95,
    "resentful": 0.95,
    "tender": 0.95,
    "shame": 0.95,
    "grief": 0.95,
    "joyful": 0.9,
    "excited": 0.9,
    "proud": 0.9,
    "sad": 0.9,
    "eager": 0.85,
    "warm": 0.85,
    "cheerful": 0.85,
    "pleased": 0.8,
    "amused": 0.8,
    "quiet": 0.75,
    "calm": 0.7,
}
DEFAULT_SALIENCE = 0.8


def event_salience(mood: str) -> float:
    """Salience score of a witnessed event, from its mood."""
    return EVENT_SALIENCE.get(mood, DEFAULT_SALIENCE)


# --- Emotion pathways -------------------------------------------------------
# Emotion is the master switch of memory priority. The *specific*
# emotion of the source event (not a coarse positive/negative split)
# decides a memory's pathway: how fast it decays (its emotion_weight
# scales the half-life), how recall ranks it, and whether it fades
# through the haze at all. Valence is kept alongside for statistics;
# the pathway is what the machinery actually keys on.
#
#   pathway    emotions                       half-life x   behaviour
#   fast_lane  joyful warm tender excited     1.0–1.5       recall priority
#              cheerful proud pleased eager                 boosted — joy
#              amused                                       rises fast
#   grudge     angry resentful                1.6           encoded strong,
#                                                           decays slowly,
#                                                           and a cue does
#                                                           not soothe it —
#                                                           it RUMINATES
#                                                           (bigger gain,
#                                                           but capped and
#                                                           exhaustible —
#                                                           see below)
#   slow_fog   down grief sad heavy           1.25–1.3      fades through
#                                                           the haze, just
#                                                           more slowly
#   buried     vulnerable shame fear          1.8           encoded the
#                                                           strongest of
#                                                           all, then
#                                                           actively pushed
#                                                           down: BURIED
#   standard   calm quiet (and anything else) 0.7–0.8       small things
#                                                           sink in weeks
EMOTION_PROFILE = {
    "tender": ("positive", "fast_lane", 1.5),
    "joyful": ("positive", "fast_lane", 1.2),
    "warm": ("positive", "fast_lane", 1.2),
    "excited": ("positive", "fast_lane", 1.1),
    "cheerful": ("positive", "fast_lane", 1.1),
    "proud": ("positive", "fast_lane", 1.1),
    "eager": ("positive", "fast_lane", 1.0),
    "pleased": ("positive", "fast_lane", 1.0),
    "amused": ("positive", "fast_lane", 1.0),
    "angry": ("negative", "grudge", 1.6),
    "resentful": ("negative", "grudge", 1.6),
    "heavy": ("negative", "slow_fog", 1.3),
    "grief": ("negative", "slow_fog", 1.3),
    "down": ("negative", "slow_fog", 1.25),
    "sad": ("negative", "slow_fog", 1.25),
    "vulnerable": ("negative", "buried", 1.8),
    "shame": ("negative", "buried", 1.8),
    "fear": ("negative", "buried", 1.8),
    "calm": ("neutral", "standard", 0.7),
    "quiet": ("neutral", "standard", 0.8),
}
DEFAULT_PROFILE = ("neutral", "standard", 0.8)

POSITIVE_BOOST = 1.15      # fast_lane memories get this recall-priority bonus
RUMINATION_GAIN = 0.8      # a grudge, recalled, strengthens MORE than usual
RUMINATION_FULL_COUNT = 3  # ...but only the first this many rehearsals
                           # gain in full; after that the dividend halves
                           # with every further rehearsal, towards zero
                           # (emotional exhaustion / reappraisal,
                           # mechanised — see rumination_gain)
RUMINATION_CAP = 0.85      # and rumination can never push a grudge's
                           # strength past this: the first burn is the
                           # hottest; rehearsal restores it at most to
                           # here, never to its encoded full strength
BURY_ENCODED_MIN = 0.6     # buried-pathway memories encoded at/above this
                           # strength are pushed down at the first advance
RESURFACE_STRENGTH = 0.40  # a resurfaced memory's strength anchor (HAZY-2
                           # territory) — but it re-enters in stages, below

# Sensory-cue dilution: a cue node itself never decays, but the
# energy it can deliver to ONE memory is divided among everything it
# has been bound to (see cue_energy). A cue bound once is a key cut
# for one lock; a cue bound twenty times is wallpaper.
CUE_BASE_ENERGY = 1.0      # the energy of a cue bound to exactly one node
BURY_MIN_ENERGY = 0.5      # breaking suppression takes at least this
                           # much delivered energy; a more diluted cue
                           # can stir a buried memory but cannot raise it


def rumination_gain(count_before: int) -> float:
    """The gain of the (count_before + 1)-th rumination of a grudge.

    The first RUMINATION_FULL_COUNT rehearsals strengthen at the full
    RUMINATION_GAIN — anger rehearsed is anger refreshed. After that
    the dividend halves with every further rehearsal, towards zero:
    there is only so much outrage a mind can spend on one slight.
    Past the ceiling, being rehearsed no longer feeds the grudge, and
    ordinary decay resumes its work on it.
    """
    n = count_before + 1
    if n <= RUMINATION_FULL_COUNT:
        return RUMINATION_GAIN
    return RUMINATION_GAIN * 0.5 ** (n - RUMINATION_FULL_COUNT)

# The way back is not the way down reversed. A resurfaced memory climbs
# a ladder, one rung per subsequent mention (see reinforce):
#   AFFECT    only the feeling is back: emotion + valence, no content
#   FRAGMENT  one isolated shard surfaces — a place, an object, half a
#             sentence — unrelated to any outline
#   (stage cleared) the gist assembles: normal HAZY recall
#   and one more ordinary recall can carry it to CLEAR.
STAGE_AFFECT = "AFFECT"
STAGE_FRAGMENT = "FRAGMENT"


def emotion_profile(mood: str) -> tuple:
    """(valence, pathway, emotion_weight) for a source event's emotion."""
    return EMOTION_PROFILE.get(mood, DEFAULT_PROFILE)


def initial_strength(confidence: float, mood: str,
                     surprise: float = 0.0) -> float:
    """Strength a fresh memory starts with.

    s0 = confidence x salience(mood) x (1 + SURPRISE_ENCODE_BOOST x
    surprise), clamped to 1.0. Surprise is orthogonal to emotion: an
    emotionally flat event nobody saw coming still encodes strongly.
    """
    raw = (confidence * event_salience(mood)
           * (1.0 + SURPRISE_ENCODE_BOOST * surprise))
    return min(1.0, raw)


def effective_half_life(emotion_weight: float = 1.0,
                        surprise: float = 0.0) -> float:
    """A memory's own clock, in days.

    HALF_LIFE_DAYS stretched by emotion_weight (emotion sets the pace)
    and by surprise (the unexpected is given more time — surprise's
    multiplier is deliberately the stronger of its two effects).
    """
    return (HALF_LIFE_DAYS * emotion_weight
            * (1.0 + SURPRISE_HALFLIFE_BOOST * surprise))


def decay_factor(days: float, emotion_weight: float = 1.0,
                 surprise: float = 0.0) -> float:
    """Multiplicative strength factor after `days` days of decay.

    The effective half-life is HALF_LIFE_DAYS x emotion_weight x
    (1 + SURPRISE_HALFLIFE_BOOST x surprise): emotion and surprise
    each stretch or compress a memory's clock on their own axis. A
    profound memory (weight 1.8) ages at barely half the speed of a
    neutral one (weight 0.7); a shocking one (surprise 0.95) runs its
    clock at barely a third of its unsurprising twin's speed.
    """
    if days <= 0:
        return 1.0
    return 0.5 ** (days / effective_half_life(emotion_weight, surprise))


def _days_between(a: str, b: str) -> int:
    return (date.fromisoformat(b) - date.fromisoformat(a)).days


# ---------------------------------------------------------------------------
# Period-first location
# ---------------------------------------------------------------------------

def locate_periods(lib, *, on_date: str = None, cue: str = None,
                   person: str = None, place: str = None,
                   item: str = None) -> list:
    """Locate the chapter(s) a question belongs to — via the index.

    This is the front door of every cross-chapter operation: first
    find the period (by the date it asks about, by the cue it names,
    by the person/place/item it mentions), then open that chapter.
    Returns period ids in chronological order.
    """
    found = set()
    if on_date is not None:
        pid = lib.period_for_date(on_date)
        if pid:
            found.add(pid)
    if cue is not None:
        found.update(lib.periods_for_cue(cue))
    if person is not None:
        found.update(lib.periods_for_entity("Person", person))
    if place is not None:
        found.update(lib.periods_for_entity("Place", place))
    if item is not None:
        found.update(lib.periods_for_entity("Item", item))
    return [pid for pid in lib.period_order if pid in found]


# ---------------------------------------------------------------------------
# Counts — aggregated over the chapters, in index order
# ---------------------------------------------------------------------------

def _counts_in(db) -> dict:
    total = db.execute("MATCH (m:Memory) RETURN count(m) AS c").data()[0]["c"]
    sunk = db.execute(
        "MATCH (m:Memory) WHERE m.sunk = true RETURN count(m) AS c"
    ).data()[0]["c"]
    buried = db.execute(
        "MATCH (m:Memory) WHERE m.buried = true RETURN count(m) AS c"
    ).data()[0]["c"]
    return {"total": total, "active": total - sunk - buried,
            "sunk": sunk, "buried": buried}


def counts(lib) -> dict:
    """Total / active / sunk / buried memory counts, over all chapters."""
    out = {"total": 0, "active": 0, "sunk": 0, "buried": 0}
    for db in lib.all_partitions():
        c = _counts_in(db)
        for k in out:
            out[k] += c[k]
    return out


def sunk_ids(lib) -> set:
    """Ids of all currently sunk memories, across all chapters."""
    out = set()
    for db in lib.all_partitions():
        rows = db.execute(
            "MATCH (m:Memory) WHERE m.sunk = true RETURN m.id AS id"
        ).data()
        out.update(r["id"] for r in rows)
    return out


def band_counts(lib) -> dict:
    """CLEAR / HAZY / SUNK / BURIED counts, from live strengths in Cypher.

    The three bands partition the *reachable* memories; buried
    memories are counted separately — they are in none of the bands,
    because burial is a state beside the bands, not a depth within
    them.
    """
    out = {"clear": 0, "hazy": 0, "sunk": 0, "buried": 0}
    for db in lib.all_partitions():
        out["clear"] += db.execute(
            "MATCH (m:Memory) WHERE m.buried = false AND m.strength >= $t "
            "RETURN count(m) AS c",
            {"t": CLEAR_THRESHOLD},
        ).data()[0]["c"]
        out["hazy"] += db.execute(
            "MATCH (m:Memory) WHERE m.buried = false AND m.strength >= $lo "
            "AND m.strength < $hi RETURN count(m) AS c",
            {"lo": SUNK_THRESHOLD, "hi": CLEAR_THRESHOLD},
        ).data()[0]["c"]
        out["sunk"] += db.execute(
            "MATCH (m:Memory) WHERE m.buried = false AND m.strength < $t "
            "RETURN count(m) AS c",
            {"t": SUNK_THRESHOLD},
        ).data()[0]["c"]
        out["buried"] += db.execute(
            "MATCH (m:Memory) WHERE m.buried = true RETURN count(m) AS c"
        ).data()[0]["c"]
    out["total"] = out["clear"] + out["hazy"] + out["sunk"] + out["buried"]
    return out


# ---------------------------------------------------------------------------
# Time, decay, burial — each chapter aged inside its own store
# ---------------------------------------------------------------------------

def _refresh_sunk(db) -> None:
    """Recompute the sunk flag from current strengths (two Cypher passes).

    Buried memories are not "sunk" — burial is a separate state, and a
    buried memory keeps sunk = false so the two never blur together.
    """
    db.execute(
        "MATCH (m:Memory) WHERE m.buried = false AND m.strength < $t "
        "SET m.sunk = true",
        {"t": SUNK_THRESHOLD},
    )
    db.execute(
        "MATCH (m:Memory) WHERE m.buried = true OR m.strength >= $t "
        "SET m.sunk = false",
        {"t": SUNK_THRESHOLD},
    )


def _bury_pass(db) -> None:
    """Push down the memories the mind refuses to keep on the surface.

    A buried-pathway memory (vulnerable / shame / fear) that was
    encoded strongly (encoded_strength >= BURY_ENCODED_MIN) is marked
    buried at the first time advance after its formation. It does not
    fade through the haze first — suppression is not decay. Its
    strength is frozen from then on (advance skips buried memories):
    the pain does not fade; it is held, unreachable, with its evidence
    chain intact.
    """
    db.execute(
        "MATCH (m:Memory) WHERE m.pathway = 'buried' AND m.buried = false "
        "AND m.buried_ever = false AND m.encoded_strength >= $min "
        "SET m.buried = true, m.buried_ever = true, m.sunk = false",
        {"min": BURY_ENCODED_MIN},
    )


def _advance_in(db, to_date: str) -> None:
    """Decay one chapter's memories up to `to_date`, inside its store."""
    groups = db.execute(
        "MATCH (m:Memory) WHERE m.buried = false "
        "RETURN DISTINCT m.strength_as_of AS d, m.emotion_weight AS w, "
        "m.surprise AS s"
    ).data()
    for row in groups:
        days = _days_between(row["d"], to_date)
        if days <= 0:
            continue
        db.execute(
            "MATCH (m:Memory) WHERE m.buried = false "
            "AND m.strength_as_of = $from AND m.emotion_weight = $w "
            "AND m.surprise = $s "
            "SET m.strength = m.strength * $f, m.strength_as_of = $to",
            {"from": row["d"], "w": row["w"], "s": row["s"],
             "f": decay_factor(days, row["w"], row["s"]), "to": to_date},
        )
    _bury_pass(db)
    _refresh_sunk(db)


def advance_time(lib, to_date: str) -> dict:
    """Decay every memory's strength up to `to_date`, chapter by chapter.

    Each chapter is aged inside its own store, in the index's
    chronological order; buried memories are skipped (their strength
    is frozen), and afterwards each chapter's bury pass pushes down
    any newly eligible buried-pathway memories. Returns the
    post-advance counts over the whole library.
    """
    for db in lib.all_partitions():
        _advance_in(db, to_date)
    return counts(lib)


def reinforce(lib, mem_id: str, on_date: str) -> dict:
    """Recall a memory on `on_date`: decay it to that date, then boost it.

    The memory's chapter is located through the index first; all the
    work then happens inside that chapter's store. The boost
    (s <- s + (1 - s) * REINFORCE_GAIN) and the `last_accessed` update
    happen in Cypher; strengths before/after are read back from the
    graph. Returns {id, pre, post, was_sunk, sunk, ...}.

    A grudge-pathway memory does not take the ordinary boost: it
    *ruminates* (see rumination_gain and RUMINATION_CAP) — a larger
    gain for the first few rehearsals, a halving dividend after that,
    a hard ceiling on what rehearsal can restore, and a
    `ruminate_count` on the node that counts every rehearsal.
    """
    db = lib.db_for_memory(mem_id)
    rows = db.execute(
        "MATCH (m:Memory {id: $id}) RETURN m.strength AS s, "
        "m.strength_as_of AS as_of, m.sunk AS sunk, "
        "m.emotion_weight AS w, m.surprise AS surprise, "
        "m.pathway AS pathway, m.resurface_stage AS stage, "
        "m.ruminate_count AS ruminate_count",
        {"id": mem_id},
    ).data()
    if not rows:
        raise KeyError(f"no memory with id {mem_id!r}")
    was_sunk = bool(rows[0]["sunk"])
    pathway = rows[0]["pathway"]
    ruminations = rows[0]["ruminate_count"] or 0

    # A resurfaced memory climbs its ladder one rung per mention before
    # strength boosts apply again: AFFECT -> FRAGMENT -> (stage cleared,
    # the gist assembles). While staged, a mention moves the stage, not
    # the strength — feeling first, then a shard, then the story.
    stage = rows[0]["stage"]
    if stage is not None:
        new_stage = STAGE_FRAGMENT if stage == STAGE_AFFECT else None
        db.execute(
            "MATCH (m:Memory {id: $id}) SET m.resurface_stage = $stage, "
            "m.last_accessed = $d, m.strength_as_of = $d",
            {"id": mem_id, "stage": new_stage, "d": on_date},
        )
        current = db.execute(
            "MATCH (m:Memory {id: $id}) RETURN m.strength AS s",
            {"id": mem_id},
        ).data()[0]["s"]
        return {
            "id": mem_id,
            "pre": current,
            "post": current,
            "was_sunk": was_sunk,
            "sunk": False,
            "pathway": pathway,
            "gain": 0.0,
            "ruminate_count": ruminations,
            "capped": False,
            "stage": new_stage,
            "stage_advanced": True,
        }
    # A grudge does not heal when it is touched — it ruminates. Recalling
    # it strengthens it MORE than an ordinary recall strengthens
    # anything — but the dividend is finite: the first few rehearsals
    # gain in full, later ones gain half as much again each time, and
    # no rehearsal can push the strength past RUMINATION_CAP. Anger
    # burns hot, then burns out.
    if pathway == "grudge":
        gain = rumination_gain(ruminations)
    else:
        gain = REINFORCE_GAIN

    days = _days_between(rows[0]["as_of"], on_date)
    if days > 0:
        db.execute(
            "MATCH (m:Memory {id: $id}) "
            "SET m.strength = m.strength * $f, m.strength_as_of = $to",
            {"id": mem_id,
             "f": decay_factor(days, rows[0]["w"], rows[0]["surprise"]),
             "to": on_date},
        )
    pre = db.execute(
        "MATCH (m:Memory {id: $id}) RETURN m.strength AS s", {"id": mem_id}
    ).data()[0]["s"]

    count_set = (", m.ruminate_count = m.ruminate_count + 1"
                 if pathway == "grudge" else "")
    db.execute(
        "MATCH (m:Memory {id: $id}) "
        "SET m.strength = m.strength + (1.0 - m.strength) * $g, "
        f"m.last_accessed = $d, m.strength_as_of = $d{count_set}",
        {"id": mem_id, "g": gain, "d": on_date},
    )
    if pathway == "grudge":
        ruminations += 1
    # The ceiling: rumination restores a grudge at most to
    # RUMINATION_CAP, never to the full strength of its first burn.
    capped = False
    if pathway == "grudge":
        current = db.execute(
            "MATCH (m:Memory {id: $id}) RETURN m.strength AS s",
            {"id": mem_id},
        ).data()[0]["s"]
        if current > RUMINATION_CAP:
            db.execute(
                "MATCH (m:Memory {id: $id}) SET m.strength = $cap",
                {"id": mem_id, "cap": RUMINATION_CAP},
            )
            capped = True
    _refresh_sunk(db)
    post = db.execute(
        "MATCH (m:Memory {id: $id}) RETURN m.strength AS s, m.sunk AS sunk",
        {"id": mem_id},
    ).data()[0]
    return {
        "id": mem_id,
        "pre": pre,
        "post": post["s"],
        "was_sunk": was_sunk,
        "sunk": bool(post["sunk"]),
        "pathway": pathway,
        "gain": gain,
        "ruminate_count": ruminations,
        "capped": capped,
        "stage": None,
        "stage_advanced": False,
    }


# ---------------------------------------------------------------------------
# Recall — banded, period-first
# ---------------------------------------------------------------------------

def default_recall(lib, keyword: str) -> list:
    """The recall path normal questions get in the forgetting era.

    Two stacked hard filters: SECRET_LOCKED memories are excluded (the
    privacy rule from Demo 2, unchanged) *and* sunk memories are
    excluded (forgotten is forgotten — until something recalls them).
    Chapters are searched in index order and the hits concatenated.

    This is the low-level keyword helper Demo 4 uses; new code should
    prefer `recall`, which shapes its answer by band.
    """
    return lib.execute_all(
        "MATCH (m:Memory) WHERE m.privacy <> 'SECRET_LOCKED' "
        "AND m.sunk = false AND m.buried = false "
        "AND m.resurface_stage IS NULL "
        "AND toLower(m.text) CONTAINS $kw "
        "RETURN m.id AS id, m.text AS text, m.strength AS strength",
        {"kw": keyword.lower()},
    )


def band_of(strength: float) -> str:
    """Which recall band a strength falls in: CLEAR / HAZY / SUNK."""
    if strength >= CLEAR_THRESHOLD:
        return BAND_CLEAR
    if strength >= SUNK_THRESHOLD:
        return BAND_HAZY
    return BAND_SUNK


def haze_level(strength: float) -> int | None:
    """Depth inside the haze: 1, 2 or 3 for a HAZY strength, else None.

    The level decides *how much* of the memory's detail fabric is
    still held (3 / 2 / 1 of its heaviest details); *which* details
    those are is the memory's own business — its detail weights.
    """
    if strength >= CLEAR_THRESHOLD or strength < SUNK_THRESHOLD:
        return None
    if strength >= HAZE1_FLOOR:
        return 1
    if strength >= HAZE2_FLOOR:
        return 2
    return 3


# form, stored rendering property, marker — one entry per haze level.
_HAZE_FORMS = {
    1: ("frayed", "haze1",
        "(hazy — the lightest details have faded)"),
    2: ("thinned", "haze2",
        "(hazy — only the heaviest details remain)"),
    3: ("outline", "haze3",
        "(hazy — only the outline and its anchor detail remain)"),
}


def recall(lib, keyword: str, *, unlocked: bool = False,
           include_sunk: bool = False, periods: list = None,
           routines: bool = True) -> list:
    """The one unified recall path — its answer is shaped by the band.

    Period-first: with `periods` given, only those chapters are
    opened; without it, recall walks every chapter in the index's
    chronological order and merges — crossing chapters always goes
    through the index's ordering, never around it.

    Matching is by keyword over all of the memory's stored renderings.
    What comes back depends on each hit's current strength:

      CLEAR   -> form "full": the complete text plus its detail
                 fields (recorded_at, source_event, confidence).
      HAZY-1  -> form "frayed": the memory's own haze1 rendering —
                 its gist plus its 3 heaviest details.
      HAZY-2  -> form "thinned": its haze2 rendering — gist plus
                 its 2 heaviest details.
      HAZY-3  -> form "outline": its haze3 rendering — gist plus
                 its single heaviest detail, the anchor.
      SUNK    -> not returned, unless include_sunk=True, in which
                 case it surfaces in gist form only, marked as sunk.

    In every hazy layer the detail fields are withheld (details is
    None) and the content is one of the renderings stored when the
    memory was fresh — selected, never rewritten at recall time.
    Which details survive into which layer is decided by the memory's
    own detail weights (build_graph.detail_inventories): details fall
    in order of weight, not of kind.

    Privacy stacks on top, unchanged: SECRET_LOCKED memories are
    invisible in every band unless unlocked=True (the caller checks
    the credential; this function only receives the verdict).

    Routine merging sits on top of all of that (routines=True, the
    default): when two or more members of one routine group survive
    the filters above, they are replaced by a single "routine" entry
    carrying the Routine node's pre-stored summary — daily recall
    answers with the habit, not with six near-identical afternoons.
    The members themselves are untouched in the graph; naming a day
    (routine_drilldown, or any provenance query) still reaches every
    one of them. A lone surviving member is returned individually —
    the last afternoon standing stands on its own. routines=False
    returns the un-merged members, which is how the demo shows the
    before/after.
    """
    pids = periods if periods else lib.period_order
    rows = []
    for pid in pids:
        db = lib.partition(pid)
        for row in db.execute(
            "MATCH (m:Memory) WHERE m.buried = false AND ("
            "toLower(m.text) CONTAINS $kw "
            "OR toLower(m.gist) CONTAINS $kw "
            "OR toLower(m.haze1) CONTAINS $kw "
            "OR toLower(m.haze2) CONTAINS $kw "
            "OR toLower(m.haze3) CONTAINS $kw) "
            "RETURN m.id AS id, m.text AS text, m.gist AS gist, "
            "m.haze1 AS haze1, m.haze2 AS haze2, m.haze3 AS haze3, "
            "m.shard AS shard, "
            "m.strength AS strength, m.privacy AS privacy, "
            "m.emotion AS emotion, m.valence AS valence, "
            "m.pathway AS pathway, m.emotion_weight AS emotion_weight, "
            "m.surprise AS surprise, m.resurface_stage AS stage, "
            "m.routine_id AS routine_id, "
            "m.recorded_at AS recorded_at, m.source_event AS source_event, "
            "m.confidence AS confidence",
            {"kw": keyword.lower()},
        ).data():
            row["_pid"] = pid
            rows.append(row)
    out = []
    for row in rows:
        if row["privacy"] == "SECRET_LOCKED" and not unlocked:
            continue
        band = band_of(row["strength"])
        # Recall priority: emotion is the master switch. A memory floats
        # up by strength x emotion_weight, with a bonus for the positive
        # fast lane — at equal strength, the joyful memory surfaces
        # before the neutral one. Joy rises fast.
        priority = row["strength"] * row["emotion_weight"]
        if row["pathway"] == "fast_lane":
            priority *= POSITIVE_BOOST
        common = {
            "id": row["id"],
            "band": band,
            "strength": row["strength"],
            "emotion": row["emotion"],
            "valence": row["valence"],
            "pathway": row["pathway"],
            "emotion_weight": row["emotion_weight"],
            "surprise": row["surprise"],
            "priority": priority,
            "stage": row["stage"],
            "routine_id": row["routine_id"],
            "period": row["_pid"],
        }
        if row["stage"] == STAGE_AFFECT:
            # The feeling is back; nothing else is. No text, no gist,
            # no shard — only the emotion and its valence.
            out.append({
                **common,
                "level": None,
                "form": "affect",
                "content": "(something about this stirs a feeling — "
                           "the feeling is back, the story is not)",
                "details": None,
            })
        elif row["stage"] == STAGE_FRAGMENT:
            # One isolated shard — a place, an object, half a moment —
            # floating free of any outline. A shard, not the story.
            out.append({
                **common,
                "level": None,
                "form": "fragment",
                "content": f"{row['shard']} (a shard, not the story)",
                "details": None,
            })
        elif band == BAND_CLEAR:
            out.append({
                **common,
                "level": None,
                "form": "full",
                "content": row["text"],
                "details": {
                    "recorded_at": row["recorded_at"],
                    "source_event": row["source_event"],
                    "confidence": row["confidence"],
                },
            })
        elif band == BAND_HAZY:
            level = haze_level(row["strength"])
            form, field, marker = _HAZE_FORMS[level]
            out.append({
                **common,
                "level": level,
                "form": form,
                "content": f"{row[field]} {marker}",
                "details": None,
            })
        elif include_sunk:  # SUNK, surfaced only on explicit request
            out.append({
                **common,
                "level": None,
                "form": "gist",
                "content": f"{row['gist']} "
                           "(sunk — surfaced only because include_sunk was set)",
                "details": None,
            })
    out.sort(key=lambda r: r["priority"], reverse=True)
    if routines:
        out = _collapse_routines(lib, out)
    return out


def _collapse_routines(lib, entries: list) -> list:
    """Replace groups of surviving routine members by their summary.

    Only unstaged entries collapse (a member mid-resurfacing keeps
    its own staged form), and only when at least two members of the
    group survived: the habit answers when the habit is what
    survived. The routine entry inherits the strongest member's band,
    strength and priority; its content is the Routine node's stored
    rendering — the full text, or the gist alone when even the
    strongest member is sunk (the include_sunk path).
    """
    groups: dict[str, list] = {}
    for entry in entries:
        rid = entry.get("routine_id")
        if rid and entry.get("stage") is None:
            groups.setdefault(rid, []).append(entry)
    suppressed: set[str] = set()
    routine_entries = []
    for rid, members in groups.items():
        if len(members) < 2:
            continue
        pid = members[0]["period"]
        rows = lib.partition(pid).execute(
            "MATCH (r:Routine {id: $rid}) RETURN r.text AS text, "
            "r.gist AS gist, r.place AS place, "
            "r.member_count AS member_count, r.first_date AS first, "
            "r.last_date AS last",
            {"rid": rid},
        ).data()
        if not rows:
            continue
        node = rows[0]
        strongest = max(members, key=lambda e: e["priority"])
        if strongest["band"] == BAND_SUNK:
            content = (f"{node['gist']} (sunk — surfaced only because "
                       f"include_sunk was set)")
        else:
            content = node["text"]
        routine_entries.append({
            "id": rid,
            "band": strongest["band"],
            "strength": strongest["strength"],
            "emotion": strongest["emotion"],
            "valence": strongest["valence"],
            "pathway": strongest["pathway"],
            "emotion_weight": strongest["emotion_weight"],
            "surprise": strongest["surprise"],
            "priority": strongest["priority"],
            "stage": None,
            "level": None,
            "form": "routine",
            "content": content,
            "details": None,
            "routine_id": rid,
            "period": pid,
            "routine": {
                "place": node["place"],
                "members": node["member_count"],
                "showing": len(members),
                "span": (node["first"], node["last"]),
            },
        })
        suppressed.update(e["id"] for e in members)
    if not routine_entries:
        return entries
    merged = [e for e in entries if e["id"] not in suppressed]
    merged.extend(routine_entries)
    merged.sort(key=lambda r: r["priority"], reverse=True)
    return merged


def routine_drilldown(lib, routine_id: str) -> list:
    """One routine's members, one by one, in date order.

    The deep-dive path: daily recall answers with the habit, but ask
    about a *day* and every day is still there — id, source-event
    date, full text, current strength and band, read from the
    chapter that holds the routine.
    """
    pid = None
    for cand in lib.period_order:
        rows = lib.partition(cand).execute(
            "MATCH (r:Routine {id: $rid}) RETURN r.id AS id",
            {"rid": routine_id},
        ).data()
        if rows:
            pid = cand
            break
    if pid is None:
        raise KeyError(f"no routine {routine_id!r} in the library")
    rows = lib.partition(pid).execute(
        "MATCH (r:Routine {id: $rid})-[:SUMMARIZES]->(m:Memory), "
        "(m)-[:DERIVED_FROM*1..5]->(e:Event) "
        "RETURN m.id AS id, e.date AS date, m.text AS text, "
        "m.strength AS strength ORDER BY date",
        {"rid": routine_id},
    ).data()
    for row in rows:
        row["band"] = band_of(row["strength"])
    return rows


# ---------------------------------------------------------------------------
# Burial and resurfacing — the cue paths cross chapters via the index
# ---------------------------------------------------------------------------

def cue_degree(lib, cue_label: str) -> int:
    """How many nodes a sensory cue is bound to, across the library.

    Counts TRIGGERED_BY edges into Cue nodes with this label in every
    chapter — memories and periods alike. A cue bound once is a key
    cut for one lock; a cue bound twenty times is wallpaper.
    """
    rows = lib.execute_all(
        "MATCH (n)-[:TRIGGERED_BY]->(c:Cue {label: $label}) "
        "RETURN count(n) AS c",
        {"label": cue_label},
    )
    return sum(r["c"] for r in rows)


def cue_energy(lib, cue_label: str) -> float:
    """The activation energy a cue delivers to ONE of its memories.

    The cue itself never decays — the song is still the song. But its
    energy is divided among everything it has been bound to since:
    energy = CUE_BASE_ENERGY / degree. Hear a song once in three
    years and it is a lightning strike; set it as your morning alarm
    and it is just a sound. (Gemini's critique, adopted: in reality
    cues do not stay sharp — they are diluted by reuse, and the
    1/degree law is the mechanisation.)
    """
    degree = cue_degree(lib, cue_label)
    return CUE_BASE_ENERGY / degree if degree else 0.0


def _resurface_in(db, mem_id: str, on_date: str, via: str,
                  entry_strength: float = RESURFACE_STRENGTH) -> dict:
    """Bring one buried or sunk memory back — but only the feeling, at first.

    The memory re-enters at `entry_strength` with resurface_stage =
    AFFECT: recall can now *see* it, but all it returns is the emotion
    and valence — no text, no gist, no shard. Each later mention climbs
    one rung (see reinforce): AFFECT -> FRAGMENT -> gist (HAZY) ->
    CLEAR. The way back is not the way down reversed: forgetting shed
    detail in layers, but remembering returns feeling first, then a
    shard, then the story.

    The entry strength is where cue dilution lands (see
    sense_resurface): a fully-charged cue restores RESURFACE_STRENGTH;
    a diluted one sets the memory down lower — possibly below the
    sinking line, so the feeling and the shard surface, and then,
    unless life mentions it again in time, it slips back under before
    the story can assemble.
    """
    db.execute(
        "MATCH (m:Memory {id: $id}) SET m.buried = false, m.sunk = false, "
        "m.strength = $s, m.strength_as_of = $d, m.last_accessed = $d, "
        "m.resurface_stage = $stage",
        {"id": mem_id, "s": entry_strength, "d": on_date,
         "stage": STAGE_AFFECT},
    )
    row = db.execute(
        "MATCH (m:Memory {id: $id}) RETURN m.emotion AS emotion, "
        "m.valence AS valence, m.pathway AS pathway",
        {"id": mem_id},
    ).data()[0]
    return {
        "id": mem_id,
        "via": via,
        "strength": entry_strength,
        "stage": STAGE_AFFECT,
        "emotion": row["emotion"],
        "valence": row["valence"],
        "pathway": row["pathway"],
    }


def cue_resurface(lib, on_date: str, *, person: str = None,
                  place: str = None, item: str = None) -> list:
    """A life event acts as a cue: it shares a person, place or item with
    a buried memory's source event — and the buried memory resurfaces.

    This is the ONLY way back from burial besides a sensory cue: no
    recall query, however permissive, reaches a buried memory. The
    provenance audit can still *see* it (audits ignore accessibility),
    but seeing is not surfacing.

    The cue crosses chapters through the index: the named entity's
    APPEARS_IN edges say which chapters it appears in, and only those
    chapters are opened. Each result carries its "period" — a person
    can call a memory back from a chapter the cue-giver never visited.
    """
    candidates = set()
    if person:
        candidates.update(lib.periods_for_entity("Person", person))
    if place:
        candidates.update(lib.periods_for_entity("Place", place))
    if item:
        candidates.update(lib.periods_for_entity("Item", item))
    if not (person or place or item):
        candidates.update(lib.period_order)

    found = {}
    for pid in lib.period_order:
        if pid not in candidates:
            continue
        db = lib.partition(pid)
        if person:
            rows = db.execute(
                "MATCH (m:Memory)-[:DERIVED_FROM*1..5]->(e:Event)-[:WITH]->"
                "(p:Person {name: $name}) WHERE m.buried = true "
                "RETURN DISTINCT m.id AS id",
                {"name": person},
            ).data()
            for r in rows:
                found[r["id"]] = (pid, f"person:{person}")
        if place:
            rows = db.execute(
                "MATCH (m:Memory)-[:DERIVED_FROM*1..5]->(e:Event)"
                "-[:EXPERIENCED_AT]->(pl:Place {name: $name}) "
                "WHERE m.buried = true RETURN DISTINCT m.id AS id",
                {"name": place},
            ).data()
            for r in rows:
                found[r["id"]] = (pid, f"place:{place}")
        if item:
            rows = db.execute(
                "MATCH (m:Memory) WHERE m.buried = true AND ("
                "EXISTS { (m)-[:INVOLVES]->(:Item {name: $name}) } OR "
                "EXISTS { (m)-[:DERIVED_FROM*1..5]->(:Event)-[:INVOLVES]->"
                "(:Item {name: $name}) }) RETURN m.id AS id",
                {"name": item},
            ).data()
            for r in rows:
                found[r["id"]] = (pid, f"item:{item}")
    out = []
    for mem_id in sorted(found):
        pid, via = found[mem_id]
        result = _resurface_in(lib.partition(pid), mem_id, on_date, via)
        result["period"] = pid
        out.append(result)
    return out


def sense_resurface(lib, cue_label: str, on_date: str) -> list:
    """A sensory cue fires — a song, a smell, a taste (the Proust effect).

    Sensory cues are bound to memories at encoding time as TRIGGERED_BY
    edges to Cue nodes, and the edges do not decay: memories fade, cues
    persist. The index's FIRES_IN edges say which chapters the cue is
    bound in; those chapters — and only those — are opened, and the
    cue reaches what person/place cues could not: sunk memories and
    even buried ones, in whichever chapter they lie. A buried memory
    resurfaced by sense bypassed the haze on the way down, but it
    returns through it.

    Dilution: the cue's energy is divided among everything it is
    bound to (cue_energy = 1 / degree), and the energy decides what
    the firing can do. A sunk memory is raised at entry strength
    RESURFACE_STRENGTH x energy — a diluted cue sets it down low, and
    it can slip back under before its ladder is climbed. A *buried*
    memory is stricter: suppression only breaks at energy >=
    BURY_MIN_ENERGY; below that the cue reaches it but cannot raise
    it — the result is reported as stirred (raised = False), and the
    memory stays exactly where suppression put it.
    """
    degree = cue_degree(lib, cue_label)
    energy = CUE_BASE_ENERGY / degree if degree else 0.0
    out = []
    for pid in lib.periods_for_cue(cue_label):
        db = lib.partition(pid)
        rows = db.execute(
            "MATCH (m:Memory)-[:TRIGGERED_BY]->(c:Cue {label: $label}) "
            "WHERE m.buried = true OR m.sunk = true "
            "RETURN m.id AS id, m.buried AS was_buried, "
            "m.emotion AS emotion, m.valence AS valence, "
            "m.pathway AS pathway",
            {"label": cue_label},
        ).data()
        for row in rows:
            if row["was_buried"] and energy < BURY_MIN_ENERGY:
                # Reached, but not raised: the feeling moved and
                # nothing surfaced. The state in the graph is
                # untouched — still buried, still frozen.
                out.append({
                    "id": row["id"],
                    "via": f"sense:{cue_label}",
                    "strength": None,
                    "stage": None,
                    "emotion": row["emotion"],
                    "valence": row["valence"],
                    "pathway": row["pathway"],
                    "was_buried": True,
                    "period": pid,
                    "energy": energy,
                    "degree": degree,
                    "raised": False,
                    "stirred": True,
                })
                continue
            result = _resurface_in(
                db, row["id"], on_date, via=f"sense:{cue_label}",
                entry_strength=RESURFACE_STRENGTH * energy)
            result["was_buried"] = bool(row["was_buried"])
            result["period"] = pid
            result["energy"] = energy
            result["degree"] = degree
            result["raised"] = True
            result["stirred"] = False
            out.append(result)
    out.sort(key=lambda r: r["id"])
    return out


# ---------------------------------------------------------------------------
# Periods: the top layer of autobiographical memory
# ---------------------------------------------------------------------------
# Conway's model of autobiographical memory is hierarchical: lifetime
# periods -> general events -> specific details. Sometimes what comes
# back is not an event but an era. Periods are the store's first-level
# containers; events and memories are filed under exactly one, by the
# date of the event their provenance chain ends at.

def _priority(row) -> float:
    p = row["strength"] * row["emotion_weight"]
    if row["pathway"] == "fast_lane":
        p *= POSITIVE_BOOST
    return p


def _recall_period_in(db, period_id: str) -> dict:
    """Period recall inside one chapter's own store."""
    rows = db.execute(
        "MATCH (p:Period {id: $pid}) "
        "RETURN p.id AS id, p.name AS name, p.start_date AS start_date, "
        "p.end_date AS end_date, p.atmosphere AS atmosphere, "
        "p.dominant_emotion AS dominant_emotion",
        {"pid": period_id},
    ).data()
    if not rows:
        raise KeyError(f"no period {period_id!r} in this partition")
    period = rows[0]
    mems = db.execute(
        "MATCH (m:Memory)-[:PART_OF]->(p:Period {id: $pid}) "
        "WHERE m.buried = false AND m.privacy <> 'SECRET_LOCKED' "
        "RETURN m.id AS id, m.gist AS gist, m.strength AS strength, "
        "m.emotion_weight AS emotion_weight, m.pathway AS pathway, "
        "m.emotion AS emotion",
        {"pid": period_id},
    ).data()
    for mem in mems:
        mem["priority"] = _priority(mem)
    mems.sort(key=lambda r: r["priority"], reverse=True)
    period["memories"] = [
        {"id": m["id"], "gist": m["gist"], "emotion": m["emotion"],
         "priority": m["priority"]}
        for m in mems[:5]
    ]
    period["memory_count"] = len(mems)
    return period


def recall_period(lib, name: str) -> dict:
    """Answer 'what was that time like?' at the period layer.

    The chapter is located in the index by name, then opened: the
    answer is the period's atmosphere and dominant emotion, plus the
    gists of its emotionally strongest reachable memories (ranked by
    recall priority) — the shape of an era, not one event from it.
    """
    rows = lib.index.execute(
        "MATCH (p:Period) WHERE toLower(p.name) CONTAINS $name "
        "RETURN p.id AS pid",
        {"name": name.lower()},
    ).data()
    if not rows:
        raise KeyError(f"no period matching {name!r}")
    pid = rows[0]["pid"]
    return _recall_period_in(lib.partition(pid), pid)


def period_emergence(lib, cue_label: str) -> dict | None:
    """The static layer view of a period cue: affect / fragments / gist.

    (Superseded by spreading_activation for the demo; kept as the
    simple three-layer reading of what an era's own cue brings back.)
    """
    for pid in lib.periods_for_cue(cue_label):
        db = lib.partition(pid)
        rows = db.execute(
            "MATCH (p:Period)-[:TRIGGERED_BY]->(c:Cue {label: $label}) "
            "RETURN p.id AS id, p.name AS name, "
            "p.atmosphere AS atmosphere, "
            "p.dominant_emotion AS dominant_emotion",
            {"label": cue_label},
        ).data()
        if not rows:
            continue
        period = rows[0]
        frags = db.execute(
            "MATCH (m:Memory)-[:PART_OF]->(p:Period {id: $pid}) "
            "WHERE m.privacy <> 'SECRET_LOCKED' "
            "RETURN m.id AS id, m.shard AS shard, m.strength AS strength, "
            "m.emotion_weight AS emotion_weight, m.pathway AS pathway "
            "ORDER BY m.id",
            {"pid": period["id"]},
        ).data()
        for frag in frags:
            frag["priority"] = _priority(frag)
        frags.sort(key=lambda r: r["priority"], reverse=True)
        full = _recall_period_in(db, period["id"])
        return {
            "period": period["name"],
            "affect": {
                "atmosphere": period["atmosphere"],
                "dominant_emotion": period["dominant_emotion"],
            },
            "fragments": [
                {"id": f["id"], "shard": f["shard"]} for f in frags[:3]
            ],
            "gist": {
                "atmosphere": period["atmosphere"],
                "strongest": full["memories"][:3],
            },
        }
    return None


# ---------------------------------------------------------------------------
# Spreading activation: how things actually come back
# ---------------------------------------------------------------------------
# There is no fixed cascade. A cue lands on whichever node it is bound
# to — a memory, a period, sometimes just an emotion's neighbourhood —
# and activation spreads from there along the graph's edges, losing
# strength by edge type, accumulating where paths converge. A node
# surfaces when its activation crosses a threshold; the ORDER of
# surfacing is whatever the numbers do. Two canonical shapes fall out
# of the same mechanism: a concrete little object lands on one memory
# (a shard first, then the era opens around it, then the era rains
# shards, then the gist assembles), while an era's song lands on the
# period itself (the atmosphere first, then shards fall one by one,
# then one memory takes shape). No dice anywhere: same graph, same
# cue, same trace, every run.
#
# The spread runs inside chapters: within a chapter, shared-person
# links carry the full ABOUT factor, and activation circulates and
# accumulates until the chapter is lit. Across chapters, the same
# link must cross the index bridge: once the spread settles, each
# cross-chapter shared-person link transmits one damped *echo* —
# source activation x ABOUT x BRIDGE_FACTOR, capped by construction
# below the AFFECT threshold (1.0 x 0.55 x 0.30 = 0.165 < 0.30). An
# echo primes a memory in another era; it never ignites it, and it
# does not re-enter the spread. That asymmetry is the storage layout
# made audible: inside one chapter of life, one thing leads to
# another; to actually *reach* another chapter, the cue must be
# bound there too — the page-turn costs you the cascade.
SPREAD_INJECT = 0.55       # activation a cue injects at its landing node(s)
SPREAD_FACTORS = {         # how much activation survives each edge type
    "part_of": 0.85,       # memory <-> its period: the strongest bridge
    "same_event": 0.7,     # memories distilled from one witnessed event
    "about": 0.55,         # memories about the same person (not the self)
}
BRIDGE_FACTOR = 0.30       # a shared-person link that crosses chapters
                           # keeps only this fraction of the ABOUT
                           # factor: 0.55 x 0.30 = 0.165. Cross-chapter
                           # association is real, but it is faint —
                           # reaching another era takes a stronger cue
                           # or a luckier landing than reaching within
                           # the chapter you are already in.
ACT_AFFECT = 0.30          # a memory stirs: feeling, no content yet
ACT_FRAGMENT = 0.50        # a shard surfaces
ACT_GIST = 0.72            # the memory takes shape as its gist
ACT_PERIOD = 0.30          # a period opens: its atmosphere arrives
ACT_PERIOD_GIST = 0.60     # ... and assembles once enough shards are up
SELF_PERSON = "Theo"       # the self is the medium, not a cue: activation
                           # does not spread through him, or one name
                           # would light up the whole graph


def spreading_activation(lib, cue_label: str, max_rounds: int = 10) -> dict:
    """Run spreading activation from a cue and return the emergence trace.

    The trace lists, in order, every threshold crossing: memories
    surfacing as AFFECT, then FRAGMENT, then GIST; a period opening;
    a period's gist assembling (period activation >= ACT_PERIOD_GIST
    with at least three member memories at FRAGMENT or above). The
    result also carries "echoes": the damped one-shot transmissions
    across index bridges to memories in other chapters (see the
    bridge pass at the end) — primed, measurably, but below the
    surfacing threshold by construction.
    Buried and SECRET_LOCKED memories are not part of the spread graph
    at all — association does not reach what suppression holds down;
    they have their own cue paths (cue_resurface / sense_resurface).
    """
    mems = []
    for pid, rows in lib.execute_each(
        "MATCH (m:Memory) WHERE m.buried = false "
        "AND m.privacy <> 'SECRET_LOCKED' "
        "RETURN m.id AS id, m.shard AS shard, m.gist AS gist, "
        "m.emotion AS emotion, m.source_event AS source_event"
    ):
        for r in rows:
            r["period"] = pid
            mems.append(r)
    periods = lib.index.execute(
        "MATCH (p:Period) RETURN p.id AS id, p.name AS name, "
        "p.atmosphere AS atmosphere, p.dominant_emotion AS dominant_emotion"
    ).data()
    mem_period = {
        r["m"]: r["p"] for r in lib.index.execute(
            "MATCH (r:MemoryRef)-[:FILED_IN]->(p:Period) "
            "RETURN r.id AS m, p.id AS p"
        ).data()
    }
    about = {}
    for pid, rows in lib.execute_each(
        "MATCH (m:Memory)-[:ABOUT]->(p:Person) "
        "RETURN m.id AS m, p.name AS person"
    ):
        for r in rows:
            about.setdefault(r["m"], set()).add(r["person"])

    # Adjacency over ('m', id) / ('p', id) node keys.
    adj = {}

    def link(a, b, factor):
        adj.setdefault(a, []).append((b, factor))
        adj.setdefault(b, []).append((a, factor))

    mem_ids = {m["id"] for m in mems}
    for mid in mem_ids:
        if mid in mem_period:
            link(("m", mid), ("p", mem_period[mid]),
                 SPREAD_FACTORS["part_of"])
    by_event = {}
    for m in mems:
        by_event.setdefault(m["source_event"], []).append(m["id"])
    for ids in by_event.values():
        for a in ids:
            for b in ids:
                if a < b:
                    link(("m", a), ("m", b), SPREAD_FACTORS["same_event"])
    by_person = {}
    for mid, people in about.items():
        if mid not in mem_ids:
            continue
        for person in people:
            if person != SELF_PERSON:
                by_person.setdefault(person, []).append(mid)
    bridge_pairs = []   # (a, b, person): shared-person links that
                        # cross a chapter boundary — the index bridges
    for person, ids in by_person.items():
        for a in ids:
            for b in ids:
                if a >= b:
                    continue
                if mem_period.get(a) == mem_period.get(b):
                    # Inside one chapter, a shared person is a full
                    # bridge: one thing leads to another.
                    link(("m", a), ("m", b), SPREAD_FACTORS["about"])
                else:
                    # Across chapters the link is not part of the
                    # spread graph at all; it is collected as a bridge
                    # and evaluated once, damped, after the spread
                    # settles (see the echo pass below).
                    bridge_pairs.append((a, b, person))

    # Injection: the cue lands on every node bound to it, in whichever
    # chapter that binding lives. The injected activation is diluted
    # like every other cue effect (cue_energy): a cue bound to many
    # nodes lands softer on each of them.
    inject = SPREAD_INJECT * cue_energy(lib, cue_label)
    act = {}
    for pid in lib.period_order:
        db = lib.partition(pid)
        for r in db.execute(
            "MATCH (n)-[:TRIGGERED_BY]->(c:Cue {label: $label}) "
            "RETURN labels(n) AS labels, n.id AS id",
            {"label": cue_label},
        ).data():
            key = ("m" if r["labels"][0] == "Memory" else "p", r["id"])
            if key[0] == "p" or key[1] in mem_ids:
                act[key] = max(act.get(key, 0.0), inject)

    mem_by_id = {m["id"]: m for m in mems}
    period_by_id = {p["id"]: p for p in periods}
    levels = {}      # node key -> highest level reached
    gist_done = set()
    steps = []

    def record(round_no):
        for key in sorted(act, key=lambda k: (k[0], k[1])):
            a = act[key]
            reached = levels.get(key, 0)
            if key[0] == "m":
                for lvl, threshold, name in (
                        (1, ACT_AFFECT, "affect"),
                        (2, ACT_FRAGMENT, "fragment"),
                        (3, ACT_GIST, "gist")):
                    if a >= threshold and reached < lvl:
                        steps.append({
                            "round": round_no, "kind": "memory",
                            "id": key[1], "level": name, "activation": a,
                            "shard": mem_by_id[key[1]]["shard"],
                            "gist": mem_by_id[key[1]]["gist"],
                            "emotion": mem_by_id[key[1]]["emotion"],
                        })
                        reached = lvl
                levels[key] = reached
            else:
                if a >= ACT_PERIOD and reached < 1:
                    steps.append({
                        "round": round_no, "kind": "period",
                        "id": key[1], "level": "atmosphere",
                        "activation": a,
                        "name": period_by_id[key[1]]["name"],
                        "atmosphere": period_by_id[key[1]]["atmosphere"],
                        "dominant_emotion":
                            period_by_id[key[1]]["dominant_emotion"],
                    })
                    levels[key] = 1
                if (a >= ACT_PERIOD_GIST and key[1] not in gist_done
                        and sum(1 for mk, lv in levels.items()
                                if mk[0] == "m" and lv >= 2
                                and mem_period.get(mk[1]) == key[1]) >= 3):
                    steps.append({
                        "round": round_no, "kind": "period_gist",
                        "id": key[1], "level": "gist", "activation": a,
                        "name": period_by_id[key[1]]["name"],
                    })
                    gist_done.add(key[1])

    record(0)
    for round_no in range(1, max_rounds + 1):
        delta = {}
        for key in sorted(act, key=lambda k: (k[0], k[1])):
            if act[key] <= 0:
                continue
            for nb, factor in adj.get(key, []):
                contrib = act[key] * factor
                delta[nb] = 1 - (1 - delta.get(nb, 0.0)) * (1 - contrib)
        changed = False
        for key, gain in delta.items():
            new = 1 - (1 - act.get(key, 0.0)) * (1 - gain)
            if new > act.get(key, 0.0) + 1e-9:
                act[key] = new
                changed = True
        record(round_no)
        if not changed:
            break

    # The bridge pass: with the spread settled, each cross-chapter
    # shared-person link transmits a single damped echo from whichever
    # endpoint lit up to the one in the other chapter. An echo is
    # reported, not spread: it can stir a memory across the page
    # (it is primed, measurably), but at most SPREAD x ABOUT x
    # BRIDGE_FACTOR = 0.165 of activation it stays below the AFFECT
    # threshold — an echo alone never brings another era back.
    echoes = {}
    for a, b, person in bridge_pairs:
        for src, dst in ((a, b), (b, a)):
            src_act = act.get(("m", src), 0.0)
            if src_act <= 0:
                continue
            value = src_act * SPREAD_FACTORS["about"] * BRIDGE_FACTOR
            if value > echoes.get(dst, {}).get("activation", 0.0):
                echoes[dst] = {
                    "id": dst,
                    "period": mem_period.get(dst),
                    "activation": value,
                    "via_person": person,
                    "from": src,
                }
    return {"cue": cue_label, "steps": steps,
            "activation": {f"{k[0]}:{k[1]}": v for k, v in act.items()},
            "echoes": sorted(echoes.values(),
                             key=lambda e: (-e["activation"], e["id"]))}


# ---------------------------------------------------------------------------
# Surprise: the chapter break
# ---------------------------------------------------------------------------
# Event segmentation theory (Zacks and colleagues): the brain does not
# record life as one continuous stream — it cuts it into chapters, and
# the cuts land where prediction fails. A high-surprise event is a
# candidate cut. In this graph a Period's opening is marked at build
# time: if the period's earliest event carries surprise >=
# CHAPTER_BREAK_THRESHOLD, the event is linked (Event)-[:STARTS]->
# (Period) and the period's `opened_by` property names it. A shock of
# the same size landing mid-chapter opens nothing — the chapter was
# already running; it is simply remembered vividly inside it. The
# STARTS edge lives in the chapter's own partition, next to the event
# and the period it opened.

def chapter_breaks(lib) -> list:
    """Periods whose opening event was a surprise — the chapter breaks.

    Returns one dict per broken-open period: the period, its opening
    event, and that event's surprise. Periods that began routinely
    (their first event under the threshold) are absent.
    """
    out = []
    for pid, rows in lib.execute_each(
        "MATCH (e:Event)-[:STARTS]->(p:Period) "
        "RETURN p.id AS period_id, p.name AS period, e.id AS event_id, "
        "e.title AS title, e.date AS date, e.surprise AS surprise"
    ):
        out.extend(rows)
    out.sort(key=lambda r: lib.period_meta[r["period_id"]]["start_date"])
    return out
