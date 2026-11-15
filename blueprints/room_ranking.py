"""Room rankings proved from count intervals, without guessing unresolved endpoints."""
import json
import re

from . import calculation_evidence as policy, room_queries
from .schemas import Answer, TraceStep


def requested(question):
    # Only a complete, unscoped question takes the free shortcut. Other wording stays with the planner/agent.
    match = re.fullmatch(r"\s*which room has (?:the )?(most|fewest) (doors|windows)\s*[?.!]?\s*", question, re.I)
    return (match[2].lower()[:-1], match[1].lower()) if match else None


def rank(scene, kind="door", order="most"):
    if kind not in ("door", "window") or order not in ("most", "fewest"):
        raise ValueError("Unsupported ranking")
    rooms = [e for e in scene.entities if e.type == "room"]
    if not rooms or kind not in scene.covers or "room" not in scene.covers:
        return None
    ids = {r.id for r in rooms}
    counts = {r.id: [] for r in rooms}
    possible = {r.id: [] for r in rooms}
    excluded, unresolved = [], []
    for e in scene.entities:
        if e.type != kind:
            continue
        reason = policy.exclusion(e, scene)
        if reason:
            excluded.append({"id": e.id, "reason": reason, "score": e.conf})
            continue
        linked = room_queries.endpoints(e, scene)
        if linked - ids:
            return None  # broken metadata is not a bounded endpoint ambiguity
        for rid in linked:
            counts[rid].append(e.id)
        status = e.attrs.get("room_link_status")
        if status in ("uncertain", "partial") or not linked:
            unresolved.append(e.id)
            try:
                candidates = json.loads(e.attrs.get("room_link_candidates", "[]"))
            except (TypeError, ValueError):
                candidates = []
            if not isinstance(candidates, list) or any(not isinstance(x, str) or x not in ids for x in candidates):
                candidates = []
            # Empty/malformed candidates mean unknown, never proof that no room is affected.
            candidates = set(candidates) if candidates else {r.id for r in rooms if r.page == e.page}
            for rid in candidates - linked:
                possible[rid].append(e.id)
    rows = [{"id": r.id, "label": " ".join(filter(None, [r.label, r.number])) + (f" (apartment {r.apartment})" if r.apartment else ""),
             "lower": len(counts[r.id]), "upper": len(counts[r.id]) + len(possible[r.id])} for r in rooms]
    if unresolved and not any(counts.values()):
        return None  # no room relationships: preserve the existing planner/agent fallback
    winners = [r for r in rows if all(
        (r["lower"] > other["upper"] if order == "most" else r["upper"] < other["lower"])
        for other in rows if other is not r)]
    # Exact ties can be reported, but overlapping uncertain intervals cannot be called a tie.
    if not winners and all(r["lower"] == r["upper"] for r in rows):
        best = (max if order == "most" else min)(r["lower"] for r in rows)
        winners = [r for r in rows if r["lower"] == best]
    winner_ids = {r["id"] for r in winners}
    context = [r["id"] for r in winners]
    included = list(dict.fromkeys(i for rid in context for i in counts[rid]))
    for e in scene.entities:
        if e.type == kind and e.id not in included and not policy.exclusion(e, scene):
            excluded.append({"id": e.id, "reason": "Unresolved connection; included in possible count bounds" if e.id in unresolved
                             else "Connected to another room", "score": e.conf})
    score = policy.score([e for e in scene.entities if e.id in included], rooms) if winners else 0
    def span(r):
        return str(r["lower"]) if r["lower"] == r["upper"] else f'{r["lower"]}–{r["upper"]}'
    explanation = "; ".join(f'{r["label"] or r["id"]}: {span(r)} {kind}s' for r in winners)
    if winners:
        explanation += ". " + ("The unresolved connections cannot change the winner." if unresolved else "All qualifying connections are resolved.")
        explanation += " Ranking uses qualifying detected objects; it does not guarantee the detector found every object."
    else:
        explanation = "Cannot establish a winner: possible count ranges overlap. " + "; ".join(
            f'{r["label"] or r["id"]}: {span(r)}' for r in sorted(rows, key=lambda r: r["lower"], reverse=order == "most")[:6])
    supported = bool(winners) and (not policy.raster(scene) or score + 1e-12 >= policy.THRESHOLD)
    if winners and not supported:
        explanation = f"Ranking source support {score:.0%} is below the provisional {policy.THRESHOLD:.0%} cutoff. " + explanation
    audit = {"threshold": policy.THRESHOLD, "queries": [{"name": "ranking", "type": kind,
        "included": included, "context": context, "excluded": excluded,
        "unresolved": unresolved, "score": score, "coverage": 1}]}
    return Answer(answer=", ".join(r["label"] or r["id"] for r in winners) if supported else None,
                  confidence=score, tier=scene.tier, evidence=list(dict.fromkeys(context + included + unresolved)),
                  interpretation=explanation if supported else None, error=None if supported else explanation,
                  confidence_note="Source support score; ranking verified against every room's lower and upper count bounds, not a calibrated probability.",
                  trace=[TraceStep(step="rank_rooms", detail=json.dumps({"type": kind, "order": order, "rooms": rows, "unresolved": unresolved})),
                         TraceStep(step="calculation_evidence", detail=json.dumps(audit))])


def answer_question(question, scene):
    intent = requested(question)
    if not intent:
        return None
    result = rank(scene, *intent)
    if result is None or result.answer is not None:
        return result
    # Spend only when resolving a connection can actually change the outcome.
    if policy.raster(scene) and (result.error or "").startswith("Cannot establish a winner"):
        from .brain.room_link_vision import resolve
        steps = resolve(scene, intent[0])
        result = rank(scene, *intent)
        if result is not None:
            result.trace = steps + result.trace
    return result
