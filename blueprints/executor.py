"""Lane A. Runs a QueryPlan against a Scene. No drawing-specific code belongs here: it only reads the schema."""
import ast
import math
import operator
import time

from .schemas import Answer, Entity, Query, QueryPlan, Scene, TraceStep

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
    return value is not None and value.strip().casefold() == wanted.strip().casefold()


def select(q: Query, scene: Scene) -> list[Entity]:
    return [
        e for e in scene.entities
        if e.type == q.type
        and (q.subtype is None or e.subtype == q.subtype)
        and (q.label is None or _same(e.label, q.label))
        and (q.number is None or _same(e.number, q.number))
        and (q.level is None or _same(e.level, q.level))
        and (q.apartment is None or _same(e.apartment, q.apartment))
        and (q.outdoor is None or e.outdoor == q.outdoor)
        and all(k in e.attrs and e.attrs[k] == v for k, v in q.where.items())
    ]


def execute(plan: QueryPlan, scene: Scene) -> Answer:
    t0 = time.perf_counter()

    def done(**fields) -> Answer:
        step = TraceStep(step="execute", ms=(time.perf_counter() - t0) * 1000)
        return Answer(tier=scene.tier, interpretation=plan.interpretation, assumptions=plan.assumptions,
                      trace=[step], **fields)

    values: dict[str, float] = {}
    evidence: list[str] = []
    confidence, shown = 1.0, ""
    for name, q in plan.queries.items():
        key = f"{q.type}:{q.subtype}" if q.subtype else q.type
        if key not in scene.covers:
            # An uncovered key would count as zero and look confident. Refuse, so the pipeline escalates.
            return done(error=f"scene from {scene.builder} does not cover {key}")
        hits = select(q, scene)
        if q.op == "count":
            values[name] = len(hits)
            shown = f"{len(hits)} x {key}"
        else:
            if not hits:
                return done(error=f"nothing matched the {name} query")  # a label mismatch, not a real zero
            missing = [e.id for e in hits if q.qty not in e.qty]
            if missing:
                return done(error=f"no {q.qty} on {', '.join(missing[:3])}")
            terms = [e.qty[q.qty] for e in hits]
            values[name] = round(sum(terms), 6)
            shown = (" + ".join(f"{t:g}" for t in terms) if len(terms) <= 12 else f"sum of {len(terms)} {key}") + f" = {values[name]:g} {q.qty}"
        evidence += [e.id for e in hits]
        # ponytail: product of entity confidences, blind to missed detections. Replace with the count
        # distribution over near-threshold candidates (Poisson-binomial) once the detector reports them.
        confidence *= math.prod(e.conf for e in hits)

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
    return done(answer=value, unit=plan.unit, confidence=confidence, evidence=evidence, formula=shown)
