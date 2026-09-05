# JALADHAR

Urban flood nowcasting: a rainfall nowcast drives a GPU 2D shallow-water solver over a 10 m terrain
grid, coupled every timestep to a directed graph of the city's storm drains (capture → capacity-
limited routing → surcharge back onto the street). Output is a per-street flood status and depth
band with lead time, served through a live dashboard and a vehicle-aware routing API.

**Smart India Hackathon 2026 — problem statement 26085** (MoES / NCMRWF, Software, Disaster
Management). Development city: Bengaluru. Next city: open decision — see the handover.

## Start here

| | |
|---|---|
| [`docs/HANDOVER.md`](docs/HANDOVER.md) | **the only project document** — state, measured findings, what the demo shows vs what is real, city-switch plan, 2–3 month roadmap, open items |
| [`AGENTS.md`](AGENTS.md) | standing rules, binding on every agent and human |
| [`deploy.sh`](deploy.sh) | `./deploy.sh --check` verifies a checkout; `./deploy.sh` launches dashboard (8501) + routing API (8502) |
| `configs/` | every city-specific value lives here; `configs/contracts/` are the frozen machine-read interfaces |

## Status in one table (2026-09-05)

| | |
|---|---|
| Terrain conditioning, 2D solver, forcing adapters, validation harness | **built, measured** |
| Drain graph (1,721 nodes / 1,587 edges, cited CPHEEO capacities, observed vs synthesised tagged) | **built** |
| Surface ↔ drain coupling with surcharge return | **built, executed at tile scope; not used in the demo** — 96.87% of surcharge lands on drain reaches with no mapped outlet |
| 3 h forecast product, 8-frame series, sub-minute on a storm tile | **built** — driven by rainfall that actually fell (perfect nowcast), not an issued nowcast |
| Dashboard (LIVE / DEMO modes, watchlist, search, per-street detail) | **built** |
| Routing API (vehicle-aware, gated) | **built — refuses by design** while the accuracy gate fails |
| Live IMD nowcast ingestion | adapter written and contract-tested; **no endpoint configured, never fetched** |
| Street-level accuracy gate G1 | **FAIL** — 104/399 complaint points, lift 1.30 vs ≥60% / ≥3.0 |

## Quick start

```bash
./deploy.sh --check      # verifies every artifact the demo loads; starts nothing
./deploy.sh              # dashboard http://127.0.0.1:8501 · routing http://127.0.0.1:8502/health
CUDA_VISIBLE_DEVICES="" .venv/bin/python -m pytest -q -p no:cacheprovider
```

`data/` and `runs/` do not ship in git. A full checkout with the Bengaluru artifacts is
distributed as an archive (see the handover, "Restore").
