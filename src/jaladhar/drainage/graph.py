"""Thin shim exposing the frozen contract entrypoint.
configs/contracts/drain_graph.json names"""

from jaladhar.drainage.stitch import (
    EdgeRec,
    NodeRec,
    Reach,
    StitchResult,
    stitch_components,
)

__all__ = ["Reach", "NodeRec", "EdgeRec", "StitchResult", "stitch_components"]
