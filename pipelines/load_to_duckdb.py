"""Load the raw CRM CSVs into DuckDB as typed, validated tables in the `raw` schema.

    data/raw/dim_accounts.csv       -> raw.accounts
    data/raw/dim_reps.csv           -> raw.reps
    data/raw/dim_date.csv           -> raw.dates
    data/raw/fct_opportunities.csv  -> raw.opportunities
    data/raw/fct_stage_history.csv  -> raw.stage_history
    data/raw/fct_activities.csv     -> raw.activities

What it does:
- Checks each CSV's header against an explicit column contract (names, order,
  types), so the loader never relies on DuckDB guessing types.
- Trims whitespace on every text column and turns empty strings into NULL.
- Runs integrity checks (primary keys, foreign keys, allowed stage values,
  amount/probability ranges, date ordering) inside the same transaction as the
  load. If any check fails, the whole load rolls back and whatever was in the
  database before stays untouched.

dbt reads these tables as sources. Business logic belongs in dbt, not here.

Usage:
    .venv/bin/python pipelines/load_to_duckdb.py
    .venv/bin/python pipelines/load_to_duckdb.py --raw-dir data/raw --db crm_analytics.duckdb
"""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from pathlib import Path

import duckdb

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_RAW_DIR = PROJECT_ROOT / "data" / "raw"
DEFAULT_DB_PATH = PROJECT_ROOT / "crm_analytics.duckdb"
RAW_SCHEMA = "raw"

VALID_STAGES = ("Prospecting", "Discovery", "Demo", "Negotiation", "Closed-Won", "Closed-Lost")


@dataclass(frozen=True)
class TableContract:
    """What one raw table must look like: its source file, columns and primary key."""

    table: str
    csv_name: str
    columns: dict[str, str]  # column name -> DuckDB type, in CSV header order
    primary_key: str


CONTRACTS: tuple[TableContract, ...] = (
    TableContract(
        table="accounts",
        csv_name="dim_accounts.csv",
        columns={
            "account_id": "VARCHAR",
            "company_name": "VARCHAR",
            "industry": "VARCHAR",
            "arr": "BIGINT",
            "employee_count": "INTEGER",
            "tier": "VARCHAR",
            "region": "VARCHAR",
        },
        primary_key="account_id",
    ),
    TableContract(
        table="reps",
        csv_name="dim_reps.csv",
        columns={
            "rep_id": "VARCHAR",
            "rep_name": "VARCHAR",
            "region": "VARCHAR",
            "segment": "VARCHAR",
            "annual_quota": "BIGINT",
        },
        primary_key="rep_id",
    ),
    TableContract(
        table="dates",
        csv_name="dim_date.csv",
        columns={
            "date_key": "INTEGER",
            "date_day": "DATE",
            "year": "INTEGER",
            "quarter": "INTEGER",
            "quarter_label": "VARCHAR",
            "month": "INTEGER",
            "month_name": "VARCHAR",
            "day_of_month": "INTEGER",
            "day_of_week": "INTEGER",
            "day_name": "VARCHAR",
            "iso_week": "INTEGER",
            "is_weekend": "BOOLEAN",
            "first_day_of_month": "DATE",
            "first_day_of_quarter": "DATE",
        },
        primary_key="date_day",
    ),
    TableContract(
        table="opportunities",
        csv_name="fct_opportunities.csv",
        columns={
            "opportunity_id": "VARCHAR",
            "account_id": "VARCHAR",
            "rep_id": "VARCHAR",
            "created_date": "DATE",
            "close_date": "DATE",
            "stage": "VARCHAR",
            "stage_entered_date": "DATE",
            "amount": "DECIMAL(14,2)",
            "probability": "DECIMAL(5,4)",
        },
        primary_key="opportunity_id",
    ),
    TableContract(
        table="stage_history",
        csv_name="fct_stage_history.csv",
        columns={
            "opportunity_id": "VARCHAR",
            "stage_sequence": "INTEGER",
            "stage": "VARCHAR",
            "entered_date": "DATE",
            "exited_date": "DATE",
        },
        # Composite key: one row per opportunity per stage step.
        primary_key="opportunity_id || '#' || stage_sequence",
    ),
    TableContract(
        table="activities",
        csv_name="fct_activities.csv",
        columns={
            "activity_id": "VARCHAR",
            "opportunity_id": "VARCHAR",
            "activity_type": "VARCHAR",
            "activity_timestamp": "TIMESTAMP",
        },
        primary_key="activity_id",
    ),
)

