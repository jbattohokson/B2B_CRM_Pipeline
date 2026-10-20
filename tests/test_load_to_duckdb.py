"""Tests for pipelines/load_to_duckdb.py: typed load, cleaning, validation, and atomic rollback.

Every test runs on a private copy of the real Olist CSVs, edited where a test needs bad data.
"""

import shutil
from pathlib import Path

import duckdb
import pandas as pd
import pytest

from pipelines.load_to_duckdb import (
    CONTRACTS,
    RAW_SCHEMA,
    VALID_ORIGINS,
    LoaderError,
    SchemaError,
    ValidationError,
    load,
    main,
)

MQLS = "olist_marketing_qualified_leads_dataset.csv"
DEALS = "olist_closed_deals_dataset.csv"


@pytest.fixture
def work_dir(raw_dir, tmp_path) -> Path:
    """A private, editable copy of the real CSVs for one test."""
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


def _set(column: str, row: int, value: str):
    def edit(df):
        df.loc[row, column] = value

    return edit


def _query(db_path: Path, sql: str):
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        return con.execute(sql).fetchall()
    finally:
        con.close()


# --- Happy path ------------------------------------------------------------


def test_loads_the_real_dataset(work_dir, db_path):
    assert load(work_dir, db_path) == {"mqls": 8000, "closed_deals": 842}


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
    assert load(work_dir, db_path) == load(work_dir, db_path)


def test_every_real_origin_is_allowed(raw_dir):
    origins = set(pd.read_csv(raw_dir / MQLS)["origin"].dropna())
    assert origins == set(VALID_ORIGINS)


# --- Cleaning --------------------------------------------------------------


def test_whitespace_is_trimmed_so_keys_still_join(work_dir, db_path):
    def pad(df):
        df["mql_id"] = "  " + df["mql_id"] + " "

    _edit_csv(work_dir / DEALS, pad)
    load(work_dir, db_path)  # the foreign-key check would fail if the padding survived
    assert _query(db_path, f"SELECT COUNT(*) FROM {RAW_SCHEMA}.closed_deals WHERE mql_id <> TRIM(mql_id)")[0][0] == 0


def test_blank_origin_becomes_null(work_dir, db_path):
    load(work_dir, db_path)
    assert _query(db_path, f"SELECT COUNT(*) FROM {RAW_SCHEMA}.mqls WHERE origin IS NULL")[0][0] == 60


def test_true_false_text_loads_as_boolean(work_dir, db_path):
    load(work_dir, db_path)
    rows = _query(db_path, f"SELECT has_company, COUNT(*) FROM {RAW_SCHEMA}.closed_deals GROUP BY 1 ORDER BY 1")
    assert rows == [(False, 5), (True, 58), (None, 779)]


# --- Validation ------------------------------------------------------------


def test_deal_for_unknown_lead_fails(work_dir, db_path):
    _edit_csv(work_dir / DEALS, _set("mql_id", 0, "not-a-real-lead"))
    with pytest.raises(ValidationError, match="closed_deals.mql_id has values not found"):
        load(work_dir, db_path)


def test_duplicate_lead_fails(work_dir, db_path):
    _edit_csv(work_dir / MQLS, lambda df: df.loc.__setitem__((1, "mql_id"), df.loc[0, "mql_id"]))
    with pytest.raises(ValidationError, match="duplicate primary key"):
        load(work_dir, db_path)


def test_missing_required_field_fails(work_dir, db_path):
    _edit_csv(work_dir / DEALS, _set("sdr_id", 0, ""))
    with pytest.raises(ValidationError, match="closed_deals.sdr_id has NULLs"):
        load(work_dir, db_path)


def test_unknown_origin_fails(work_dir, db_path):
    _edit_csv(work_dir / MQLS, _set("origin", 0, "carrier_pigeon"))
    with pytest.raises(ValidationError, match="mqls.origin outside"):
        load(work_dir, db_path)


def test_duplicate_seller_fails(work_dir, db_path):
    _edit_csv(work_dir / DEALS, lambda df: df.loc.__setitem__((1, "seller_id"), df.loc[0, "seller_id"]))
    with pytest.raises(ValidationError, match="seller_id is not unique"):
        load(work_dir, db_path)


def test_negative_declared_revenue_fails(work_dir, db_path):
    _edit_csv(work_dir / DEALS, _set("declared_monthly_revenue", 0, "-5"))
    with pytest.raises(ValidationError, match="declared_monthly_revenue is negative"):
        load(work_dir, db_path)


def test_failed_load_leaves_previous_data_intact(work_dir, db_path):
    good = load(work_dir, db_path)
    _edit_csv(work_dir / DEALS, _set("mql_id", 0, "not-a-real-lead"))
    with pytest.raises(ValidationError):
        load(work_dir, db_path)
    for table, n in good.items():
        assert _query(db_path, f"SELECT COUNT(*) FROM {RAW_SCHEMA}.{table}")[0][0] == n
    assert _query(
        db_path, f"SELECT COUNT(*) FROM {RAW_SCHEMA}.closed_deals WHERE mql_id = 'not-a-real-lead'"
    )[0][0] == 0


def test_uncastable_value_raises_loader_error(work_dir, db_path):
    _edit_csv(work_dir / MQLS, _set("first_contact_date", 0, "yesterday"))
    with pytest.raises(LoaderError, match=f"Failed to load {MQLS}"):
        load(work_dir, db_path)


# --- Schema / file problems -----------------------------------------------


def test_missing_csv_raises_schema_error(work_dir, db_path):
    (work_dir / DEALS).unlink()
    with pytest.raises(SchemaError, match="fetch_data.py"):
        load(work_dir, db_path)
    assert not db_path.exists()  # header checks run before the database is touched


def test_renamed_column_raises_schema_error(work_dir, db_path):
    _edit_csv(work_dir / MQLS, lambda df: df.rename(columns={"origin": "source"}, inplace=True))
    with pytest.raises(SchemaError, match=r"missing \['origin'\]; unexpected \['source'\]"):
        load(work_dir, db_path)


def test_locked_database_raises_loader_error(work_dir, db_path):
    load(work_dir, db_path)
    # A read-only handle on the same file stands in for another process holding it open.
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

    (work_dir / MQLS).unlink()
    assert main(["--raw-dir", str(work_dir), "--db", str(db_path)]) == 1
    assert "database unchanged" in capsys.readouterr().out
