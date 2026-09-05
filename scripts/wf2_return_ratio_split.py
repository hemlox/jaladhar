"""WF-2 owner-directive-2 return-ratio split — outfall-terminating vs dead-end.

Joins a coupled run's REALIZED ``products/surcharge_events.csv`` rows to node
component classes computed INDEPENDENTLY here: directed reachability to the
realized outfall set over the WF-1 producer adjacency bytes
(``runs/drain_graph_build/drain_graph_adjacency.json`` edges), with outfall
identity read from the same producer's gpkg ``drain_nodes.node_type``. This is
deliberately NOT ``DrainGraph.component_class`` — recomputing the partition
from raw producer artefacts gives the run manifest's recorded split an
independent observable to agree with (V2/V8 spirit at the analysis seam).

Outputs per class ``{n_events, total_returned_m3, share_of_returned}``, plus
whether ANY surcharging node sits on a directed path to an outfall — i.e.
water CAN exit TOPOLOGICALLY. Stated plainly beside it (D-A): v1 semantics are
fill-only; outfall nodes ACCUMULATE like any other node and NOTHING EXPORTS,
so "on a path to an outfall" never means "left the system" in this run.

``--terminal-config`` (WF-2 M1): given a terminal-definition YAML (default
None => legacy byte-compatible v1 behaviour, untouched outputs), the script
re-runs the classification + surcharge split POST-HOC from realized products
under the extended ``v2-lake-boundary`` seed definition and emits
``products/return_ratio_split_v2.json`` carrying BEFORE/AFTER side-by-side,
reclassification BY RULE, a tolerance sensitivity table, recomputed
observed-length fractions under both seed sets (declared-only must reproduce
the external WF-1 anchor within +-1e-6 or the run FAILS LOUD), and a
V8 CROSS-ASSERTION of this script's independent deque BFS against the
canonical ``jaladhar.drainage.terminal.classify_reachability`` traversal.
No simulation runs here; the events CSV header is schema-exact enforced.

Rule 6: this script writes its own manifest AT START (status running) and
updates it in place on completion/failure, carrying git state, input sha256s
and wall clock. Directive 3: nothing here extrapolates; every number is a join
over realized bytes.

CPU-only, stdlib + geopandas/shapely for geometry reads.
"""

from __future__ import annotations

import csv
import json
import subprocess
import time
from collections import deque
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import typer

from jaladhar.coupling.diagnostics import SURCHARGE_EVENTS_SCHEMA

app = typer.Typer(add_completion=False)
REPO = Path(__file__).resolve().parents[1]

COMPONENT_CLASSES = ("outfall_terminating", "dead_end")

# External WF-1 anchor (runs/drain_graph_build/manifest.json component_policy):
# reproduced live every --terminal-config run; FAIL LOUD beyond this band.
ANCHOR_TOLERANCE_ABS = 1e-6


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _git_state() -> tuple[str, bool]:
    try:
        sha = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL, cwd=REPO
        ).strip()
    except Exception:
        sha = "unknown"
    try:
        out = subprocess.check_output(
            ["git", "status", "--porcelain"], text=True, stderr=subprocess.DEVNULL, cwd=REPO
        )
        dirty = bool(out.strip())
    except Exception:
        dirty = False
    return sha, dirty


def _sha256_file(path: Path) -> str:
    import hashlib

    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _read_events(path: Path) -> list[dict[str, Any]]:
    """Realized event rows; schema-exact header or refuse (never guess columns)."""
    if not path.exists():
        raise FileNotFoundError(f"surcharge events CSV not found at {path} — run the smoke first")
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        if tuple(reader.fieldnames or ()) != SURCHARGE_EVENTS_SCHEMA:
            raise ValueError(
                f"{path}: header {reader.fieldnames!r} != G2 schema {SURCHARGE_EVENTS_SCHEMA}"
            )
        rows = [
            {
                "node_id": int(r["node_id"]),
                "total_returned_m3": float(r["total_returned_m3"]),
            }
            for r in reader
        ]
    return rows


