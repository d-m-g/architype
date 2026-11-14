"""Queries over both endpoints of a room connection, counting each object once."""
import json

ROOM_FILTERS = {"connected_room_id": "id", "connected_room_number": "number", "connected_room_label": "label",
                "connected_room_type": "subtype", "connected_room_apartment": "apartment", "connected_room_level": "level"}


def _same(a, b):
    return a.strip().casefold() == b.strip().casefold() if isinstance(a, str) and isinstance(b, str) else a == b


def targets(query, scene):
    filters = {ROOM_FILTERS[k]: v for k, v in query.where.items() if k in ROOM_FILTERS}
    return [r for r in scene.entities if r.type == "room" and
            all(_same(getattr(r, k), v) for k, v in filters.items())] if filters else []


def endpoints(entity, scene):
    rooms = None
    result = set()
    for suffix in ("", "2"):
        room_id = entity.attrs.get("room_id"+suffix)
        if room_id:
            result.add(room_id)
            continue
        # Older CAD parsers record room numbers rather than ids. Never resolve ambiguous numbers.
        number = entity.attrs.get("room"+suffix)
        label = entity.attrs.get("room_label"+suffix)
        if number is None and label is None:
            continue
        if rooms is None:
            rooms = [r for r in scene.entities if r.type == "room" and r.page == entity.page]
        matches = [r for r in rooms if (number is None or _same(r.number, number))
                   and (label is None or _same(r.label, label))
                   and (entity.apartment is None or r.apartment == entity.apartment)]
        if len(matches) == 1:
            result.add(matches[0].id)
    return result


def uncertainty(query, scene, candidates):
    """A plausible unresolved endpoint prevents a confident undercount in the requested scope."""
    if not ROOM_FILTERS.keys() & query.where.keys():
        return None
    wanted = {r.id for r in targets(query, scene)}
    if not wanted:
        return "No room matches the requested connection filters; cannot verify a per-room count."
    known_rooms = {r.id for r in scene.entities if r.type == "room"}
    for entity in candidates:
        linked = endpoints(entity, scene)
        if linked - known_rooms:
            return f"Cannot verify the per-room count: {entity.id} references a missing room."
        if linked & wanted:
            continue  # already counted once, even if its other endpoint is unknown
        status = entity.attrs.get("room_link_status")
        if status in ("partial", "uncertain"):
            try:
                possible = json.loads(entity.attrs.get("room_link_candidates", "[]"))
            except (TypeError, ValueError):
                return f"Cannot verify the per-room count: {entity.id} has invalid connection candidates."
            if not isinstance(possible, list) or not all(isinstance(item, str) for item in possible):
                return f"Cannot verify the per-room count: {entity.id} has invalid connection candidates."
            if (not possible and status == "uncertain") or wanted.intersection(possible):
                return f"Cannot verify the per-room count: {entity.id} has an unresolved room connection."
        elif not linked:
            # No relationship metadata is not evidence of zero connections.
            return f"Cannot verify the per-room count: {entity.id} has no room connection data."
    return None
