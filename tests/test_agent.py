"""Tests for the local AI agent layer: SQL guardrails, schema context, Ollama checks, CrewAI wiring.

Everything here runs without Ollama except the one test marked `ollama`, which
is opt-in (RUN_OLLAMA_TESTS=1) because it takes minutes on a laptop CPU.
"""

import io
import json
import os
import urllib.error

import pandas as pd
import pytest

from agent import crew_agent
from agent.crew_agent import (
    AgentError,
    AnalysisResult,
    OllamaConfig,
    OllamaUnavailableError,
    QueryRecord,
    _model_matches,
    ask,
    build_agent,
    build_llm,
    build_task,
    check_ollama,
    make_sql_tool,
)
from agent.schema_context import (
    MART_TABLES,
    METRIC_GLOSSARY,
    SUMMARIZED_TABLES,
    build_schema_context,
    fetch_mart_columns,
)
from agent.sql_tool import (
    QueryExecutionError,
    QueryResult,
    UnsafeQueryError,
    clean_sql,
    format_for_llm,
    run_select,
    validate_select,
)

# --- validate_select -------------------------------------------------------


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 1",
        "select count(*) from marts.fct_pipeline_velocity where is_stalled",
        "WITH t AS (SELECT 1 AS x) SELECT x FROM t",
        "  SELECT 1 ;  ",
        "```sql\nSELECT 1\n```",
    ],
)
def test_select_queries_are_allowed(sql):
    assert validate_select(sql).lower().startswith(("select", "with"))


def test_clean_sql_strips_fences_and_semicolons():
    assert clean_sql("```sql\nSELECT 1;\n```") == "SELECT 1"


@pytest.mark.parametrize(
    "sql, reason",
    [
        ("INSERT INTO marts.dim_reps VALUES (1)", "INSERT"),
        ("UPDATE marts.dim_reps SET annual_quota = 0", "UPDATE"),
        ("DELETE FROM marts.dim_reps", "DELETE"),
        ("DROP TABLE marts.dim_reps", "DROP"),
        ("CREATE TABLE x AS SELECT 1", "CREATE"),
        ("ATTACH 'other.db'", "ATTACH"),
        ("COPY marts.dim_reps TO 'out.csv'", "COPY"),
        ("SET enable_external_access = true", "SET"),
        ("SELECT 1; DROP TABLE marts.dim_reps", "Exactly one statement"),
        ("SELECT 1; SELECT 2", "Exactly one statement"),
        ("", "empty"),
        ("   ;  ", "empty"),
        ("SELEC oops FROM", "could not be parsed"),
    ],
)
def test_non_select_queries_are_rejected(sql, reason):
    with pytest.raises(UnsafeQueryError, match=reason):
        validate_select(sql)


def test_non_string_rejected():
    with pytest.raises(UnsafeQueryError):
        validate_select(None)


# --- run_select ------------------------------------------------------------


def test_run_select_returns_dataframe(marts_db):
    result = run_select(
        "SELECT account_tier, count(*) AS n FROM marts.fct_pipeline_velocity GROUP BY 1 ORDER BY 1", db_path=marts_db
    )
    assert list(result.dataframe.columns) == ["account_tier", "n"]
    assert set(result.dataframe["account_tier"]) == {"SMB", "Mid-Market", "Enterprise"}
    assert not result.truncated


def test_run_select_caps_rows_and_flags_truncation(marts_db):
    result = run_select("SELECT * FROM marts.fct_pipeline_velocity", db_path=marts_db, max_rows=10)
    assert len(result.dataframe) == 10
    assert result.truncated


def test_external_file_access_is_blocked(marts_db, raw_dir):
    """A SELECT can't read local files through table functions."""
    with pytest.raises(QueryExecutionError, match="(?i)permission"):
        run_select(f"SELECT * FROM read_csv('{raw_dir / 'dim_reps.csv'}')", db_path=marts_db)


def test_database_is_not_modified_by_rejected_writes(marts_db):
    before = run_select("SELECT count(*) AS n FROM marts.dim_reps", db_path=marts_db).dataframe["n"][0]
    with pytest.raises(UnsafeQueryError):
        run_select("DELETE FROM marts.dim_reps", db_path=marts_db)
    after = run_select("SELECT count(*) AS n FROM marts.dim_reps", db_path=marts_db).dataframe["n"][0]
    assert before == after


def test_bad_column_raises_execution_error(marts_db):
    with pytest.raises(QueryExecutionError, match="no_such_column"):
        run_select("SELECT no_such_column FROM marts.dim_reps", db_path=marts_db)