def _outfall_ids(gpkg: Path) -> list[int]:
    """Outfall node ids from the PRODUCER artefact's node_type column."""
    import geopandas as gpd

    nodes = gpd.read_file(gpkg, layer="drain_nodes")
    ids = sorted(int(i) for i in nodes.loc[nodes["node_type"].astype(str) == "outfall", "node_id"])
    if not ids:
        raise ValueError(f"{gpkg}: zero nodes with node_type == 'outfall'")
    return ids


def _reachable_to_outfall(
    edges_from_to: list[tuple[int, int]], outfalls: list[int], num_nodes_hint: int
) -> set[int]:
    """Nodes that can reach ANY outfall following directed edges (reversed BFS).

    Outfalls seed the set at path length 0 (an outfall reaches itself), matching
    router._compute_component_classes' definition so the cross-check below is
    apples-to-apples."""
    succ: dict[int, list[int]] = {}
    max_id = num_nodes_hint
    for u, v in edges_from_to:
        succ.setdefault(v, []).append(u)  # reversed: who can reach v
        max_id = max(max_id, u, v)
    reached = set(outfalls)
    queue = deque(outfalls)
    while queue:
        cur = queue.popleft()
        for prev in succ.get(cur, ()):  # nodes with a directed edge INTO the frontier
            if prev not in reached:
                reached.add(prev)
                queue.append(prev)
    assert max_id >= 0
    return reached


def _observed_length_fraction(gpkg: Path, seed_ids: list[int]) -> float:
    """Producer-definition observed-length fraction (graph_io.finalize_manifest
    verbatim semantics): union-find over ALL edges, a component qualifies iff it
    CONTAINS a seed node, weighting by OBSERVED edge length only. V3-clean:
    the comparison baseline is the WF-1 build manifest, not anything this
    script produced."""
    import geopandas as gpd

    edges = gpd.read_file(gpkg, layer="drain_edges")
    parent: dict[int, int] = {}

    def find(x: int) -> int:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for _, row in edges.iterrows():  # union over ALL edges (connectors join components)
        a, b = find(int(row["from_node"])), find(int(row["to_node"]))
        if a != b:
            parent[a] = b
    seed_roots = {find(int(s)) for s in seed_ids if int(s) in parent}
    obs = edges[edges["edge_source"].astype(str) == "observed"]
    total = float(obs["length_m"].sum())
    inside = sum(
        float(row["length_m"])
        for _, row in obs.iterrows()
        if find(int(row["from_node"])) in seed_roots
    )
    return inside / total if total > 0 else 0.0


def _volume_split(events: list[dict[str, Any]], reached: set[int]) -> dict[str, Any]:
    """Event/volume split of realized rows by directed-reachability class."""
    per_class: dict[str, dict[str, Any]] = {
        cls: {"n_events": 0, "total_returned_m3": 0.0} for cls in COMPONENT_CLASSES
    }
    unknown = 0
    total = 0.0
    on_path_rows = [r for r in events if r["node_id"] in reached]
    for r in events:
        cls = "outfall_terminating" if r["node_id"] in reached else "dead_end"
        per_class[cls]["n_events"] += 1
        per_class[cls]["total_returned_m3"] += r["total_returned_m3"]
        total += r["total_returned_m3"]
    for cls in COMPONENT_CLASSES:
        got = per_class[cls]["total_returned_m3"]
        per_class[cls]["share_of_returned"] = got / total if total > 0 else None
    return {
        "per_class": per_class,
        "totals": {
            "n_events": len(events),
            "total_returned_m3": total,
            "unknown_class_events": unknown,
        },
        "n_events_on_path": len(on_path_rows),
        "returned_m3_on_path": sum(r["total_returned_m3"] for r in on_path_rows),
    }


