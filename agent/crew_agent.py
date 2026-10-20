"""Local RevOps analyst agent: CrewAI on Ollama, answering questions with read-only SQL.

Flow for one question:
  1. Check that Ollama is reachable and the model is pulled (clear error if not).
  2. Build a "Senior RevOps Analyst" agent whose backstory carries the live mart
     schema and metric definitions (agent/schema_context.py), so the local model
     doesn't guess table or column names.
  3. The agent calls `query_crm_database` (read-only SELECT, agent/sql_tool.py)
     as many times as it needs. Failed queries come back as error text so the
     model can correct itself.
  4. Every query, with its result DataFrame or error, is recorded so the
     Streamlit app can show the data behind the answer, not just the prose.

Everything runs locally. CrewAI's tracing and OpenTelemetry export are switched
off below, before crewai is imported.

Usage:
    .venv/bin/python -m agent.crew_agent "Which reps have deals stuck in negotiation over 45 days?"
    OLLAMA_MODEL=llama3.1 .venv/bin/python -m agent.crew_agent "..."   # needs 16 GB+ RAM
"""

from __future__ import annotations

import os

# Must be set before crewai is imported: keep every run fully local.
os.environ.setdefault("CREWAI_TRACING_ENABLED", "false")
os.environ.setdefault("OTEL_SDK_DISABLED", "true")
os.environ.setdefault("CREWAI_DISABLE_TELEMETRY", "true")

import argparse  # noqa: E402
import json  # noqa: E402
import time  # noqa: E402
import urllib.error  # noqa: E402
import urllib.request  # noqa: E402
from dataclasses import dataclass, field  # noqa: E402
from pathlib import Path  # noqa: E402

import pandas as pd  # noqa: E402
from crewai import LLM, Agent, Crew, Process, Task  # noqa: E402
from crewai.tools import tool  # noqa: E402

from agent.fact_check import find_unverified_figures  # noqa: E402
from agent.schema_context import build_schema_context, load_dbt_vars  # noqa: E402
from agent.sql_tool import QueryError, QueryResult, format_for_llm, run_select  # noqa: E402

DEFAULT_OLLAMA_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
# llama3.2 (3B) by default: llama3.1 (8B) couldn't finish one question on an 8 GB M2
# (see README Testing status). Set OLLAMA_MODEL=llama3.1 on machines with 16 GB+.
DEFAULT_OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.2")


class AgentError(Exception):
    """The agent could not produce an answer. The message is fit to show the user."""


class OllamaUnavailableError(AgentError):
    """Ollama isn't running, or the requested model isn't pulled."""


@dataclass(frozen=True)
class OllamaConfig:
    base_url: str = DEFAULT_OLLAMA_URL
    model: str = DEFAULT_OLLAMA_MODEL  # Ollama tag, e.g. "llama3.2", "llama3.1" or "mistral"
    temperature: float = 0.1           # low: we want consistent SQL, not creativity
    request_timeout: float = 300.0     # seconds per LLM call; CPU-only inference is slow
    max_iterations: int = 6            # tool-call/reasoning rounds before the agent must answer
    max_attempts: int = 2              # re-run the crew if no query succeeded (small models
                                       # sometimes print a tool call as text instead of making it)


@dataclass
class QueryRecord:
    """One tool call by the agent: the SQL, and either its result or the error."""

    sql: str
    result: QueryResult | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.result is not None


@dataclass
class AnalysisResult:
    question: str
    answer: str
    model: str
    elapsed_seconds: float
    queries: list[QueryRecord] = field(default_factory=list)
    attempts: int = 1

    @property
    def successful_queries(self) -> list[QueryRecord]:
        return [q for q in self.queries if q.ok]

    @property
    def grounded(self) -> bool:
        """True if at least one query succeeded, so the answer had real data behind it."""
        return bool(self.successful_queries)

    @property
    def unverified_figures(self) -> list[str]:
        """Numbers in the answer that match no value in any successful query result."""
        frames = [q.result.dataframe for q in self.successful_queries]
        # Constants stated in the schema briefing are fair to quote.
        allowed = (load_dbt_vars()["stalled_days_threshold"], 365)
        return find_unverified_figures(self.answer, frames, question=self.question, allowed_values=allowed)

    @property
    def final_dataframe(self) -> pd.DataFrame | None:
        """Result of the last successful query: usually the table the answer is based on."""
        ok = self.successful_queries
        return ok[-1].result.dataframe if ok else None


