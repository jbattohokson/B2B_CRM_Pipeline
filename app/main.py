"""Streamlit app: Executive KPI Dashboard, Local AI Revenue Copilot, Pipeline Risk Simulator.

Run from the project root:
    .venv/bin/streamlit run app/main.py

Everything reads the warehouse read-only (see app/data.py). The copilot talks to
a local Ollama server through agent/crew_agent.py; nothing leaves the machine.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Streamlit puts app/ on sys.path, not the project root; the agent and app packages live there.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from agent.crew_agent import (  # noqa: E402
    DEFAULT_OLLAMA_MODEL,
    DEFAULT_OLLAMA_URL,
    AgentError,
    OllamaConfig,
    OllamaUnavailableError,
    ask,
    check_ollama,
)
from agent.sql_tool import QueryError  # noqa: E402
from app import charts, data  # noqa: E402
from app import simulator as sim  # noqa: E402
from datetime import timedelta  # noqa: E402

st.set_page_config(page_title="Pipeline Velocity & Forecast", page_icon="📈", layout="wide")

SEGMENTS = ["All"] + data.TIER_ORDER
SUGGESTED_QUESTIONS = [
    "Which reps have deals stuck in negotiation over 45 days?",
    "Which rep has the largest expected revenue gap this quarter?",
    "What is the stage-to-stage conversion rate out of Demo for Enterprise accounts?",
    "How does average sales cycle differ by account tier for won deals?",
]


# ---------------------------------------------------------------------------
# Cached data access (keyed on the db path so tests and the real DB don't mix)
# ---------------------------------------------------------------------------


@st.cache_data(show_spinner=False)
def cached_kpis(db: str, segment: str) -> dict:
    return data.load_kpis(db, segment)


@st.cache_data(show_spinner=False)
def cached_funnel(db: str, segment: str) -> pd.DataFrame:
    return data.load_funnel(db, segment)


@st.cache_data(show_spinner=False)
def cached_reps(db: str, segment: str) -> pd.DataFrame:
    return data.load_rep_performance(db, segment)


@st.cache_data(show_spinner=False)
def cached_stalled(db: str, segment: str) -> pd.DataFrame:
    return data.load_stalled_deals(db, segment)


@st.cache_data(show_spinner=False)
def cached_open_deals(db: str) -> pd.DataFrame:
    return data.load_open_deals(db)


@st.cache_data(show_spinner=False)
def cached_targets(db: str) -> pd.DataFrame:
    return data.load_segment_targets(db)


@st.cache_data(ttl=30, show_spinner=False)
def ollama_models(base_url: str) -> tuple[list[str], str | None]:
    """(installed model names, error message or None). Probed with a dummy model name."""
    try:
        return check_ollama(OllamaConfig(base_url=base_url, model="__probe__")), None
    except OllamaUnavailableError as exc:
        message = str(exc)
        if "isn't pulled" in message:  # server is up; parse the installed list from the probe error
            installed = message.split("installed: ", 1)[1].split(")", 1)[0]
            return ([] if installed == "none" else [m.strip() for m in installed.split(",")]), None
        return [], message


def theme_mode() -> str:
    theme = getattr(st.context, "theme", None)
    return "dark" if theme is not None and theme.type == "dark" else "light"


TILE_WIDTH = 170  # px; tiles wrap onto new rows instead of truncating on narrow screens


def tile(container, label: str, value: str, note: str | None = None, *, change: bool = False, help: str | None = None):
    """A bordered KPI tile. note is descriptive (no arrow) unless change=True."""
    container.metric(label, value, note, delta_color="normal" if change else "off",
                     delta_arrow="auto" if change else "off", help=help, border=True, width=TILE_WIDTH)


def table_view(df: pd.DataFrame, label: str = "Table view") -> None:
    with st.expander(label):
        st.dataframe(df, hide_index=True, use_container_width=True)


# ---------------------------------------------------------------------------
# Sidebar and preflight
# ---------------------------------------------------------------------------

db_path = str(data.default_db_path())
as_of = data.as_of_date()
quarter_start, quarter_end = data.current_quarter_bounds(as_of)
quarter = data.quarter_label(as_of)
mode = theme_mode()

with st.sidebar:
    st.header("Pipeline Velocity")
    st.caption(f"As of **{as_of:%b %d, %Y}** · forecasting **{quarter}** "
               f"(ends {quarter_end - timedelta(days=1):%b %d}).")
    st.caption("Synthetic, seeded CRM data. See the README's Data honesty section.")
    st.divider()
    st.subheader("Local AI model")
    models, ollama_error = ollama_models(DEFAULT_OLLAMA_URL)
    if ollama_error:
        st.error(ollama_error, icon="⚠️")
        model_choice = DEFAULT_OLLAMA_MODEL
    elif not models:
        st.warning(f"Ollama is running but has no models. Run `ollama pull {DEFAULT_OLLAMA_MODEL}`.", icon="⚠️")
        model_choice = DEFAULT_OLLAMA_MODEL
    else:
        names = sorted({m.split(":")[0] if m.endswith(":latest") else m for m in models})
        default_index = names.index(DEFAULT_OLLAMA_MODEL) if DEFAULT_OLLAMA_MODEL in names else 0
        model_choice = st.selectbox("Model", names, index=default_index,
                                    help="llama3.2 (3B) runs on 8 GB RAM; llama3.1 (8B) needs 16 GB+.")
        st.caption(f"✅ Ollama connected at {DEFAULT_OLLAMA_URL}")

try:
    missing = data.missing_marts(db_path)
except QueryError as exc:
    st.error(f"{exc}", icon="⚠️")
    st.stop()
if missing:
    st.error(
        f"The warehouse is missing {', '.join(missing)}. Build it from the project root:\n\n"
        "```\n.venv/bin/python pipelines/generate_data.py\n.venv/bin/python pipelines/load_to_duckdb.py\n"
        "DBT_PROFILES_DIR=. .venv/bin/dbt build\n```",
        icon="⚠️",
    )
    st.stop()

st.title("B2B SaaS Pipeline Velocity & Forecast")
tab_kpi, tab_copilot, tab_sim = st.tabs(["📊 Executive KPIs", "🤖 AI Revenue Copilot", "🎛️ Pipeline Risk Simulator"])


# ---------------------------------------------------------------------------
# Tab 1: Executive KPI dashboard
# ---------------------------------------------------------------------------

with tab_kpi:
    segment = st.segmented_control("Segment", SEGMENTS, default="All", key="kpi_segment") or "All"
    k = cached_kpis(db_path, segment)

    row = st.container(horizontal=True, gap="small")
    tile(row, "Open pipeline", charts.money(k["open_pipeline"]), f"{int(k['open_deals'])} open deals")
    tile(row, "Won QTD", charts.money(k["won_qtd"]), f"of {charts.money(k['quarterly_quota'])} quota")
    tile(row, "Forecast · CRM", f"{k['attainment_crm']:.0%}", f"{k['attainment_crm'] - 1:+.0%} vs quota",
         change=True,
         help="(Won QTD + Σ amount × CRM stage probability for deals closing this quarter) ÷ quarterly quota")
    tile(row, "Forecast · history", f"{k['attainment_historical']:.0%}",
         f"{k['attainment_historical'] - 1:+.0%} vs quota", change=True,
         help="Same, but weighting each deal by the historical win rate from its stage and tier")
    tile(row, "Stalled deals", f"{int(k['stalled_deals'])}", f"{charts.money(k['stalled_pipeline'])} at risk",
         help="Open, in Negotiation for more than 45 days")

    funnel = cached_funnel(db_path, segment)
    gap = k["attainment_crm"] - k["attainment_historical"]
    if gap > 0.02:
        negotiation = funnel[funnel["stage_name"] == "Negotiation"]
        hist_win = (negotiation["historical_win_rate_from_stage"] * negotiation["deals_entered"]).sum() / max(
            negotiation["deals_entered"].sum(), 1
        )
        st.info(
            f"The CRM's stage probabilities project **{gap:.0%} more attainment** than this pipeline has "
            f"historically converted at. Late stages are over-weighted (Negotiation: 70% in the CRM vs "
            f"{hist_win:.0%} historically). Plan against the historical number.",
            icon="ℹ️",
        )

    left, right = st.columns(2)
    with left:
        st.subheader("Funnel: how far deals get")
        st.caption("Share of all created deals that reached each stage, by account tier.")
        st.plotly_chart(charts.funnel_reach(funnel, mode), use_container_width=True, theme=None)
        table_view(funnel[["account_tier", "stage_name", "deals_entered", "pct_of_created_reaching_stage",
                           "conversion_rate", "historical_win_rate_from_stage"]])
    with right:
        st.subheader("Velocity: days spent per stage")
        st.caption("Average days in each stage for deals that left it. Hover for the median: "
                   "Negotiation's long tail is stalled deals.")
        st.plotly_chart(charts.stage_duration(funnel, mode), use_container_width=True, theme=None)
        table_view(funnel[["account_tier", "stage_name", "avg_days_in_stage", "median_days_in_stage"]])

    reps = cached_reps(db_path, segment)
    left, right = st.columns(2)
    with left:
        st.subheader("At-risk deals by rep")
        st.caption("Unweighted pipeline stuck in Negotiation more than 45 days.")
        if (reps["stalled_deal_count"] > 0).any():
            st.plotly_chart(charts.stalled_by_rep(reps, mode), use_container_width=True, theme=None)
        else:
            st.success("No stalled deals in this segment.", icon="✅")
        table_view(cached_stalled(db_path, segment), "Stalled deals (table view)")
    with right:
        st.subheader(f"{quarter} revenue gap by rep")
        basis = st.radio("Weighting", ["CRM stage probabilities", "Historical win rates"], horizontal=True,
                         key="gap_basis")
        basis_key = "crm" if basis.startswith("CRM") else "historical"
        st.caption("Quarterly quota − (won QTD + weighted pipeline closing this quarter). "
                   "Red = projected shortfall, blue = ahead.")
        st.plotly_chart(charts.revenue_gap_by_rep(reps, mode, basis_key), use_container_width=True, theme=None)
        table_view(reps[["rep_name", "rep_segment", "quarterly_quota", "won_qtd", "forecast_weighted_pipeline",
                         "expected_revenue_gap", "expected_revenue_gap_historical", "projected_attainment"]])


# ---------------------------------------------------------------------------
# Tab 2: Local AI Revenue Copilot
# ---------------------------------------------------------------------------


def render_analysis(result) -> None:
    st.markdown(result.answer or "_The model returned an empty answer._")
    if not result.grounded:
        st.error("No query succeeded, so this answer isn't backed by data. Try rephrasing.", icon="⚠️")
    elif result.unverified_figures:
        st.warning(
            "These figures don't match any value in the query results and may be invented by the model: "
            + ", ".join(f"`{f}`" for f in result.unverified_figures),
            icon="⚠️",
        )
    st.caption(f"{result.model} · {result.elapsed_seconds:.0f}s · {len(result.queries)} quer"
               f"{'y' if len(result.queries) == 1 else 'ies'} · attempt {result.attempts}")
    for i, q in enumerate(result.queries, start=1):
        with st.expander(f"Query {i} {'✅' if q.ok else '❌'}", expanded=q.ok and q is result.successful_queries[-1]):
            st.code(q.sql, language="sql")
            if q.ok:
                st.dataframe(q.result.dataframe, hide_index=True, use_container_width=True)
                if q.result.truncated:
                    st.caption("Result capped at 200 rows.")
            else:
                st.error(q.error)


with tab_copilot:
    st.caption(
        f"Ask in plain English. A local **{model_choice}** model writes read-only SQL against the dbt marts, "
        "runs it, and summarizes. Expect about 45–110 s per answer on a laptop CPU. Every query and its "
        "result table is shown, and figures that don't match the data are flagged."
    )
    history = st.session_state.setdefault("chat", [])

    cols = st.columns(len(SUGGESTED_QUESTIONS))
    clicked = None
    for col, q in zip(cols, SUGGESTED_QUESTIONS):
        if col.button(q, use_container_width=True, disabled=bool(ollama_error)):
            clicked = q

    for turn in history:
        with st.chat_message("user"):
            st.markdown(turn["question"])
        with st.chat_message("assistant"):
            if "error" in turn:
                st.error(turn["error"], icon="⚠️")
            else:
                render_analysis(turn["result"])

    typed = st.chat_input("Ask about pipeline, forecast, or reps…", disabled=bool(ollama_error))
    question = clicked or typed
    if question:
        with st.chat_message("user"):
            st.markdown(question)
        with st.chat_message("assistant"):
            with st.status(f"Asking {model_choice}…", expanded=False) as status:
                try:
                    result = ask(question, OllamaConfig(model=model_choice), db_path=db_path)
                    status.update(label=f"Answered in {result.elapsed_seconds:.0f}s", state="complete")
                    turn = {"question": question, "result": result}
                except AgentError as exc:
                    status.update(label="Failed", state="error")
                    turn = {"question": question, "error": str(exc)}
            if "error" in turn:
                st.error(turn["error"], icon="⚠️")
            else:
                render_analysis(turn["result"])
        history.append(turn)

    if history and st.button("Clear conversation"):
        st.session_state["chat"] = []
        st.rerun()


# ---------------------------------------------------------------------------
# Tab 3: Pipeline risk simulator
# ---------------------------------------------------------------------------

with tab_sim:
    st.caption(
        f"Re-project **{quarter}** bookings: won-to-date plus each open deal's amount × win probability, "
        "counting only deals that still close before quarter end. The baseline uses the same weighting "
        "with no changes, so the deltas show the effect of the levers alone."
    )
    controls, results = st.columns([1, 2.2], gap="large")
    with controls:
        basis_label = st.radio("Win probability source", ["Historical win rates", "CRM stage probabilities"],
                               key="sim_basis",
                               help="Historical = conversion actually observed from each stage and tier.")
        win_change = st.slider("Win-rate change", -50, 50, 0, step=5, format="%d%%",
                               help="Relative change to every open deal's win probability.")
        cycle_change = st.slider("Sales-cycle change", -30, 100, 0, step=5, format="%d%%",
                                 help="Relative change to each open deal's remaining time to close. "
                                      "Longer cycles push deals past quarter end.")
        write_off = st.toggle("Write off stalled negotiations",
                              help="Treat open deals stuck in Negotiation more than 45 days as lost.")

    scenario = sim.Scenario(
        win_rate_change=win_change / 100,
        cycle_change=cycle_change / 100,
        basis="historical" if basis_label.startswith("Historical") else "crm",
        write_off_stalled=write_off,
    )
    deals = cached_open_deals(db_path)
    targets = cached_targets(db_path)
    projection = sim.project_by_segment(deals, targets, scenario, as_of, quarter_end)
    total = projection.set_index("rep_segment").loc["Total"]

    with results:
        st.caption("Changes are relative to the baseline (same weighting, no levers moved).")
        row = st.container(horizontal=True, gap="small")
        delta_money = total["scenario_projected"] - total["baseline_projected"]
        delta_pts = (total["scenario_attainment"] - total["baseline_attainment"]) * 100
        moved = abs(delta_money) >= 1
        tile(row, "Scenario projected", charts.money(total["scenario_projected"]),
             (("+" if delta_money > 0 else "") + charts.money(delta_money)) if moved else "no change",
             change=moved)
        tile(row, "Scenario attainment", f"{total['scenario_attainment']:.0%}",
             f"{delta_pts:+.0f} pts" if moved else "no change", change=moved)
        gap_value = total["scenario_gap"]
        tile(row, "Gap to quota", charts.money(gap_value) if gap_value > 0 else "Covered",
             "shortfall" if gap_value > 0 else f"{charts.money(-gap_value)} cushion")
        tile(row, "Deals slipping", f"{int(total['deals_slipped'])}", "out of the quarter",
             help="Open deals that close this quarter in the baseline but not in the scenario.")

        needed = sim.required_win_rate_change(deals, targets, scenario, as_of, quarter_end)
        if gap_value > 0:
            if needed is None:
                st.warning("Even winning every deal still in the quarter wouldn't reach quota.", icon="⚠️")
            else:
                st.info(f"Under these assumptions, win rates would need to rise **{needed:+.0%}** "
                        "(relative) across the board to hit quota.", icon="ℹ️")

        st.plotly_chart(charts.scenario_attainment(projection, mode), use_container_width=True, theme=None)
        view = projection[["rep_segment", "quarterly_quota", "won_qtd", "baseline_projected", "scenario_projected",
                           "baseline_attainment", "scenario_attainment", "deals_slipped"]]
        table_view(view)