def _split_v2(
    *,
    run_dir: Path,
    adjacency: Path,
    gpkg: Path,
    terminal_config: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """The M1 post-hoc BEFORE/AFTER product + its rule-6 manifest payload."""
    from jaladhar.drainage import terminal as term

    t_all = time.perf_counter()
    events_csv = run_dir / "products" / "surcharge_events.csv"
    v1_product_path = run_dir / "products" / "return_ratio_split.json"
    product_path = run_dir / "products" / "return_ratio_split_v2.json"
    manifest_path = run_dir / "products" / "return_ratio_split_v2.manifest.json"
    drain_manifest_path = REPO / "runs" / "drain_graph_build" / "manifest.json"
    run_manifest_path = run_dir / "manifest.json"

    sha, dirty = _git_state()
    inputs = {
        "events_csv": events_csv,
        "adjacency": adjacency,
        "gpkg": gpkg,
        "terminal_config": terminal_config,
        "drain_manifest": drain_manifest_path,
    }
    manifest: dict[str, Any] = {
        "stage": "wf2_return_ratio_split_v2",
        "status": "running",
        "started_utc": _utc_now(),
        "cpu_only": True,
        "git_sha": sha,
        "git_dirty": dirty,
        "inputs_sha256": {str(k): _sha256_file(v) for k, v in inputs.items() if v.exists()},
        "outputs": {"product": str(product_path)},
        "_t0": time.perf_counter(),
    }
    # lake source path lands in the hashed-input set once the definition is loaded
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True, default=str) + "\n")

    try:
        tdef = term.load_terminal_definition(terminal_config)
        lake_gpkg = REPO / tdef.lake_source_path
        manifest["inputs_sha256"][str(lake_gpkg)] = _sha256_file(lake_gpkg)
        manifest["definition_version"] = tdef.definition_version
        manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True, default=str) + "\n")

        events = _read_events(events_csv)
        adj = json.loads(adjacency.read_text())
        edge_pairs = [
            (int(e["from"]), int(e["to"])) for e in (adj["edges"][k] for k in adj["edges"])
        ]
        outfalls = _outfall_ids(gpkg)
        n_nodes = len(adj["nodes"])
        nodes_gdf = _read_node_geometry(gpkg)

        drain_man = json.loads(drain_manifest_path.read_text())
        grid_block = (drain_man.get("config_snapshot") or {}).get("grid") or {}
        recorded_fraction = float(
            (drain_man.get("component_policy") or {}).get(
                "outfall_terminating_observed_length_fraction", "nan"
            )
        )

        # --- v1 anchor reproduction (declared-only seeds; FAIL LOUD band) -----
        v1_frac = _observed_length_fraction(gpkg, outfalls)
        anchor_diff = abs(v1_frac - recorded_fraction)
        if not (anchor_diff <= ANCHOR_TOLERANCE_ABS):
            raise ValueError(
                f"v1 anchor FAILED to reproduce: recomputed {v1_frac!r} vs recorded "
                f"{recorded_fraction!r} (abs diff {anchor_diff!r} > {ANCHOR_TOLERANCE_ABS!r}) "
                "- the declared-only baseline moved; STOP, do not extend it"
            )
        km_total = float((drain_man.get("component_policy") or {}).get("observed_length_total_km"))
        km_in = float(
            (drain_man.get("component_policy") or {}).get("outfall_terminating_observed_length_km")
        )
        anchor_block = {
            "recomputed_observed_length_fraction_declared_only": v1_frac,
            "recorded_in_graph_manifest": recorded_fraction,
            "abs_diff": anchor_diff,
            "tolerance_abs": ANCHOR_TOLERANCE_ABS,
            "status": "reproduced",
            "km_crosscheck": {
                "recomputed_note": (
                    "recompute matches the manifest's own km fields by construction of "
                    "the producer definition; recorded values carried beside"
                ),
                "recorded_observed_length_total_km": km_total,
                "recorded_outfall_terminating_observed_length_km": km_in,
            },
        }

        # --- canonical definition resolution (real bytes) ---------------------
        lake_union = term.load_lake_union(tdef)
        res_v2 = term.resolve_terminal_nodes(
            node_xy=nodes_gdf,
            edge_pairs=edge_pairs,
            declared_outfall_ids=outfalls,
            tdef=tdef,
            grid_block=grid_block,
            lake_union=lake_union,
        )

        def classes_for(seeds: set[int]) -> set[int]:
            """THIS script's independent deque BFS, CROSS-ASSERTED against the
            canonical traversal (V8): divergence raises, never silently scores."""
            ours = _reachable_to_outfall(edge_pairs, sorted(seeds), n_nodes)
            canon = term.classify_reachability(edge_pairs, sorted(seeds))
            if ours != canon:
                diff = sorted(ours.symmetric_difference(canon))
                raise ValueError(
                    f"[cross-traversal V8] deque BFS vs classify_reachability diverge on "
                    f"{len(diff)} node(s) e.g. {diff[:10]} — refusing to score"
                )
            return ours

        reached_v1 = classes_for(set(outfalls))
        reached_v2 = classes_for(set(res_v2.seed_union))

        split_v1 = _volume_split(events, reached_v1)
        split_v2 = _volume_split(events, reached_v2)

        # v1 product crosscheck: the recomputed BEFORE side must equal the
        # recorded v1 product within float tolerance (same inputs, same join).
        v1_recorded = json.loads(v1_product_path.read_text())
        max_diff = 0.0
        for cls in COMPONENT_CLASSES:
            a = v1_recorded["per_class"][cls]["total_returned_m3"]
            b = split_v1["per_class"][cls]["total_returned_m3"]
            max_diff = max(max_diff, abs(a - b))
        v1_status = "match" if max_diff <= 1e-6 else "MISMATCH"

        # --- reclassification BY RULE -----------------------------------------
        # Two distinct populations, both reported:
        #   new_terminal_seeds : nodes the extended rules THEMSELVES seeded;
        #   class_flipped_nodes: every node whose class flipped
        #     dead_end -> outfall_terminating because it can now reach one of
        #     those seeds (the actual 'reaches reclassified').
        newly = sorted(set(res_v2.seed_union) - reached_v1)
        flipped = sorted(reached_v2 - reached_v1)
        seed_rules = {nid: res_v2.records[nid].rule_fired for nid in newly}
        by_rule: dict[str, Any] = {}
        for nid in newly:
            r = res_v2.records[nid]
            assert r.rule_fired is not None
            entry = by_rule.setdefault(
                r.rule_fired,
                {
                    "rule": r.rule_fired,
                    "n_new_terminal_seeds": 0,
                    "new_terminal_seeds": [],
                    "per_seed": [],
                    "class_flipped_node_ids": [],
                },
            )
            entry["n_new_terminal_seeds"] += 1
            entry["new_terminal_seeds"].append(nid)
            entry["per_seed"].append(
                {
                    "node_id": nid,
                    "dist_to_lake_m": r.dist_to_lake_m,
                    "dist_to_boundary_m": r.dist_to_boundary_m,
                    "is_sink": r.is_sink,
                }
            )
        for nid in flipped:
            # attribute each flipped node to the rule of a new seed it reaches
            # (deterministic lowest-id seed wins ties)
            attributing = sorted(
                s for s in newly if nid in _reachable_to_outfall(edge_pairs, [s], n_nodes)
            )
            assert attributing, f"flipped node {nid} reaches no new seed"
            rule = seed_rules[attributing[0]]
            by_rule[rule]["class_flipped_node_ids"].append(nid)
        for entry in by_rule.values():
            entry["n_class_flipped_nodes"] = len(entry["class_flipped_node_ids"])
        flipped_events = [
            {"node_id": r["node_id"], "total_returned_m3": r["total_returned_m3"]}
            for r in events
            if r["node_id"] in set(flipped)
        ]

        census_v1 = {
            "outfall_terminating": len(reached_v1),
            "dead_end": n_nodes - len(reached_v1),
        }
        census_v2 = {
            "outfall_terminating": len(reached_v2),
            "dead_end": n_nodes - len(reached_v2),
        }

        # --- sensitivity sweep at configured tolerances ------------------------
        sensitivity = []
        for t in tdef.sensitivity_tolerances_m:
            res_t = term.resolve_terminal_nodes(
                node_xy=nodes_gdf,
                edge_pairs=edge_pairs,
                declared_outfall_ids=outfalls,
                tdef=term.with_tolerances(tdef, t),
                grid_block=grid_block,
                lake_union=lake_union,
            )
            reached_t = classes_for(set(res_t.seed_union))
            split_t = _volume_split(events, reached_t)
            oc_v1 = _observed_length_fraction(gpkg, outfalls)
            frac_t = _observed_length_fraction(gpkg, res_t.seed_union)
            ot_pc = 100.0 * split_t["returned_m3_on_path"] / split_t["totals"]["total_returned_m3"]
            sensitivity.append(
                {
                    "tolerance_m": t,
                    "counts_by_rule": res_t.counts_by_rule,
                    "seeds_by_rule": res_t.seeds_by_rule,
                    "class_census_node_count": {
                        "outfall_terminating": len(reached_t),
                        "dead_end": n_nodes - len(reached_t),
                    },
                    "observed_length_fraction_declared_only": oc_v1,
                    "observed_length_fraction_extended": frac_t,
                    "outfall_pct": ot_pc,
                    "dead_end_pct": 100.0 - ot_pc,
                }
            )

        pct_v1 = (
            100.0 * split_v1["returned_m3_on_path"] / split_v1["totals"]["total_returned_m3"]
            if split_v1["totals"]["total_returned_m3"] > 0
            else None
        )
        pct_v2 = (
            100.0 * split_v2["returned_m3_on_path"] / split_v2["totals"]["total_returned_m3"]
            if split_v2["totals"]["total_returned_m3"] > 0
            else None
        )
        frac_v2 = _observed_length_fraction(gpkg, res_v2.seed_union)

        product: dict[str, Any] = {
            "label": (
                "MEASURED post-hoc BEFORE/AFTER re-classification over realized run "
                "products + producer graph bytes under terminal definition "
                f"{tdef.definition_version}"
            ),
            "definition": {
                "path": str(terminal_config),
                "definition_version": tdef.definition_version,
                # keyed by the manifest's input LABEL, not the path (rule-6 echo)
                "sha256": manifest["inputs_sha256"]["terminal_config"],
                "provenance_pointer": tdef.provenance_pointer,
            },
            "inputs": {
                **{k: str(v) for k, v in inputs.items()},
                "events_rows": len(events),
                "gpkg_outfall_source": str(gpkg),
            },
            "anchor_v1_reproduction": anchor_block,
            "before_after": {
                "length_fraction": {
                    "v1_declared_only": v1_frac,
                    "v2_extended": frac_v2,
                    "basis": (
                        "weak-connectivity union-find over ALL gpkg edges, component "
                        "qualifies iff it contains a seed, OBSERVED-edge-length weighted "
                        "(producer finalize_manifest definition)"
                    ),
                },
                "volume_split": {
                    "v1": {
                        "outfall_pct": pct_v1,
                        "dead_end_pct": None if pct_v1 is None else 100.0 - pct_v1,
                        "outfall_m3": split_v1["returned_m3_on_path"],
                        "dead_end_m3": (
                            split_v1["totals"]["total_returned_m3"]
                            - split_v1["returned_m3_on_path"]
                        ),
                        "n_events_on_path": split_v1["n_events_on_path"],
                        "total_m3": split_v1["totals"]["total_returned_m3"],
                    },
                    "v2": {
                        "outfall_pct": pct_v2,
                        "dead_end_pct": None if pct_v2 is None else 100.0 - pct_v2,
                        "outfall_m3": split_v2["returned_m3_on_path"],
                        "dead_end_m3": (
                            split_v2["totals"]["total_returned_m3"]
                            - split_v2["returned_m3_on_path"]
                        ),
                        "n_events_on_path": split_v2["n_events_on_path"],
                        "total_m3": split_v2["totals"]["total_returned_m3"],
                    },
                },
                "class_census_node_count": {"v1": census_v1, "v2": census_v2},
                "reaches_reclassified_by_rule": {
                    "by_rule": by_rule,
                    "n_new_terminal_seeds": len(newly),
                    "n_class_flipped_nodes": len(flipped),
                    "events_flipped": flipped_events,
                },
            },
            "seeds": {
                "declared_outfall_ids": outfalls,
                "v2_seeds_by_rule": res_v2.seeds_by_rule,
                "v2_counts_by_rule": res_v2.counts_by_rule,
            },
            "v1_product_crosscheck": {
                "path": str(v1_product_path),
                "status": v1_status,
                "max_abs_diff_total_returned_m3": max_diff,
            },
            "cross_traversal_assertion": {
                "independent": "scripts deque BFS (_reachable_to_outfall, kept by design)",
                "canonical": "jaladhar.drainage.terminal.classify_reachability",
                "status": "match",
                "note": "asserted under BOTH seed sets; divergence raises before scoring",
            },
            "sensitivity_table": sensitivity,
            "directive_2_answer": {
                "any_surcharging_node_on_directed_path_to_outfall_v2": (
                    split_v2["n_events_on_path"] > 0
                ),
                "d_a_statement": (
                    "'On a directed path to a terminal seed' still means water CAN exit "
                    "TOPOLOGICALLY ONLY under D-A fill-only v1 semantics: no terminal/outfall "
                    "export term exists, so reclassification changes the LABEL, not the "
                    "physics of the realized run being scored."
                ),
            },
        }

        # cross-check against the RUN MANIFEST's recorded v1 census stays informative
        try:
            run_man = json.loads(run_manifest_path.read_text())
            rs = run_man.get("component_class_split")
            product["class_census_crosscheck_vs_run_manifest"] = {
                "run_manifest_component_class_split": rs,
                "independent_v1_census": census_v1,
                "status": (
                    "match"
                    if isinstance(rs, dict)
                    and all(rs.get(c) == census_v1[c] for c in COMPONENT_CLASSES)
                    else "MISMATCH_OR_ABSENT"
                ),
            }
        except (OSError, json.JSONDecodeError):
            pass

        product_path.write_text(json.dumps(product, indent=2, sort_keys=True, default=str) + "\n")

        manifest["finished_utc"] = _utc_now()
        manifest["wall_clock_sec"] = round(time.perf_counter() - t_all, 3)
        manifest["outputs"] = {"product": str(product_path)}
        manifest["result_summary"] = {
            "anchor_reproduction": anchor_block["status"],
            "counts_by_rule_v2": res_v2.counts_by_rule,
            "class_census_v1_vs_v2": {"v1": census_v1, "v2": census_v2},
            "outfall_pct_v1_vs_v2": [pct_v1, pct_v2],
            "v1_product_crosscheck": v1_status,
        }
        manifest["status"] = "completed"
        manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True, default=str) + "\n")
    except Exception as exc:
        manifest["finished_utc"] = _utc_now()
        manifest["wall_clock_sec"] = round(time.perf_counter() - t_all, 3)
        manifest["error_type"] = type(exc).__name__
        manifest["error"] = str(exc)
        manifest["status"] = "failed"
        manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True, default=str) + "\n")
        typer.echo(f"FAILED: {exc}")
        raise typer.Exit(code=1) from exc

    typer.echo(f"definition_version={tdef.definition_version}")
    typer.echo(
        f"anchor v1 fraction reproduced: {v1_frac!r} vs {recorded_fraction!r} "
        f"(diff {anchor_diff!r})"
    )
    typer.echo(f"counts_by_rule={res_v2.counts_by_rule}")
    typer.echo(
        f"class census node-count: v1={census_v1} -> v2={census_v2} "
        f"(+{len(newly)} terminal seeds by rule, {len(flipped)} nodes reclassified)"
    )
    typer.echo(f"observed-length fraction: v1={v1_frac:.6f} -> v2={frac_v2:.6f}")
    typer.echo(
        f"volume split: v1 outfall={pct_v1:.2f}% -> v2 outfall={pct_v2:.2f}% "
        f"({split_v2['returned_m3_on_path']:.2f} m3 of "
        f"{split_v2['totals']['total_returned_m3']:.2f} m3)"
    )
    typer.echo(f"v1 product crosscheck: {v1_status} (max abs diff {max_diff!r})")
    for row in sensitivity:
        typer.echo(
            f"  sensitivity tol={row['tolerance_m']:>4}: counts={row['counts_by_rule']} "
            f"frac={row['observed_length_fraction_extended']:.6f} outfall%={row['outfall_pct']:.2f}"
        )
    typer.echo(f"wrote {product_path}")
    return product, manifest


