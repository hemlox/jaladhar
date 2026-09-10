"""Direct unit coverage for ``jaladhar.coupling.solver_hook._uncoupled_steps_covered`` —
the K1 twin-denominator derivation (spec §12 row #6 / §10.6).

WHY THIS FILE EXISTS (fix-round finding): the function is the SOLE producer of
``kill_thresholds.K1.uncoupled_equivalent_steps_at_final_tau`` in every coupled-run
manifest (runtime check site solver_hook.py ~:730; committed final-tau sites ~:794/:834),
yet it had ZERO direct test coverage repo-wide and ``twin_cum`` is not serialized — so a
regression in the denominator derivation would only be observable through full coupled
runs whose green assertions use the same arithmetic. White-box import of the private
function is deliberate here (unit seam); the invariant-level behaviour stays with
test_inv06_dt_not_collapsed.py.

V2 statement: if this derivation were broken (e.g. bisect_left instead of bisect_right,
off-by-one at prefix ends), I would observe — directly, below — a tau EXACTLY on a step
end returning that step as NOT covered (denominator short by one per boundary), an empty
twin schedule yielding >= 1 covered step, or a pre-schedule tau counting steps that have
not happened yet. These are pure-function observables over hand-typed prefixes; no mirror
is possible because the expected values are derived by hand from bisect_right semantics,
not from another code path.

V7 scope: pure-function domain — arbitrary float prefixes/taus exercised at the boundary
cases (exact step ends, strictly-inside taus, empty/single/duplicated-cumulative prefixes);
NOT covered here: the twin schedule's PRODUCTION (measured by the host simulator, pinned
by test_inv06's driver-loop run) and manifest serialization.
"""

from __future__ import annotations

import pytest

from jaladhar.coupling.solver_hook import _uncoupled_steps_covered

UNIFORM_PREFIX = [10.0, 20.0, 30.0]


@pytest.mark.parametrize(
    "tau, expected",
    [
        (-1.0, 0),
        (0.0, 0),
        (9.999999, 0),
        (10.0, 1),
        (10.5, 1),
        (19.999999, 1),
        (20.0, 2),
        (20.5, 2),
        (29.999999, 2),
        (30.0, 3),
        (100.0, 3),
    ],
)
def test_known_uniform_prefix(tau: float, expected: int) -> None:
    assert _uncoupled_steps_covered(UNIFORM_PREFIX, tau) == expected


@pytest.mark.parametrize(
    "prefix, tau, expected",
    [
        ([2.5, 7.4, 7.900001], 2.5, 1),
        ([2.5, 7.4, 7.900001], 7.4, 2),
        ([2.5, 7.4, 7.900001], 7.3999, 1),
        ([2.5, 7.4, 7.900001], 7.900001, 3),
        ([5.0], 4.999999, 0),
        ([5.0], 5.0, 1),
        ([5.0], 5.000001, 1),
    ],
)
def test_known_nonuniform_and_single_step_prefixes(
    prefix: list[float], tau: float, expected: int
) -> None:
    assert _uncoupled_steps_covered(prefix, tau) == expected


@pytest.mark.parametrize("tau", [0.0, 12.5, 1e9])
def test_empty_prefix_yields_zero_for_any_tau(tau: float) -> None:
    assert _uncoupled_steps_covered([], tau) == 0


def test_tau_before_first_step_counts_nothing() -> None:
    assert _uncoupled_steps_covered(UNIFORM_PREFIX, UNIFORM_PREFIX[0] - 1e-6) == 0


def test_duplicated_cumulative_times_count_at_their_shared_end() -> None:
    assert _uncoupled_steps_covered([10.0, 10.0, 20.0], 9.999999) == 0
    assert _uncoupled_steps_covered([10.0, 10.0, 20.0], 10.0) == 2
    assert _uncoupled_steps_covered([10.0, 10.0, 20.0], 20.0) == 3
