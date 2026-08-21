"""Unit checks for Sentinel-1 acquisition; all S3 state is mocked.

Independent observable (V2): if pagination or object classification is broken,
the returned keys will omit a required VV input or include VH/unrelated SAFE
metadata.  Deliberate mutation evidence: replacing ``IsTruncated`` with False
in the first mocked page makes ``test_paginated_required_object_selection`` red.

Scope (V7): two synthetic S3 pages, four required VV object classes, excluded
VH/quick-look/manifest objects, exact-size cache skip, and atomic ``.part``
promotion under pytest ``tmp_path``.  This does not validate live CDSE contents.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jaladhar.validation import fetch_s1

PREFIX = "Sentinel-1/example.SAFE/"


def _obj(rel: str, size: int = 4) -> dict[str, object]:
    return {"Key": PREFIX + rel, "Size": size}


class PagedS3:
    def __init__(self) -> None:
        self.list_calls: list[dict[str, object]] = []
        self.download_calls: list[tuple[str, str, str]] = []

    def list_objects_v2(self, **kwargs):
        self.list_calls.append(kwargs)
        if "ContinuationToken" not in kwargs:
            return {
                "IsTruncated": True,
                "NextContinuationToken": "page-2",
                "Contents": [
                    _obj("measurement/scene-vv-test.tiff"),
                    _obj("measurement/scene-vh-test.tiff"),
                    _obj("quick-look.png"),
                ],
            }
        assert kwargs["ContinuationToken"] == "page-2"
        return {
            "IsTruncated": False,
            "Contents": [
                _obj("annotation/scene-vv-test.xml"),
                _obj("annotation/calibration/calibration-scene-vv-test.xml"),
                _obj("annotation/calibration/noise-scene-vv-test.xml"),
                _obj("manifest.safe"),
            ],
        }

    def download_file(self, bucket: str, key: str, target: str) -> None:
        self.download_calls.append((bucket, key, target))
        Path(target).write_bytes(b"data")


def test_paginated_required_object_selection() -> None:
    s3 = PagedS3()

    selected = fetch_s1.list_required_objects(s3, PREFIX)

    assert [obj["Key"][len(PREFIX) :] for obj in selected] == [
        "measurement/scene-vv-test.tiff",
        "annotation/scene-vv-test.xml",
        "annotation/calibration/calibration-scene-vv-test.xml",
        "annotation/calibration/noise-scene-vv-test.xml",
    ]
    assert len(s3.list_calls) == 2
    assert s3.list_calls[1]["ContinuationToken"] == "page-2"


def test_download_skips_exact_size_and_atomically_promotes_parts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    s3 = PagedS3()
    monkeypatch.setattr(fetch_s1, "client", lambda: s3)
    monkeypatch.setattr(fetch_s1, "SCENES", {"scene": PREFIX})
    existing = tmp_path / "scene/measurement/scene-vv-test.tiff"
    existing.parent.mkdir(parents=True)
    existing.write_bytes(b"data")

    fetch_s1.main(out=tmp_path)

    downloaded = {key[len(PREFIX) :] for _, key, _ in s3.download_calls}
    assert "measurement/scene-vv-test.tiff" not in downloaded
    assert downloaded == {
        "annotation/scene-vv-test.xml",
        "annotation/calibration/calibration-scene-vv-test.xml",
        "annotation/calibration/noise-scene-vv-test.xml",
    }
    assert not list(tmp_path.rglob("*.part"))
    for rel in downloaded:
        assert (tmp_path / "scene" / rel).read_bytes() == b"data"


def test_missing_required_kind_is_rejected() -> None:
    s3 = PagedS3()
    original = s3.list_objects_v2

    def listing_without_noise(**kwargs):
        response = original(**kwargs)
        response["Contents"] = [
            obj for obj in response["Contents"] if "noise-scene-vv" not in obj["Key"]
        ]
        return response

    s3.list_objects_v2 = listing_without_noise

    with pytest.raises(RuntimeError, match="noise.*0"):
        fetch_s1.list_required_objects(s3, PREFIX)


def test_download_rejects_size_mismatch_before_promotion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    s3 = PagedS3()
    monkeypatch.setattr(fetch_s1, "client", lambda: s3)
    monkeypatch.setattr(fetch_s1, "SCENES", {"scene": PREFIX})

    def short_download(_bucket: str, _key: str, target: str) -> None:
        Path(target).write_bytes(b"x")

    s3.download_file = short_download
    with pytest.raises(RuntimeError, match="object size mismatch"):
        fetch_s1.main(out=tmp_path)
    assert not list(tmp_path.rglob("*.tiff"))


def test_verified_target_removes_stale_randomized_partial(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    s3 = PagedS3()
    monkeypatch.setattr(fetch_s1, "client", lambda: s3)
    monkeypatch.setattr(fetch_s1, "SCENES", {"scene": PREFIX})
    target = tmp_path / "scene/measurement/scene-vv-test.tiff"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"data")
    stale = target.with_name(target.name + ".part.random-id")
    stale.write_bytes(b"interrupted")

    fetch_s1.main(out=tmp_path)

    assert not stale.exists()
    assert target.read_bytes() == b"data"
