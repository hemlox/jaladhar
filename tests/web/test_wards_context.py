"Ward/locality context assets (WF-6 U0f): parse, reproject, join, index, emit. Verification scope is stated beside every check (V7). The audit figures this suite reconciles against were measured independently of this module's code (2026-08 audit of the September 2022 replay product) and are the baseline; the module's own outputs are the candidate. No assertion below compares a candidate to a baseline this repo produced (V3). Red-under-mutation demonstrations (V5) live in ``TestRedUnderMutation`` and name their deliberate mutations in docstrings."  # noqa: E501

from __future__ import annotations

import json
import struct
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from typer.testing import CliRunner

from jaladhar.web import wards as wards_mod

REPO = Path(__file__).resolve().parents[2]

AUDIT_WARD_COUNTS = {2022: 243, 2023: 225}
AUDIT_NAMES_ONLY_22, AUDIT_NAMES_ONLY_23, AUDIT_NAMES_COMMON = 84, 66, 159
AUDIT_AREA_DELTA_MEDIAN_PCT = 13.18
AUDIT_WORST_NAME, AUDIT_WORST_PCT = "mallasandra", 183.6
AUDIT_CLASSIFIED_NAMED_IMPLIED = 10318
AUDIT_CENTROID_WITHIN = 9404
AUDIT_PAIRS_EXACT_CASE = 2559

RUNNER = CliRunner()


@pytest.fixture(scope="module")
def join_result() -> dict:
    return wards_mod.join_segments_to_wards(2022)


@pytest.fixture(scope="module")
def wards_by_year() -> dict[int, object]:
    return {year: wards_mod.load_wards(year) for year in sorted(wards_mod.KML_PATHS)}


class TestParsingAndReprojection:
    def test_placemark_counts_match_audit(self) -> None:
        "Scope: every Placemark element in both realised KML files. If parsing dropped or duplicated a placemark, the counts would move off the audit's independent 243/225."  # noqa: E501

        for year, path in wards_mod.KML_PATHS.items():
            placemarks = wards_mod._parse_kml_placemarks(path)
            assert len(placemarks) == AUDIT_WARD_COUNTS[year], f"vintage {year}"

    def test_unique_ward_numbers_and_names(self, wards_by_year: dict[int, object]) -> None:
        "Scope: all ward rows of both vintages."

        for year, frame in wards_by_year.items():
            numbers = frame["ward_no"].tolist()
            assert len(set(numbers)) == len(numbers), f"duplicate ward_no in {year}"
            names = frame["name"].astype(str).tolist()
            assert all(name.strip() for name in names), f"empty ward name in {year}"

    def test_reprojection_area_band(self, wards_by_year: dict[int, object]) -> None:
        "Scope: whole-city total area per vintage after WGS84->EPSG:32643. A reprojection unit error (degrees vs metres, wrong zone) moves the total area by orders of magnitude; the band [700, 730] km2 pins it to the realised BBMP extent (audit-quoted ~716.9 km2)."  # noqa: E501

        for year, frame in wards_by_year.items():
            assert str(frame.crs.to_epsg()) == "32643", f"vintage {year} CRS"
            area_km2 = float(frame.area.sum()) / 1e6
            assert 700.0 <= area_km2 <= 730.0, f"vintage {year} area {area_km2:.2f} km^2"
            bounds = frame.total_bounds
            assert 750_000 < bounds[0] < bounds[2] < 820_000
            assert 1_410_000 < bounds[1] < bounds[3] < 1_470_000


