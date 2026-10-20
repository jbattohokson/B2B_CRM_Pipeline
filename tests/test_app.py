"""Tests for the Streamlit app layer: data access, simulator math, charts, and a full-app smoke run.

Everything runs against the fixture warehouse (the real Olist CSVs + real loader + dbt build).
"""

from pathlib import Path

import duckdb
import pandas as pd
import pytest

from app import charts, data
from app import simulator as sim
from app.simulator import Scenario


@pytest.fixture(scope="module")
def sources(marts_db):
    return data.load_source_conversion(marts_db)


@pytest.fixture(scope="module")
def sources_2018(marts_db):
    return data.load_source_conversion(marts_db, "2018 leads")


def _scalar(db_path, sql):
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        return con.execute(sql).fetchone()[0]
    finally:
        con.close()


# --- data access ------------------------------------------------------------


def test_marts_ready(marts_db):
    assert data.missing_marts(marts_db) == []


def test_missing_marts_reported_on_raw_only_db(raw_dir, tmp_path):
    from pipelines.load_to_duckdb import load

    db = tmp_path / "raw_only.duckdb"
    load(raw_dir, db)
    assert data.missing_marts(db) == list(data.REQUIRED_MARTS)


def test_headline_kpis_match_the_real_data(marts_db):
    k = data.load_kpis(marts_db)
    assert k["mqls"] == 8000
    assert k["deals_won"] == 842
    assert k["conversion_rate"] == pytest.approx(842 / 8000)
    assert k["median_days_to_close"] == _scalar(marts_db, "SELECT median(days_to_close) FROM marts.fct_lead_funnel")


def test_2018_period_is_a_subset(marts_db):
    all_leads, recent = data.load_kpis(marts_db), data.load_kpis(marts_db, "2018 leads")
    expected = _scalar(marts_db, "SELECT count(*) FROM marts.fct_lead_funnel WHERE first_contact_date >= '2018-01-01'")
    assert recent["mqls"] == expected
    assert 0 < recent["mqls"] < all_leads["mqls"]
    assert recent["deals_won"] <= all_leads["deals_won"]


def test_source_conversion_reconciles_with_dbt_mart(marts_db, sources):
    assert sources["mqls"].sum() == 8000 and sources["deals_won"].sum() == 842
    assert sources["share_of_mqls"].sum() == pytest.approx(1.0)
    assert list(sources["conversion_rate"]) == sorted(sources["conversion_rate"], reverse=True)
    mart = data.query(marts_db, "SELECT lead_source, conversion_rate, conversion_rate_ci_low AS lo, "
                                "conversion_rate_ci_high AS hi FROM marts.agg_lead_source_conversion")
    merged = sources.merge(mart, on="lead_source", suffixes=("", "_mart"))
    assert len(merged) == len(sources) == 10
    assert merged["conversion_rate"].to_numpy() == pytest.approx(merged["conversion_rate_mart"].to_numpy())
    # The Python Wilson interval and the dbt macro use the same formula.
    assert merged["ci_low"].to_numpy() == pytest.approx(merged["lo"].to_numpy(), abs=1e-9)
    assert merged["ci_high"].to_numpy() == pytest.approx(merged["hi"].to_numpy(), abs=1e-9)


@pytest.mark.parametrize("wins, trials", [(0, 10), (5, 10), (10, 10), (842, 8000)])
def test_wilson_interval_brackets_the_rate(wins, trials):
    lo, hi = data.wilson_interval(wins, trials)
    eps = 1e-12  # (centre - spread) is 0 up to float rounding when wins == 0
    assert -eps <= lo <= wins / trials <= hi <= 1 + eps


def test_wilson_interval_handles_zero_trials():
    assert data.wilson_interval(0, 0) == (0.0, 0.0)


def test_rep_tables_sum_to_won_deals(marts_db):
    for frame in (data.load_sdrs(marts_db), data.load_reps(marts_db)):
        assert frame["deals_won"].sum() == 842
        assert frame["share_of_deals"].sum() == pytest.approx(1.0)
        assert frame["label"].is_unique


def test_cohorts_cover_every_lead(marts_db):
    cohorts = data.load_cohorts(marts_db)
    assert cohorts["mqls"].sum() == 8000 and cohorts["deals_won"].sum() == 842
    assert cohorts["cohort_month"].is_monotonic_increasing


# --- simulator --------------------------------------------------------------


def _total(projection: pd.DataFrame) -> pd.Series:
    return projection.set_index("lead_source").loc["Total"]


def test_historical_volume_reproduces_actual_deals(sources):
    """At the real total volume with no levers moved, expected deals = deals actually won."""
    projection = sim.project(sources, Scenario(monthly_mqls=float(sources["mqls"].sum())))
    total = _total(projection)
    assert total["scenario_deals"] == pytest.approx(842)
    assert total["baseline_deals"] == pytest.approx(842)
    assert total["scenario_conversion_rate"] == pytest.approx(842 / 8000)


