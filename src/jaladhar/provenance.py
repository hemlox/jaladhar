"""Shared lifecycle contract for provenance-bearing runs."""

from __future__ import annotations

import copy
import json
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class DirtyTreeError(RuntimeError):
    """Raised before a provenance-bearing run when its source tree is dirty."""


def _git(repo_root: Path, *args: str) -> str:
    try:
        return subprocess.check_output(
            ["git", *args], cwd=repo_root, text=True, stderr=subprocess.PIPE
        ).rstrip("\r\n")
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RuntimeError(f"cannot establish Git provenance for {repo_root}: {exc}") from exc


def require_clean_git(repo_root: Path) -> str:
    """Return the real HEAD commit, refusing absent or dirty repositories."""
    sha = _git(repo_root, "rev-parse", "--verify", "HEAD^{commit}")
    dirty = _git(repo_root, "status", "--porcelain", "--untracked-files=all")
    if dirty:
        paths = [line[3:] for line in dirty.splitlines()]
        shown = ", ".join(paths[:8])
        suffix = " ..." if len(paths) > 8 else ""
        raise DirtyTreeError(f"refusing provenance-bearing run from dirty tree: {shown}{suffix}")
    return sha


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, delete=False
    ) as handle:
        json.dump(payload, handle, indent=2, default=str)
        handle.write("\n")
        tmp_path = Path(handle.name)
    tmp_path.replace(path)


# Public for reports which are part of a provenance-bearing run.
write_json_atomic = _write_json_atomic


class RunManifest:
    """Write one manifest at start and update that same file to a terminal state."""

    def __init__(
        self,
        path: Path,
        *,
        stage: str,
        repo_root: Path,
        resolved_config: dict[str, Any],
        config_paths: dict[str, str] | None = None,
        compute_budget: dict[str, Any] | None = None,
        fields: dict[str, Any] | None = None,
    ) -> None:
        self.path = path
        self.stage = stage
        self.repo_root = repo_root
        self.resolved_config = copy.deepcopy(resolved_config)
        self.config_paths = copy.deepcopy(config_paths or {})
        self.compute_budget = copy.deepcopy(compute_budget)
        self.fields = copy.deepcopy(fields or {})
        self._manifest: dict[str, Any] | None = None

    def _assert_owned_state(self) -> None:
        if self._manifest is None:
            raise RuntimeError("manifest must be started before it can be updated")
        try:
            realized = json.loads(self.path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError("run manifest was removed or corrupted by another writer") from exc
        if realized != self._manifest:
            raise RuntimeError("run manifest was overwritten by another writer")

    def start(self) -> dict[str, Any]:
        if self.path.exists():
            raise FileExistsError(
                f"refusing to replace existing run manifest {self.path}; use a fresh run directory"
            )
        sha = require_clean_git(self.repo_root)
        payload: dict[str, Any] = {
            "stage": self.stage,
            "status": "running",
            "git_sha": sha,
            "start_time_iso": _utc_now(),
            "resolved_config": self.resolved_config,
            "config_paths": self.config_paths,
            **self.fields,
        }
        if self.compute_budget is not None:
            payload["compute_budget"] = self.compute_budget
        _write_json_atomic(self.path, payload)
        self._manifest = payload
        return copy.deepcopy(payload)

    def _finish(
        self,
        status: str,
        fields: dict[str, Any] | None = None,
        *,
        verify_owner: bool = True,
    ) -> dict[str, Any]:
        if verify_owner:
            self._assert_owned_state()
        elif self._manifest is None:
            raise RuntimeError("manifest must be started before it can be finished")
        assert self._manifest is not None
        if self._manifest.get("status") != "running":
            raise RuntimeError("only a running manifest can transition to a terminal status")
        payload = {
            **self._manifest,
            **copy.deepcopy(fields or {}),
            "status": status,
            "end_time_iso": _utc_now(),
        }
        _write_json_atomic(self.path, payload)
        self._manifest = payload
        return copy.deepcopy(payload)

    def update_running(self, fields: dict[str, Any]) -> dict[str, Any]:
        """Add realized pre-launch fields while preserving the running lifecycle."""
        self._assert_owned_state()
        assert self._manifest is not None
        if self._manifest["status"] != "running":
            raise RuntimeError("only a running manifest can be updated")
        payload = {**self._manifest, **copy.deepcopy(fields), "status": "running"}
        _write_json_atomic(self.path, payload)
        self._manifest = payload
        return copy.deepcopy(payload)

    def complete(self, fields: dict[str, Any] | None = None) -> dict[str, Any]:
        return self._finish("completed", fields)

    def fail(self, error: BaseException, fields: dict[str, Any] | None = None) -> dict[str, Any]:
        failure_fields = {
            **(fields or {}),
            "error_type": type(error).__name__,
            "error": str(error),
        }
        try:
            return self._finish("failed", failure_fields)
        except RuntimeError as ownership_error:
            # Preserve the foreign/corrupt bytes as the primary evidence.  A
            # separate atomic sidecar records why this owner could not finish.
            sidecar = self.path.with_name(f"{self.path.stem}.failure.json")
            _write_json_atomic(
                sidecar,
                {
                    "stage": self.stage,
                    "status": "manifest_ownership_lost",
                    "failure_time_iso": _utc_now(),
                    **failure_fields,
                    "ownership_error": str(ownership_error),
                    "manifest_path": str(self.path),
                },
            )
            raise RuntimeError(
                f"run failed, and its manifest ownership was lost; preserved the "
                f"manifest and wrote {sidecar}"
            ) from ownership_error
