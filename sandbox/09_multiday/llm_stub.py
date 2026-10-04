"""Phase 10: a fake Mode Selection that speaks the agreed JSON schema.

The real one is the LLM team's (Sec. 4.3.6). This stands in so the whole
daily loop can run end to end without them. It must be swappable for the
real thing WITHOUT changing the pipeline - so the contract is the schema,
not the function.

It also deliberately misbehaves sometimes, so the retry-and-fallback path
(Sec. 4.5.6) actually gets exercised instead of sitting untested.
"""
import json
import random

import jsonschema

# ===== the contract. Confirm these field names with the LLM team. ==========
MODE_SELECTION_SCHEMA = {
    "type": "object",
    "required": ["agent_id", "day", "selections"],
    "properties": {
        "agent_id": {"type": "string"},
        "day": {"type": "integer", "minimum": 1},
        "selections": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["trip_id", "mode"],
                "properties": {
                    "trip_id": {"type": "string"},
                    "mode": {"enum": ["walk", "bike", "car", "jeepney"]},
                    "route_id": {"type": ["string", "null"]},
                    "reasoning": {"type": "string"},
                },
            },
        },
    },
}

K_MAX = 3          # retries before falling back (Sec. 4.5.6)


def _choose(alts, habits, rng):
    """Habit-weighted pick among feasible alternatives, nudged by speed."""
    if not alts:
        return None
    scored = []
    fastest = min(a["estimated_total_time_s"] for a in alts)
    for a in alts:
        habit = habits.get(a["mode"], 0.0)
        speed_bonus = fastest / a["estimated_total_time_s"]      # 1.0 for fastest
        scored.append((habit * 0.7 + speed_bonus * 0.3, a))
    scored.sort(key=lambda t: t[0], reverse=True)
    top = scored[0][1]
    return top


def select_modes(agent_id, day, trips_with_alts, habits, rng=None,
                 misbehave=False):
    """Stand-in for the LLM. Returns a Mode Selection object (maybe invalid)."""
    rng = rng or random.Random(0)
    selections = []
    for trip_id, alts in trips_with_alts.items():
        pick = _choose(alts, habits, rng)
        if pick is None:
            continue
        selections.append({
            "trip_id": trip_id,
            "mode": pick["mode"],
            "route_id": pick.get("route_id"),
            "reasoning": "habit weight %.2f; %d s estimated"
                         % (habits.get(pick["mode"], 0.0),
                            pick["estimated_total_time_s"]),
        })

    out = {"agent_id": agent_id, "day": day, "selections": selections}

    if misbehave:
        # exactly the kind of thing a real LLM does: a mode that isn't real
        out["selections"][0]["mode"] = "tricycle"
    return out


def validate(obj):
    """True if the object matches the contract, else (False, message)."""
    try:
        jsonschema.validate(obj, MODE_SELECTION_SCHEMA)
        return True, None
    except jsonschema.ValidationError as e:
        return False, e.message


def select_with_fallback(agent_id, day, trips_with_alts, habits, log,
                         misbehave_on=(), persist_on=()):
    """Call the stub, validate, retry k_max times, then fall back and flag.

    Fallback rule (Sec. 4.5.6): assign the agent's highest-weight FEASIBLE
    mode and flag the agent-day.
    """
    for attempt in range(1, K_MAX + 1):
        # misbehave_on: recovers on the last attempt (tests RETRY)
        # persist_on:   never recovers                (tests FALLBACK)
        misbehave = ((day in misbehave_on) and attempt < K_MAX) or (day in persist_on)
        obj = select_modes(agent_id, day, trips_with_alts, habits,
                           misbehave=misbehave)
        ok, why = validate(obj)
        if ok:
            # extra semantic check the schema cannot express: was that mode
            # actually offered for that trip?
            bad = [s["trip_id"] for s in obj["selections"]
                   if s["mode"] not in
                   {a["mode"] for a in trips_with_alts.get(s["trip_id"], [])}]
            if not bad:
                if attempt > 1:
                    log("  llm_stub accepted on attempt %d" % attempt)
                return obj, False
            why = "mode not among offered alternatives for %s" % ", ".join(bad)
        log("  llm_stub attempt %d rejected: %s" % (attempt, why))

    # --- fallback ---------------------------------------------------------
    log("  FALLBACK after %d attempts - using highest-weight feasible mode" % K_MAX)
    selections = []
    for trip_id, alts in trips_with_alts.items():
        if not alts:
            continue
        best = max(alts, key=lambda a: habits.get(a["mode"], 0.0))
        selections.append({"trip_id": trip_id, "mode": best["mode"],
                           "route_id": best.get("route_id"),
                           "reasoning": "FALLBACK: highest-weight feasible mode"})
    return {"agent_id": agent_id, "day": day, "selections": selections}, True


def update_habits(habits, outcomes, learning_rate=0.25):
    """Stand-in for End-of-Day Reflection (Sec. 4.3.7).

    The real version is the LLM's judgment. This just moves weight away from
    modes that ran late and toward modes that ran on time.
    """
    new = dict(habits)
    for o in outcomes:
        mode = o["realized_mode"]
        # delay_ratio 1.0 = as expected; >1 = worse than expected
        signal = 1.0 - min(1.0, max(-1.0, (o["delay_ratio"] - 1.0)))
        new[mode] = max(0.0, new.get(mode, 0.0) + learning_rate * signal * 0.2)
    total = sum(new.values()) or 1.0
    return {k: round(v / total, 3) for k, v in new.items()}