# (child table, child column, parent table, parent column)
FOREIGN_KEYS = (
    ("opportunities", "account_id", "accounts", "account_id"),
    ("opportunities", "rep_id", "reps", "rep_id"),
    ("stage_history", "opportunity_id", "opportunities", "opportunity_id"),
    ("activities", "opportunity_id", "opportunities", "opportunity_id"),
)

# Columns that may legitimately be NULL. Every other column is required.
NULLABLE_COLUMNS = {("stage_history", "exited_date")}


class LoaderError(Exception):
    """Base class for anything that stops a load, with a message fit for the console."""


class SchemaError(LoaderError):
    """A CSV is missing, or its header doesn't match the contract."""


class ValidationError(LoaderError):
    """Data loaded but failed one or more integrity checks; the load was rolled back."""

    def __init__(self, failures: list[str]):
        self.failures = failures
        super().__init__("Validation failed:\n  - " + "\n  - ".join(failures))


def _sql_string(value: str) -> str:
    """Quote a value as a SQL string literal."""
    return "'" + value.replace("'", "''") + "'"


def check_header(path: Path, contract: TableContract) -> None:
    """Fail fast if the file is missing or its header doesn't match the contract exactly."""
    if not path.is_file():
        raise SchemaError(
            f"Missing {path}. Run `python pipelines/generate_data.py` first to create the raw CSVs."
        )
    with path.open(newline="", encoding="utf-8") as fh:
        header = next(csv.reader(fh), None)
    if header is None:
        raise SchemaError(f"{path.name} is empty (no header row).")
    header = [h.strip() for h in header]
    expected = list(contract.columns)
    if header != expected:
        missing = [c for c in expected if c not in header]
        extra = [c for c in header if c not in expected]
        detail = []
        if missing:
            detail.append(f"missing {missing}")
        if extra:
            detail.append(f"unexpected {extra}")
        if not detail:
            detail.append(f"columns out of order, expected {expected}")
        raise SchemaError(f"{path.name} header mismatch: {'; '.join(detail)}.")


def build_load_sql(path: Path, contract: TableContract) -> str:
    """CREATE OR REPLACE statement that reads one CSV with explicit types and light cleaning.

    Text columns are read as VARCHAR first, then trimmed, then cast, so stray
    whitespace in IDs or numbers can't break joins or casts. Empty strings
    become NULL.
    """
    # Read everything as VARCHAR so cleaning happens before any type conversion.
    read_columns = ", ".join(f"{_sql_string(c)}: 'VARCHAR'" for c in contract.columns)
    select_list = ",\n    ".join(
        f"CAST(NULLIF(TRIM({col}), '') AS {dtype}) AS {col}" for col, dtype in contract.columns.items()
    )
    return (
        f"CREATE OR REPLACE TABLE {RAW_SCHEMA}.{contract.table} AS\n"
        f"SELECT\n    {select_list}\n"
        f"FROM read_csv({_sql_string(str(path))}, header = true, columns = {{{read_columns}}})"
    )