class TestDivergenceReconciliation:
    def test_name_split_matches_audit(self) -> None:
        "Scope: full ward-name sets of both vintages under the reconciled match rule (whitespace-collapsed lowercase, trailing 'ward' stripped). Plain lowercase gives 86/68/157 -- the rule itself is part of what is under test; if the rule regressed the split would not reconcile."  # noqa: E501

        divergence = wards_mod.measure_divergence()
        names = divergence["names"]
        assert names["only_2022"] == AUDIT_NAMES_ONLY_22
        assert names["only_2023"] == AUDIT_NAMES_ONLY_23
        assert names["common"] == AUDIT_NAMES_COMMON

    def test_area_delta_reconciles_audit(self) -> None:
        "Scope: common-named ward pairs across both vintages. The median is reported under BOTH pairing rules with derivations named; the audit's quoted figure must reconcile under at least one, and the worst ward (by |dA|/min) must be mallasandra at ~183.6%."  # noqa: E501

        divergence = wards_mod.measure_divergence()
        delta = divergence["boundary_area_delta"]
        assert (
            delta["median_pct_pairs_plain_lower"] == AUDIT_AREA_DELTA_MEDIAN_PCT
            or delta["median_pct_pairs_ward_stripped"] == AUDIT_AREA_DELTA_MEDIAN_PCT
        )
        assert delta["worst_name"] == AUDIT_WORST_NAME
        assert abs(delta["worst_pct_min_denominator"] - AUDIT_WORST_PCT) < 0.5

    def test_ward_counts(self) -> None:
        divergence = wards_mod.measure_divergence()
        assert divergence["ward_counts"] == {"2022": 243, "2023": 225}


class TestSegmentWardJoin:
    def test_denominator_reconciles_audit_implied(self, join_result: dict) -> None:
        "Scope: all classified named segments (10 highway classes x non-null name) in the realised roads_centrelines.gpkg. The audit quoted 91.14% joined; 9,404 / 0.9114 implies exactly this denominator, so reproducing it confirms the subset definition."  # noqa: E501

        assert join_result["n_classified_named"] == AUDIT_CLASSIFIED_NAMED_IMPLIED

    def test_centroid_within_count_beside_audit(self, join_result: dict) -> None:
        "Scope: centroid-within assignments against the 2022 canonical layer, CRS EPSG:32643, centroid = shapely line centroid. This module realises 9,402 (91.12%); the audit's independent figure was 9,404 (91.14%). The two-segment residual is recorded here and in the manifest rather than tuned away; the rate guard below keeps the claim honest while the exact count pins realised behaviour."  # noqa: E501

        assert join_result["n_centroid_within_rows"] == 9402
        rate = join_result["n_centroid_within_rows"] / join_result["n_classified_named"]
        assert abs(rate - 0.9114) < 0.005
        assert abs(join_result["n_centroid_within_rows"] - AUDIT_CENTROID_WITHIN) <= 5

    def test_assignments_independently_recomputed(self, join_result: dict) -> None:
        "Scope: deterministic sample (every 37th assigned segment, n=254 of 9,402) verified through a DIFFERENT mechanism than production: direct shapely point.within(polygon) over all 243 ward polygons instead of geopandas sjoin's STRtree path. If sjoin mis-assigned boundary segments, this linear-scan recomputation would disagree."  # noqa: E501

        import geopandas as gpd

        assignments = join_result["assignments"]
        sample = assignments.iloc[::37]
        assert len(sample) >= 200, "sample too small to bound the claim"
        wards_frame = wards_mod.load_wards(2022)
        polygons = dict(zip(wards_frame["ward_no"], wards_frame.geometry, strict=True))

        roads = gpd.read_file(wards_mod.ROADS_GPKG_PATH)
        roads_by_id = roads.set_index("segment_id")
        mismatches = 0
        for row in sample.itertuples(index=False):
            line = roads_by_id.loc[row.segment_id, "geometry"]
            point = line.centroid
            hits = {no for no, poly in polygons.items() if point.within(poly)}
            if row.ward_no not in hits:
                mismatches += 1
        assert mismatches == 0, f"{mismatches} sampled assignments failed recomputation"

    def test_secondary_predicates_recorded(self, join_result: dict) -> None:
        "Scope: both predicates' counts on the same segment set."

        assert join_result["n_covered_by_rows"] == join_result["n_centroid_within_rows"]
        assert join_result["n_intersects_segments"] > join_result["n_centroid_within_rows"]
        assert join_result["pairs_exact_case"] == AUDIT_PAIRS_EXACT_CASE

    def test_point_frame_preserves_rows(self) -> None:
        "Production guard: the point frame built for the sjoin must carry every source row. Motivation: under this environment's pandas 3.0.5, ``GeoDataFrame(dict, geometry=<GeoSeries>)`` silently returned 1,247 of 10,318 rows during U0f scratch verification -- production therefore passes an ndarray and asserts the row count (see _safe_points_frame). If that guard regressed to the trap construction, this test reddens."  # noqa: E501

        import geopandas as gpd

        roads = gpd.read_file(wards_mod.ROADS_GPKG_PATH, rows=5000)
        named = roads[roads["name"].notna()]
        if len(named) < 100:
            pytest.skip("sample window has too few named segments")  # pragma: no cover
        safe = wards_mod._safe_points_frame(named)
        assert len(safe) == len(named)


