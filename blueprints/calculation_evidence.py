"""Provisional raster calculation evidence policy; source detections remain inspectable."""
import math
import re

THRESHOLD = .60
OBJECT_TYPES = {"door", "window", "fixture"}


def raster(scene):
    return scene.tier == "detect" and scene.path.lower().endswith((".png", ".jpg", ".jpeg"))


def exclusion(entity, scene):
    if entity.attrs.get("calculation_excluded"):
        return str(entity.attrs.get("calculation_exclusion_reason", "Excluded detection"))
    if raster(scene) and entity.type in OBJECT_TYPES and entity.conf < THRESHOLD:
        return f"Detection score {entity.conf:.1%} is below the provisional {THRESHOLD:.0%} cutoff"
    return None


def _box(entity):
    return (min(x for x, y in entity.geom), min(y for x, y in entity.geom),
            max(x for x, y in entity.geom), max(y for x, y in entity.geom))


def _outside_plan(entity, scene):
    bounds = [e for e in scene.entities if e.type in ("level", "room") and e.page == entity.page and len(e.geom) >= 3]
    if not entity.geom or not bounds:
        return False
    boxes = [_box(e) for e in bounds]
    a, b, c, d = min(p[0] for p in boxes), min(p[1] for p in boxes), max(p[2] for p in boxes), max(p[3] for p in boxes)
    x0, y0, x1, y1 = _box(entity)
    return x1 < a-.03 or x0 > c+.03 or y1 < b-.03 or y0 > d+.03


def classify_review(scene, entity, decision):
    """Use explicit classification, or narrowly migrate old cached reviews with corroborating geometry."""
    observed = decision.observed_type if decision.classification_clear else None
    legend = decision.non_object_kind == "legend" if observed == "non_object" else False
    reason = decision.reason.strip()
    # Legacy payloads did not distinguish object classification from endpoint certainty.
    if observed is None and reason.casefold() == "legend item outside floor plan":
        observed, legend = "non_object", True
    if observed is None and re.match(r"^Exterior window(?: on\b|/opening\b)", reason, re.I):
        observed = "window"
    if observed == "non_object" and legend and _outside_plan(entity, scene):
        entity.attrs.update(calculation_excluded=True, calculation_exclusion_reason="Legend symbol outside the floor plan",
                            room_link_status="excluded", room_link_candidates="[]")
        return True
    if observed in OBJECT_TYPES and observed != entity.type:
        # Fixture subtype classification needs its own evidence; this migration handles door/window confusion.
        if observed not in ("door", "window") or entity.type not in ("door", "window"):
            return False
        entity.attrs.setdefault("detection_original_type", entity.type)
        entity.type = observed
        entity.attrs.update(calculation_type_reason=reason, calculation_type_origin="vision")
    return False


def deduplicate_corrected(scene):
    """A relabelled window must not count twice when the detector already found the same window."""
    for entity in scene.entities:
        if not entity.attrs.get("detection_original_type") or not entity.geom:
            continue
        a, b, c, d = _box(entity)
        for other in scene.entities:
            if other is entity or other.page != entity.page or other.type != entity.type or not other.geom:
                continue
            if other.attrs.get("calculation_excluded") or other.conf <= entity.conf:
                continue
            x0, y0, x1, y1 = _box(other)
            overlap = max(0, min(c, x1)-max(a, x0))*max(0, min(d, y1)-max(b, y0))
            union = (c-a)*(d-b)+(x1-x0)*(y1-y0)-overlap
            if union > 0 and overlap/union >= .5:
                entity.attrs.update(calculation_excluded=True, calculation_duplicate_of=other.id,
                                    calculation_exclusion_reason=f"Corrected to {entity.type}; duplicate of {other.id}")
                break


def score(items, rooms=()):
    """Geometric-mean detection support, bounded by requested-room support; not calibrated accuracy."""
    typical = math.exp(sum(math.log(max(e.conf, 1e-9)) for e in items)/len(items)) if items else (1.0 if rooms else 0.0)
    return min(typical, min((r.conf for r in rooms), default=1.0))
