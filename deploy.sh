#!/usr/bin/env bash
# JALADHAR — one-command deploy.
#
#   ./deploy.sh              start dashboard + routing API
#   ./deploy.sh --check      verify the restore is intact, start nothing
#
# Restores are expected at /home/darshil/Desktop/sih/clginternal (the venv has
# absolute paths baked in). If you unpack elsewhere, pass --rebuild-venv.

set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="${REPO}/.venv/bin/python"
DASH_PORT="${DASH_PORT:-8501}"
API_PORT="${API_PORT:-8502}"

export JALADHAR_CPU_ONLY=1 JALADHAR_DEVICE=cpu CUDA_VISIBLE_DEVICES="" NVIDIA_VISIBLE_DEVICES=""

say() { printf '  %s\n' "$*"; }

# ---------------------------------------------------------------- preflight
echo "JALADHAR deploy — checking restore integrity"

MISSING=0
need() { [ -e "${REPO}/$1" ] || { say "MISSING: $1"; MISSING=1; }; }

# demo product + everything the dashboard and routing API actually load
need "runs/wf8_3h_forecast_frames/manifest.json"
need "runs/wf3_replay2_uncoupled_baseline_frames_v2/manifest.json"
need "runs/wf3_uncoupled_3h_forecast_warm_product/products/segment_status.csv"
need "runs/wf3_replay2_gates_v7_excluded/g1_score.json"
need "runs/wf8_sim_vs_sim_3h_vs_replay/comparison.json"
need "data/interim/terrain/roads_centrelines.gpkg"
need "data/interim/terrain/roads_segment_lookup.csv"
need "data/interim/context/segment_ward_2022.csv.gz"
need "data/curation/vehicle_wading_policy.json"
need "data/processed/buffered/elevation.tif"

if [ "$MISSING" -ne 0 ]; then
  echo
  echo "Restore is incomplete — the paths above are required. Re-extract the archive."
  exit 1
fi
say "all required artifacts present"

# ------------------------------------------------------------------- venv
if [ "${1:-}" = "--rebuild-venv" ] || ! "$PY" -c "import torch, rasterio, geopandas" 2>/dev/null; then
  echo
  echo "Rebuilding virtualenv (needs network, ~5-10 min)"
  command -v uv >/dev/null 2>&1 || { echo "  uv not found: pip install uv"; exit 1; }
  ( cd "$REPO" && uv venv --python 3.11 .venv && uv pip install -e . )
fi
say "venv OK ($("$PY" -c 'import torch;print("torch "+torch.__version__)' 2>/dev/null || echo 'unknown'))"

[ "${1:-}" = "--check" ] && { echo; echo "Check passed. Nothing started."; exit 0; }

# ------------------------------------------------------------------ launch
for p in "$DASH_PORT" "$API_PORT"; do
  pid=$(ss -ltnp 2>/dev/null | grep ":${p} " | grep -oP 'pid=\K[0-9]+' | head -1 || true)
  [ -n "${pid:-}" ] && { kill "$pid" 2>/dev/null || true; say "freed port ${p}"; }
done
sleep 1

mkdir -p "${REPO}/logs"
echo
echo "Starting services"

nohup "$PY" -m jaladhar.web.app serve \
  --host 127.0.0.1 --port "$DASH_PORT" \
  > "${REPO}/logs/deploy_dashboard.log" 2>&1 &

nohup "$PY" -m jaladhar.routing.api serve \
  --depth-product-file runs/wf3_uncoupled_3h_forecast_warm_product/products/segment_status.csv \
  --gate-report-file runs/wf3_replay2_gates_v7_excluded/g1_score.json \
  --vehicle-policy-file data/curation/vehicle_wading_policy.json \
  --server-max-snap-distance-m 25.0 \
  --host 127.0.0.1 --port "$API_PORT" \
  > "${REPO}/logs/deploy_routing.log" 2>&1 &

for i in $(seq 1 24); do
  code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 4 "http://127.0.0.1:${DASH_PORT}/" 2>/dev/null || true)
  [ "$code" = "200" ] && break
  sleep 5
done

echo
if [ "${code:-}" = "200" ]; then
  echo "  ✓ Dashboard   http://127.0.0.1:${DASH_PORT}"
  echo "  ✓ Routing API http://127.0.0.1:${API_PORT}/health"
  echo
  echo "  Frames warm over ~40s after start — open at index 0 and scrub forward."
  echo "  Logs: logs/deploy_dashboard.log, logs/deploy_routing.log"
else
  echo "  Dashboard did not come up. See logs/deploy_dashboard.log"
  exit 1
fi