# ---------------------------------------------------------------------------
# Ollama health check
# ---------------------------------------------------------------------------


def _model_matches(requested: str, installed: str) -> bool:
    """'llama3.1' matches 'llama3.1:latest'; 'llama3.1:8b' must match exactly."""
    if ":" in requested:
        return installed == requested
    return installed.split(":", 1)[0] == requested


def check_ollama(config: OllamaConfig, timeout: float = 5.0) -> list[str]:
    """Verify Ollama is up and has the model. Returns the installed model names."""
    url = config.base_url.rstrip("/") + "/api/tags"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            payload = json.load(resp)
    except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
        raise OllamaUnavailableError(
            f"Can't reach Ollama at {config.base_url} ({exc}). Start it with `ollama serve` "
            "(or open the Ollama app) and try again."
        ) from exc
    except json.JSONDecodeError as exc:
        raise OllamaUnavailableError(f"Unexpected response from {url}; is that really Ollama?") from exc

    installed = [m.get("name", "") for m in payload.get("models", [])]
    if not any(_model_matches(config.model, name) for name in installed):
        have = ", ".join(installed) or "none"
        raise OllamaUnavailableError(
            f"Ollama model '{config.model}' isn't pulled (installed: {have}). "
            f"Run `ollama pull {config.model}`, or set OLLAMA_MODEL to one you have."
        )
    return installed


# ---------------------------------------------------------------------------
# Tool, agent, task
# ---------------------------------------------------------------------------


def make_sql_tool(log: list[QueryRecord], db_path: Path | str | None = None):
    """A CrewAI tool bound to one question's query log and database."""

    @tool("query_crm_database")
    def query_crm_database(sql_query: str) -> str:
        """Run ONE read-only DuckDB SELECT against the CRM marts and get the result as a
        markdown table. Use schema-qualified names like marts.fct_pipeline_velocity.
        Only SELECT (or WITH ... SELECT) is allowed. Aggregate in SQL instead of pulling
        raw rows. Results are capped at 200 rows. If you get an ERROR, fix the SQL and
        call this tool again."""
        record = QueryRecord(sql=sql_query)
        log.append(record)
        try:
            record.result = run_select(sql_query, db_path=db_path)
        except QueryError as exc:
            record.error = str(exc)
            return f"ERROR: {exc}"
        return format_for_llm(record.result)

    return query_crm_database


def build_llm(config: OllamaConfig) -> LLM:
    return LLM(
        model=f"ollama/{config.model}",
        base_url=config.base_url,
        temperature=config.temperature,
        timeout=config.request_timeout,
    )


def build_agent(llm: LLM, sql_tool, schema_context: str, max_iterations: int = 6) -> Agent:
    return Agent(
        role="Senior RevOps Analyst",
        goal=(
            "Answer sales leadership's questions about pipeline, forecast and rep performance "
            "with numbers pulled from the CRM warehouse, then turn them into concrete actions."
        ),
        backstory=(
            "You are a revenue operations analyst at a B2B SaaS company. You know the company's "
            "dbt star schema in DuckDB by heart, described below. You never guess a number: every "
            "figure in your answers comes from a query you ran with the query_crm_database tool. "
            "You only use the tables and columns listed here.\n\n" + schema_context
        ),
        tools=[sql_tool],
        llm=llm,
        allow_delegation=False,
        max_iter=max_iterations,
        verbose=False,
    )


