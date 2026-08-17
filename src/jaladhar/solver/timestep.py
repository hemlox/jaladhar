"""CFL timestep control: record-then-replay.

Adaptive dt makes the STEP COUNT depend on the parameters, which perturbs the
autograd graph between optimiser iterations and — worse — makes a
finite-difference gradcheck compare two runs of different length. That check is
the strongest correctness evidence available for the whole differentiable path,
so the design protects it.

Record-then-replay: a `no_grad` pass computes and records the dt schedule; the
differentiable pass replays that schedule FIXED. Costs ~2x forward (backward
dominates, so real overhead is well under 2x) and buys a fixed graph,
deterministic checkpoint segmentation, and an exact FD gradcheck.

ON STALENESS: RE-RECORD AND RETRY, NEVER HALT. A stale schedule is a
recoverable numerical-control condition, and a hard halt would kill an
overnight calibration run at 3am over something re-recording fixes in seconds.
This is deliberately ASYMMETRIC with `mass.py`, which halts hard: a mass breach
means the scheme itself is broken and continuing would yield a completed run
that looks valid. Recoverable -> retry. Broken -> halt.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import torch


@dataclass
class TimestepController:
    """CFL schedule recorder/replayer."""

    dx: float
    alpha: float
    gravity: float = 9.81
    min_dt_s: float = 0.01
    max_dt_s: float = 10.0
    cfl_ceiling: float | None = None

    schedule: list[float] = field(default_factory=list)
    rerecord_events: list[dict[str, float]] = field(default_factory=list)
    floor_hits: int = 0
    rejections: int = 0

    def __post_init__(self) -> None:
        if self.cfl_ceiling is None:
            self.cfl_ceiling = 0.85

    def dt_for(self, h: torch.Tensor) -> float:
        """CFL dt for the current state. Called ONLY under no_grad, during
        recording — never inside the differentiable replay, so no `.item()`
        ever executes in the hot path."""
        h_max = float(h.max())
        if h_max <= 0.0:
            return self.max_dt_s
        dt = self.alpha * self.dx / (self.gravity * h_max) ** 0.5
        if dt < self.min_dt_s:
            # Hitting the floor is REPORTED, never silently accepted — it means
            # the CFL condition wants a smaller step than the run permits, and
            # the run is then not actually CFL-compliant.
            self.floor_hits += 1
            return self.min_dt_s
        return min(dt, self.max_dt_s)

    def courant(self, h: torch.Tensor, dt: float) -> float:
        """Realised Courant number. Invariant 10 asserts this stays <= alpha."""
        h_max = float(h.max())
        if h_max <= 0.0:
            return 0.0
        return dt * (self.gravity * h_max) ** 0.5 / self.dx

    def is_stale(self, h: torch.Tensor, dt: float) -> bool:
        return self.courant(h, dt) > self.alpha

    def note_rerecord(self, iteration: int, step: int, courant: float) -> None:
        """Log a re-record. If this fires on EVERY iteration the learning rate
        is too high, and `diagnose()` says so in those words rather than
        leaving a human to infer it from a wall of retry messages."""
        self.rerecord_events.append(
            {"iteration": float(iteration), "step": float(step), "courant": courant}
        )

    def diagnose(self, n_iterations: int) -> str | None:
        if n_iterations > 0 and len(self.rerecord_events) >= n_iterations:
            return (
                f"dt schedule was re-recorded on {len(self.rerecord_events)} of "
                f"{n_iterations} iterations — that is every iteration. The learning "
                "rate is too high: parameters are moving far enough per step that the "
                "recorded schedule is stale before it can be replayed."
            )
        return None

    def compress(self) -> dict[str, object]:
        """Manifest form. A calibration run whose schedule is not stored is not
        reproducible (CLAUDE.md rule 6). The schedule is smooth and highly
        repetitive, so it is stored run-length-encoded rather than as ~10^4
        raw floats."""
        if not self.schedule:
            return {"n_steps": 0, "runs": []}
        runs: list[list[float]] = []
        cur, count = self.schedule[0], 1
        for dt in self.schedule[1:]:
            if abs(dt - cur) < 1e-12:
                count += 1
            else:
                runs.append([cur, float(count)])
                cur, count = dt, 1
        runs.append([cur, float(count)])
        return {
            "n_steps": len(self.schedule),
            "total_time_s": sum(self.schedule),
            "dt_min_s": min(self.schedule),
            "dt_max_s": max(self.schedule),
            "min_dt_floor_hits": self.floor_hits,
            "retry_attempts": self.rejections,
            "rejections": self.rejections,  # backwards-compatible alias
            "cfl_ceiling": self.cfl_ceiling,
            "n_rerecords": len(self.rerecord_events),
            "runs_rle": runs,
        }
