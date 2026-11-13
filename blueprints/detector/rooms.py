"""Lane A, after the Swiss parser. Adds rooms by type and the floors shown to a raster scene.

Kept apart from raster.py so A and B can work on rasters at the same time without touching the same file.
"""
from pathlib import Path

from ..schemas import Scene


def add_rooms(scene: Scene, file: Path) -> None:
    """Append room and level entities to scene.entities and the keys you cover to scene.covers."""
    raise NotImplementedError("lane A: raster rooms and floors not written yet")
