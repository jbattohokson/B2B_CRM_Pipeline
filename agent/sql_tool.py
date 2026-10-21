"""Read-only SQL execution against crm_analytics.duckdb for the AI agent.

The model writes the SQL, so treat every query as untrusted. Three layers of protection:

1. Parse with DuckDB's own parser and accept exactly one statement, of type SELECT
   (CTEs and SELECT-only PRAGMAs count). This rejects INSERT/UPDATE/DELETE/DDL,
   ATTACH, COPY, SET, and stacked statements like "SELECT 1; DROP ...".
2. Open the database read_only, so even a SELECT that sneaks a write past the
   parser can't modify anything.
3. Set enable_external_access=false, so table functions like read_csv(),
   read_text() and glob() can't read local files or URLs through a "SELECT".

Results are capped at max_rows, and a timer interrupts queries that run too long.
"""

from __future__ import annotations

import os
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import duckdb
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = Path(os.getenv("CRM_DB_PATH", PROJECT_ROOT / "crm_analytics.duckdb"))
DEFAULT_MAX_ROWS = 200
DEFAULT_TIMEOUT_SECONDS = 15.0

# LLMs often wrap SQL in a markdown fence, sometimes with a language tag.
_CODE_FENCE = re.compile(r"^```[a-zA-Z]*\s*|\s*```$")


class QueryError(Exception):
    """Base class: the query was not run, or failed. The message is safe to show the model."""


class UnsafeQueryError(QueryError):
    """The SQL is not a single read-only SELECT statement."""


class QueryExecutionError(QueryError):
    """The SQL was allowed but failed: bad column, timeout, missing or locked database."""


@dataclass
class QueryResult:
    sql: str
    dataframe: pd.DataFrame
    truncated: bool
    elapsed_seconds: float


def clean_sql(sql: str) -> str:
    """Strip whitespace, markdown code fences and trailing semicolons."""
    text = _CODE_FENCE.sub("", sql.strip()).strip()
    return text.rstrip(";").strip()


def validate_select(sql: str) -> str:
    """Return the cleaned SQL if it's exactly one SELECT statement; otherwise raise UnsafeQueryError."""
    if not isinstance(sql, str):
        raise UnsafeQueryError("Query must be a string of SQL.")
    query = clean_sql(sql)
    if not query:
        raise UnsafeQueryError("Query is empty.")
    try:
        statements = duckdb.extract_statements(query)
    except duckdb.Error as exc:
        raise UnsafeQueryError(f"SQL could not be parsed: {exc}") from exc
    if len(statements) != 1:
        raise UnsafeQueryError(f"Exactly one statement is allowed; got {len(statements)}.")
    kind = statements[0].type
    if kind != duckdb.StatementType.SELECT:
        raise UnsafeQueryError(f"Only SELECT queries are allowed; got {kind.name}.")
    return query


def open_read_only(db_path: Path | str | None = None) -> duckdb.DuckDBPyConnection:
    """Read-only, no-external-access connection, with readable errors for the usual failures."""
    path = Path(db_path) if db_path else DEFAULT_DB_PATH
    if not path.is_file():
        raise QueryExecutionError(
            f"Database {path} not found. Build it first: pipelines/load_to_duckdb.py, then `DBT_PROFILES_DIR=. dbt build`."
        )
    try:
        return duckdb.connect(str(path), read_only=True, config={"enable_external_access": False})
    except duckdb.Error as exc:
        raise QueryExecutionError(
            f"Could not open {path} read-only (is a loader or dbt run writing to it right now?): {exc}"
        ) from exc


def run_select(
    sql: str,
    db_path: Path | str | None = None,
    max_rows: int = DEFAULT_MAX_ROWS,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> QueryResult:
    """Validate and run one SELECT. Returns up to max_rows rows and whether more existed."""
    query = validate_select(sql)
    con = open_read_only(db_path)
    # DuckDB has no per-statement timeout, so interrupt the connection from a timer thread.
    timer = threading.Timer(timeout_seconds, con.interrupt)
    started = time.perf_counter()
    timer.start()
    try:
        cursor = con.execute(query)
        columns = [col[0] for col in cursor.description]
        rows = cursor.fetchmany(max_rows + 1)
    except duckdb.InterruptException as exc:
        raise QueryExecutionError(
            f"Query timed out after {timeout_seconds:g}s. Aggregate more or add filters."
        ) from exc
    except duckdb.Error as exc:
        raise QueryExecutionError(f"{type(exc).__name__}: {exc}") from exc
    finally:
        timer.cancel()
        con.close()

    truncated = len(rows) > max_rows
    df = pd.DataFrame(rows[:max_rows], columns=columns)
    return QueryResult(query, df, truncated, round(time.perf_counter() - started, 3))


def format_for_llm(result: QueryResult, max_rows_shown: int = 50) -> str:
    """Render a result as a compact markdown table for the model to read."""
    df = result.dataframe
    if df.empty:
        return "Query ran successfully and returned 0 rows."
    shown = df.head(max_rows_shown)
    table = shown.to_markdown(index=False, floatfmt=",.2f")
    notes = [f"{len(df)} row(s) returned"]
    if len(df) > max_rows_shown:
        notes.append(f"showing first {max_rows_shown}")
    if result.truncated:
        notes.append(f"result capped at {len(df)} rows; aggregate or add LIMIT for the full picture")
    return f"{table}\n\n({'; '.join(notes)})"