def validation_queries() -> list[tuple[str, str]]:
    """(description, SQL returning a violation count) for every integrity rule."""
    checks: list[tuple[str, str]] = []
    for c in CONTRACTS:
        t = f"{RAW_SCHEMA}.{c.table}"
        checks.append((f"{t} has no rows", f"SELECT CASE WHEN COUNT(*) = 0 THEN 1 ELSE 0 END FROM {t}"))
        checks.append(
            (f"{t}: duplicate primary key ({c.primary_key})",
             f"SELECT COUNT(*) - COUNT(DISTINCT {c.primary_key}) FROM {t}")
        )
        for col in c.columns:
            if (c.table, col) not in NULLABLE_COLUMNS:
                checks.append((f"{t}.{col} has NULLs", f"SELECT COUNT(*) FROM {t} WHERE {col} IS NULL"))

    for child, child_col, parent, parent_col in FOREIGN_KEYS:
        checks.append(
            (f"{RAW_SCHEMA}.{child}.{child_col} has values not found in {RAW_SCHEMA}.{parent}.{parent_col}",
             f"SELECT COUNT(*) FROM {RAW_SCHEMA}.{child} c "
             f"WHERE NOT EXISTS (SELECT 1 FROM {RAW_SCHEMA}.{parent} p WHERE p.{parent_col} = c.{child_col})")
        )

    stage_list = ", ".join(_sql_string(s) for s in VALID_STAGES)
    for table in ("opportunities", "stage_history"):
        checks.append(
            (f"{RAW_SCHEMA}.{table}.stage outside {list(VALID_STAGES)}",
             f"SELECT COUNT(*) FROM {RAW_SCHEMA}.{table} WHERE stage NOT IN ({stage_list})")
        )

    o = f"{RAW_SCHEMA}.opportunities"
    sh = f"{RAW_SCHEMA}.stage_history"
    checks += [
        ("opportunities.amount must be > 0", f"SELECT COUNT(*) FROM {o} WHERE amount <= 0"),
        ("opportunities.probability must be between 0 and 1",
         f"SELECT COUNT(*) FROM {o} WHERE probability NOT BETWEEN 0 AND 1"),
        ("opportunities.close_date before created_date",
         f"SELECT COUNT(*) FROM {o} WHERE close_date < created_date"),
        ("opportunities.stage_entered_date before created_date",
         f"SELECT COUNT(*) FROM {o} WHERE stage_entered_date < created_date"),
        ("stage_history.exited_date before entered_date",
         f"SELECT COUNT(*) FROM {sh} WHERE exited_date < entered_date"),
        # Every opportunity's current stage must match its latest stage_history row.
        ("opportunities.stage disagrees with the latest stage_history row",
         f"SELECT COUNT(*) FROM {o} JOIN ("
         f"  SELECT opportunity_id, arg_max(stage, stage_sequence) AS last_stage FROM {sh} GROUP BY 1"
         f") h USING (opportunity_id) WHERE h.last_stage <> {o}.stage"),
    ]
    return checks


def run_validations(con: duckdb.DuckDBPyConnection) -> list[str]:
    """Run every integrity check and return a description of each one that failed."""
    failures = []
    for description, sql in validation_queries():
        violations = con.execute(sql).fetchone()[0]
        if violations:
            failures.append(f"{description} ({violations} rows)")
    return failures


def connect(db_path: Path) -> duckdb.DuckDBPyConnection:
    """Open the DuckDB file with a readable message for the common failure modes."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        return duckdb.connect(str(db_path))
    except duckdb.Error as exc:
        # Usually another process (Streamlit, a dbt run, a DuckDB CLI) holds the write lock
        # (IOException), or this process already has it open read-only (ConnectionException).
        raise LoaderError(
            f"Could not open {db_path} for writing. Is another process (Streamlit app, dbt, "
            f"DuckDB CLI) holding it open? Close it and retry.\nDuckDB said: {exc}"
        ) from exc


def load(raw_dir: Path = DEFAULT_RAW_DIR, db_path: Path = DEFAULT_DB_PATH) -> dict[str, int]:
    """Load and validate every raw table atomically. Returns {table: row_count}.

    Raises SchemaError (bad/missing CSV), ValidationError (integrity check
    failed) or LoaderError (database problems). In every failure case the
    database is left as it was before the call.
    """
    for contract in CONTRACTS:
        check_header(raw_dir / contract.csv_name, contract)

    con = connect(db_path)
    try:
        con.execute("BEGIN TRANSACTION")
        try:
            con.execute(f"CREATE SCHEMA IF NOT EXISTS {RAW_SCHEMA}")
            for contract in CONTRACTS:
                try:
                    con.execute(build_load_sql(raw_dir / contract.csv_name, contract))
                except duckdb.Error as exc:
                    # Typically a value that won't cast (e.g. "abc" in a DATE column).
                    raise LoaderError(f"Failed to load {contract.csv_name}: {exc}") from exc

            failures = run_validations(con)
            if failures:
                raise ValidationError(failures)

            counts = {
                c.table: con.execute(f"SELECT COUNT(*) FROM {RAW_SCHEMA}.{c.table}").fetchone()[0]
                for c in CONTRACTS
            }
            con.execute("COMMIT")
            return counts
        except Exception:
            con.execute("ROLLBACK")
            raise
    finally:
        con.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Load raw CRM CSVs into DuckDB.")
    parser.add_argument("--raw-dir", type=Path, default=DEFAULT_RAW_DIR)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    args = parser.parse_args(argv)

    try:
        counts = load(args.raw_dir, args.db)
    except LoaderError as exc:
        print(f"Load aborted, database unchanged.\n{exc}")
        return 1

    print(f"Loaded {len(counts)} tables into {args.db} (schema '{RAW_SCHEMA}'), all checks passed:")
    for table, n in counts.items():
        print(f"  {RAW_SCHEMA}.{table:<16} {n:>7,} rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
