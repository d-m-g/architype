"""Lane A. IFC model -> Scene, for ifc_models and ifc_bench.

A question may name the model (.ifc) or a plan rendered from it (<model>_plan-<level>.pdf). Both resolve to the
same model, and some PDF questions ask about the whole model, so always build from the .ifc.
"""
from pathlib import Path

from ..schemas import Scene


def build_scene(path: str, file: Path) -> Scene:
    raise NotImplementedError("lane A: IFC parser not written yet")
