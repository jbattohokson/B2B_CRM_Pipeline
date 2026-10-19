"""Tests for pipelines/load_to_duckdb.py: typed load, cleaning, validation, and atomic rollback."""

import shutil
from pathlib import Path

import duckdb
import pandas as pd
import pytest

from pipelines.generate_data import ALL_STAGES
from pipelines.load_to_duckdb import (
    CONTRACTS,
    RAW_SCHEMA,
    VALID_STAGES,
    LoaderError,
    SchemaError,
    ValidationError,
    load,
    main,
)


@pytest.fixture
def work_dir(raw_dir, tmp_path) -> Path:
    """A private, editable copy of the generated CSVs for one test."""
    dest = tmp_path / "raw"
    shutil.copytree(raw_dir, dest)
    return dest


@pytest.fixture
def db_path(tmp_path) -> Path:
    return tmp_path / "test.duckdb"


def _edit_csv(path: Path, edit) -> None:
    """Read a CSV as all-text, apply edit(df) in place, and write it back."""
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    edit(df)
    df.to_csv(path, index=False, lineterminator="\n")


def _set(column: str, rows: slice, value: str):
    """Edit function for _edit_csv that overwrites column values on the given row slice."""

    def edit(df):
        df.loc[rows, column] = value

    return edit


def _query(db_path: Path, sql: str):
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        return con.execute(sql).fetchall()
    finally:
        con.close()


# --- Happy path ------------------------------------------------------------


def test_loads_every_table_with_matching_row_counts(work_dir, db_path, tables):
    counts = load(work_dir, db_path)
    csv_for_table = {c.table: c.csv_name.removesuffix(".csv") for c in CONTRACTS}
    assert counts == {t: len(tables[stem]) for t, stem in csv_for_table.items()}


def test_columns_get_contract_types(work_dir, db_path):
    load(work_dir, db_path)
    rows = _query(
        db_path,
        f"SELECT table_name, column_name, data_type FROM information_schema.columns "
        f"WHERE table_schema = '{RAW_SCHEMA}'",
    )
    actual = {(t, c): dtype for t, c, dtype in rows}
    for contract in CONTRACTS:
        for col, dtype in contract.columns.items():
            assert actual[(contract.table, col)] == dtype, (contract.table, col)


def test_reload_is_idempotent(work_dir, db_path):
    first = load(work_dir, db_path)
    second = load(work_dir, db_path)
    assert first == second
    assert _query(db_path, f"SELECT COUNT(*) FROM {RAW_SCHEMA}.opportunities")[0][0] == first["opportunities"]


def test_loader_and_generator_agree_on_stages():
    assert set(VALID_STAGES) == set(ALL_STAGES)


# --- Cleaning --------------------------------------------------------------


def test_whitespace_is_trimmed_so_keys_still_join(work_dir, db_path):
    def pad(df):
        df["account_id"] = "  " + df["account_id"] + " "
        df["stage"] = df["stage"] + "  "

    _edit_csv(work_dir / "fct_opportunities.csv", pad)
    load(work_dir, db_path)  # would fail FK + stage checks if not trimmed
    padded = _query(
        db_path,
        f"SELECT COUNT(*) FROM {RAW_SCHEMA}.opportunities WHERE account_id <> TRIM(account_id) OR stage <> TRIM(stage)",
    )
    assert padded[0][0] == 0


def test_blank_exited_date_becomes_null(work_dir, db_path, tables):
    load(work_dir, db_path)
    nulls = _query(db_path, f"SELECT COUNT(*) FROM {RAW_SCHEMA}.stage_history WHERE exited_date IS NULL")[0][0]
    # Exactly one open (current) step per opportunity.
    assert nulls == len(tables["fct_opportunities"])


# --- Validation failures roll back ----------------------------------------


def test_orphan_foreign_key_fails(work_dir, db_path):
    _edit_csv(work_dir / "fct_opportunities.csv", _set("rep_id", slice(0, 4), "REP-999"))
    with pytest.raises(ValidationError, match=r"rep_id has values not found.*\(5 rows\)"):
        load(work_dir, db_path)


def test_null_foreign_key_fails(work_dir, db_path):
    _edit_csv(work_dir / "fct_activities.csv", _set("opportunity_id", slice(0, 1), "  "))
    with pytest.raises(ValidationError, match=r"activities\.opportunity_id has NULLs \(2 rows\)"):
        load(work_dir, db_path)