def test_long_query_is_interrupted(marts_db):
    with pytest.raises(QueryExecutionError, match="timed out"):
        run_select("SELECT sum(a.range * b.range) FROM range(200000) a, range(200000) b", db_path=marts_db,
                   timeout_seconds=0.3)


def test_missing_database_raises(tmp_path):
    with pytest.raises(QueryExecutionError, match="not found"):
        run_select("SELECT 1", db_path=tmp_path / "nope.duckdb")


def test_format_for_llm_notes_empty_and_truncated():
    empty = QueryResult("SELECT 1", pd.DataFrame({"x": []}), truncated=False, elapsed_seconds=0)
    assert "0 rows" in format_for_llm(empty)
    capped = QueryResult("SELECT 1", pd.DataFrame({"x": range(5)}), truncated=True, elapsed_seconds=0)
    text = format_for_llm(capped, max_rows_shown=3)
    assert "showing first 3" in text and "capped" in text
    assert text.count("\n|") == 4  # header separator + 3 rows


# --- schema context --------------------------------------------------------


def test_every_listed_table_exists(marts_db):
    columns = fetch_mart_columns(marts_db)
    assert set(MART_TABLES) <= set(columns)


def test_glossary_columns_exist(marts_db):
    """Catches drift: renaming a mart column must update the agent's metric definitions."""
    columns = {t: {c for c, _ in cols} for t, cols in fetch_mart_columns(marts_db).items()}
    for table, column in METRIC_GLOSSARY:
        assert column in columns[table], f"{table}.{column} in glossary but not in the warehouse"


def test_context_lists_real_columns_and_conventions(marts_db):
    context = build_schema_context(marts_db)
    columns = fetch_mart_columns(marts_db)
    for table in MART_TABLES:
        assert f"marts.{table}" in context
        if table not in SUMMARIZED_TABLES:
            for column, _ in columns[table]:
                assert column in context, f"{table}.{column} missing from context"
    assert "as-of date" in context and "45 days" in context


def test_context_fits_small_context_window(marts_db):
    """Ollama defaults to a 4,096-token window; keep the schema well under half of it."""
    assert len(build_schema_context(marts_db)) / 4 < 1600


def test_context_fails_clearly_without_marts(raw_dir, tmp_path):
    from pipelines.load_to_duckdb import load

    db = tmp_path / "raw_only.duckdb"
    load(raw_dir, db)
    with pytest.raises(RuntimeError, match="dbt build"):
        build_schema_context(db)


# --- Ollama health check ---------------------------------------------------


@pytest.mark.parametrize(
    "requested, installed, expected",
    [
        ("llama3.1", "llama3.1:latest", True),
        ("llama3.1", "llama3.1:8b", True),
        ("llama3.1", "llama3.2:latest", False),
        ("llama3.1:8b", "llama3.1:latest", False),
        ("mistral", "mistral:7b", True),
    ],
)
def test_model_matching(requested, installed, expected):
    assert _model_matches(requested, installed) is expected


def _fake_tags(models):
    def fake_urlopen(url, timeout):
        return io.BytesIO(json.dumps({"models": [{"name": m} for m in models]}).encode())

    return fake_urlopen


def test_check_ollama_passes_when_model_present(monkeypatch):
    monkeypatch.setattr(crew_agent.urllib.request, "urlopen", _fake_tags(["llama3.1:latest"]))
    assert check_ollama(OllamaConfig(model="llama3.1")) == ["llama3.1:latest"]


def test_check_ollama_explains_missing_model(monkeypatch):
    monkeypatch.setattr(crew_agent.urllib.request, "urlopen", _fake_tags(["llama3.2:latest"]))
    with pytest.raises(OllamaUnavailableError, match=r"ollama pull llama3.1.*|installed: llama3.2"):
        check_ollama(OllamaConfig(model="llama3.1"))


def test_check_ollama_explains_server_down(monkeypatch):
    def refuse(url, timeout):
        raise urllib.error.URLError("Connection refused")

    monkeypatch.setattr(crew_agent.urllib.request, "urlopen", refuse)
    with pytest.raises(OllamaUnavailableError, match="ollama serve"):
        check_ollama(OllamaConfig())


def test_ask_fails_fast_when_ollama_unreachable(marts_db):
    # Port 9 (discard) has nothing listening, so the health check fails before any CrewAI work.
    with pytest.raises(OllamaUnavailableError):
        ask("How much pipeline is open?", OllamaConfig(base_url="http://127.0.0.1:9"), db_path=marts_db)


def test_ask_rejects_empty_question():
    with pytest.raises(AgentError, match="Ask a question"):
        ask("   ")


# --- CrewAI wiring (no LLM calls) -----------------------------------------


