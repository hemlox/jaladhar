"""Kinematic-wave overland flow on a plane under constant rainfall.

WHY THIS TEST EXISTS
--------------------
EA benchmark Test 8A (rainfall applied directly to an urban surface) has no
public input data — see OPEN-ITEMS.md item 9. Losing it left a specific hole:
Ritter and Stoker are dam breaks, Thacker is a bowl oscillation, MacDonald is
steady channel flow. **None of them involves rainfall.** Invariant 7 does test
the rain source term, but with routing switched off entirely (flat, closed, no
sinks). So without this case, nothing in the ladder exercises rainfall COUPLED
to friction and routing — the only regime JALADHAR actually operates in.

THE SOLUTION
------------
At steady state every drop falling upslope of `x` must pass through `x`, so the
unit-width discharge is `q(x) = R*x`. Manning with friction slope = bed slope
then gives the profile, and Woolhiser & Liggett (1967) give the time to
equilibrium:

    h(x)  = (n * R * x / sqrt(S)) ** (3/5)
    t_eq  = (n * L / (sqrt(S) * R**(2/3))) ** (3/5)

THIS IS A CHARACTERISATION SWEEP, NOT A PASS/FAIL TEST
------------------------------------------------------
The kinematic approximation drops inertia and the pressure gradient; ACC keeps
both but drops advection. Their validity ranges run in OPPOSITE directions with
slope, and that is what makes this measurement possible:

  * kinematic validity RISES with slope — the kinematic wave number
    k = S*L/(h0*F0^2) grows monotonically and is >> 10 across the entire
    practical range, so the reference never binds;
  * ACC validity FALLS with slope — its low-Froude assumption degrades as the
    flow accelerates.

So across this range the reference stays trustworthy while ACC degrades, which
means the departure from the analytical solution IS the ACC approximation
error, isolated and attributable. Sweeping slope and plotting
`|h_ACC - h_kinematic|` against Froude therefore yields a MEASURED degradation
curve for this implementation on this grid — replacing "published work shows
degradation for Fr >~ 0.5" with our own number. Per CLAUDE.md V1 a citation is
a declaration; the curve is realized state.

It also cross-checks the ANUGA comparison from an independent direction. That
one is model-to-model; this one is analytical. If both locate the same Froude
limit, two independent methods agree and tau_hi becomes our own measurement
rather than a number pinned to someone else's paper (invariant 28).

NUMERICAL HAZARD, RECORDED DELIBERATELY
---------------------------------------
At S = 3e-2 the Froude number is 1.018 and the kinematic wave number is 1018.2.
They are numerically near-identical by pure coincidence and differ by a factor
of 1000. Do not "correct" one into the other. `validity_table()` computes both
from a single source so the two can never be transcribed apart.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

GRAVITY = 9.81

# Below this the kinematic reference is not trustworthy. It is never reached in
# the practical range here (minimum observed k is ~104), which is precisely why
# the reference can be treated as ground truth while ACC is characterised.
KINEMATIC_K_FLOOR = 10.0

# ACC's local-inertial formulation degrades as Froude rises. This value is the
# LITERATURE PRIOR used only to pre-state where degradation is expected; the
# whole point of the sweep is to replace it with a measured figure.
ACC_FROUDE_PRIOR = 0.5


@dataclass(frozen=True)
class PlaneCase:
    """One (slope, rain, roughness, length) overland-flow case."""

    slope: float
    rain_m_s: float
    manning_n: float
    length_m: float

    @property
    def h_outlet(self) -> float:
        """Steady-state depth at the outlet."""
        return (self.manning_n * self.rain_m_s * self.length_m / math.sqrt(self.slope)) ** 0.6

    @property
    def froude(self) -> float:
        h = self.h_outlet
        return (self.rain_m_s * self.length_m / h) / math.sqrt(GRAVITY * h)

    @property
    def kinematic_number(self) -> float:
        """k = S*L/(h0*F0^2). Larger = kinematic approximation more valid."""
        return self.slope * self.length_m / (self.h_outlet * self.froude**2)

    @property
    def t_eq_s(self) -> float:
        """Woolhiser & Liggett time to equilibrium."""
        return (
            self.manning_n * self.length_m / (math.sqrt(self.slope) * self.rain_m_s ** (2.0 / 3.0))
        ) ** 0.6

    def depth_profile(self, x: np.ndarray) -> np.ndarray:
        """Steady-state h(x). x measured downslope from the divide."""
        return (self.manning_n * self.rain_m_s * x / math.sqrt(self.slope)) ** 0.6

    def local_validity_ratio(self, x: float) -> float:
        """|dh/dx| / S at position `x` — the LOCAL kinematic validity test.

        Kinematic theory sets the friction slope equal to the BED slope, which
        requires the depth gradient to be negligible against it. Since
        h ~ x^(3/5), dh/dx = 0.6*h/x, so this ratio grows as slope falls.

        THIS CRITERION, NOT `kinematic_number`, IS WHAT ACTUALLY BINDS AT LOW
        SLOPE. The outlet-based k rises monotonically with slope and reports
        "valid everywhere" (104 at S=1e-4), but measurement showed 30% error
        there — because k is evaluated at the OUTLET while the approximation
        fails UPSTREAM, where h is small and x is small. Found empirically by
        the degradation sweep, whose error curve tracks this ratio almost
        exactly:  2.371 -> 0.297,  0.568 -> 0.108,  0.119 -> 0.022,
        0.028 -> 0.008,  0.006 -> 0.004.
        """
        h = self.depth_profile(np.asarray([x]))[0]
        return float(0.6 * h / x / self.slope)

    @property
    def kinematic_valid(self) -> bool:
        """Outlet-based check only. Use `local_validity_ratio` for the binding
        constraint at low slope — see its docstring."""
        return self.kinematic_number > KINEMATIC_K_FLOOR

    @property
    def acc_expected_valid(self) -> bool:
        """PRIOR expectation only — the sweep exists to measure the real limit."""
        return self.froude < ACC_FROUDE_PRIOR


# Extra points between 1e-2 and 3e-2 to locate the ACC degradation onset,
# which the first sweep bracketed only coarsely (Fr 0.62 -> 1.02).
DEFAULT_SLOPES = (1e-4, 3e-4, 1e-3, 3e-3, 1e-2, 1.5e-2, 2e-2, 2.5e-2, 3e-2)
# The ratio above which the kinematic REFERENCE is itself untrustworthy, so
# disagreement cannot be attributed to ACC. Set from the measured curve.
LOCAL_VALIDITY_CEILING = 0.15


def validity_table(
    slopes: tuple[float, ...] = DEFAULT_SLOPES,
    rain_mm_hr: float = 55.0,
    manning_n: float = 0.03,
    length_m: float = 1000.0,
) -> list[dict[str, float | bool]]:
    """The pinned convergence band, COMPUTED rather than transcribed.

    Every quoted figure in the plan, the writeup and any slide derives from this
    one function. A hand-copied table is how a stale number reaches a deck; this
    cannot drift from itself.
    """
    r = rain_mm_hr / 1000.0 / 3600.0
    rows = []
    for s in slopes:
        c = PlaneCase(slope=s, rain_m_s=r, manning_n=manning_n, length_m=length_m)
        rows.append(
            {
                "slope": s,
                "h_outlet_m": c.h_outlet,
                "froude": c.froude,
                "kinematic_number": c.kinematic_number,
                "t_eq_min": c.t_eq_s / 60.0,
                "kinematic_valid": c.kinematic_valid,
                "acc_expected_valid": c.acc_expected_valid,
            }
        )
    return rows


def expected_agreement_band(
    rows: list[dict[str, float | bool]],
) -> tuple[list[float], list[float]]:
    """(slopes where BOTH hold, slopes where ACC is expected to degrade).

    The second list is not a list of excused failures — those cases are
    ASSERTED to disagree, which makes them a discriminating check rather than a
    gap in coverage.
    """
    ok = [r["slope"] for r in rows if r["kinematic_valid"] and r["acc_expected_valid"]]
    degrade = [r["slope"] for r in rows if r["kinematic_valid"] and not r["acc_expected_valid"]]
    return ok, degrade
