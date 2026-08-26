"""Thin shim exposing the frozen contract entrypoint.

configs/contracts/drain_graph.json names
`src/jaladhar/drainage/graph.py::stitch_components` as the producer entrypoint;
this module exists solely to re-export it byte-exactly (spec deviation D14).
"""

from jaladhar.drainage.stitch import (
    EdgeRec,
    NodeRec,
    Reach,
    StitchResult,
    stitch_components,
)

__all__ = ["Reach", "NodeRec", "EdgeRec", "StitchResult", "stitch_components"]