def test_mix_shift_conserves_volume_and_moves_share(sources_2018):
    scen = Scenario(monthly_mqls=1000, mix_shift={("social", "paid_search"): 0.05})
    p = sim.project(sources_2018, scen).set_index("lead_source")
    assert p.loc["Total", "scenario_mqls"] == pytest.approx(1000)
    assert p.loc["paid_search", "scenario_mqls"] - p.loc["paid_search", "baseline_mqls"] == pytest.approx(50)
    assert p.loc["social", "baseline_mqls"] - p.loc["social", "scenario_mqls"] == pytest.approx(50)


def test_shift_cannot_exceed_source_share(sources_2018):
    hist = sources_2018.set_index("lead_source")["share_of_mqls"]
    shifted = sim.apply_mix_shift(hist, {("other", "paid_search"): 0.9})
    assert shifted["other"] == pytest.approx(0.0)
    assert shifted.sum() == pytest.approx(1.0)


def test_unknown_source_in_shift_raises(sources_2018):
    with pytest.raises(ValueError, match="Unknown lead source"):
        sim.project(sources_2018, Scenario(monthly_mqls=100, mix_shift={("fax", "social"): 0.1}))


def test_conversion_lift_scales_deals_and_range_brackets(sources_2018):
    base = _total(sim.project(sources_2018, Scenario(monthly_mqls=1000)))
    lifted = _total(sim.project(sources_2018, Scenario(monthly_mqls=1000, conversion_change=0.1)))
    assert lifted["scenario_deals"] == pytest.approx(base["scenario_deals"] * 1.1)
    assert lifted["scenario_deals_low"] <= lifted["scenario_deals"] <= lifted["scenario_deals_high"]


def test_mqls_needed_hits_target(sources_2018):
    projection = sim.project(sources_2018, Scenario(monthly_mqls=1000))
    needed = sim.mqls_needed(projection, 100)
    rate = _total(projection)["scenario_conversion_rate"]
    assert needed * rate >= 100 > (needed - 1) * rate


def test_mqls_needed_none_when_conversion_is_zero(sources_2018):
    projection = sim.project(sources_2018, Scenario(monthly_mqls=0))
    assert sim.mqls_needed(projection, 10) is None


@pytest.mark.parametrize("kwargs", [{"monthly_mqls": -1}, {"monthly_mqls": 1, "conversion_change": -1},
                                    {"monthly_mqls": 1, "mix_shift": {("a", "b"): -0.1}}])
def test_scenario_rejects_invalid_levers(kwargs):
    with pytest.raises(ValueError):
        Scenario(**kwargs)


def test_historical_monthly_mqls(sources_2018):
    assert sim.historical_monthly_mqls(sources_2018, months=5) == pytest.approx(sources_2018["mqls"].sum() / 5)


# --- charts -----------------------------------------------------------------


@pytest.mark.parametrize("mode", ["light", "dark"])
def test_all_charts_build(marts_db, sources, sources_2018, mode):
    cohorts = data.load_cohorts(marts_db)
    figs = [
        charts.source_conversion(sources, 842 / 8000, mode),
        charts.cohort_conversion(cohorts, mode),
        charts.cohort_cycle(cohorts, mode),
        charts.rep_leaderboard(data.load_sdrs(marts_db), mode),
        charts.rep_leaderboard(data.load_reps(marts_db), mode),
        charts.scenario_deals(sim.project(sources_2018, Scenario(monthly_mqls=1000)), mode),
    ]
    assert all(len(f.data) > 0 for f in figs)


@pytest.mark.parametrize("value, text", [(0.1053, "10.5%"), (0.0, "0.0%"), (1.0, "100.0%")])
def test_pct_format(value, text):
    assert charts.pct(value) == text


# --- full app smoke test ----------------------------------------------------


def test_app_renders_all_tabs_and_simulator_reacts(marts_db, monkeypatch):
    from streamlit.testing.v1 import AppTest

    monkeypatch.setenv("CRM_DB_PATH", str(marts_db))
    app_path = Path(__file__).resolve().parent.parent / "app" / "main.py"
    at = AppTest.from_file(str(app_path), default_timeout=60).run()
    assert not at.exception, at.exception
    assert len(at.tabs) == 3
    metrics = {m.label: m for m in at.metric}
    assert metrics["MQLs"].value == "8,000"
    assert metrics["Deals won"].value == "842"
    assert metrics["Conversion"].value == "10.5%"
    assert metrics["Expected deals / month"].delta == "no change"

    lift = next(s for s in at.slider if s.label == "Conversion change (all sources)")
    lift.set_value(20).run()
    assert not at.exception, at.exception
    delta = next(m.delta for m in at.metric if m.label == "Expected deals / month")
    assert delta.startswith("+") and "vs baseline" in delta
