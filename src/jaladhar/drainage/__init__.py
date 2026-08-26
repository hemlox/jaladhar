"""JALADHAR drainage graph construction (WF-1).

CPU-only package: no torch/CUDA imports anywhere in this tree.

Modules:
- loader    robust BBMP KML ingestion -> EPSG:32643 reach tables + diagnostics
- stitch    pinned snap/D8-walk stitching (frozen entrypoint re-exported by graph.py)
- capacity  cited rational+Manning capacity assignment (observed edges only)
- graph_io  candidate artefact writer + load-time asserting reader

Contract: configs/contracts/drain_graph.json (FROZEN v1.1.0).
Config:   configs/drainage.yaml (rule 7: resolved at startup, aggregated errors).
"""

__all__ = ["loader", "stitch", "capacity", "graph_io"]
