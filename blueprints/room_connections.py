"""Rank rooms using recorded source relationships. No model calls or invented geometry links."""
import hashlib
import json
import math
import re
import time
from dataclasses import dataclass

from .schemas import Answer, Entity, Scene, TraceStep


def _normal(value):
    return str(value).strip().casefold() if value is not None else ""


def room_name(room: Entity, show_page=False) -> str:
    name = room.label or "Room"
    if room.number and room.number not in name:
        name += f" {room.number}"
    context = [f"apartment {room.apartment}" if room.apartment else None,
               room.level or (f"page {room.page+1}" if show_page else None)]
    return name + (" ("+", ".join(part for part in context if part)+")" if any(context) else "")


@dataclass
class RoomRanking:
    answer: Answer
    rows: list[dict]
    unresolved: list[dict]
    winners: list[str]

    def tool_result(self, result_id: str, limit: int = 5) -> dict:
        """Bound model context; the server retains the complete answer/evidence behind result_id."""
        limit = max(1, min(int(limit), 20))
        return {"result_id": result_id, "answer": self.answer.answer, "unit": self.answer.unit,
                "error": self.answer.error, "complete": not self.unresolved and self.answer.answer is not None,
                "winner_count": len(self.winners), "winning_room_ids": self.winners[:20],
                "winning_room_ids_truncated": len(self.winners) > 20, "ranking_truncated": len(self.rows) > limit,
                "ranking": [{k: v for k, v in row.items() if k != "object_ids"} for row in self.rows[:limit]],
                "total_rooms": len(self.rows), "evidence_count": len(self.answer.evidence),
                "unresolved_count": len(self.unresolved), "unresolved_examples": self.unresolved[:5]}


