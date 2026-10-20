"""Streamlit app: Funnel KPI Dashboard, Local AI Funnel Copilot, Lead Mix Simulator.

Run from the project root:
    .venv/bin/streamlit run app/main.py

Everything reads the warehouse read-only (see app/data.py). The copilot talks to
a local Ollama server through agent/crew_agent.py; nothing leaves the machine.
The data is Olist's real (anonymized) seller-acquisition funnel.
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

st.set_page_config(page_title="Lead Funnel Conversion", page_icon="📈", layout="wide")

SUGGESTED_QUESTIONS = [
    "Which lead source converts MQLs into deals best?",
    "Which SDR closed the most deals, and how fast?",
    "How did conversion change between 2017 and 2018 lead cohorts?",
    "Which business segments close fastest?",
]


# ---------------------------------------------------------------------------
# Cached data access (keyed on the db path so tests and the real DB don't mix)
# ---------------------------------------------------------------------------


@st.cache_data(show_spinner=False)
def cached_kpis(db: str, period: str) -> dict:
    return data.load_kpis(db, period)


@st.cache_data(show_spinner=False)
def cached_sources(db: str, period: str) -> pd.DataFrame:
    return data.load_source_conversion(db, period)


@st.cache_data(show_spinner=False)
def cached_cohorts(db: str) -> pd.DataFrame:
    return data.load_cohorts(db)


@st.cache_data(show_spinner=False)
def cached_sdrs(db: str) -> pd.DataFrame:
    return data.load_sdrs(db)


@st.cache_data(show_spinner=False)
def cached_reps(db: str) -> pd.DataFrame:
    return data.load_reps(db)


@st.cache_data(show_spinner=False)
def cached_segments(db: str) -> pd.DataFrame:
    return data.load_segments(db)


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
mode = theme_mode()

with st.sidebar:
    st.header("Lead Funnel")
    st.caption(f"Olist seller-acquisition funnel · data through **{as_of:%b %d, %Y}**.")
    st.caption("Real, anonymized data (CC BY-NC-SA 4.0). See the README's Data honesty section.")
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
        "```\n.venv/bin/python pipelines/load_to_duckdb.py\nDBT_PROFILES_DIR=. .venv/bin/dbt build\n```",
        icon="⚠️",
    )
    st.stop()

st.title("B2B Lead Funnel: Conversion, Speed & Rep Output")
tab_kpi, tab_copilot, tab_sim = st.tabs(["📊 Funnel KPIs", "🤖 AI Funnel Copilot", "🎛️ Lead Mix Simulator"])


# ---------------------------------------------------------------------------
# Tab 1: Funnel KPI dashboard
# ---------------------------------------------------------------------------

with tab_kpi:
    period = st.segmented_control("Leads", list(data.PERIODS), default="All leads", key="kpi_period",
                                  help="2018 leads arrived after the sales team started closing deals "
                                       "(December 2017). Earlier leads mostly closed months later.") or "All leads"
    k = cached_kpis(db_path, period)

    row = st.container(horizontal=True, gap="small")
    tile(row, "MQLs", f"{int(k['mqls']):,}", "marketing-qualified leads")
    tile(row, "Deals won", f"{int(k['deals_won']):,}", f"{int(k['active_sdrs'])} SDRs · {int(k['active_reps'])} reps")
    tile(row, "Conversion", charts.pct(k["conversion_rate"]), "MQL to signed seller",
         help="Won leads ÷ all leads in the selected period")
    tile(row, "Median days to close", f"{k['median_days_to_close']:.0f}", "first contact to signed")
    tile(row, "Closed within 30 days", charts.pct(k["share_won_within_30_days"], 0), "of won deals")

    sources = cached_sources(db_path, period)
    st.subheader("Which lead sources convert")
    st.caption("MQL-to-deal conversion by source, with 95% intervals. Blue beats the overall rate, red trails it. "
               "Small sources have wide intervals: don't over-read them.")
    st.plotly_chart(charts.source_conversion(sources, k["conversion_rate"], mode), use_container_width=True,
                    theme=None)
    table_view(sources[["lead_source", "mqls", "deals_won", "conversion_rate", "ci_low", "ci_high",
                        "median_days_to_close"]])

    cohorts = cached_cohorts(db_path)
    left, right = st.columns(2)
    with left:
        st.subheader("Conversion by lead cohort")
        st.caption("Month of first contact. The 90-day bar only appears once every lead in the cohort is 90+ "
                   "days old. Look at the jump in January 2018.")
        st.plotly_chart(charts.cohort_conversion(cohorts, mode), use_container_width=True, theme=None)
        table_view(cohorts)
    with right:
        st.subheader("Speed to close by cohort")
        st.caption("Median days from first contact to signed, won deals only. 2017 leads waited months for "
                   "the sales team to reach them.")
        st.plotly_chart(charts.cohort_cycle(cohorts, mode), use_container_width=True, theme=None)

    left, right = st.columns(2)
    with left:
        st.subheader("SDR output")
        st.caption("Deals won per SDR (top 15). The data only names reps on won deals, so this is volume "
                   "and speed, not a conversion rate.")
        sdrs = cached_sdrs(db_path)
        st.plotly_chart(charts.rep_leaderboard(sdrs, mode), use_container_width=True, theme=None)
        table_view(sdrs)
    with right:
        st.subheader("Closer output")
        st.caption("Deals won per sales rep (top 15), same caveat.")
        reps = cached_reps(db_path)
        st.plotly_chart(charts.rep_leaderboard(reps, mode), use_container_width=True, theme=None)
        table_view(reps)
    table_view(cached_segments(db_path), "Won deals by business segment (table view)")


# ---------------------------------------------------------------------------
# Tab 2: Local AI Funnel Copilot
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
        f"Ask in plain English. A local **{model_choice}** model writes read-only SQL against the funnel marts, "
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

    typed = st.chat_input("Ask about leads, conversion, or reps…", disabled=bool(ollama_error))
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
# Tab 3: Lead mix simulator
# ---------------------------------------------------------------------------

with tab_sim:
    st.caption(
        "Plan next month's lead volume. Expected deals = planned MQLs from each source × that source's "
        "conversion rate from 2018 leads (after the sales team ramped). The baseline keeps today's mix at "
        "your volume, so the deltas show the effect of the levers alone."
    )
    sim_sources = cached_sources(db_path, "2018 leads")
    hist_monthly = sim.historical_monthly_mqls(sim_sources, months=5)
    source_names = list(sim_sources["lead_source"])

    controls, results = st.columns([1, 2.2], gap="large")
    with controls:
        volume = st.slider("MQLs per month", 200, 3000, int(round(hist_monthly, -1)), step=50,
                           help=f"2018 averaged {hist_monthly:,.0f} MQLs a month.")
        st.markdown("**Shift the mix**")
        from_source = st.selectbox("Move volume from", source_names,
                                   index=source_names.index("social") if "social" in source_names else 0)
        to_source = st.selectbox("…to", source_names,
                                 index=source_names.index("paid_search") if "paid_search" in source_names else 0)
        shift = st.slider("Share of total MQLs to move", 0, 30, 0, step=1, format="%d pts")
        lift = st.slider("Conversion change (all sources)", -30, 50, 0, step=5, format="%d%%",
                         help="Relative change, e.g. faster SDR follow-up. +10% turns 12% into 13.2%.")

    mix = {(from_source, to_source): shift / 100} if shift and from_source != to_source else {}
    scenario = sim.Scenario(monthly_mqls=volume, mix_shift=mix, conversion_change=lift / 100)
    projection = sim.project(sim_sources, scenario)
    total = projection.set_index("lead_source").loc["Total"]

    with results:
        row = st.container(horizontal=True, gap="small")
        delta = total["scenario_deals"] - total["baseline_deals"]
        moved = abs(delta) >= 0.05
        tile(row, "Expected deals / month", f"{total['scenario_deals']:.0f}",
             f"{delta:+.1f} vs baseline" if moved else "no change", change=moved)
        tile(row, "Planning range", f"{total['scenario_deals_low']:.0f}-{total['scenario_deals_high']:.0f}",
             "from 95% source intervals")
        tile(row, "Blended conversion", charts.pct(total["scenario_conversion_rate"]),
             f"baseline {charts.pct(total['conversion_rate'])}")
        target = st.number_input("Deal target per month", min_value=1, value=int(round(total["baseline_deals"] * 1.2)))
        needed = sim.mqls_needed(projection, target)
        if needed is not None:
            st.info(f"At this mix and conversion, **{target} deals** a month needs about **{needed:,} MQLs**.",
                    icon="ℹ️")
        st.plotly_chart(charts.scenario_deals(projection, mode), use_container_width=True, theme=None)
        table_view(projection)
