"""Tests for the Streamlit app layer: data access, simulator math, charts, and a full-app smoke run.

Everything runs against the fixture warehouse (small seeded dataset + real loader + dbt build).
"""

from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from app import charts, data
from app import simulator as sim
from app.simulator import Scenario

from tests.conftest import SMALL_CONFIG  # noqa: E402

# The fixture warehouse is built with the small config's as-of date.
AS_OF = SMALL_CONFIG.as_of


@pytest.fixture(scope="module")
def quarter_end():
    return data.current_quarter_bounds(AS_OF)[1]


@pytest.fixture(scope="module")
def deals(marts_db):
    return data.load_open_deals(marts_db)


@pytest.fixture(scope="module")
def targets(marts_db):
    return data.load_segment_targets(marts_db)


# --- data -------------------------------------------------------------------


@pytest.mark.parametrize(
    "as_of, expected",
    [
        (date(2026, 2, 14), (date(2026, 1, 1), date(2026, 4, 1))),
        (date(2026, 6, 30), (date(2026, 4, 1), date(2026, 7, 1))),
        (date(2026, 8, 15), (date(2026, 7, 1), date(2026, 10, 1))),
        (date(2026, 11, 2), (date(2026, 10, 1), date(2027, 1, 1))),
    ],
)
def test_current_quarter_bounds(as_of, expected):
    assert data.current_quarter_bounds(as_of) == expected


def test_quarter_label():
    assert data.quarter_label(date(2026, 8, 15)) == "2026-Q3"


def test_marts_ready_and_missing(marts_db, raw_dir, tmp_path):
    from pipelines.load_to_duckdb import load

    assert data.missing_marts(marts_db) == []
    raw_only = tmp_path / "raw_only.duckdb"
    load(raw_dir, raw_only)
    assert data.missing_marts(raw_only) == list(data.REQUIRED_MARTS)


def test_kpis_match_direct_sql(marts_db):
    k = data.load_kpis(marts_db)
    direct = data.query(
        marts_db,
        "SELECT sum(open_pipeline_amount) AS p, sum(stalled_deal_count) AS s, count(*) AS n "
        "FROM marts.dim_sales_performance",
    ).iloc[0]
    assert k["open_pipeline"] == pytest.approx(float(direct["p"]))
    assert k["stalled_deals"] == direct["s"]
    assert k["reps"] == direct["n"]
    assert k["attainment_crm"] == pytest.approx(k["projected_crm"] / k["quarterly_quota"])


def test_segment_filter_partitions_totals(marts_db):
    total = data.load_kpis(marts_db)["open_pipeline"]
    parts = sum(data.load_kpis(marts_db, seg)["open_pipeline"] for seg in data.TIER_ORDER)
    assert parts == pytest.approx(total)


def test_funnel_is_ordered_and_excludes_rollup(marts_db):
    funnel = data.load_funnel(marts_db)
    assert "All" not in set(funnel["account_tier"].astype(str))
    first = funnel[funnel["account_tier"] == "SMB"]["stage_name"].tolist()
    assert first == data.STAGE_ORDER
    assert data.load_funnel(marts_db, "Enterprise")["account_tier"].astype(str).unique().tolist() == ["Enterprise"]


def test_stalled_deals_match_flag(marts_db):
    stalled = data.load_stalled_deals(marts_db)
    assert len(stalled) == data.load_kpis(marts_db)["stalled_deals"]
    assert (stalled["days_in_current_stage"] > 45).all()


# --- simulator --------------------------------------------------------------


def _total(projection: pd.DataFrame) -> pd.Series:
    return projection.set_index("rep_segment").loc["Total"]


def test_zero_scenario_reconciles_to_mart(marts_db, deals, targets, quarter_end):
    """No levers moved = the dbt mart's projected bookings (CRM basis to the cent)."""
    mart = data.query(
        marts_db,
        "SELECT sum(projected_bookings)::DOUBLE AS crm, sum(projected_bookings_historical)::DOUBLE AS hist "
        "FROM marts.dim_sales_performance",
    ).iloc[0]
    crm = _total(sim.project_by_segment(deals, targets, Scenario(basis="crm"), AS_OF, quarter_end))
    hist = _total(sim.project_by_segment(deals, targets, Scenario(basis="historical"), AS_OF, quarter_end))
    assert crm["scenario_projected"] == pytest.approx(mart["crm"], abs=0.01)
    # The mart rounds each historical-weighted deal to cents; allow that rounding to accumulate.
    assert hist["scenario_projected"] == pytest.approx(mart["hist"], abs=1.0)
    assert crm["deals_slipped"] == 0


def test_longer_cycles_never_increase_bookings(deals, targets, quarter_end):
    previous = None
    for change in (0.0, 0.25, 0.5, 1.0):
        total = _total(sim.project_by_segment(deals, targets, Scenario(cycle_change=change), AS_OF, quarter_end))
        if previous is not None:
            assert total["scenario_projected"] <= previous["scenario_projected"] + 1e-6
            assert total["deals_slipped"] >= previous["deals_slipped"]
        previous = total


def test_zero_win_rate_leaves_only_won_qtd(deals, targets, quarter_end):
    total = _total(sim.project_by_segment(deals, targets, Scenario(win_rate_change=-1.0), AS_OF, quarter_end))
    assert total["scenario_projected"] == pytest.approx(total["won_qtd"])