def build_task(question: str, agent: Agent) -> Task:
    return Task(
        description=(
            f"Question from a sales director: {question}\n\n"
            "Steps:\n"
            "1. Decide which marts table answers it (usually fct_pipeline_velocity or "
            "dim_sales_performance).\n"
            "2. Call query_crm_database with ONE DuckDB SELECT that computes the answer. "
            "Aggregate in SQL, sort meaningfully, and keep the result under about 25 rows.\n"
            "3. If the tool returns ERROR, read the message, fix the SQL and retry.\n"
            "4. Never do arithmetic yourself. If the answer needs a total, average or count, compute "
            "it in SQL (for example a second query without GROUP BY), then quote that number exactly.\n"
            "5. Answer using only numbers that appear in the query results."
        ),
        expected_output=(
            "Markdown with three parts:\n"
            "**Answer:** two or three sentences that directly answer the question with specific numbers "
            "copied from the query results.\n"
            "**Key figures:** up to five bullets, each quoting a number exactly as it appears in the query "
            "results. Fewer bullets is fine; never add a figure you did not query.\n"
            "**Recommendations:** two or three specific, operational next steps for sales leadership."
        ),
        agent=agent,
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def ask(
    question: str,
    config: OllamaConfig | None = None,
    db_path: Path | str | None = None,
    skip_health_check: bool = False,
) -> AnalysisResult:
    """Answer one natural-language question. Raises AgentError with a user-facing message."""
    question = (question or "").strip()
    if not question:
        raise AgentError("Ask a question about the pipeline, forecast or reps.")
    config = config or OllamaConfig()
    if not skip_health_check:
        check_ollama(config)

    try:
        schema_context = build_schema_context(db_path)
    except (QueryError, RuntimeError) as exc:
        raise AgentError(f"Can't read the warehouse schema: {exc}") from exc

    # One log across attempts, so the UI can show every query that was tried.
    log: list[QueryRecord] = []
    llm = build_llm(config)
    started = time.perf_counter()
    answer = ""
    attempt = 0
    for attempt in range(1, max(config.max_attempts, 1) + 1):
        attempt_start = len(log)
        agent = build_agent(llm, make_sql_tool(log, db_path), schema_context, config.max_iterations)
        crew = Crew(agents=[agent], tasks=[build_task(question, agent)], process=Process.sequential, verbose=False)
        try:
            output = crew.kickoff()
        except Exception as exc:  # CrewAI/LLM failures surface as many exception types
            raise AgentError(
                f"The local model failed to complete the analysis: {type(exc).__name__}: {exc}"
            ) from exc
        answer = (output.raw or "").strip()
        # Grounded on this attempt: done. Otherwise the answer has no data behind it; try again.
        if any(q.ok for q in log[attempt_start:]):
            break

    return AnalysisResult(
        question=question,
        answer=answer,
        model=config.model,
        elapsed_seconds=round(time.perf_counter() - started, 1),
        queries=log,
        attempts=attempt,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Ask the local RevOps analyst agent a question.")
    parser.add_argument("question")
    parser.add_argument("--model", default=DEFAULT_OLLAMA_MODEL, help="Ollama model tag (default: %(default)s)")
    parser.add_argument("--base-url", default=DEFAULT_OLLAMA_URL)
    args = parser.parse_args(argv)

    try:
        result = ask(args.question, OllamaConfig(base_url=args.base_url, model=args.model))
    except AgentError as exc:
        print(f"Error: {exc}")
        return 1

    for i, q in enumerate(result.queries, start=1):
        print(f"--- Query {i} ({'ok' if q.ok else 'error'}) ---\n{q.sql}")
        print(format_for_llm(q.result) if q.ok else f"ERROR: {q.error}")
    print(f"\n=== Answer ({result.model}, {result.elapsed_seconds}s, attempt {result.attempts}) ===\n{result.answer}")
    if not result.grounded:
        print("\nWarning: no query succeeded, so this answer is not backed by data.")
    elif result.unverified_figures:
        print(f"\nWarning: figures not found in any query result: {', '.join(result.unverified_figures)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
