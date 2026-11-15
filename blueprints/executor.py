"""Lane A. Runs a QueryPlan against a Scene. No drawing-specific code belongs here: it only reads the schema."""
import ast
import math
import operator
import time
import json

from .schemas import Answer, Entity, Query, QueryPlan, Scene, TraceStep
from . import room_queries
from . import calculation_evidence as policy

_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
        ast.FloorDiv: operator.floordiv, ast.USub: operator.neg}
_FUNCS = {"ceil": math.ceil, "floor": math.floor, "round": round, "min": min, "max": max, "abs": abs}


def evaluate(formula: str, names: dict[str, float]) -> float:
    """Arithmetic over named numbers. The formula may come from a model, so this never calls eval()."""
    if len(formula) > 200:
        raise ValueError("formula too long")

    def ev(node):
        if isinstance(node, ast.Constant) and type(node.value) in (int, float):
            return node.value
        if isinstance(node, ast.Name) and node.id in names:
            return names[node.id]
        if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
            return _OPS[type(node.op)](ev(node.left), ev(node.right))
        if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
            return _OPS[type(node.op)](ev(node.operand))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _FUNCS and not node.keywords:
            return _FUNCS[node.func.id](*[ev(arg) for arg in node.args])
        raise ValueError(f"not allowed in a formula: {ast.unparse(node)[:40]}")

    return ev(ast.parse(formula, mode="eval").body)


def _same(value: str | None, wanted: str) -> bool:
    return isinstance(value, str) and value.strip().casefold() == wanted.strip().casefold()


def select(q: Query, scene: Scene, *, apply_policy=True) -> list[Entity]:
    connected = bool(room_queries.ROOM_FILTERS.keys() & q.where.keys())
    wanted = {r.id for r in room_queries.targets(q, scene)} if connected else set()
    return [
        e for e in scene.entities
        if e.type == q.type
        and (not apply_policy or not policy.exclusion(e, scene) or q.type not in policy.OBJECT_TYPES or q.op != "count")
        and (q.subtype is None or e.subtype == q.subtype)
        # attrs.long_name: a name the label wraps, e.g. "Bathroom 1" for the IFC label "Bathroom 1 (A104)"
        and (q.label is None or _same(e.label, q.label) or _same(e.attrs.get("long_name"), q.label))
        and (q.number is None or _same(e.number, q.number))
        and (q.level is None or _same(e.level, q.level))
        and (q.apartment is None or _same(e.apartment, q.apartment))
        and (q.outdoor is None or e.outdoor == q.outdoor)
        and (q.min_m2 is None or e.qty.get("m2", -math.inf) >= q.min_m2)
        and (q.max_m2 is None or e.qty.get("m2", math.inf) <= q.max_m2)
        and ("entity_id" not in q.where or e.id == q.where["entity_id"])
        and all(k in e.attrs and (_same(e.attrs[k], v) if isinstance(v, str) and isinstance(e.attrs[k], str)
                                  else e.attrs[k] == v) for k, v in q.where.items() if k not in room_queries.ROOM_FILTERS and k != "entity_id")
        and (not connected or bool(room_queries.endpoints(e, scene) & wanted))
    ]