def test_probabilities_are_clipped_to_one(deals, quarter_end):
    applied = sim.apply_scenario(deals, Scenario(win_rate_change=1.0), AS_OF, quarter_end)
    assert applied["sim_probability"].between(0, 1).all()


def test_write_off_removes_exactly_stalled_in_quarter_value(deals, targets, quarter_end):
    base = sim.apply_scenario(deals, Scenario(), AS_OF, quarter_end)
    stalled_value = base.loc[base["is_stalled"], "sim_expected"].sum()
    before = _total(sim.project_by_segment(deals, targets, Scenario(), AS_OF, quarter_end))
    after = _total(sim.project_by_segment(deals, targets, Scenario(write_off_stalled=True), AS_OF, quarter_end))
    assert before["scenario_projected"] - after["scenario_projected"] == pytest.approx(stalled_value)


def test_required_win_rate_change_hits_quota(deals, targets, quarter_end):
    # The small fixture runs ~74% of quota on CRM weights but can reach it, so the bisection is exercised.
    scenario = Scenario(basis="crm")
    needed = sim.required_win_rate_change(deals, targets, scenario, AS_OF, quarter_end)
    assert needed not in (None, 0.0), "fixture scenario should start short of quota but be reachable"
    at = _total(sim.project_by_segment(
        deals, targets, Scenario(needed, 0.0, "crm"), AS_OF, quarter_end))
    below = _total(sim.project_by_segment(
        deals, targets, Scenario(needed - 0.01, 0.0, "crm"), AS_OF, quarter_end))
    assert at["scenario_projected"] >= at["quarterly_quota"]
    assert below["scenario_projected"] < below["quarterly_quota"]


@pytest.mark.parametrize(
    "kwargs", [{"basis": "gut-feel"}, {"win_rate_change": -1.5}, {"cycle_change": -1.0}]
)
def test_scenario_rejects_invalid_levers(kwargs):
    with pytest.raises(ValueError):
        Scenario(**kwargs)


# --- charts -----------------------------------------------------------------


@pytest.mark.parametrize("mode", ["light", "dark"])
def test_all_charts_build(marts_db, deals, targets, quarter_end, mode):
    funnel = data.load_funnel(marts_db)
    reps = data.load_rep_performance(marts_db)
    projection = sim.project_by_segment(deals, targets, Scenario(), AS_OF, quarter_end)
    for fig in (
        charts.funnel_reach(funnel, mode),
        charts.stage_duration(funnel, mode),
        charts.stalled_by_rep(reps, mode),
        charts.revenue_gap_by_rep(reps, mode, "historical"),
        charts.scenario_attainment(projection, mode),
    ):
        assert fig.data, "figure has no traces"
        assert fig.layout.yaxis.automargin and fig.layout.xaxis.automargin


def test_tier_color_follows_entity_not_rank(marts_db):
    """Filtering to one tier must not repaint it with slot 1's color."""
    only_enterprise = charts.funnel_reach(data.load_funnel(marts_db, "Enterprise"), "light")
    assert [t.marker.color for t in only_enterprise.data] == [charts.PALETTES["light"]["tiers"]["Enterprise"]]


def test_gap_colors_encode_sign(marts_db):
    reps = data.load_rep_performance(marts_db)
    fig = charts.revenue_gap_by_rep(reps, "light")
    p = charts.PALETTES["light"]
    for value, color in zip(fig.data[0].x, fig.data[0].marker.color):
        assert color == (p["short"] if value > 0 else p["ahead"])


@pytest.mark.parametrize("value, text", [(1_373_500, "$1.37M"), (212_400, "$212k"), (950, "$950"), (-95_000, "-$95k")])
def test_money_format(value, text):
    assert charts.money(value) == text


# --- full app smoke test ----------------------------------------------------


def test_app_renders_all_tabs_and_simulator_reacts(marts_db, monkeypatch):
    from streamlit.testing.v1 import AppTest

    monkeypatch.setenv("CRM_DB_PATH", str(marts_db))
    app_path = Path(__file__).resolve().parent.parent / "app" / "main.py"
    at = AppTest.from_file(str(app_path), default_timeout=60).run()
    assert not at.exception, at.exception
    assert len(at.tabs) == 3
    labels = [m.label for m in at.metric]
    assert {"Open pipeline", "Won QTD", "Stalled deals", "Scenario projected", "Deals slipping"} <= set(labels)

    def slipping() -> str:
        return next(m.value for m in at.metric if m.label == "Deals slipping")

    assert slipping() == "0"
    cycle = next(s for s in at.slider if s.label == "Sales-cycle change")
    cycle.set_value(100).run()
    assert not at.exception, at.exception
    assert int(slipping()) > 0


def test_unreachable_quota_returns_none(deals, quarter_end):
    """If even winning every in-quarter deal falls short, say so instead of returning a number."""
    huge = pd.DataFrame({"rep_segment": ["SMB", "Mid-Market", "Enterprise"],
                         "quarterly_quota": [1e12, 1e12, 1e12], "won_qtd": [0.0, 0.0, 0.0]})
    assert sim.required_win_rate_change(deals, huge, Scenario(), AS_OF, quarter_end) is None