def test_sql_tool_logs_success_and_returns_markdown(marts_db):
    log = []
    tool = make_sql_tool(log, db_path=marts_db)
    out = tool.run(sql_query="SELECT count(*) AS stalled FROM marts.fct_pipeline_velocity WHERE is_stalled")
    assert "stalled" in out and "1 row(s)" in out
    assert len(log) == 1 and log[0].ok


def test_sql_tool_returns_error_text_for_model_to_fix(marts_db):
    log = []
    tool = make_sql_tool(log, db_path=marts_db)
    out = tool.run(sql_query="DROP TABLE marts.dim_reps")
    assert out.startswith("ERROR:") and "Only SELECT" in out
    assert len(log) == 1 and not log[0].ok and "Only SELECT" in log[0].error


def test_agent_and_task_carry_schema_and_tool(marts_db):
    config = OllamaConfig(model="llama3.1")
    llm = build_llm(config)
    assert llm.model.endswith("llama3.1")
    context = build_schema_context(marts_db)
    agent = build_agent(llm, make_sql_tool([], marts_db), context, max_iterations=4)
    assert "marts.fct_pipeline_velocity" in agent.backstory
    assert [t.name for t in agent.tools] == ["query_crm_database"]
    assert agent.allow_delegation is False and agent.max_iter == 4
    task = build_task("Which reps are behind?", agent)
    assert "Which reps are behind?" in task.description
    assert "Recommendations" in task.expected_output


def test_tracing_and_telemetry_disabled():
    assert os.environ["CREWAI_TRACING_ENABLED"] == "false"
    assert os.environ["OTEL_SDK_DISABLED"] == "true"


def test_analysis_result_grounding():
    ok = QueryRecord("SELECT 1", result=QueryResult("SELECT 1", pd.DataFrame({"x": [1]}), False, 0))
    bad = QueryRecord("SELECT nope", error="Binder Error")
    result = AnalysisResult("q", "a", "m", 1.0, queries=[ok, bad])
    assert result.grounded
    assert result.final_dataframe.equals(ok.result.dataframe)
    assert not AnalysisResult("q", "a", "m", 1.0, queries=[bad]).grounded
    assert AnalysisResult("q", "a", "m", 1.0).final_dataframe is None


# --- Live end-to-end (opt-in) ---------------------------------------------


@pytest.mark.ollama
@pytest.mark.skipif(os.getenv("RUN_OLLAMA_TESTS") != "1", reason="set RUN_OLLAMA_TESTS=1 to run against local Ollama")
def test_live_agent_answers_from_data(marts_db):
    config = OllamaConfig(model=os.getenv("OLLAMA_MODEL", "llama3.1"))
    result = ask("How many open deals are stalled in negotiation?", config, db_path=marts_db)
    assert result.grounded, f"no successful query; tried: {[q.sql for q in result.queries]}"
    assert any("is_stalled" in q.sql or "Negotiation" in q.sql for q in result.successful_queries)
    assert result.answer


class _FakeCrew:
    """Stands in for crewai.Crew: attempt 1 never queries (like a model that prints the tool call
    as text instead of making it); later attempts call the tool for real."""

    attempts = 0

    def __init__(self, agents, tasks, **kwargs):
        self.agent = agents[0]

    def kickoff(self):
        type(self).attempts += 1
        if type(self).attempts >= 2:
            self.agent.tools[0].run(sql_query="SELECT count(*) AS n FROM marts.fct_pipeline_velocity")
            return type("Out", (), {"raw": "grounded answer"})()
        return type("Out", (), {"raw": '{"name": "query_crm_database", ...}'})()


def test_ask_retries_when_first_attempt_is_ungrounded(marts_db, monkeypatch):
    _FakeCrew.attempts = 0
    monkeypatch.setattr(crew_agent, "Crew", _FakeCrew)
    result = ask("How many deals?", OllamaConfig(max_attempts=2), db_path=marts_db, skip_health_check=True)
    assert result.attempts == 2
    assert result.grounded and result.answer == "grounded answer"


def test_ask_stops_after_max_attempts(marts_db, monkeypatch):
    _FakeCrew.attempts = -10  # never reaches the grounded branch
    monkeypatch.setattr(crew_agent, "Crew", _FakeCrew)
    result = ask("How many deals?", OllamaConfig(max_attempts=2), db_path=marts_db, skip_health_check=True)
    assert result.attempts == 2
    assert not result.grounded


def test_ask_wraps_crew_exceptions(marts_db, monkeypatch):
    class Boom(_FakeCrew):
        def kickoff(self):
            raise ValueError("model returned garbage")

    monkeypatch.setattr(crew_agent, "Crew", Boom)
    with pytest.raises(AgentError, match="model returned garbage"):
        ask("How many deals?", db_path=marts_db, skip_health_check=True)
