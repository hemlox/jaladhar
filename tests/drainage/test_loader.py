"""WF-1 loader tests: tiny KML fixtures in tmp_path, partial recovery, gate refusal.
assertion observes realized geometry, not the loader's own transform output (V2).
1. GATE REFUSAL: fixture with one Point placemark among valid lines pushes the
2. PARTIAL RECOVERY: one deliberately malformed placemark among valid ones is
removing the malformed placemark moves the counter to 0 (counter liveness, V5
mutation recorded beside the test).
Scope note (V7): these tests exercise the full parse->transform->sort->diagnose
(163/870/5815) are asserted only in the real-data run, not here."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from pyproj import Transformer
from typer.testing import CliRunner

import jaladhar.drainage.loader as loader_mod
from jaladhar.drainage.loader import (
    LoaderError,
    app,
    load_reaches,
    resolve_config,
)
from jaladhar.drainage.stitch import Reach

REPO = Path(loader_mod.REPO)

KML_HEAD = (
    '<?xml version="1.0" encoding="utf-8"?>\n'
    '<kml xmlns="http://www.opengis.net/kml/2.2" '
    'xmlns:gx="http://www.google.com/kml/ext/2.2">'
    '<Document id="root_doc">\n'
)
KML_TAIL = "</Document></kml>\n"

TF = Transformer.from_crs("EPSG:4326", "EPSG:32643", always_xy=True)


def _utm(lon: float, lat: float) -> tuple[float, float]:
    x, y = TF.transform(lon, lat)
    return float(x), float(y)


def _pm(oid: int | None, body: str) -> str:
    data = ""
    if oid is not None:
        data = (
            '<ExtendedData><SchemaData schemaUrl="#sch">'
            f'<SimpleData name="OBJECTID">{oid}</SimpleData>'
            f'<SimpleData name="Length">{oid % 97}.5</SimpleData>'
            f'<SimpleData name="SHAPE_Leng">{(oid % 97) * 1000}.5</SimpleData>'
            "</SchemaData></ExtendedData>"
        )
    return f"  <Placemark>{data}{body}</Placemark>\n"


def _ls(coords: str) -> str:
    return f"<LineString><coordinates>{coords}</coordinates></LineString>"


def _multi(*lines: str) -> str:
    return "<MultiGeometry>" + "".join(_ls(c) for c in lines) + "</MultiGeometry>"


def _write(path: Path, placemarks: list[str]) -> None:
    path.write_text(KML_HEAD + "".join(placemarks) + KML_TAIL)


PRIMARY_PMS = [
    _pm(3, _ls("77.500,13.000 77.501,13.000 77.502,13.001")),
    _pm(1, _ls("77.510,13.010 77.511,13.010")),
    _pm(2, _multi("77.520,13.020 77.521,13.020", "77.522,13.022 77.523,13.023")),
    _pm(None, _ls("77.53,13.03 not_a_coord,13.03")),
]

SECONDARY_PMS = [
    _pm(11, _ls("77.540,13.040 77.541,13.041")),
    _pm(12, _ls("77.541,13.041 77.542,13.042")),
]

TERTIARY_PMS = [
    _pm(21, _ls("77.550,13.050 77.551,13.051")),
    _pm(22, _ls("77.551,13.051 77.552,13.052")),
    _pm(23, _multi("77.560,13.060 77.561,13.061", "77.565,13.065 77.566,13.066")),
    _pm(
        24,
        _ls("77.50005,13.00005 77.571,13.071"),
    ),
    (
        '  <Placemark><ExtendedData><SchemaData schemaUrl="#sch">'
        '<SimpleData name="OBJECTID">25</SimpleData></SchemaData></ExtendedData>'
        "<gx:LineString><gx:coord>77.580 13.080 0 77.581 13.081 0</gx:coord></gx:LineString>"
        "</Placemark>\n"
    ),
]


@pytest.fixture(autouse=True)
def _unbind_config():
    yield
    loader_mod.bind_config(None)


@pytest.fixture()
def fixtures(tmp_path: Path) -> dict[str, Path]:
    paths = {
        "primary": tmp_path / "primary_drains_2022.kml",
        "secondary": tmp_path / "secondary_drains_2022.kml",
        "tertiary": tmp_path / "tertiary_drains_2022.kml",
    }
    _write(paths["primary"], PRIMARY_PMS)
    _write(paths["secondary"], SECONDARY_PMS)
    _write(paths["tertiary"], TERTIARY_PMS)
    return paths


def _fingerprint(reaches: list[Reach]) -> list[tuple]:
    return [(r.reach_id, r.drain_class, round(r.length_m, 9), r.geom.wkb_hex) for r in reaches]


def test_counts_ids_crs_and_objectid_sort(fixtures) -> None:
    reaches, diag = load_reaches(
        str(fixtures["primary"]), str(fixtures["secondary"]), str(fixtures["tertiary"])
    )
    assert len(reaches) == 5
    assert all(r.drain_class in ("primary", "secondary") for r in reaches)

    prim = [r for r in reaches if r.drain_class == "primary"]
    sec = [r for r in reaches if r.drain_class == "secondary"]
    assert [r.reach_id for r in prim] == [1, 2, 3]
    assert [r.reach_id for r in sec] == [4, 5]

    # OBJECTID sort realized: document order was oid 3,1,2(multipart) but id 1 must be oid 1.
    exp_oid1 = _utm(77.510, 13.010)
    assert prim[0].geom.coords[0] == pytest.approx(exp_oid1, abs=1e-6)
    assert len(prim[1].geom.coords) == 4
    exp_oid2 = _utm(77.520, 13.020)
    assert prim[1].geom.coords[0] == pytest.approx(exp_oid2, abs=1e-6)

    # Realized CRS: coordinates are UTM metres in the Bengaluru range, matching an
    # INDEPENDENT pyproj transform of the fixture's first lon/lat (V2 observable).
    for r in reaches:
        x, y = r.geom.coords[0]
        assert 700_000 < x < 900_000, f"x_m {x} not UTM-like"
        assert 1_400_000 < y < 1_500_000, f"y_m {y} not UTM-like"
    seg = _utm(77.540, 13.040)
    seg2 = _utm(77.541, 13.041)
    assert sec[0].length_m == pytest.approx(
        ((seg[0] - seg2[0]) ** 2 + (seg[1] - seg2[1]) ** 2) ** 0.5, rel=1e-9
    )

    assert diag["total_reaches"] == 5
    assert diag["gate"]["passed"] is True
    assert diag["gate"]["invalid_geoms"] == 0


def test_deterministic_across_loads_and_row_shuffle(fixtures, tmp_path) -> None:
    fp1 = _fingerprint(load_reaches(str(fixtures["primary"]), str(fixtures["secondary"]))[0])
    fp2 = _fingerprint(load_reaches(str(fixtures["primary"]), str(fixtures["secondary"]))[0])
    assert fp1 == fp2

    shuffled = tmp_path / "primary_shuffled.kml"
    _write(shuffled, list(reversed(PRIMARY_PMS)))
    fp_shuf = _fingerprint(load_reaches(str(shuffled), str(fixtures["secondary"]))[0])
    assert [p[:3] for p in fp_shuf] == [
        p[:3] for p in fp1
    ], "reach_id assignment must follow OBJECTID sort, not document row order"


def test_diagnostics_keys_present(fixtures) -> None:
    _reaches, diag = load_reaches(
        str(fixtures["primary"]), str(fixtures["secondary"]), str(fixtures["tertiary"])
    )
    for cls in ("primary", "secondary"):
        d = diag[cls]
        for key in (
            "geom_count",
            "failed_parse",
            "parse_rung",
            "sort_basis",
            "invalid_geometry_count",
            "self_intersection_count",
            "null_empty_count",
            "multipart_count",
            "vertex_spacing_stats",
            "endpoint_multiplicity",
            "length_crosscheck",
        ):
            assert key in d, f"{cls}.{key} missing"
        for stat in ("min", "p50", "mean", "max"):
            assert stat in d["vertex_spacing_stats"]
        for tol in ("0.5_m", "2.0_m", "10.0_m"):
            assert tol in d["endpoint_multiplicity"]
        lc = d["length_crosscheck"]
        for key in (
            "measured_length_sum_m",
            "median_ratio_measured_over_length_attr",
            "kml_length_attr_sum_m",
            "delta_measured_minus_shape_leng_as_recorded",
        ):
            assert key in lc
    assert "vertex_order_downhill" in diag
    vd = diag["vertex_order_downhill"]
    assert "vertex_order_downhill_fraction" in vd and "vertex_order_downhill_reason" in vd
    if vd["vertex_order_downhill_fraction"] is not None:
        assert 0.0 <= vd["vertex_order_downhill_fraction"] <= 1.0
    for key in (
        "tertiary_parsed_count",
        "tertiary_failed_count",
        "tertiary_component_count_10m",
        "tertiary_junction_candidates_endpoints_10m",
        "reaches_emitted",
    ):
        assert key in diag["tertiary"]


def test_endpoint_multiplicity_histogram_moves(fixtures) -> None:
    _reaches, diag = load_reaches(str(fixtures["primary"]), str(fixtures["secondary"]))
    hist = diag["secondary"]["endpoint_multiplicity"]["0.5_m"]["cluster_size_to_endpoint_count"]
    assert "2" in hist, "the two secondary placemarks share one exact endpoint: cluster of 2"


def test_tertiary_report_only_never_emits_reaches(fixtures) -> None:
    reaches, diag = load_reaches(
        str(fixtures["primary"]), str(fixtures["secondary"]), str(fixtures["tertiary"])
    )
    t = diag["tertiary"]
    assert t["reaches_emitted"] == 0
    assert not any(r.drain_class == "tertiary" for r in reaches)
    assert t["tertiary_placemark_count"] == 5
    assert t["tertiary_parsed_count"] == 6
    assert t["tertiary_component_count_10m"] == 5
    assert t["tertiary_junction_candidates_endpoints_10m"] >= 1
    assert t["tertiary_failed_count"] == 0


def test_tertiary_absent_is_reported_not_fatal(fixtures) -> None:
    _reaches, diag = load_reaches(str(fixtures["primary"]), str(fixtures["secondary"]))
    assert diag["tertiary"]["reaches_emitted"] == 0
    assert "reason" in diag["tertiary"]


# RED DEMO 2 + counter liveness: partial recovery counts a malformed placemark


def test_malformed_placemark_skipped_and_counted(fixtures) -> None:
    # features still load. Mutation record (V5): removing the defect moves the
    reaches, diag = load_reaches(str(fixtures["primary"]), str(fixtures["secondary"]))
    assert diag["primary"]["failed_parse"] == 1
    assert diag["primary"]["geom_count"] == 3
    assert diag["primary"]["placemark_count"] == 4
    assert len(reaches) == 5


def test_malformed_counter_liveness(fixtures, tmp_path) -> None:
    clean = tmp_path / "primary_clean.kml"
    _write(clean, PRIMARY_PMS[:-1])  # mutation: drop the deliberate defect
    _reaches, diag = load_reaches(str(clean), str(fixtures["secondary"]))
    assert diag["primary"]["failed_parse"] == 0
    assert diag["primary"]["geom_count"] == 3


# RED DEMO 1: gate refusal on bad fixture


def test_invalid_fraction_gate_refuses(fixtures, tmp_path) -> None:
    bad = tmp_path / "primary_bad.kml"
    _write(bad, PRIMARY_PMS[:-1] + [_pm(9, "<Point><coordinates>77.5,13.0</coordinates></Point>")])
    with pytest.raises(LoaderError) as excinfo:
        load_reaches(str(bad), str(fixtures["secondary"]))
    msg = str(excinfo.value)
    assert "invalid-geometry fraction" in msg
    assert "max_invalid_fraction" in msg
    assert "non_line_geometry" in msg


def test_gate_not_fired_below_threshold(fixtures) -> None:
    reaches, diag = load_reaches(str(fixtures["primary"]), str(fixtures["secondary"]))
    assert diag["gate"]["observed_invalid_fraction"] == 0.0
    assert len(reaches) == 5


def test_resolve_config_aggregates_all_problems(tmp_path) -> None:
    cfg = {
        "drainage": {
            "grid": {"width": 10, "height": 10},
            "inputs": {
                "kml_primary": "missing_primary.kml",
            },
        }
    }
    p = tmp_path / "bad.yaml"
    p.write_text(yaml.safe_dump(cfg))
    with pytest.raises(ValueError) as excinfo:
        resolve_config(str(p))
    msg = str(excinfo.value)
    assert "9 problem(s)" in msg
    for fragment in (
        "drainage.crs",
        "drainage.grid.transform",
        "drainage.inputs.kml_secondary",
        "drainage.inputs.kml_tertiary_report_only",
        "drainage.inputs.elevation_surface",
        "drainage.inputs.elevation_sha256",
        "drainage.diagnostics.max_invalid_fraction",
        "drainage.outputs.run_dir",
        "does not exist: missing_primary.kml",
    ):
        assert fragment in msg, f"aggregated error must name {fragment}"


def test_resolve_config_ok_on_repo_config() -> None:
    cfg = resolve_config(str(REPO / "configs" / "drainage.yaml"))
    assert cfg["crs"] == "EPSG:32643"
    assert cfg["diagnostics"]["max_invalid_fraction"] == 0.01


# CLI: run --json + rule-6 manifest lifecycle


def _tmp_cli_config(fixtures, tmp_path: Path) -> Path:
    base = yaml.safe_load((REPO / "configs" / "drainage.yaml").read_text())
    base["drainage"]["inputs"]["kml_primary"] = str(fixtures["primary"])
    base["drainage"]["inputs"]["kml_secondary"] = str(fixtures["secondary"])
    base["drainage"]["inputs"]["kml_tertiary_report_only"] = str(fixtures["tertiary"])
    base["drainage"]["outputs"]["run_dir"] = str(tmp_path / "run")
    p = tmp_path / "cli_drainage.yaml"
    p.write_text(yaml.safe_dump(base))
    return p


def test_cli_run_json_and_manifest_lifecycle(fixtures, tmp_path) -> None:
    cfg_path = _tmp_cli_config(fixtures, tmp_path)
    runner = CliRunner()
    result = runner.invoke(app, ["run", "--config", str(cfg_path), "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["census"]["total"] == 5
    assert payload["census"]["primary"] == 3
    assert payload["census"]["secondary"] == 2
    assert payload["diagnostics"]["tertiary"]["tertiary_parsed_count"] == 6

    manifest_path = Path(payload["manifest_path"])
    assert manifest_path.exists()
    manifest = json.loads(manifest_path.read_text())
    assert manifest["status"] == "completed"
    assert manifest["started_utc"] <= manifest["finished_utc"]
    assert manifest["git_sha"] != ""
    assert manifest["stage"] == "wf1_load"


def test_cli_gate_refusal_exits_nonzero(fixtures, tmp_path) -> None:
    bad = tmp_path / "primary_bad.kml"
    _write(bad, PRIMARY_PMS[:-1] + [_pm(9, "<Point><coordinates>77.5,13.0</coordinates></Point>")])
    cfg_path = _tmp_cli_config(fixtures, tmp_path)
    base = yaml.safe_load(cfg_path.read_text())
    base["drainage"]["inputs"]["kml_primary"] = str(bad)
    cfg_path.write_text(yaml.safe_dump(base))

    runner = CliRunner()
    result = runner.invoke(app, ["run", "--config", str(cfg_path)])
    assert result.exit_code == 1
    assert "[loader-refused]" in result.stderr or "[loader-refused]" in result.stdout

    manifest_dir = tmp_path / "run"
    manifest = json.loads((manifest_dir / "loader_manifest.json").read_text())
    assert manifest["status"] == "refused"
    assert "invalid-geometry fraction" in manifest["error"]
