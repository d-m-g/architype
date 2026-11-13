"""Lane C. The expensive path: a large model with the page image and scene tools."""
from ..schemas import Answer, Scene


def escalate(question: str, path: str, scene: Scene | None, draft: Answer) -> Answer:
    """Called when the cheap path gave no answer or one below pipeline.ESCALATE_BELOW.

    scene is None when no builder exists for the drawing (the scans). Return a new Answer with tier="llm",
    keeping draft.trace and adding your own steps. Put evidence the scene does not have in extra_entities.
    Raise NotImplementedError to keep the draft.
    """
    raise NotImplementedError("lane C: escalation agent not written yet")
