#!/usr/bin/env python3
"""Runner script for Water Tracing, Catchment Water Budgeting, and Hypothesis Falsification.

Executes Part 0 (Validation Gate), Part 1 (Closed Catchment Water Budgets), Part 2 (Hypothesis Falsification
& Storage Defect Fix), and Part 3 (Antecedent Evidence Ledger).
"""

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))

from jaladhar.analysis.water_tracer import app

if __name__ == "__main__":
    app()