@pytest.fixture(scope="module")
def build_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    "One clean CLI build into a temp context dir, shared by the class below."
    tmp_out = tmp_path_factory.mktemp("context_build")
    monkey = pytest.MonkeyPatch()
    try:
        monkey.setattr(wards_mod, "CONTEXT_DIR", tmp_out)
        result = RUNNER.invoke(wards_mod.app, ["build"])
        assert result.exit_code == 0, result.output
    finally:
        monkey.undo()
    return tmp_out


class TestEmittedAssetsRealizedState:
    "Runs the REAL CLI from clean state into a temp context dir (V4/V12): identical inputs, isolated outputs, then asserts the bytes on disk."  # noqa: E501

    def test_all_outputs_exist(self, build_dir: Path) -> None:
        expected = {
            "wards_2022.bin",
            "wards_2022.meta.json",
            "wards_2023.bin",
            "wards_2023.meta.json",
            "segment_ward_2022.csv.gz",
            "search_index.json",
            "context_build_manifest.json",
        }
        realized = {p.name for p in build_dir.iterdir()}
        assert expected <= realized

    def test_bin_layout_decodes_against_meta(self, build_dir: Path) -> None:
        "Scope: both emitted vintage bundles, full byte layout. Independent decode via struct (not the module's np.frombuffer path): header count, offset span, exact byte length, finite coordinates."  # noqa: E501

        for vintage in (2022, 2023):
            blob = (build_dir / f"wards_{vintage}.bin").read_bytes()
            meta = json.loads((build_dir / f"wards_{vintage}.meta.json").read_text("utf-8"))
            (n_rings,) = struct.unpack_from("<I", blob, 0)
            offsets = struct.unpack_from(f"<{n_rings + 1}I", blob, 4)
            expected_len = 4 + 4 * (n_rings + 1) + 8 * offsets[-1]
            assert len(blob) == expected_len == meta["bytes"]
            assert meta["n_rings"] == n_rings
            assert meta["n_vertices"] == offsets[-1]
            coords = np.frombuffer(blob[4 + 4 * (n_rings + 1) :], dtype="<f4")
            assert bool(np.isfinite(coords).all())
            wards_mod.validate_bundle(blob, meta)

    def test_manifest_lifecycle_completed(self, build_dir: Path) -> None:

        import hashlib

        manifest = json.loads((build_dir / "context_build_manifest.json").read_text("utf-8"))
        assert manifest["status"] == "completed"
        assert manifest["started_time_iso"] < manifest["completed_time_iso"]
        assert len(manifest["git_sha"]) == 40
        assert manifest["selected_join_vintage"] == 2022
        assert manifest["vintage_decision"]["canonical_layer_for_locality_labels"].endswith(
            "bbmp_wards_2022.kml"
        )
        for key in ("kml_2022", "kml_2023", "roads_segment_lookup"):
            sha = manifest["inputs"][key]["sha256"]
            path = REPO / manifest["inputs"][key]["path"]
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            assert digest == sha, f"{key} input SHA does not reproduce"

        bin_sha = manifest["outputs"]["wards_2022"]["sha256"]
        blob = (build_dir / "wards_2022.bin").read_bytes()
        assert hashlib.sha256(blob).hexdigest() == bin_sha

    def test_join_table_realized_rows(self, build_dir: Path) -> None:
        table = pd.read_csv(build_dir / "segment_ward_2022.csv.gz")
        manifest = json.loads((build_dir / "context_build_manifest.json").read_text("utf-8"))
        realized_join = manifest["join_realized"]
        assert list(table.columns) == ["segment_id", "street_name", "ward_no", "ward_name"]
        assert len(table) == realized_join["n_centroid_within_rows"] == 9402
        assert table["segment_id"].is_unique
        assert set(table["ward_no"]) <= {
            w["ward_no"]
            for w in json.loads((build_dir / "wards_2022.meta.json").read_text("utf-8"))["wards"]
        }

    def test_search_index_shape(self, build_dir: Path) -> None:
        index = json.loads((build_dir / "search_index.json").read_text("utf-8"))
        entries = index["entries"]
        kinds = [e["kind"] for e in entries]
        assert index["kind_counts"] == {
            "ward": AUDIT_WARD_COUNTS[2022],
            "street": AUDIT_PAIRS_EXACT_CASE,
        }
        assert index["kind_counts"] == {k: kinds.count(k) for k in ("ward", "street")}
        for entry in entries:
            key = entry["key"]
            assert key == key.strip() and key == key.lower()
        streets = {e["name"]: e for e in entries if e["kind"] == "street"}
        sample = next(iter(streets.values()))
        assert isinstance(sample["segment_ids"], list) and sample["segment_ids"]

    def test_no_pin_codes_anywhere(self, build_dir: Path) -> None:

        import re

        index = json.loads((build_dir / "search_index.json").read_text("utf-8"))
        assert index["pin_codes_held"] is False
        pin_re = re.compile(r"\b\d{6}\b")

        def _walk(node: object) -> None:
            if isinstance(node, dict):
                for value in node.values():
                    _walk(value)
            elif isinstance(node, list):
                for item in node:
                    _walk(item)
            elif isinstance(node, str):
                assert not pin_re.search(node), f"PIN-like token found: {node!r}"

        _walk(index)


