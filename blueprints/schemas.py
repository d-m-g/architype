"""The shared contract. Everything that crosses a lane boundary is one of these models.

Shared file: change it only when all four agree, bump SCHEMA_VERSION, and update fixtures/ in the same commit
(test_contract.py fails if they drift).
"""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

SCHEMA_VERSION = 1

EntityType = Literal["room", "apartment", "level", "door", "window", "fixture", "wall", "slab", "column", "row", "other"]
# Allowed subtypes per entity type. Types not listed here take no subtype. Adding a word is a one-line change.
SUBTYPES: dict[str, set[str]] = {
    "room": {"bedroom", "kitchen", "bathroom", "wc_room", "sauna", "balcony", "living", "corridor", "storage", "other"},
    "fixture": {"toilet", "sink", "bathtub", "shower", "other"},
    "slab": {"floor", "roof", "base", "landing", "other"},  # a roof slab is not floor area
}
Unit = Literal["count", "m2", "m", "m3", "ft2"]  # same strings as "unit" in questions.jsonl
# A room's "m2" is its floor area, "m" its perimeter and "wall_m2" its wall area net of doors and windows
# (blueprints/outlines.py), so paint and wall-tile questions are sums like any other.
QtyUnit = Literal["m2", "m", "m3", "ft2", "wall_m2"]
Tier = Literal["parse", "detect", "llm", "none"]
Scalar = bool | int | float | str
Point = tuple[float, float]  # (x, y), each 0..1 of the page image, origin top-left


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")  # a misspelled field is an error, not a silently ignored one


class TraceStep(Strict):
    """One unit of work and what it cost. Build these with blueprints.costs so prices stay consistent."""

    step: str  # "scene", "plan", "execute", "escalate", ...
    model: str | None = None  # model or GPU type that did the work; None for plain CPU code
    in_tokens: int = 0
    out_tokens: int = 0
    gpu_s: float = 0.0
    usd: float = 0.0
    ms: float = 0.0
    cached: bool = False
    detail: str | None = None  # what the step did, for the answer card: the plan read, a tool call and its result


class Page(Strict):
    """The image the viewer shows. Entity.geom is normalised to this image."""

    # "data/..." for a dataset file, or "pages/..." for a page a builder rendered into .cache/pages/.
    # The viewer loads it from /api/image?path=<image>.
    image: str
    width: int  # pixels
    height: int


class Entity(Strict):
    """One thing in a drawing: a room, a door, a toilet, a wall, a schedule row."""

    id: str  # unique in the scene and stable across rebuilds: answers cite it as evidence
    type: EntityType
    subtype: str | None = None  # normalised category from SUBTYPES, in English whatever the drawing's language
    label: str | None = None  # name as drawn: "Bagno", "TUALETTRUUM"
    number: str | None = None  # identifier as drawn: "5.06", "57-3"
    level: str | None = None  # label of the level it is on: "4.korrus"
    apartment: str | None = None  # label of the apartment it belongs to: "A"
    outdoor: bool = False  # balcony, loggia, terrace: the area questions exclude these
    page: int = 0  # index into Scene.pages
    geom: list[Point] = []  # polygon; a box is its 4 corners; empty when there is nothing to draw
    qty: dict[QtyUnit, float] = {}  # measured quantities, keyed by unit: {"m2": 4.49}
    attrs: dict[str, Scalar] = {}  # anything else: schedule columns, door type, elevation_m
    conf: float = Field(1.0, ge=0, le=1)  # probability this entity is real and correctly typed
    ref: str | None = None  # where it came from: "ifc:<GlobalId>", "dxf:<handle>", "det:<index>"

    @model_validator(mode="after")
    def _check(self):
        if self.subtype is not None and self.subtype not in SUBTYPES.get(self.type, ()):
            raise ValueError(f"subtype {self.subtype!r} is not allowed for type {self.type!r}; see SUBTYPES")
        if any(not 0 <= v <= 1 for point in self.geom for v in point):
            raise ValueError("geom must be normalised 0..1 page coordinates, not pixels")
        return self


