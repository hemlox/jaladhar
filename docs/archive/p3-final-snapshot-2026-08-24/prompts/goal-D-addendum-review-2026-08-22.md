# Goal D addendum — reviewer verdict on Goals A/B/C and five binding instructions

**Reviewer verdict, 2026-08-22: A, B, C accepted. Goal D is GO.** Read this WITH
`goal-D-merge-replay-calibrate-verdict-2026-08-22.md`; where they differ, this addendum wins.

Arithmetic reconciliation (evidence of good faith, stated per the review rules): A's fractions
reproduce its submask cell counts exactly (11,847 x 0.0940 = 1,114; 26,860 x 0.1917 ~= 5,148);
A's v2 FPRs are identical to v1's to five figures, proving thresholds were NOT retuned; B's
Bellandur factors are the reciprocals of its measured ratios (1/0.078 = 12.8); B's n=78 is
consistent with 63 geocoded stations over 100 usable samples; C's 33/32/16 counts add up from
the v2 baseline plus one promotion. No unreconciled number found.

## Instructions

1. **Record Goal A's boundary deviation in the final writeup — accepted, not hidden.** A's own
   auxiliary rule said amend only if held-out mean >= 0.90; realized mean was 0.790, middle
   zone, and A amended anyway under the resolution plan's senior uniform-correction guard
   (§A: held-out masks get the same correction when their fractions drop materially). The
   reviewer accepts that adjudication: the amendment's substance (targets 0.094/0.192 vs
   held-out 0.790 — an enormous separation) is met, and the 0.90 premise was itself
   miscalibrated for hyacinth-prone Bengaluru lakes. YOUR check before scoring any CSI: assert
   from A's v2 manifests that (a) held-out masks were uniformly corrected and (b) threshold
   selection was re-run on the corrected held-out set. If either fails, stop and report — do
   not score extent.
2. **Anchoring window discipline for replay #2.** B's anchored artifact applies night-1-calibrated
   factors to every interval; over the full Aug-28–Sep-10 window that inflates totals ~195→494 mm.
   Your replay is the 48-h event window only — assert that the forcing actually consumed by the
   solver is anchored ONLY within the replay window, with antecedent handled by the
   storage-at-spill initial condition (never by replaying anchored pre-event rain). State the
   night-2 factor-extrapolation limitation in the run manifest, and run B's enabled sanity
   check: anchored night-2 station cumulatives vs the seven night-2 alert values — report the
   comparison, whatever it shows.
3. **Calibration data split under the realized N=16.** Strict-depth rows number 16, not 30+.
   Fit calibration primarily against Goal A's validated extent reference restricted to its
   validated strata (dense signal, city-wide), plus the GT training split; the GT holdout
   (committed seed, fixed before scoring) stays untouched and is the depth-axis denominator —
   stated beside every depth number. The rubric operates unchanged; 16 < 30 is a named V11
   shortfall in the verdict, not a reason to touch the rubric.
4. **Confirm C's rubric red-mutation record exists** (`runs/gt_curation_v2/` — the deliberately
   mis-banded and cue-absent rows reddening the audit, per Goal C's V5 contract). C's summary
   documents other V6 records but does not explicitly cite this one. If it is missing, it is a
   ten-minute follow-up on the C branch BEFORE the verdict cites the rubric — not a merge
   blocker.
5. **Seam assertions at merge (V8):** GT v3 SHA `ecce7c0f4c5a0bc40907a550539cf7b041e5b6ffc657a1ddfc4519ef373e33dd`;
   B's anchored-table SHA chain (unanchored `fd1bcfd6…` → anchored `940c936c…`) via the
   fail-closed adapter (explicit config flip, SHA-asserted); A's extent-reference grid identity
   vs the solver grid before any CSI; `configs/validation.yaml` already points at v3 (C
   repointed — expect no edit needed; verify, don't re-edit). Post-merge, the full unified suite
   must run 0 failures before any GPU authorization.

Everything else in the Goal D prompt stands unchanged, including the signed rubric — no
threshold moves, whatever the replays show.