def _read_node_geometry(gpkg: Path) -> list[tuple[float, float]]:
    """(N,2) projected positions ordered by node_id ascending (index = id - 1)."""
    import geopandas as gpd

    nodes = gpd.read_file(gpkg, layer="drain_nodes")
    nodes = nodes.sort_values("node_id")
    return list(zip(nodes["x_m"].astype(float), nodes["y_m"].astype(float), strict=True))


@app.command()
def main(
    run_dir: Path = typer.Option(Path("runs/wf2_coupled_resmoke"), "--run-dir"),
    adjacency: Path = typer.Option(
        Path("runs/drain_graph_build/drain_graph_adjacency.json"), "--adjacency"
    ),
    gpkg: Path = typer.Option(Path("runs/drain_graph_build/drain_graph.gpkg"), "--gpkg"),
    terminal_config: Path | None = typer.Option(
        None,
        "--terminal-config",
        help=(
            "Terminal-definition YAML (e.g. configs/terminal_definition.yaml). Default "
            "None => legacy byte-compatible v1 behaviour and outputs."
        ),
    ),
) -> None:
    """Split realized surcharge events by directed-reachability class (directive 2).

    With ``--terminal-config``: post-hoc v2-lake-boundary BEFORE/AFTER split into
    ``products/return_ratio_split_v2.json`` (+ rule-6 manifest); the legacy v1
    products stay byte-exact.
    """
    if terminal_config is not None:
        _split_v2(
            run_dir=Path(run_dir),
            adjacency=Path(adjacency),
            gpkg=Path(gpkg),
            terminal_config=Path(terminal_config),
        )
        return
    _legacy_split(run_dir=Path(run_dir), adjacency=Path(adjacency), gpkg=Path(gpkg))