class Scene(Strict):
    """Everything extracted from one drawing. Built once per drawing and cached."""

    schema_version: int = SCHEMA_VERSION
    path: str  # dataset path of the drawing the questions name: "data/swiss_dwellings/x.pdf"
    builder: str  # code that built it, with a version: "swiss_dxf@1"
    tier: Literal["parse", "detect"]
    # What the builder looked for, as "type" or "type:subtype". A count of zero only means "none" for a
    # covered key; the executor refuses to answer about anything else.
    covers: list[str]
    pages: list[Page] = []
    entities: list[Entity] = []
    trace: list[TraceStep] = []  # cost of building the scene, paid once per drawing

    @model_validator(mode="after")
    def _check(self):
        ids = [e.id for e in self.entities]
        if len(ids) != len(set(ids)):
            raise ValueError("entity ids must be unique within a scene")
        for key in self.covers:
            kind, _, sub = key.partition(":")
            Entity(id="_", type=kind, subtype=sub or None)  # raises on an unknown type or subtype
        return self


class Query(Strict):
    """Select entities, then count them or sum one quantity. Text filters match exactly, ignoring case."""

    op: Literal["count", "sum"]
    type: EntityType
    subtype: str | None = None
    label: str | None = None
    number: str | None = None
    level: str | None = None
    apartment: str | None = None
    outdoor: bool | None = None
    where: dict[str, Scalar] = {}  # equality on Entity.attrs; an entity without the attr does not match
    min_m2: float | None = None  # keep entities with an area of at least this; an entity without an area does not match
    max_m2: float | None = None  # ... of at most this
    qty: QtyUnit | None = None  # which quantity to sum

    @model_validator(mode="after")
    def _check(self):
        if (self.op == "sum") != (self.qty is not None):
            raise ValueError("qty is required for sum and not allowed for count")
        Entity(id="_", type=self.type, subtype=self.subtype)
        return self


class QueryPlan(Strict):
    """A question turned into queries. With one query the answer is its value; otherwise formula combines them."""

    queries: dict[str, Query]  # name -> query; the names are the variables of formula
    formula: str | None = None  # arithmetic over query names and numeric assumptions: "ceil(area * 1.1 / tile_m2)"
    unit: Unit
    interpretation: str | None = None  # the reading chosen for an ambiguous question, shown to the user
    assumptions: dict[str, Scalar] = {}  # shown on the answer card; numeric ones are usable in formula

    @model_validator(mode="after")
    def _check(self):
        if self.formula is None and len(self.queries) != 1:
            raise ValueError("a plan without a formula needs exactly one query")
        return self


class Answer(BaseModel):
    """What /api/ask returns."""

    answer: int | float | str | None = None  # None means "could not answer"; see error
    unit: Unit | None = None
    confidence: float = Field(0.0, ge=0, le=1)  # probability the answer is correct
    confidence_note: str | None = None  # how confidence was set, in plain words, and how often that was right in eval
    tier: Tier = "none"  # the most expensive tier that contributed
    evidence: list[str] = []  # Entity ids from the scene to highlight
    extra_entities: list[Entity] = []  # evidence the escalation model found that is not in the scene
    formula: str | None = None  # the computation, readable: "5.08 + 18.91 = 23.99"
    interpretation: str | None = None
    assumptions: dict[str, Scalar] = {}
    trace: list[TraceStep] = []  # cost of answering this question, excluding the scene build
    error: str | None = None

    @computed_field
    @property
    def cost_usd(self) -> float:
        return sum(step.usd for step in self.trace)


class EvalRow(BaseModel):
    """One line of an eval run log (runs/*.jsonl). The dashboard reads these."""

    path: str
    source: str
    question: str
    type: str  # count | area | length | volume | text
    gold: int | float | str
    gold_unit: str | None
    pred: int | float | str | None
    pred_unit: str | None
    correct: bool | None  # None: free-form text, not auto-scored
    confidence: float
    tier: Tier
    cost_usd: float
    ms: float
    n_evidence: int
    error: str | None = None
