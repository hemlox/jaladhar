"""Tests for the path-with-spaces Whitebox installation repair utility."""

from __future__ import annotations

import importlib.util
import stat
from pathlib import Path


def _load_module():
    script = Path(__file__).resolve().parents[1] / "scripts" / "repair_whitebox_permissions.py"
    spec = importlib.util.spec_from_file_location("repair_whitebox_permissions", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_repair_permissions_marks_binaries_not_metadata_executable(tmp_path: Path) -> None:
    module = _load_module()
    package = tmp_path / "Local Disk" / "whitebox"
    root_binary = package / "whitebox_tools"
    nested_binary = package / "WBT" / "whitebox_tools"
    plugin = package / "plugins" / "rho8_flow_accumulation"
    metadata = package / "plugins" / "rho8_flow_accumulation.json"
    for path in (root_binary, nested_binary, plugin, metadata):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)

    changed = module.repair_permissions(package)

    assert set(changed) == {root_binary, nested_binary, plugin}
    for binary in (root_binary, nested_binary, plugin):
        assert binary.stat().st_mode & stat.S_IXUSR
    assert not metadata.stat().st_mode & stat.S_IXUSR
