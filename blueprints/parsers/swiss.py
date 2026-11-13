"""Lane A. Swiss Dwellings floor plan -> Scene.

Questions name the .pdf. The same-name .dxf (file.with_suffix(".dxf")) has one layer per kind of thing:
DOOR, WINDOW, FIXTURE, ROOM_OUTLINE, ROOM_TEXT, UNIT_OUTLINE, WALL. The same-name .png is the page image.
"""
from pathlib import Path

from ..schemas import Scene


def build_scene(path: str, file: Path) -> Scene:
    raise NotImplementedError("lane A: swiss parser not written yet")
