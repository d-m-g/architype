"""Shared file. The dataset README's scoring rules and the one train/test split everybody uses."""
import hashlib

# Relative tolerance per question type. Types not listed (text, other) are free-form.
TOLERANCE = {"count": 0.0, "area": 0.02, "length": 0.02, "volume": 0.02}


def split_of(path: str) -> str:
    """'test' for a fixed 20% of drawings, else 'train'. Train and tune on 'train' only.

    Hashes the path without its extension, so the same drawing in another format lands in the same split.
    """
    stem = path.rsplit(".", 1)[0]
    return "test" if int(hashlib.md5(stem.encode()).hexdigest(), 16) % 5 == 0 else "train"


def is_correct(pred, pred_unit, gold, gold_unit, qtype) -> bool | None:
    """True or False for numeric questions, None when the question cannot be auto-scored."""
    if qtype not in TOLERANCE:
        return None  # ponytail: text answers are unscored; add a model judge when the 0.7% of text questions matter
    try:
        g = float(gold)
    except (TypeError, ValueError):
        return None
    if pred_unit and gold_unit and pred_unit != gold_unit:
        return False
    try:
        p = float(pred)
    except (TypeError, ValueError):
        return False
    return abs(p - g) <= TOLERANCE[qtype] * abs(g)
