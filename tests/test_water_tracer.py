"""Unit and Invariant Tests for Water Tracing & Hypothesis Falsification Module.

Verifies:
1. Config pre-flight resolution (CLAUDE.md Rule 7).
2. Manifest provenance and completeness (CLAUDE.md Rule 6).
3. Part 0 Validation Gate:
   - Check 1: Flow accumulation conservation (diff == 0 cells).
   - Check 2: Downstream flow monotonicity (0 violations).
   - Check 3: Known-answer lake catchment areas against published literature.
   - Check 4: Lake polygon containment in delineated catchments (>= 95%).
4. Catchment budgets cannot claim closure without measured face flux (R4).
5. Part 2 storage accounting defect fix: Class 1 unused <= All basins unused.
6. Empty-lake hypothesis falsifier invariants (R5, W3).
7. Evidence ledger schema and institutional source attribution (R3).
"""

from __future__ import annotations

import gzip
import json
import math
from pathlib import Path

import pytest
import yaml
from affine import Affine

from jaladhar.analysis.water_tracer import (
    compile_antecedent_evidence_ledger,
    raster_cell_center,
    resolve_analysis_config,
)

REPO = Path(__file__).resolve().parents[1]


def _water_manifest() -> dict:
    path = REPO / "runs/analysis/water_tracing/manifest.json"
    if not path.exists():
        pytest.skip("BLOCKED: water-tracing manifest requires the full terrain and Phase 3 rerun")
    return json.loads(path.read_text())


def test_raster_cell_center_uses_realized_transform() -> None:
    """Coordinate reporting follows the raster transform, including buffer offset.

    Observable if false: changing the raster origin would leave the reported
    coordinate unchanged. Deliberate mutation: replacing this transform with
    the former hardcoded origin makes the expected centre fail.

    Scope: one non-default affine transform and buffered-space cell.
    """
    transform = Affine.translation(500_000.0, 1_500_000.0) * Affine.scale(10.0, -10.0)

    assert raster_cell_center(transform, row=50, col=70) == (500_705.0, 1_499_495.0)


def test_resolve_analysis_config_valid() -> None:
    """Full-data pre-flight succeeds only when every configured artifact exists."""
    cfg_path = REPO / "configs/analysis.yaml"
    with open(cfg_path) as f:
        cfg = yaml.safe_load(f)
    missing = [
        value
        for key, value in cfg["paths"].items()
        if key != "runs_dir" and not (REPO / value).exists()
    ]
    if missing:
        pytest.skip(f"BLOCKED: analysis artifacts not built: {missing}")
    resolve_analysis_config(cfg)


def test_resolve_analysis_config_missing_key() -> None:
    """Pre-flight check fails immediately with aggregated error on missing keys (Rule 7)."""
    with open(REPO / "configs/analysis.yaml") as f:
        invalid_cfg = yaml.safe_load(f)
    del invalid_cfg["paths"]["depth_final_buffered"]

    # The remaining unavailable artifacts are intentional: the observable here is
    # that this new producer-consumer seam appears in the aggregated pre-flight error.
    with pytest.raises(KeyError, match="paths.depth_final_buffered"):
        resolve_analysis_config(invalid_cfg)


def test_manifest_provenance() -> None:
    """Execution manifest conforms to Rule 6 (git SHA, status completed, wall clock, etc.)."""
    m = _water_manifest()

    assert m["stage"] == "water_tracing_and_hypothesis_falsification"
    assert m["status"] == "completed"
    assert "git_sha" in m and len(m["git_sha"]) >= 7
    assert "start_time_iso" in m
    assert "wall_clock_sec" in m and m["wall_clock_sec"] > 0
    assert len(m["target_points"]) == 4


def test_part0_validation_gate_invariants() -> None:
    """Assert that Part 0 passed all 4 required validation checks."""
    m = _water_manifest()

    p0 = m["part0_validation_gate"]
    assert p0["all_checks_passed"] is True

    # Check 1: Conservation
    assert p0["check1_conservation_passed"] is True
    assert p0["check1_difference_cells"] == 0
    assert p0["check1_outlet_cells_sum"] == p0["check1_total_cells"]

    # Check 2: Monotonicity
    assert p0["check2_monotonicity_passed"] is True
    assert p0["check2_violations_count"] == 0
    assert p0["check2_paths_sampled"] >= 1000

    # Check 3: Known-Answer Validation
    assert p0["check3_known_answer_passed"] is True
    assert 130.0 <= p0["check3_bellandur_catchment_km2"] <= 180.0
    assert 240.0 <= p0["check3_varthur_catchment_km2"] <= 300.0
    assert "Ramachandra" in p0["check3_bellandur_citation"]

    # Check 4: Containment
    assert p0["check4_containment_passed"] is True
    assert p0["check4_bellandur_containment_pct"] >= 95.0
    assert p0["check4_varthur_containment_pct"] >= 95.0


def test_catchment_water_budgets_are_measured_but_not_falsely_closed() -> None:
    """A rerun reports its transport account but does not claim catchment closure.

    Scope: full water-tracing manifest. BLOCKED until terrain and Phase 3 are
    rebuilt. If the old remainder-derived writer or a closure claim returns,
    the explicit status and realized transport terms make this test red.
    """
    m = _water_manifest()

    budgets = m["part1_catchment_budgets"]
    for pid in ["GT_17", "GT_15", "GT_06", "GT_05"]:
        assert pid in budgets
        b = budgets[pid]

        # Area non-zero
        assert b["catchment_cells"] >= 1
        assert b["catchment_area_m2"] >= 100.0
        assert b["rainfall_volume_m3"] > 0.0

        assert b["budget_closed"] is False
        assert b["boundary_outflux_volume_m3"] is not None
        assert b["net_routed_out_volume_m3"] == b["boundary_outflux_volume_m3"]
        assert b["residual_volume_m3"] is not None
        assert b["budget_status"].startswith("MEASURED, NOT CLOSED:")


