from datetime import date

from jaladhar.forcing import fetch_imerg


class _Response:
    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        data_url = "https://example.invalid/granule.HDF5"
        return {
            "feed": {
                "entry": [
                    {"links": [{"href": data_url}]},
                    {"links": [{"href": data_url}]},
                    {"links": [{"href": "https://example.invalid/readme.txt"}]},
                ]
            }
        }


def test_cmr_listing_deduplicates_download_targets(monkeypatch) -> None:
    """V5 red mutation: returning ``urls`` directly produces two entries.
    Scope: one mocked CMR page; live collection completeness is not claimed."""
    monkeypatch.setattr(fetch_imerg.requests, "get", lambda *args, **kwargs: _Response())

    urls = fetch_imerg.granule_urls(date(2022, 9, 5), date(2022, 9, 5), "test-token")

    assert urls == ["https://example.invalid/granule.HDF5"]