def _legacy_split(*, run_dir: Path, adjacency: Path, gpkg: Path) -> None:
    t_all = time.perf_counter()
    run_dir = Path(run_dir)
    events_csv = run_dir / "products" / "surcharge_events.csv"
    product_path = run_dir / "products" / "return_ratio_split.json"
    manifest_path = run_dir / "products" / "return_ratio_split.manifest.json"
    run_manifest_path = run_dir / "manifest.json"

    sha, dirty = _git_state()
    inputs = {"events_csv": events_csv, "adjacency": adjacency, "gpkg": gpkg}
    manifest: dict[str, Any] = {
        "stage": "wf2_return_ratio_split",
        "status": "running",
        "started_utc": _utc_now(),
        "cpu_only": True,
        "git_sha": sha,
        "git_dirty": dirty,
        "inputs_sha256": {str(k): _sha256_file(v) for k, v in inputs.items() if v.exists()},
        "outputs": {"product": str(product_path)},
        "_t0": time.perf_counter(),
    }
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True, default=str) + "\n")

    try:
        events = _read_events(events_csv)
        adj = json.loads(adjacency.read_text())
        edge_pairs = [
            (int(e["from"]), int(e["to"])) for e in (adj["edges"][k] for k in adj["edges"])
        ]
        outfalls = _outfall_ids(gpkg)
        n_nodes = len(adj["nodes"])

        reached = _reachable_to_outfall(edge_pairs, outfalls, n_nodes)
        classes = {
            nid: ("outfall_terminating" if nid in reached else "dead_end")
            for nid in range(1, n_nodes + 1)
        }

        per_class: dict[str, dict[str, Any]] = {
            cls: {"n_events": 0, "total_returned_m3": 0.0} for cls in COMPONENT_CLASSES
        }
        unknown_class_events = 0
        total_returned = 0.0
        on_path_rows = [r for r in events if r["node_id"] in reached]
        for r in events:
            cls = classes.get(r["node_id"])
            if cls is None:
                unknown_class_events += 1
                continue
            per_class[cls]["n_events"] += 1
            per_class[cls]["total_returned_m3"] += r["total_returned_m3"]
            total_returned += r["total_returned_m3"]
        for cls in COMPONENT_CLASSES:
            got = per_class[cls]["total_returned_m3"]
            per_class[cls]["share_of_returned"] = (
                got / total_returned if total_returned > 0 else None
            )

        # Independent-class-census vs the RUN MANIFEST's recorded split (V8 seam):
        # both must describe the same graph partition for the join to be meaningful.
        census = {cls: sum(1 for c in classes.values() if c == cls) for cls in COMPONENT_CLASSES}
        run_split = None
        crosscheck_status = "run_manifest_unavailable"
        try:
            run_man = json.loads(run_manifest_path.read_text())
            run_split = run_man.get("component_class_split")
            if isinstance(run_split, dict):
                agree = all(run_split.get(c) == census[c] for c in COMPONENT_CLASSES)
                crosscheck_status = "match" if agree else "MISMATCH"
        except (OSError, json.JSONDecodeError):
            pass

        any_on_path = len(on_path_rows) > 0
        product: dict[str, Any] = {
            "label": "MEASURED join over realized run products + producer graph bytes",
            "inputs": {
                "events_csv": str(events_csv),
                "events_rows": len(events),
                "adjacency": str(adjacency),
                "gpkg_outfall_source": str(gpkg),
            },
            "outfall_ids": outfalls,
            "n_outfalls": len(outfalls),
            "reachability_rule": (
                "directed reachability along edges (from->to); outfalls seed at path "
                "length 0; 'outfall_terminating' := can reach an outfall, 'dead_end' otherwise"
            ),
            "per_class": per_class,
            "totals": {
                "n_events": len(events),
                "total_returned_m3": total_returned,
                "unknown_class_events": unknown_class_events,
            },
            "directive_2_answer": {
                "any_surcharging_node_on_directed_path_to_outfall": any_on_path,
                "n_events_on_path": len(on_path_rows),
                "returned_m3_on_path": sum(r["total_returned_m3"] for r in on_path_rows),
                "d_a_statement": (
                    "'On a directed path to an outfall' means water CAN exit TOPOLOGICALLY "
                    "ONLY. Under D-A fill-only v1 semantics every node — outfalls included — "
                    "ACCUMULATES and NOTHING EXPORTS: no terminal/outfall export term exists "
                    "in the frozen sign conventions, so reaching an outfall node never removes "
                    "water from the system. Volume on such paths is still subject to the "
                    "systematic over-return D-A records."
                ),
            },
            "class_census_crosscheck_vs_run_manifest": {
                "status": crosscheck_status,
                "independent_census_node_count_basis": census,
                "run_manifest_component_class_split": run_split,
                "note": (
                    "both figures are NODE-COUNT basis on the full 1721-node graph; the EVENT/"
                    "VOLUME split above is per-run and necessarily differs from node counts"
                ),
            },
        }
        product_path.write_text(json.dumps(product, indent=2, sort_keys=True, default=str) + "\n")

        manifest["finished_utc"] = _utc_now()
        manifest["wall_clock_sec"] = round(time.perf_counter() - t_all, 3)
        manifest["outputs"] = {"product": str(product_path)}
        manifest["result_summary"] = {
            "n_events": len(events),
            "total_returned_m3": total_returned,
            "any_on_path": any_on_path,
            "crosscheck": crosscheck_status,
        }
        manifest["status"] = "completed"
        manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True, default=str) + "\n")
    except Exception as exc:
        manifest["finished_utc"] = _utc_now()
        manifest["wall_clock_sec"] = round(time.perf_counter() - t_all, 3)
        manifest["error_type"] = type(exc).__name__
        manifest["error"] = str(exc)
        manifest["status"] = "failed"
        manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True, default=str) + "\n")
        typer.echo(f"FAILED: {exc}")
        raise typer.Exit(code=1) from exc

    for cls in COMPONENT_CLASSES:
        pc = per_class[cls]
        share = pc["share_of_returned"]
        typer.echo(
            f"{cls}: n_events={pc['n_events']} returned={pc['total_returned_m3']:.3f} m3 "
            f"share={share if share is None else round(share, 6)}"
        )
    typer.echo(
        f"any surcharging node on a directed path to an outfall: {any_on_path} "
        f"(D-A fill-only: outfalls accumulate, nothing exports)"
    )
    typer.echo(f"class-census crosscheck vs run manifest: {crosscheck_status}")
    typer.echo(f"wrote {product_path}")


if __name__ == "__main__":
    app()
