from __future__ import annotations

import copy
import importlib.util
import json
import sys
import tempfile
from dataclasses import replace
from pathlib import Path
from typing import Any

import torch

from jaladhar.coupling.config import resolve_config
from jaladhar.coupling.router import build_drain_graph
from jaladhar.solver.state import StaticFields, load_solver_config


def static_fields(
    shape: tuple[int, int],
    *,
    drain_cap_m_s: float = 3.2e-6,
    roughness: float = 0.03,
    boundary_roughness: float = 1e-4,
    zeroed: bool = False,
) -> StaticFields:
    hh, ww = shape

    def z(*sizes):
        return torch.zeros(*sizes, dtype=torch.float32)

    def fill(*sizes, value=0.0):
        return torch.full((*sizes,), value, dtype=torch.float32)

    cap = 0.0 if zeroed else drain_cap_m_s
    n = 0.0 if zeroed else roughness
    edge_s = 0.0 if zeroed else boundary_roughness
    return StaticFields(
        dz_x=z(hh, ww - 1),
        dz_y=z(hh - 1, ww),
        n_x=fill(hh, ww - 1, value=n),
        n_y=fill(hh - 1, ww, value=n),
        c_x=torch.ones(hh, ww - 1, dtype=torch.float32) if not zeroed else z(hh, ww - 1),
        c_y=torch.ones(hh - 1, ww, dtype=torch.float32) if not zeroed else z(hh - 1, ww),
        drain_cap_m_s=fill(hh, ww, value=cap),
        infil_rate_m_s=z(hh, ww),
        edge_w_n=fill(hh, value=n),
        edge_e_n=fill(hh, value=n),
        edge_n_n=fill(ww, value=n),
        edge_s_n=fill(ww, value=n),
        edge_w_s=fill(hh, value=edge_s),
        edge_e_s=fill(hh, value=edge_s),
        edge_n_s=fill(ww, value=edge_s),
        edge_s_s=fill(ww, value=edge_s),
        edge_open=(0.0, 0.0, 0.0, 0.0),
        shape=shape,
    )


def chain_graph(
    rows: int = 64,
    cols: int = 64,
    n_nodes: int = 8,
    cap: float = 0.05,
    *,
    widths: list[float] | tuple[float, ...] | None = None,
    outfall: bool = True,
    manifest: dict[str, Any] | None = None,
    row_divisor: int = 8,
    elevation_top: float = 10.0,
) -> Any:
    base_r, base_c = max(1, rows // row_divisor), max(1, cols // row_divisor)
    cells = [(base_r + i, base_c) for i in range(n_nodes)]
    assert cells[-1][0] < rows and cells[-1][1] < cols
    nmap = torch.full((rows, cols), -1, dtype=torch.int32)
    for i, (r, c) in enumerate(cells):
        nmap[r, c] = i + 1
    edges = [(i, i + 1, cap) for i in range(1, n_nodes)]
    outfall_mask = torch.zeros(n_nodes, dtype=torch.bool)
    if outfall:
        outfall_mask[-1] = True
    return build_drain_graph(
        edge_from=torch.tensor([e[0] - 1 for e in edges], dtype=torch.int64),
        edge_to=torch.tensor([e[1] - 1 for e in edges], dtype=torch.int64),
        capacity_bearing=torch.ones(len(edges), dtype=torch.bool),
        q_cap_nom_m3s=torch.tensor([float(e[2]) for e in edges], dtype=torch.float64),
        width_mean_m=torch.tensor(widths or [6.71] * n_nodes, dtype=torch.float64),
        shaft_length_proxy_m=1.0,
        node_elev_m=torch.linspace(elevation_top, 0.0, n_nodes, dtype=torch.float64),
        contrib_area_m2=torch.zeros(n_nodes, dtype=torch.float64),
        outfall_node=outfall_mask,
        node_cell_row=torch.tensor([c[0] for c in cells], dtype=torch.int32),
        node_cell_col=torch.tensor([c[1] for c in cells], dtype=torch.int32),
        node_id_map=nmap,
        node_cell_count=torch.ones(n_nodes, dtype=torch.int64),
        manifest=manifest,
    )


def coupling_config(
    repo: Path, tmp_path: Path, *, subdir: str = "run", falsifier_set: Path | None = None
):
    base = resolve_config(repo / "configs" / "coupling.yaml", repo)
    run_dir = tmp_path / subdir
    outputs = replace(
        base.outputs,
        run_dir=run_dir,
        manifest=run_dir / "manifest.json",
        surcharge_events_csv=run_dir / "products" / "surcharge_events.csv",
        event_continuity_csv=run_dir / "products" / "event_continuity.csv",
        depth_series_dir=run_dir / "depth",
    )
    result = replace(base, outputs=outputs)
    if falsifier_set is not None:
        result = replace(
            result, diagnostics=replace(result.diagnostics, falsifier_set=falsifier_set)
        )
    return result


def hermetic_config(repo: Path, tmp_path: Path, *, subdir: str = "run"):
    fset = tmp_path / f"falsifier_{subdir}.json"
    fset.write_text(
        json.dumps(
            {"n_predicted_edges": 1, "predicted_node_ids": [1], "source_gpkg_sha256": "0" * 64}
        )
    )
    return coupling_config(repo, tmp_path, subdir=subdir, falsifier_set=fset)


def closed_solver_config(repo: Path) -> dict[str, Any]:
    result = copy.deepcopy(load_solver_config(repo / "configs" / "solver.yaml", repo))
    result["boundaries"]["mode"] = "closed"
    return result


def load_mutated_source(
    source_path: Path,
    replacements: list[tuple[str, str]],
    *,
    tag: str,
    prefix: str,
    directory: Path = Path("/tmp/opencode"),
) -> tuple[Any, Path]:
    """Apply exact textual mutations to a temp copy and import that copy standalone."""
    source = source_path.read_text()
    mutated = source
    for old, new in replacements:
        count = mutated.count(old)
        if count != 1:
            raise AssertionError(
                f"[{prefix} red demo] mutation anchor occurs {count}x (need exactly 1)"
            )
        mutated = mutated.replace(old, new)
    if mutated == source:
        raise AssertionError(f"[{prefix} red demo] replacements produced no textual change")
    directory.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix=f"{prefix}_{tag}_", dir=directory))
    path = work / f"{source_path.stem}_mutated.py"
    path.write_text(mutated)
    name = f"_{prefix}_{tag}_mutated"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise AssertionError(f"[{prefix} red demo] could not spec-load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    return module, path
