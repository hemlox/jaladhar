"""Boundary acquisition contract checks.

V2 observable: a missing boundary is fetched to the configured path, while a
different byte stream is rejected before grid construction. V5 red mutation:
removing the SHA comparison lets the second test accept changed bytes.
Scope: temporary byte fixtures; the realized 225-feature/area checks remain in
the terrain grid tests and the production loader.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from jaladhar.terrain import fetch


def _cfg(payload: bytes) -> dict:
    return {
        "boundary": {
            "path": "data/raw/boundary/test.kml",
            "source_url": "https://example.invalid/test.kml",
            "source_name": "test boundary",
            "licence": "Public Domain",
            "expected_sha256": hashlib.sha256(payload).hexdigest(),
            "expected_size_bytes": len(payload),
        }
    }


def test_missing_boundary_is_fetched_and_verified(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = b"verified-boundary"

    def fake_download(_url: str, dest: Path) -> tuple[str, int]:
        dest.write_bytes(payload)
        return "ok", len(payload)

    monkeypatch.setattr(fetch, "download_file", fake_download)
    result = fetch.ensure_boundary(_cfg(payload), tmp_path)

    assert result["sha256"] == hashlib.sha256(payload).hexdigest()
    assert (tmp_path / result["path"]).read_bytes() == payload


def test_changed_boundary_bytes_are_rejected(tmp_path: Path) -> None:
    expected = b"expected-boundary"
    path = tmp_path / "data/raw/boundary/test.kml"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"different-boundary")

    with pytest.raises(ValueError, match="byte contract mismatch"):
        fetch.ensure_boundary(_cfg(expected), tmp_path)
