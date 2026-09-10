"""solver correctness on their own — a Phase 0 finding, since LISFLOOD-FP proved
with closed-form solutions, not synthetic terrain standing in for Bengaluru.
categorically distinct from fabricating elevation data, which the rule"""

from __future__ import annotations

from jaladhar.solver.analytical.dambreak import (
    MacDonaldCase,
    RitterCase,
    StokerCase,
    ThackerCase,
)

__all__ = [
    "MacDonaldCase",
    "RitterCase",
    "StokerCase",
    "ThackerCase",
]
