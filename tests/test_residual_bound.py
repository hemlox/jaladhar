"""V5 red-mutation record: replacing ``min`` with ``max`` in
observed chosen bounds become 100 and 75 instead of 75 and 50, respectively).
Scope: arithmetic derivation only; realized raster counts remain blocked on the"""

import pytest

from jaladhar.terrain.conditioning import (
    ConditioningError,
    enforce_residual_bound,
    residual_bound_components,
)


def test_domain_density_component_is_binding() -> None:
    bounds = residual_bound_components(domain_cells=10_000, pre_conditioning_d4_pits=400)

    assert bounds == {
        "domain_density_bound": 75,
        "pre_conditioning_d4_pit_fraction_bound": 100,
        "chosen_bound": 75,
    }


def test_pre_conditioning_pit_component_is_binding() -> None:
    bounds = residual_bound_components(domain_cells=10_000, pre_conditioning_d4_pits=200)

    assert bounds == {
        "domain_density_bound": 75,
        "pre_conditioning_d4_pit_fraction_bound": 50,
        "chosen_bound": 50,
    }


def test_component_bounds_floor_fractional_results() -> None:
    bounds = residual_bound_components(domain_cells=999, pre_conditioning_d4_pits=7)

    assert bounds == {
        "domain_density_bound": 7,
        "pre_conditioning_d4_pit_fraction_bound": 1,
        "chosen_bound": 1,
    }


def test_realized_count_above_binding_bound_is_rejected() -> None:
    bounds = residual_bound_components(domain_cells=10_000, pre_conditioning_d4_pits=200)

    enforce_residual_bound(50, bounds)
    with pytest.raises(ConditioningError, match="realized=51, bound=50"):
        enforce_residual_bound(51, bounds)


@pytest.mark.parametrize("domain_cells,pre_pits", [(-1, 0), (0, -1)])
def test_negative_inputs_are_rejected(domain_cells: int, pre_pits: int) -> None:
    with pytest.raises(ValueError, match="non-negative"):
        residual_bound_components(domain_cells, pre_pits)