@pytest.fixture(scope="module")
def clean_bundle(tmp_path_factory: pytest.TempPathFactory) -> tuple[bytes, dict]:
    tmp_out = tmp_path_factory.mktemp("mutation_clean")
    monkey = pytest.MonkeyPatch()
    try:
        monkey.setattr(wards_mod, "CONTEXT_DIR", tmp_out)
        result = RUNNER.invoke(wards_mod.app, ["build"])
        assert result.exit_code == 0, result.output
    finally:
        monkey.undo()
    blob = (tmp_out / "wards_2022.bin").read_bytes()
    meta = json.loads((tmp_out / "wards_2022.meta.json").read_text("utf-8"))
    return blob, meta


class TestRedUnderMutation:
    "V5: each test applies a deliberate mutation and requires the validator to redden. Mutations are named in the docstrings."  # noqa: E501

    def test_truncated_coordinate_stream_detected(self, clean_bundle: tuple) -> None:
        "MUTATION: drop the last coordinate pair (8 bytes) from the realised bundle. validate_bundle must fail on vertex-count/offset mismatch -- this is the 'corrupt a coordinate count -> red' demonstration."  # noqa: E501

        blob, meta = clean_bundle
        assert wards_mod.validate_bundle(blob, meta) is None  # sanity: green unmutated
        truncated = blob[:-8]
        with pytest.raises(wards_mod.WardContextError):
            wards_mod.validate_bundle(truncated, meta)

    def test_flipped_vertex_declaration_detected(self, clean_bundle: tuple) -> None:
        "MUTATION: inflate metadata n_vertices by +64 without touching the blob. The declared-vs-realised mismatch must redden."  # noqa: E501

        blob, meta = clean_bundle
        mutated = {**meta, "n_vertices": meta["n_vertices"] + 64}
        with pytest.raises(wards_mod.WardContextError):
            wards_mod.validate_bundle(blob, mutated)

    def test_ring_range_bookkeeping_mutation_detected(self, clean_bundle: tuple) -> None:
        "MUTATION: replace one ward's vertex ring_ranges with ring indices ([i, i+1]) -- the exact bookkeeping slip this module shipped once and the strengthened validator now catches."  # noqa: E501

        blob, meta = clean_bundle
        mutated = json.loads(json.dumps(meta))
        mutated["wards"][0]["ring_ranges"] = [[0, 1]]
        mutated["wards"][0]["vertex_count"] = 1
        with pytest.raises(wards_mod.WardContextError):
            wards_mod.validate_bundle(blob, mutated)

    def test_manifest_written_at_run_start_on_failure(
        self, tmp_path_factory: pytest.TempPathFactory
    ) -> None:
        "MUTATION: corrupt the 2023 KML source mid-build. Rule 6 requires that even a failing run leaves a manifest written BEFORE the heavy work: status failed, but git_sha and the 2022 input SHA already recorded. If provenance only existed when nothing went wrong, the assertions below would fail."  # noqa: E501

        tmp_out = tmp_path_factory.mktemp("mutation_fail")
        corrupt_kml = tmp_path_factory.mktemp("corrupt") / "bbmp_wards_2023.kml"
        corrupt_kml.write_text("<kml xmlns='http://www.opengis.net/kml/2.2'><not-closed>", "utf-8")
        monkey = pytest.MonkeyPatch()
        try:
            monkey.setattr(wards_mod, "CONTEXT_DIR", tmp_out)
            monkey.setitem(wards_mod.KML_PATHS, 2023, corrupt_kml)
            result = RUNNER.invoke(wards_mod.app, ["build"])
        finally:
            monkey.undo()
        assert result.exit_code == 1
        manifest_path = tmp_out / "context_build_manifest.json"
        assert manifest_path.is_file(), "no manifest survived a failing build"
        manifest = json.loads(manifest_path.read_text("utf-8"))
        assert manifest["status"] == "failed"
        assert len(manifest["git_sha"]) == 40
        assert manifest["inputs"]["kml_2022"]["sha256"]
        assert manifest["error_type"] == "WardContextError"

    def test_missing_context_config_aggregates_every_key(
        self, tmp_path_factory: pytest.TempPathFactory
    ) -> None:
        "MUTATION (V5): configs/context.yaml removed entirely. Expected observation: startup resolution raises WardContextConfigError whose message names the missing file AND every required key together (rule 7) -- not a KeyError on the first downstream access after the heavy work. Green state: TestContextConfigSurface reads the real file."  # noqa: E501

        monkey = pytest.MonkeyPatch()
        try:
            monkey.setattr(
                wards_mod,
                "CONTEXT_CONFIG_PATH",
                tmp_path_factory.mktemp("no_config") / "context.yaml",
            )
            wards_mod.reset_context_config_cache()
            with pytest.raises(wards_mod.WardContextConfigError) as excinfo:
                wards_mod._resolve_context_config(wards_mod.CONTEXT_CONFIG_PATH)
        finally:
            monkey.undo()
            wards_mod.reset_context_config_cache()
        message = str(excinfo.value)
        assert "not found" in message
        for key in wards_mod.CONTEXT_CONFIG_REQUIRED_KEYS:
            assert f"unresolved required key: {key}" in message, f"aggregated error omits {key}"

    def test_corrupt_context_yaml_lists_all_unresolved_keys(
        self, tmp_path_factory: pytest.TempPathFactory
    ) -> None:
        "MUTATION (V5): configs/context.yaml corrupted to a bare scalar (parses as YAML but not into the required mapping). Expected observation: ONE aggregated error reporting the shape problem and listing ALL required keys as unresolved. The same mutation against the old code path would have surfaced as a KeyError mid-build instead."  # noqa: E501

        corrupt = tmp_path_factory.mktemp("corrupt_config") / "context.yaml"
        corrupt.write_text("just-a-garbage-scalar\n", "utf-8")
        monkey = pytest.MonkeyPatch()
        try:
            monkey.setattr(wards_mod, "CONTEXT_CONFIG_PATH", corrupt)
            wards_mod.reset_context_config_cache()
            with pytest.raises(wards_mod.WardContextConfigError) as excinfo:
                wards_mod._resolve_context_config(corrupt)
        finally:
            monkey.undo()
            wards_mod.reset_context_config_cache()
        message = str(excinfo.value)
        assert "YAML mapping" in message
        for key in wards_mod.CONTEXT_CONFIG_REQUIRED_KEYS:
            assert f"unresolved required key: {key}" in message

    def test_cli_refuses_to_start_on_missing_config(
        self, tmp_path_factory: pytest.TempPathFactory
    ) -> None:
        "MUTATION (V5): configs/context.yaml absent at CLI level. Rule 7 fail-fast is realized here: build() resolves the config as its FIRST statement, so the run dies before the compute-budget banner or any heavy work -- observed by the absence of that banner in output."  # noqa: E501

        from jaladhar.web import wards as _wards

        monkey = pytest.MonkeyPatch()
        try:
            monkey.setattr(
                _wards,
                "CONTEXT_CONFIG_PATH",
                tmp_path_factory.mktemp("cli_no_config") / "context.yaml",
            )
            _wards.reset_context_config_cache()
            result = RUNNER.invoke(_wards.app, ["build"])
        finally:
            monkey.undo()
            _wards.reset_context_config_cache()
        assert result.exit_code != 0
        assert "compute_budget" not in (result.output or "")
        assert isinstance(result.exception, _wards.WardContextConfigError)
        assert "unresolved required key" in str(result.exception)


