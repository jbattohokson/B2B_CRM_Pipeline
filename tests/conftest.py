"""Shared fixtures: one small generated dataset, reused by the generator and loader tests."""

from datetime import date

import pytest

from pipelines.generate_data import GeneratorConfig, generate, write_csvs

# Small enough to run in about a second, big enough to produce open, stalled, won and lost deals.
SMALL_CONFIG = GeneratorConfig(
    seed=123, n_accounts=150, n_reps=12, n_opportunities=1500,
    start_date=date(2025, 1, 1), as_of=date(2026, 6, 30),
)


@pytest.fixture(scope="session")
def small_config() -> GeneratorConfig:
    return SMALL_CONFIG


@pytest.fixture(scope="session")
def tables(small_config):
    """In-memory tables from one generation run (read-only: don't mutate in tests)."""
    return generate(small_config)


@pytest.fixture(scope="session")
def raw_dir(tables, tmp_path_factory):
    """The small dataset written to CSVs in a session temp dir."""
    out = tmp_path_factory.mktemp("raw")
    write_csvs(tables, out)
    return out


@pytest.fixture(scope="session")
def marts_db(raw_dir, small_config, tmp_path_factory):
    """A full warehouse (raw + staging + marts) built from the small dataset by the real loader and dbt.

    Runs `dbt build` (models and dbt tests) into a temp DuckDB file, with the
    as-of var matched to the small config. That also proves the models and dbt
    tests hold on a second, differently seeded dataset.
    """
    import os
    import subprocess
    import sys
    from pathlib import Path

    from pipelines.load_to_duckdb import load

    work = tmp_path_factory.mktemp("warehouse")
    db_path = work / "test_crm.duckdb"
    load(raw_dir, db_path)

    project_root = Path(__file__).resolve().parent.parent
    dbt = Path(sys.executable).parent / "dbt"
    env = {**os.environ, "DBT_DB_PATH": str(db_path), "DBT_PROFILES_DIR": str(project_root)}
    proc = subprocess.run(
        [
            str(dbt), "build", "--quiet",
            "--vars", f"{{as_of_date: '{small_config.as_of.isoformat()}'}}",
            "--target-path", str(work / "target"),
            "--log-path", str(work / "logs"),
        ],
        cwd=project_root, env=env, capture_output=True, text=True, timeout=300,
    )
    assert proc.returncode == 0, f"dbt build failed:\n{proc.stdout}\n{proc.stderr}"
    return db_path
