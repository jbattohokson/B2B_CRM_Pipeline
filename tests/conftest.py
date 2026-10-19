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
