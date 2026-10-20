"""Download the Olist Marketing Funnel CSVs from Kaggle and verify them.

The CSVs are committed in data/raw/, so a fresh clone doesn't need this. Run it
to re-download them (for example after deleting the folder), or with --verify
to check the committed files match the published dataset byte for byte.

Kaggle serves this public dataset without a login through its download
endpoint. If that ever changes, download the zip from
https://www.kaggle.com/datasets/olistbr/marketing-funnel-olist by hand and
unzip it into data/raw/.

Usage:
    .venv/bin/python pipelines/fetch_data.py            # download only missing files
    .venv/bin/python pipelines/fetch_data.py --force    # re-download everything
    .venv/bin/python pipelines/fetch_data.py --verify   # check hashes, no network
"""

from __future__ import annotations

import argparse
import hashlib
import io
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_RAW_DIR = PROJECT_ROOT / "data" / "raw"
DOWNLOAD_URL = "https://www.kaggle.com/api/v1/datasets/download/olistbr/marketing-funnel-olist"

# Published file -> SHA-256 of the exact bytes Kaggle serves.
EXPECTED_SHA256 = {
    "olist_marketing_qualified_leads_dataset.csv": "5537753b7549ce4068e52e2402c29f2773c5375fc0575520ae8f3f301f666573",
    "olist_closed_deals_dataset.csv": "1433f402d8fff00ca167579de641d9525f6e846368958c166f699ef1b0c31f3d",
}


class FetchError(Exception):
    """Download or verification failed; the message says what to do."""


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify(raw_dir: Path = DEFAULT_RAW_DIR) -> list[str]:
    """Problems with the files in raw_dir (empty list = every file present and unmodified)."""
    problems = []
    for name, expected in EXPECTED_SHA256.items():
        path = raw_dir / name
        if not path.is_file():
            problems.append(f"{name} is missing")
        elif sha256(path) != expected:
            problems.append(f"{name} differs from the published dataset (hash mismatch)")
    return problems


def download_zip(url: str = DOWNLOAD_URL, timeout: int = 60) -> bytes:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return response.read()
    except urllib.error.URLError as exc:
        raise FetchError(
            f"Couldn't download {url}: {exc}. Download the dataset zip from Kaggle by hand "
            f"and unzip it into data/raw/."
        ) from exc


def extract(archive: bytes, raw_dir: Path, force: bool = False) -> list[str]:
    """Write the expected CSVs from the zip into raw_dir, checking each hash. Returns names written."""
    raw_dir.mkdir(parents=True, exist_ok=True)
    written = []
    with zipfile.ZipFile(io.BytesIO(archive)) as zf:
        available = set(zf.namelist())
        for name, expected in EXPECTED_SHA256.items():
            target = raw_dir / name
            if target.is_file() and not force:
                continue
            if name not in available:
                raise FetchError(f"{name} isn't in the downloaded archive (found: {sorted(available)}).")
            payload = zf.read(name)
            if hashlib.sha256(payload).hexdigest() != expected:
                raise FetchError(f"{name} in the download doesn't match the expected hash; not writing it.")
            target.write_bytes(payload)
            written.append(name)
    return written


def fetch(raw_dir: Path = DEFAULT_RAW_DIR, force: bool = False) -> list[str]:
    missing = [n for n in EXPECTED_SHA256 if force or not (raw_dir / n).is_file()]
    if not missing:
        return []
    return extract(download_zip(), raw_dir, force=force)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fetch and verify the Olist Marketing Funnel CSVs.")
    parser.add_argument("--raw-dir", type=Path, default=DEFAULT_RAW_DIR)
    parser.add_argument("--force", action="store_true", help="re-download even if the files exist")
    parser.add_argument("--verify", action="store_true", help="only check hashes of the existing files")
    args = parser.parse_args(argv)

    try:
        if not args.verify:
            written = fetch(args.raw_dir, force=args.force)
            print(f"Downloaded {', '.join(written)}" if written else "All files already present.")
    except FetchError as exc:
        print(f"Fetch failed: {exc}")
        return 1

    problems = verify(args.raw_dir)
    if problems:
        print("Verification failed:\n  - " + "\n  - ".join(problems))
        return 1
    print(f"Verified {len(EXPECTED_SHA256)} files against the published SHA-256 hashes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
