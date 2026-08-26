#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
python_bin="${PYTHON_BIN:-${repo_root}/.venv/bin/python}"
export CUDA_VISIBLE_DEVICES=""
export NVIDIA_VISIBLE_DEVICES=""
export JALADHAR_CPU_ONLY=1
export JALADHAR_DEVICE=cpu
export TORCH_DEVICE=cpu
exec "${python_bin}" "${repo_root}/scripts/demo/launch_demo.py" "$@"
