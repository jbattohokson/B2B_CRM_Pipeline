"""Shared fixtures: the real Olist funnel CSVs, loaded and modeled into a temp warehouse.

The full dataset is small (875 KB), so tests run on all of it rather than a
sample. That also lets tests pin real headline numbers.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
REAL_RAW_DIR = PROJECT_ROOT / "data" / "raw"


@pytest.fixture(scope="session")
def raw_dir(tmp_path_factory):
    """A session copy of the committed real CSVs (read-only: copy again before editing)."""
    out = tmp_path_factory.mktemp("raw")
    for csv in REAL_RAW_DIR.glob("*.csv"):
        shutil.copy(csv, out / csv.name)
    return out


@pytest.fixture(scope="session")
def marts_db(raw_dir, tmp_path_factory):
    """A full warehouse (raw + staging + marts) built by the real loader and `dbt build`.

    dbt build also runs every dbt test, so a failing reconciliation check fails the fixture.
    """
    from pipelines.load_to_duckdb import load

    work = tmp_path_factory.mktemp("warehouse")
    db_path = work / "test_crm.duckdb"
    load(raw_dir, db_path)

    dbt = Path(sys.executable).parent / "dbt"
    env = {**os.environ, "DBT_DB_PATH": str(db_path), "DBT_PROFILES_DIR": str(PROJECT_ROOT)}
    proc = subprocess.run(
        [str(dbt), "build", "--quiet", "--target-path", str(work / "target"), "--log-path", str(work / "logs")],
        cwd=PROJECT_ROOT, env=env, capture_output=True, text=True, timeout=300,
    )
    assert proc.returncode == 0, f"dbt build failed:\n{proc.stdout}\n{proc.stderr}"
    return db_path