def test_duplicate_primary_key_fails(work_dir, db_path):
    def dup(df):
        df.loc[1, "account_id"] = df.loc[0, "account_id"]

    _edit_csv(work_dir / "dim_accounts.csv", dup)
    with pytest.raises(ValidationError, match="duplicate primary key"):
        load(work_dir, db_path)


def test_unknown_stage_fails(work_dir, db_path):
    def bad_stage(df):
        # Change both tables so only the accepted-values rule trips, not the consistency check.
        df.loc[df["stage"] == "Closed-Won", "stage"] = "Won"

    _edit_csv(work_dir / "fct_opportunities.csv", bad_stage)
    _edit_csv(work_dir / "fct_stage_history.csv", bad_stage)
    with pytest.raises(ValidationError, match=r"opportunities\.stage outside"):
        load(work_dir, db_path)


def test_stage_disagreeing_with_history_fails(work_dir, db_path):
    def flip(df):
        idx = df.index[df["stage"] == "Closed-Lost"][0]
        df.loc[idx, ["stage", "probability"]] = ["Closed-Won", "1.0"]

    _edit_csv(work_dir / "fct_opportunities.csv", flip)
    with pytest.raises(ValidationError, match="disagrees with the latest stage_history"):
        load(work_dir, db_path)


def test_close_before_created_fails(work_dir, db_path):
    _edit_csv(work_dir / "fct_opportunities.csv", _set("close_date", slice(0, 0), "2000-01-01"))
    with pytest.raises(ValidationError, match="close_date before created_date"):
        load(work_dir, db_path)


def test_failed_load_leaves_previous_data_intact(work_dir, db_path):
    good = load(work_dir, db_path)
    _edit_csv(work_dir / "fct_opportunities.csv", _set("account_id", slice(0, 0), "ACC-NOPE"))
    with pytest.raises(ValidationError):
        load(work_dir, db_path)
    # Rolled back: the earlier good tables are still there with the same counts.
    for table, n in good.items():
        assert _query(db_path, f"SELECT COUNT(*) FROM {RAW_SCHEMA}.{table}")[0][0] == n
    assert _query(
        db_path, f"SELECT COUNT(*) FROM {RAW_SCHEMA}.opportunities WHERE account_id = 'ACC-NOPE'"
    )[0][0] == 0


def test_uncastable_value_raises_loader_error(work_dir, db_path):
    _edit_csv(work_dir / "fct_opportunities.csv", _set("amount", slice(0, 0), "lots"))
    with pytest.raises(LoaderError, match="Failed to load fct_opportunities.csv"):
        load(work_dir, db_path)


# --- Schema / file problems -----------------------------------------------


def test_missing_csv_raises_schema_error(work_dir, db_path):
    (work_dir / "dim_reps.csv").unlink()
    with pytest.raises(SchemaError, match="generate_data.py"):
        load(work_dir, db_path)
    assert not db_path.exists()  # header checks run before the database is touched


def test_renamed_column_raises_schema_error(work_dir, db_path):
    _edit_csv(work_dir / "dim_reps.csv", lambda df: df.rename(columns={"annual_quota": "quota"}, inplace=True))
    with pytest.raises(SchemaError, match=r"missing \['annual_quota'\]; unexpected \['quota'\]"):
        load(work_dir, db_path)


def test_locked_database_raises_loader_error(work_dir, db_path):
    load(work_dir, db_path)
    # A read-only handle on the same file stands in for another process holding it open.
    # In-process, DuckDB refuses the conflicting read-write connection the loader asks for.
    holder = duckdb.connect(str(db_path), read_only=True)
    try:
        with pytest.raises(LoaderError, match="Could not open"):
            load(work_dir, db_path)
    finally:
        holder.close()


# --- CLI -------------------------------------------------------------------


def test_cli_success_and_failure_exit_codes(work_dir, db_path, capsys):
    assert main(["--raw-dir", str(work_dir), "--db", str(db_path)]) == 0
    assert "all checks passed" in capsys.readouterr().out

    (work_dir / "dim_date.csv").unlink()
    assert main(["--raw-dir", str(work_dir), "--db", str(db_path)]) == 1
    assert "database unchanged" in capsys.readouterr().out
