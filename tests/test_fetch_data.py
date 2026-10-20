"""Tests for pipelines/fetch_data.py. No network: downloads are replaced with an in-memory zip."""

import io
import zipfile

import pytest

from pipelines import fetch_data
from pipelines.fetch_data import EXPECTED_SHA256, FetchError, extract, fetch, verify
from tests.conftest import REAL_RAW_DIR


def _zip(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, payload in files.items():
            zf.writestr(name, payload)
    return buf.getvalue()


@pytest.fixture
def real_zip() -> bytes:
    return _zip({name: (REAL_RAW_DIR / name).read_bytes() for name in EXPECTED_SHA256})


def test_committed_files_match_published_hashes():
    assert verify(REAL_RAW_DIR) == []


def test_verify_reports_missing_and_modified(tmp_path):
    (tmp_path / "olist_closed_deals_dataset.csv").write_text("tampered")
    problems = verify(tmp_path)
    assert any("missing" in p for p in problems)
    assert any("hash mismatch" in p for p in problems)


def test_extract_writes_verified_files(tmp_path, real_zip):
    written = extract(real_zip, tmp_path)
    assert sorted(written) == sorted(EXPECTED_SHA256)
    assert verify(tmp_path) == []


def test_extract_refuses_tampered_download(tmp_path):
    bad = _zip({name: b"not the real data" for name in EXPECTED_SHA256})
    with pytest.raises(FetchError, match="hash"):
        extract(bad, tmp_path)
    assert not any(tmp_path.iterdir())


def test_extract_explains_missing_member(tmp_path):
    with pytest.raises(FetchError, match="isn't in the downloaded archive"):
        extract(_zip({"other.csv": b"x"}), tmp_path)


def test_fetch_skips_download_when_files_present(tmp_path, monkeypatch, real_zip):
    extract(real_zip, tmp_path)
    monkeypatch.setattr(fetch_data, "download_zip", lambda *a, **k: pytest.fail("should not download"))
    assert fetch(tmp_path) == []


def test_fetch_downloads_missing_files(tmp_path, monkeypatch, real_zip):
    monkeypatch.setattr(fetch_data, "download_zip", lambda *a, **k: real_zip)
    assert sorted(fetch(tmp_path)) == sorted(EXPECTED_SHA256)


def test_main_verify_exit_codes(tmp_path):
    assert fetch_data.main(["--verify", "--raw-dir", str(REAL_RAW_DIR)]) == 0
    assert fetch_data.main(["--verify", "--raw-dir", str(tmp_path)]) == 1
