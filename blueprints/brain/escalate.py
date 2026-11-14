"""Lane C. The expensive path, called when the cheap path gave no answer or a low-confidence one.

Today: a drawing with no scene (no builder yet, or a hand drawing) is read once by Gemini (vision.read_scene) and the
plan runs on that scene with the normal executor. Next: the Nemotron agent with scene tools for low-confidence and
multi-step questions.
"""
from ..executor import execute
from ..schemas import Answer, Scene
from .plan import plan_with_trace

# ponytail: one fixed confidence for vision answers; replace with per-question-kind accuracy measured on the train split.
VISION_CONFIDENCE = 0.7


def escalate(question: str, path: str, scene: Scene | None, draft: Answer) -> Answer:
    """Return a new Answer with tier="llm", keeping draft.trace. Raise NotImplementedError to keep the draft."""
    if scene is not None:
        raise NotImplementedError("lane C: the Nemotron agent for low-confidence answers is not written yet")
    return answer_from_vision(question, path, draft)


def answer_from_vision(question: str, path: str, draft: Answer | None = None) -> Answer:
    """Answer on the Gemini vision scene: for drawings with no builder, and for uploads."""
    draft = draft or Answer()
    from .vision import read_scene
    vision, fresh = read_scene(path)
    plan, plan_steps = plan_with_trace(question, vision)
    result = execute(plan, vision)
    hits = set(result.evidence)
    return result.model_copy(update={
        "tier": "llm",
        "confidence": VISION_CONFIDENCE if result.answer is not None else 0.0,
        "extra_entities": [e for e in vision.entities if e.id in hits],  # the viewer has no scene to look these up in
        "trace": draft.trace + plan_steps + (vision.trace if fresh else []) + result.trace,
    })