def execute(plan: QueryPlan, scene: Scene) -> Answer:
    t0 = time.perf_counter()
    audit = []

    def done(**fields) -> Answer:
        step = TraceStep(step="execute", ms=(time.perf_counter() - t0) * 1000, detail=fields.get("formula") or fields.get("error"))
        if "confidence" in fields:
            fields["confidence_note"] = " ".join(why)
        audit_step = [TraceStep(step="calculation_evidence", detail=json.dumps({"threshold": policy.THRESHOLD,
            "confidence": fields.get("confidence", 0), "arithmetic": "verified" if fields.get("answer") is not None else "not completed",
            "score_rule": "min(geometric mean of included detection scores, requested room scores) × qualified evidence coverage",
            "queries": audit}))] if audit else []
        return Answer(tier=scene.tier, interpretation=plan.interpretation, assumptions=plan.assumptions,
                      trace=[step]+audit_step, **fields)

    values: dict[str, float] = {}
    evidence: list[str] = []
    confidence, shown, why, weak = 1.0, "", [], []
    for name, q in plan.queries.items():
        key = f"{q.type}:{q.subtype}" if q.subtype else q.type
        if key not in scene.covers:
            # An uncovered key would count as zero and look confident. Refuse, so the pipeline escalates.
            return done(error=f"scene from {scene.builder} does not cover {key}")
        hits = select(q, scene)
        cited_rooms = room_queries.targets(q, scene)
        if policy.raster(scene) and q.op == "count" and q.type in policy.OBJECT_TYPES:
            included = {e.id for e in hits}
            below = [e for e in select(q, scene, apply_policy=False) if not e.attrs.get("calculation_excluded") and e.conf < policy.THRESHOLD]
            weak += below
            coverage = len(hits)/(len(hits)+len(below)) if hits or below else 1.0
            support = policy.score(hits, cited_rooms)*coverage
            confidence = min(confidence, support)
            omitted, unresolved = [], []
            for entity in scene.entities:
                if entity.type != q.type and entity.attrs.get("detection_original_type") != q.type:
                    continue
                reason = policy.exclusion(entity, scene)
                if entity.type != q.type:
                    reason = f"Corrected from {q.type} to {entity.type}: {entity.attrs.get('calculation_type_reason', '')}"
                elif entity.id not in included and reason is None:
                    issue = room_queries.uncertainty(q, scene, [entity]) if cited_rooms else None
                    if issue:
                        unresolved.append(entity.id)
                        reason = entity.attrs.get("room_link_reason", "Unresolved room connection")
                    else:
                        reason = "Outside the requested room or filters"
                if entity.id not in included:
                    omitted.append({"id": entity.id, "reason": reason, "score": entity.conf})
            audit.append({"name": name, "type": q.type, "included": [e.id for e in hits],
                          "context": [r.id for r in cited_rooms], "excluded": omitted, "unresolved": unresolved,
                          "score": support, "coverage": coverage})
        if room_queries.ROOM_FILTERS.keys() & q.where.keys():
            base = q.model_copy(update={"where": {k: v for k, v in q.where.items() if k not in room_queries.ROOM_FILTERS}})
            error = room_queries.uncertainty(q, scene, select(base, scene))
            if error:
                return done(error=error, evidence=[r.id for r in cited_rooms]+[e.id for e in hits])
            cited_rooms = room_queries.targets(q, scene)
            evidence += [r.id for r in cited_rooms]
            confidence = min(confidence, min(r.conf for r in cited_rooms))
            why.append("Room connections inferred by a vision model are not independently verified." if any(
                e.attrs.get("room_link_origin") == "vision" for e in hits) else
                "Room connections inferred from raster geometry are not independently verified." if any(
                e.attrs.get("room_link_version") for e in hits) else "Room connections resolved from source metadata.")
        if q.op == "count":
            values[name] = len(hits)
            shown = f"{len(hits)} x {key}"
            # A builder sets attrs.copies when one item is drawn several times on the same spot; we count it once.
            dupes = sum(int(e.attrs.get("copies", 1)) - 1 for e in hits)
            if dupes:
                shown += f" (the file has {len(hits) + dupes} {key} objects; {dupes} are duplicates drawn on the same spot, counted once)"
        else:
            if not hits:
                return done(error=f"nothing matched the {name} query")  # a label mismatch, not a real zero
            missing = [e.id for e in hits if q.qty not in e.qty]
            if missing:
                return done(error=f"no {q.qty} on {', '.join(missing[:3])}")
            terms = [e.qty[q.qty] for e in hits]
            values[name] = round(sum(terms), 6)
            shown = (" + ".join(f"{t:g}" for t in terms) if len(terms) <= 12 else f"sum of {len(terms)} {key}") + f" = {values[name]:g} {q.qty.replace('wall_m2', 'm2 of wall')}"
        evidence += [e.id for e in hits]
        # The typical (geometric mean) confidence of the matched items; the weakest query sets the answer's.
        # A product over the items fell to ~0 for any large count (124 doors at 0.9 each) and escalated every one,
        # even when the count was right. ponytail: a heuristic, not a calibrated probability; replace with the count
        # accuracy measured per class on val (isotonic) once the confidence score is judged.
        if hits:
            typical = math.exp(sum(math.log(max(e.conf, 1e-9)) for e in hits) / len(hits))
            confidence = min(confidence, typical)
            lowest = min(e.conf for e in hits)
            why.append(f"{len(hits)} x {key}: every item read from the file, score 100 each." if lowest >= 1 else
                       f"{len(hits)} x {key}: typical item score {typical:.0%} (geometric mean; lowest {lowest:.0%}).")
        else:
            why.append(f"No {key} matched; a zero is only as sure as the reader's search for {key}, which no item score lowers.")
        if q.label and len(hits) > 1 and not all(_same(e.label, q.label) for e in hits):
            # Matched by the name inside the label, which several items share: say so instead of adding them up surely.
            confidence = min(confidence, 0.6)
            why.append(f"{len(hits)} items are named {q.label!r} ({', '.join(e.label or e.id for e in hits[:4])}) and were "
                       f"added up; ask for one by its full name to get that one alone.")
    if len(why) > 1:
        why.append("The weakest of these sets the answer's confidence.")

    if plan.formula is None:
        value = next(iter(values.values()))
    else:
        numbers = {k: v for k, v in plan.assumptions.items() if type(v) in (int, float)} | values
        try:
            value = evaluate(plan.formula, numbers)
        except (ValueError, SyntaxError, ZeroDivisionError, TypeError) as e:
            return done(error=f"formula {plan.formula!r}: {e}")
        shown = f"{plan.formula} with " + ", ".join(f"{k} = {v:g}" for k, v in numbers.items()) + f" gives {value:g}"
    if plan.unit == "count" and float(value).is_integer():
        value = int(value)
    if audit:
        why.append(f"Provisional calculation support score, not a calibrated probability. Evidence cutoff {policy.THRESHOLD:.0%}; arithmetic is verified. "
                   "Below-cutoff, excluded and out-of-scope detections are listed separately.")
        if confidence+1e-12 < policy.THRESHOLD:
            error = f"Calculation support {confidence:.0%} is below the provisional {policy.THRESHOLD:.0%} cutoff."
            if weak:
                # Name the candidates that pulled the score down, so the reader knows what to review.
                scores = ", ".join(f"{e.conf:.0%}" for e in sorted(weak, key=lambda e: -e.conf)[:5])
                span = f" The answer is between {value:g} and {value + len(weak):g}." if plan.formula is None and plan.unit == "count" else ""
                error += (f" {len(weak)} more candidate{'s' if len(weak) > 1 else ''} scored under the cutoff ({scores}) and "
                          f"{'were' if len(weak) > 1 else 'was'} not counted.{span} Confirm or reject {'them' if len(weak) > 1 else 'it'} in Correct.")
            return done(error=error,
                        confidence=confidence, evidence=evidence, formula=shown)
    return done(answer=value, unit=plan.unit, confidence=confidence, evidence=evidence, formula=shown)