def rank_rooms(scene: Scene, *, object_type="door", order="desc", apartment=None, level=None, page=None,
               include_outdoor=True) -> RoomRanking:
    """A source object counts once at each connected room. Unknown/ambiguous links block an exact ranking."""
    started = time.perf_counter()
    if object_type not in ("door", "window", "fixture") or order not in ("asc", "desc"):
        raise ValueError("Choose door/window/fixture and asc/desc")
    if not isinstance(include_outdoor, bool):
        raise ValueError("include_outdoor must be a boolean")
    if page is not None and (isinstance(page, bool) or not isinstance(page, int) or not 0 <= page < len(scene.pages)):
        raise ValueError("page must be an available zero-based page index")
    all_rooms = [e for e in scene.entities if e.type == "room"]
    rooms = [r for r in all_rooms if (apartment is None or _normal(r.apartment) == _normal(apartment))
             and (level is None or _normal(r.level) == _normal(level)) and (page is None or r.page == page)
             and (include_outdoor or not r.outdoor)]
    by_id = {r.id: r for r in all_rooms}
    by_number, by_label = {}, {}
    for room in all_rooms:
        if _normal(room.number):
            by_number.setdefault((room.page, _normal(room.number)), []).append(room)
        if _normal(room.label):
            by_label.setdefault((room.page, _normal(room.label)), []).append(room)
    counts = {r.id: set() for r in rooms}
    pages = {r.page for r in rooms}
    objects = [e for e in scene.entities if e.type == object_type and e.page in pages]
    unresolved = []

    def finish(error=None, rows=None, winners=None, evidence=None, value=None):
        evidence = evidence or []
        # Ranking also depends on the competing objects. Adding 100%-scored room contexts must not
        # raise lower object scores, so take the weaker geometric mean of the two source groups.
        groups = [rooms, objects] if value is not None else []
        score = min((math.exp(sum(math.log(max(e.conf, 1e-9)) for e in group)/len(group)) for group in groups if group), default=0)
        formula = f"{'Maximum' if order == 'desc' else 'Minimum'} distinct {object_type} count per room = {value}" if value is not None else None
        names = [room_name(by_id[r], len(pages) > 1) for r in (winners or [])]
        caption = None
        if names:
            shown = "; ".join(names[:6]) + (f"; and {len(names)-6} more rooms" if len(names) > 6 else "")
            caption = (f"Tie between {len(names)} rooms: " if len(names) > 1 else "") + shown + f" — {value} {object_type}{'' if value == 1 else 's'}" + (" each." if len(names) > 1 else ".")
            caption += " Counted from the scene's recorded room connections; rooms and counted objects are highlighted."
        trace = [TraceStep(step="room_connections", detail="Used recorded room/object links; no geometry or model inference."),
                 TraceStep(step="execute", ms=(time.perf_counter()-started)*1000, detail=formula or error)]
        if value is not None:
            trace.append(TraceStep(step="verify_answer", detail="Ranking, ties, room IDs and unique object IDs verified together without a model call."))
        answer = Answer(answer=value, unit="count" if value is not None else None, tier=scene.tier,
                        confidence=score, confidence_note="Source-derived score: the weaker typical score of rooms and object records considered in the comparison. Not a calibrated probability of completeness or correctness." if value is not None else None,
                        evidence=evidence, formula=formula, interpretation=caption, error=error, trace=trace,
                        assumptions={"count_rule": "Count each source object once for every room it connects to; drawing copies are not extra objects.",
                                     "scope": "All matching rooms on the loaded drawing; not unprovided floors or models."})
        return RoomRanking(answer, rows or [], unresolved, winners or [])

    if not rooms:
        return finish("No rooms match this scope; a room ranking cannot be determined.")
    if "room" not in scene.covers:
        return finish("The reader does not report complete room coverage; an all-room ranking cannot be determined.")
    if object_type not in scene.covers:
        return finish(f"The reader does not report complete {object_type} coverage; a room ranking cannot be determined.")
    if not objects:
        return finish(f"No source {object_type} objects are available to establish a room ranking.")
    for obj in objects:
        connected = set()
        provided = False
        for suffix in ("", "2"):
            explicit = obj.attrs.get("room_id" + suffix)
            number = obj.attrs.get("room" + suffix)
            label = obj.attrs.get("room_label" + suffix)
            if not any(_normal(v) for v in (explicit, number, label)):
                continue
            provided = True
            if explicit:
                matches = [by_id[explicit]] if explicit in by_id and by_id[explicit].page == obj.page else []
            else:
                if _normal(number):
                    candidates = {r.id: r for r in by_number.get((obj.page, _normal(number)), [])}
                    if number in by_id and by_id[number].page == obj.page:
                        candidates[number] = by_id[number]
                    matches = list(candidates.values())
                else:
                    matches = by_label.get((obj.page, _normal(label)), [])
                if len(matches) > 1 and label:
                    matches = [r for r in matches if _normal(r.label) == _normal(label)]
            if len(matches) != 1:
                unresolved.append({"object_id": obj.id, "endpoint": "room"+suffix, "reason": "ambiguous room" if matches else "room not found"})
            else:
                connected.add(matches[0].id)
        if not provided:
            unresolved.append({"object_id": obj.id, "reason": "no recorded room connection"})
        for room_id in connected.intersection(counts):
            counts[room_id].add(obj.id)
    rows = [{"room_id": r.id, "name": room_name(r, len(pages) > 1), "page": r.page, "count": len(counts[r.id]),
             "object_ids": sorted(counts[r.id])} for r in rooms]
    rows.sort(key=lambda r: ((-1 if order == "desc" else 1)*r["count"], r["name"], r["room_id"]))
    if unresolved:
        return finish(f"Cannot verify the room ranking: {len({u['object_id'] for u in unresolved})} {object_type} objects have missing or ambiguous room connections.", rows=rows)
    value = rows[0]["count"]
    winners = [r["room_id"] for r in rows if r["count"] == value]
    evidence = sorted(set(winners).union(*(counts[r] for r in winners)))
    return finish(rows=rows, winners=winners, evidence=evidence, value=value)


def ranking_id(scene: Scene, arguments: dict) -> str:
    payload = json.dumps([scene.model_dump(mode="json"), arguments], sort_keys=True, ensure_ascii=False)
    return "rooms-" + hashlib.sha256(payload.encode()).hexdigest()[:16]


def direct_room_ranking(question: str, scene: Scene) -> Answer | None:
    """Narrow full-sentence shortcut. Compound/scoped/unrecognised requests continue to the normal planner."""
    match = re.fullmatch(r"\s*(?:which|what) room(?:s)? (?:has|have) (?:the )?(most|fewest|least) (doors|windows)\s*[?.]?\s*", question, re.I)
    if not match:
        return None
    return rank_rooms(scene, object_type=match[2].lower()[:-1], order="desc" if match[1].lower() == "most" else "asc").answer
