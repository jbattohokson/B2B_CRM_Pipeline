"""Load the real Olist Marketing Funnel CSVs into DuckDB as typed, validated tables in the `raw` schema.

    data/raw/olist_marketing_qualified_leads_dataset.csv  -> raw.mqls
    data/raw/olist_closed_deals_dataset.csv               -> raw.closed_deals

What it does:
- Checks each CSV's header against an explicit column contract (names, order,
  types), so the loader never relies on DuckDB guessing types.
- Trims whitespace on every text column and turns empty strings into NULL.
- Runs integrity checks (primary keys, the deal-to-lead foreign key, allowed
  lead sources, required fields, non-negative declared figures) inside the same
  transaction as the load. If any check fails, the whole load rolls back and
  whatever was in the database before stays untouched.

Known quirks in the real data are flagged downstream in dbt, not rejected here
(one deal closes two days before its lead's first contact). Business logic
belongs in dbt.

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

# Every lead source value in the published dataset. NULL (60 leads) is allowed and becomes 'unknown' in dbt.
VALID_ORIGINS = (
    "organic_search", "paid_search", "social", "unknown", "direct_traffic", "email",
    "referral", "other", "display", "other_publicities",
)


@dataclass(frozen=True)
class TableContract:
    """What one raw table must look like: its source file, columns and primary key."""

    table: str
    csv_name: str
    columns: dict[str, str]  # column name -> DuckDB type, in CSV header order
    primary_key: str


CONTRACTS: tuple[TableContract, ...] = (
    TableContract(
        table="mqls",
        csv_name="olist_marketing_qualified_leads_dataset.csv",
        columns={
            "mql_id": "VARCHAR",
            "first_contact_date": "DATE",
            "landing_page_id": "VARCHAR",
            "origin": "VARCHAR",
        },
        primary_key="mql_id",
    ),
    TableContract(
        table="closed_deals",
        csv_name="olist_closed_deals_dataset.csv",
        columns={
            "mql_id": "VARCHAR",
            "seller_id": "VARCHAR",
            "sdr_id": "VARCHAR",
            "sr_id": "VARCHAR",
            "won_date": "TIMESTAMP",
            "business_segment": "VARCHAR",
            "lead_type": "VARCHAR",
            "lead_behaviour_profile": "VARCHAR",
            "has_company": "BOOLEAN",
            "has_gtin": "BOOLEAN",
            "average_stock": "VARCHAR",
            "business_type": "VARCHAR",
            "declared_product_catalog_size": "DOUBLE",
            "declared_monthly_revenue": "DOUBLE",
        },
        primary_key="mql_id",
    ),
)

# (child table, child column, parent table, parent column)
FOREIGN_KEYS = (("closed_deals", "mql_id", "mqls", "mql_id"),)

# Columns that must never be NULL. The real export leaves the rest blank for many rows
# (most self-declared seller attributes are empty), so everything else is nullable.
REQUIRED_COLUMNS = {
    ("mqls", "mql_id"), ("mqls", "first_contact_date"), ("mqls", "landing_page_id"),
    ("closed_deals", "mql_id"), ("closed_deals", "seller_id"), ("closed_deals", "sdr_id"),
    ("closed_deals", "sr_id"), ("closed_deals", "won_date"),
}


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
            f"Missing {path}. Run `python pipelines/fetch_data.py` to download the raw CSVs."
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
            if (c.table, col) in REQUIRED_COLUMNS:
                checks.append((f"{t}.{col} has NULLs", f"SELECT COUNT(*) FROM {t} WHERE {col} IS NULL"))

    for child, child_col, parent, parent_col in FOREIGN_KEYS:
        checks.append(
            (f"{RAW_SCHEMA}.{child}.{child_col} has values not found in {RAW_SCHEMA}.{parent}.{parent_col}",
             f"SELECT COUNT(*) FROM {RAW_SCHEMA}.{child} c "
             f"WHERE NOT EXISTS (SELECT 1 FROM {RAW_SCHEMA}.{parent} p WHERE p.{parent_col} = c.{child_col})")
        )

    m = f"{RAW_SCHEMA}.mqls"
    d = f"{RAW_SCHEMA}.closed_deals"
    origin_list = ", ".join(_sql_string(o) for o in VALID_ORIGINS)
    checks += [
        (f"mqls.origin outside {list(VALID_ORIGINS)}",
         f"SELECT COUNT(*) FROM {m} WHERE origin IS NOT NULL AND origin NOT IN ({origin_list})"),
        ("closed_deals.seller_id is not unique (one seller per won lead)",
         f"SELECT COUNT(*) - COUNT(DISTINCT seller_id) FROM {d}"),
        ("closed_deals.declared_monthly_revenue is negative",
         f"SELECT COUNT(*) FROM {d} WHERE declared_monthly_revenue < 0"),
        ("closed_deals.declared_product_catalog_size is negative",
         f"SELECT COUNT(*) FROM {d} WHERE declared_product_catalog_size < 0"),
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
    parser = argparse.ArgumentParser(description="Load the raw Olist funnel CSVs into DuckDB.")
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
        print(f"  {RAW_SCHEMA}.{table:<14} {n:>7,} rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
