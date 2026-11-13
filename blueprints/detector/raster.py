"""Lane B. Raster floor plan (cubicasa5k PNG) -> Scene with doors, windows and fixtures.

Calls the detector served on Modal. Train only on drawings where scoring.split_of(path) == "train".
"""
from pathlib import Path

from ..schemas import Scene


def build_scene(path: str, file: Path) -> Scene:
    raise NotImplementedError("lane B: raster detector not wired in yet")