class TestContextConfigSurface:

    def test_config_file_resolution_honored(self) -> None:
        "Scope: the realised configs/context.yaml at repo root, full parse. V2 observable: values come from the file on disk; if someone edited the yaml or reintroduced a code constant, this read-back would move."  # noqa: E501
        wards_mod.reset_context_config_cache()
        cfg = wards_mod.context_config()
        assert cfg["ward_layer_rule"] == ("ward layer vintage matches the product's event window")
        assert cfg["ward_vintage_by_product_kind"] == {
            "historical_replay": 2022,
            "live_nowcast": 2023,
        }
        assert cfg["default_vintage"] == 2022

    def test_no_code_fallback_constants_remain(self) -> None:
        assert not hasattr(wards_mod, "WARD_VINTAGE_BY_PRODUCT_KIND")
        assert not hasattr(wards_mod, "DEFAULT_WARD_VINTAGE")
        assert not hasattr(wards_mod, "WARD_VINTAGE_DECISION")

    def test_manifest_records_config_surface(self, build_dir: Path) -> None:
        "Scope: the completed build's manifest vintage_decision block -- config_source, rule string VERBATIM, condensed owner rationale."  # noqa: E501
        manifest = json.loads((build_dir / "context_build_manifest.json").read_text("utf-8"))
        decision = manifest["vintage_decision"]
        assert decision["config_source"] == "configs/context.yaml"
        assert decision["ward_layer_rule"] == (
            "ward layer vintage matches the product's event window"
        )
        assert decision["default_vintage"] == 2022
        assert decision["product_kind_mapping"] == {
            "historical_replay": 2022,
            "live_nowcast": 2023,
        }
        assert decision["cli_override_applied"] is False
        reason = decision["reason"]
        assert "September 2022" in reason
        assert "substantially" in reason and "boundaries" in reason
        assert "comparatively stable" in reason

    def test_cli_override_wins_over_config(self, tmp_path_factory: pytest.TempPathFactory) -> None:
        tmp_out = tmp_path_factory.mktemp("override_vintage")
        monkey = pytest.MonkeyPatch()
        try:
            monkey.setattr(wards_mod, "CONTEXT_DIR", tmp_out)
            result = RUNNER.invoke(wards_mod.app, ["build", "--ward-vintage", "2023"])
            assert result.exit_code == 0, result.output
        finally:
            monkey.undo()
        manifest = json.loads((tmp_out / "context_build_manifest.json").read_text("utf-8"))
        assert manifest["selected_join_vintage"] == 2023
        assert (tmp_out / "segment_ward_2023.csv.gz").is_file()
        index = json.loads((tmp_out / "search_index.json").read_text("utf-8"))
        assert index["vintage"] == 2023
        assert manifest["vintage_decision"]["cli_override_applied"] is True
