"""CPU-only room association for raster detections. Uncertain endpoints stay explicitly unresolved."""
import hashlib
import json
import math
from pathlib import Path

VERSION = "image-room-links@1"


def _box(entity, width, height):
    points = entity.geom
    return (min(x for x, y in points)*width, min(y for x, y in points)*height,
            max(x for x, y in points)*width, max(y for x, y in points)*height)


def _point_in(point, polygon):
    x, y = point
    inside = False
    for (ax, ay), (bx, by) in zip(polygon, polygon[1:]+polygon[:1]):
        if (ay > y) != (by > y) and x < (bx-ax)*(y-ay)/(by-ay)+ax:
            inside = not inside
    return inside


def _distance(point, polygon):
    x, y = point
    distance = float("inf")
    for (ax, ay), (bx, by) in zip(polygon, polygon[1:]+polygon[:1]):
        dx, dy = bx-ax, by-ay
        fraction = max(0, min(1, ((x-ax)*dx+(y-ay)*dy)/(dx*dx+dy*dy))) if dx or dy else 0
        distance = min(distance, math.hypot(x-ax-fraction*dx, y-ay-fraction*dy))
    return distance


def attach_room_links(scene, image_file: Path) -> bool:
    """Reuse detected boxes, room boxes and wall pixels; never invoke a model or alter object scores."""
    rooms = [e for e in scene.entities if e.type == "room" and len(e.geom) >= 3]
    objects = [e for e in scene.entities if e.type in ("door", "window", "fixture")]
    if not rooms or not objects or "room" not in scene.covers or len(scene.pages) != 1:
        return False
    room_signature = hashlib.sha256(json.dumps([(r.id, r.geom, r.label, r.number, r.subtype, r.apartment, r.level, r.outdoor)
                                                for r in rooms], sort_keys=True).encode()).hexdigest()
    def signature(obj):
        return hashlib.sha256(json.dumps([VERSION, room_signature, obj.geom, obj.type]).encode()).hexdigest()[:20]
    pending = [e for e in objects if e.attrs.get("room_link_geometry") != signature(e)
               and (str(e.attrs.get("room_link_version", "")).startswith("image-room-links@") or not any(
                   e.attrs.get(k) for k in ("room", "room2", "room_id", "room_id2")))]
    if not pending:
        return False
    import numpy as np
    from PIL import Image
    with Image.open(image_file) as opened:
        image = opened.convert("RGB")
        image.thumbnail((2048, 2048))
    width, height = image.size
    dark = np.max(np.asarray(image), axis=2) < 100
    scale = max(width, height)/1000
    tolerance = max(2.5, 5*scale)
    polygons = {r.id: [(x*width, y*height) for x, y in r.geom] for r in rooms}
    boxes = {r.id: _box(r, width, height) for r in rooms}
    by_id = {r.id: r for r in rooms}
    areas = {r.id: (boxes[r.id][2]-boxes[r.id][0])*(boxes[r.id][3]-boxes[r.id][1]) for r in rooms}

    def owners(point):
        hits = [r for r in rooms if _point_in(point, polygons[r.id])]
        if len(hits) > 1:
            non_corridors = [r for r in hits if r.subtype != "corridor"]
            hits = non_corridors or hits  # a corridor box may include a neighbouring rectangular room
        if not hits:
            near = sorted((_distance(point, polygons[r.id]), r.id) for r in rooms)
            if near[0][0] <= tolerance and (len(near) == 1 or near[1][0]-near[0][0] > max(1, scale)):
                hits = [by_id[near[0][1]]]
        if len(hits) > 1:
            hits.sort(key=lambda r: areas[r.id])
            a, b = boxes[hits[0].id], boxes[hits[1].id]
            overlap = max(0, min(a[2], b[2])-max(a[0], b[0]))*max(0, min(a[3], b[3])-max(a[1], b[1]))
            if areas[hits[0].id] < areas[hits[1].id]*.5 and overlap > areas[hits[0].id]*.95:
                hits = hits[:1]
        # A bounding box may span a neighbouring hallway. Reject an endpoint if a solid wall
        # separates it from the room's centre. This can abstain on concave rooms; it cannot prove a link.
        visible = []
        for room in hits:
            box = boxes[room.id]
            centre = ((box[0]+box[2])/2, (box[1]+box[3])/2)
            length = math.dist(point, centre)
            run = longest = 0
            for i in range(max(1, round(4*scale)), max(1, int(length*.85))):
                x = round(point[0]+(centre[0]-point[0])*i/max(length, 1))
                y = round(point[1]+(centre[1]-point[1])*i/max(length, 1))
                run = run+1 if 0 <= x < width and 0 <= y < height and dark[y, x] else 0
                longest = max(longest, run)
            if longest < max(3, round(4*scale)):
                visible.append(room.id)
        return set(visible)

    def patch_fraction(x0, y0, x1, y1):
        x0, y0, x1, y1 = int(max(0, x0)), int(max(0, y0)), int(min(width, x1)), int(min(height, y1))
        return float(dark[y0:y1, x0:x1].mean()) if x1 > x0 and y1 > y0 else 0

    def wall_candidates(box):
        x0, y0, x1, y1 = box
        band, pad = max(2, round(2*scale)), max(4, round(6*scale))
        candidates = []
        for horizontal in (True, False):
            lo, hi = (y0, y1) if horizontal else (x0, x1)
            profile = []
            for p in range(max(0, int(lo-tolerance)), int(hi+tolerance)+1):
                if horizontal:
                    left = patch_fraction(x0-pad, p-band/2, x0, p+band/2+1)
                    right = patch_fraction(x1, p-band/2, x1+pad, p+band/2+1)
                    middle = patch_fraction(x0+(x1-x0)*.25, p-band/2, x1-(x1-x0)*.25, p+band/2+1)
                else:
                    left = patch_fraction(p-band/2, y0-pad, p+band/2+1, y0)
                    right = patch_fraction(p-band/2, y1, p+band/2+1, y1+pad)
                    middle = patch_fraction(p-band/2, y0+(y1-y0)*.25, p+band/2+1, y1-(y1-y0)*.25)
                score = min(left, right)
                if score >= .5 and middle < .4:
                    profile.append((p, score))
            groups = []
            for point in profile:
                if not groups or point[0] > groups[-1][-1][0]+1:
                    groups.append([])
                groups[-1].append(point)
            for group in groups:
                p = (group[0][0]+group[-1][0])/2
                offset = (group[-1][0]-group[0][0])/2 + max(3, 4*scale)
                probes = [((x0+x1)/2, p-offset), ((x0+x1)/2, p+offset)] if horizontal else [(p-offset, (y0+y1)/2), (p+offset, (y0+y1)/2)]
                candidates.append((max(score for _, score in group), probes))
        return sorted(candidates, key=lambda item: -item[0])

    for obj in pending:
        for key in list(obj.attrs):
            if key.startswith("room_link_") or key in {"room", "room2", "room_id", "room_id2", "room_label", "room_label2", "room_type", "room_type2"}:
                del obj.attrs[key]
        obj.attrs.update(room_link_version=VERSION, room_link_geometry=signature(obj))
        if len(obj.geom) < 3:
            obj.attrs.update(room_link_status="uncertain", room_link_candidates=json.dumps([r.id for r in rooms]), room_link_reason="Object has no located geometry")
            continue
        x0, y0, x1, y1 = box = _box(obj, width, height)
        margin = max(tolerance, max(x1-x0, y1-y0)*.5)
        possible = {r.id for r in rooms if boxes[r.id][0] <= x1+margin and boxes[r.id][2] >= x0-margin and boxes[r.id][1] <= y1+margin and boxes[r.id][3] >= y0-margin}
        known, remaining, method = set(), set(possible), "unresolved geometry"
        if obj.type == "fixture":
            candidate = owners(((x0+x1)/2, (y0+y1)/2))
            if len(candidate) == 1:
                known, remaining, method = candidate, set(), "room containment"
        elif obj.type == "window":
            offset = tolerance + (y1-y0)/2 if x1-x0 >= y1-y0 else tolerance + (x1-x0)/2
            probes = [((x0+x1)/2, (y0+y1)/2-offset), ((x0+x1)/2, (y0+y1)/2+offset)] if x1-x0 >= y1-y0 else [((x0+x1)/2-offset, (y0+y1)/2), ((x0+x1)/2+offset, (y0+y1)/2)]
            sets = [owners(p) for p in probes]
            if all(len(s) <= 1 for s in sets) and any(sets):
                known = set().union(*sets)
                indoor = {id for id in known if not by_id[id].outdoor}
                known, remaining, method = indoor or known, set(), "window edge probes"
        else:
            # Adjacent swing boxes can look like a single opening across a hallway. Without
            # room segmentation their endpoints are ambiguous; do not invent a connection.
            touching = False
            for other in objects:
                if other is obj or other.type != "door" or len(other.geom) < 3:
                    continue
                a, b, c, d = _box(other, width, height)
                x_overlap, y_overlap = min(x1, c)-max(x0, a), min(y1, d)-max(y0, b)
                if (x_overlap > 0 and y_overlap >= -tolerance) or (y_overlap > 0 and x_overlap >= -tolerance):
                    touching = True
                    break
            candidates = [] if touching else wall_candidates(box)
            if candidates:
                top = candidates[0][0]
                proposals = []
                ambiguous = False
                for score, probes in candidates:
                    if score < top-.1:
                        continue
                    sets = [owners(p) for p in probes]
                    if any(len(s) > 1 for s in sets):
                        ambiguous = True
                        continue
                    union = set().union(*sets)
                    if union:
                        proposals.append((union, all(sets)))
                if not ambiguous and proposals:
                    known = set.intersection(*(p[0] for p in proposals))
                    complete = all(p[0] == known and p[1] and len(p[0]) == 2 for p in proposals)
                    remaining = set() if complete else possible-known
                    method = "wall-supported door probes"
        for suffix, room_id in zip(("", "2"), sorted(known)):
            room = by_id[room_id]
            obj.attrs["room_id"+suffix] = room_id
            if room.number:
                obj.attrs["room"+suffix] = room.number
            obj.attrs["room_label"+suffix] = room.label or room.subtype or "room"
            obj.attrs["room_type"+suffix] = room.subtype or "other"
        complete = bool(known and not remaining) if obj.type != "door" else bool(known and len(known) == 2 and not remaining)
        obj.attrs.update(room_link_status="linked" if complete else "partial" if known else "uncertain",
                         room_link_candidates=json.dumps(sorted(remaining)), room_link_reason=method)
    return bool(pending)