def test_part2_subset_superset_storage_defect_fixed() -> None:
    """Assert that Defect 1 is resolved: Class 1 unused storage <= All basins unused storage."""
    m = _water_manifest()

    hyp = m["part2_hypothesis_falsification"]

    unused_class1 = hyp["total_unused_storage_class1_lakes_m3"]
    unused_all = hyp["total_unused_storage_all_basins_m3"]

    # Invariant: Subset must be strictly <= Superset
    assert unused_class1 <= unused_all
    assert (
        hyp["unused_storage_class1_lakes_pct_of_storm"]
        <= hyp["unused_storage_all_basins_pct_of_storm"]
    )

    # Exact derivation (Amendment 3): total rain from manifest, not hard-coded.
    # 51.40% is a current FINDING, not a universal invariant; historical 5.37% differs.
    total_rain = hyp["total_domain_rainfall_m3"]
    unused_m3 = hyp["total_unused_storage_all_basins_m3"]
    pct = hyp["unused_storage_all_basins_pct_of_storm"]
    assert math.isfinite(total_rain) and total_rain > 0
    assert math.isfinite(unused_m3) and unused_m3 >= 0
    assert math.isfinite(pct)
    assert pct == round(unused_m3 / total_rain * 100, 2)
    # Red mutation: changing pct by 0.01 must fail exact check
    assert pct != round(unused_m3 / total_rain * 100, 2) + 0.01


def test_empty_lake_hypothesis_falsifier_verdicts() -> None:
    """A run must not emit a causal verdict without a realized point trace."""
    m = _water_manifest()

    hyp = m["part2_hypothesis_falsification"]
    verdicts = hyp["per_point_verdicts"]

    # The report deliberately withholds verdicts until a realized point trace exists.
    for pid in ["GT_17", "GT_15", "GT_06", "GT_05"]:
        assert verdicts[pid]["verdict"] == "UNRESOLVED"
        assert verdicts[pid]["upstream_basin_count"] is None


def test_evidence_ledger_integrity() -> None:
    """Evidence ledger contains non-empty entries with verified URLs and tags."""
    ledger_path = REPO / "runs/analysis/water_tracing/antecedent_evidence_ledger.json"
    if not ledger_path.exists():
        pytest.skip("BLOCKED: evidence ledger is emitted by the full water-tracing rerun")

    with open(ledger_path) as f:
        ledger = json.load(f)

    assert len(ledger) >= 50
    for e in ledger:
        assert len(e["quote"]) > 20
        assert e["url"].startswith("http")
        assert e["reliability_label"] in ["[FINDING]", "[READING]"]
        assert e["institutional_source"] in ["IMD", "KSNDMC", "BBMP", "Media Observation"]


def test_mixed_institutional_sentence_preserves_each_attribution(tmp_path: Path) -> None:
    """A mixed sentence yields distinct IMD and KSNDMC evidence entries.

    Observable if false: the if/elif source classifier emits only the first
    institution and quote-only deduplication collapses the other attribution.

    Deliberate mutation demonstrated red: restoring the former IMD-first
    ``if/elif`` classifier leaves only the IMD entry and fails this assertion.
    """
    sentence = (
        "According to KSNDMC, Varthur recorded 83.5 mm of rain over 24 hours, "
        "while Bengaluru recorded 131.6 mm of rain according to IMD."
    )
    articles = [
        {
            "full_text": sentence,
            "source": "Test outlet",
            "url": "https://example.test/mixed-attribution",
            "meta_date": "2022-09-05",
        },
        {
            "full_text": sentence,
            "source": "Test outlet",
            "url": "https://example.test/mixed-attribution",
            "meta_date": "2022-09-05",
        },
    ]
    corpus_path = tmp_path / "articles.json"
    corpus_path.write_text(json.dumps(articles))

    matching = [
        entry
        for entry in compile_antecedent_evidence_ledger(corpus_path)
        if entry.quote == sentence
    ]

    assert sorted(entry.institutional_source for entry in matching) == ["IMD", "KSNDMC"]


def test_bundled_corpus_retains_ksndmc_station_evidence(tmp_path: Path) -> None:
    """Bundled station rainfall sentence is realized as KSNDMC evidence.

    Observable if false: the sentence containing Bellandur 67.5 mm,
    Hallenayakanahalli 74 mm, and Varthur 83.5 mm has no KSNDMC-labelled entry.

    Deliberate mutation demonstrated red: quote-only deduplication combined
    with the former IMD-first classifier removes the KSNDMC attribution.
    """
    bundled_path = REPO / "data/seed/fetched_articles.json.gz"
    with gzip.open(bundled_path, "rt", encoding="utf-8") as f:
        articles = json.load(f)
    corpus_path = tmp_path / "bundled_articles.json"
    corpus_path.write_text(json.dumps(articles))

    ledger = compile_antecedent_evidence_ledger(corpus_path)
    station_entries = [
        entry
        for entry in ledger
        if "Bellandur, Hallenayakanahalli, Varthur recorded 67.5 mm, 74mm, 83.5 mm" in entry.quote
    ]

    assert station_entries, "bundled KSNDMC station sentence was not extracted"
    assert "KSNDMC" in {entry.institutional_source for entry in station_entries}
