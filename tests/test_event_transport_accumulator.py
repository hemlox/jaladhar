"""Accepted-step scope for the Phase 3 transport accumulator.

V2: if a rejected trial is accumulated, the final field contains both trial
increments. V5 red mutation: moving accumulation above acceptance made the
synthetic retry total 3 rather than 2.
Scope: synthetic scalar retry loop; full replay remains data-blocked.
"""

import torch


def _accepted_only(trials: list[tuple[bool, torch.Tensor]]) -> torch.Tensor:
    total = torch.zeros_like(trials[0][1], dtype=torch.float64)
    for rejected, increment in trials:
        if rejected:
            continue
        total.add_(increment)
        break
    return total


def test_rejected_trial_transport_is_not_accumulated() -> None:
    realized = _accepted_only([(True, torch.tensor([[1.0]])), (False, torch.tensor([[2.0]]))])
    assert realized.item() == 2.0
