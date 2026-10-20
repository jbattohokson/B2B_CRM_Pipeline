"""Pipeline risk simulator: re-project current-quarter bookings under what-if assumptions.

Projected bookings = won QTD + sum(amount x win probability) over open deals
expected to close by quarter end. This is the same definition as
dim_sales_performance, so a zero-change scenario reproduces the mart's numbers
exactly (a test checks this).

The levers:
- win_rate_change: relative change to every open deal's win probability
  (+10% turns 0.40 into 0.44), clipped to [0, 1].
- cycle_change: relative change to the time each open deal has left before it
  closes. +50% means a deal expected in 20 days now lands in 30, which can push
  it past quarter end and out of this quarter's forecast.
- basis: weight deals by the CRM's stage probabilities ("crm") or by historical
  win rates from each stage and tier ("historical").
- write_off_stalled: treat deals stalled in Negotiation as lost (probability 0).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd

BASES = {"crm": "crm_probability", "historical": "historical_probability"}


@dataclass(frozen=True)
class Scenario:
    win_rate_change: float = 0.0   # -1.0 .. +1.0 (relative)
    cycle_change: float = 0.0      # -0.5 .. +1.0 (relative to days remaining)
    basis: str = "crm"
    write_off_stalled: bool = False

    def __post_init__(self) -> None:
        if self.basis not in BASES:
            raise ValueError(f"basis must be one of {list(BASES)}")
        if self.win_rate_change < -1:
            raise ValueError("win_rate_change can't go below -100%")
        if self.cycle_change <= -1:
            raise ValueError("cycle_change must be above -100%")


def apply_scenario(open_deals: pd.DataFrame, scenario: Scenario, as_of: date, quarter_end: date) -> pd.DataFrame:
    """Per-deal scenario columns: probability, simulated close date, in-quarter flag, expected value."""
    deals = open_deals.copy()
    prob = deals[BASES[scenario.basis]].astype(float) * (1 + scenario.win_rate_change)
    if scenario.write_off_stalled:
        prob = prob.where(~deals["is_stalled"].astype(bool), 0.0)
    deals["sim_probability"] = prob.clip(0.0, 1.0)

    close = pd.to_datetime(deals["close_date"])
    days_left = (close - pd.Timestamp(as_of)).dt.days.clip(lower=0)
    # Ceil, so a stretched cycle never pulls a deal earlier through rounding.
    sim_days = np.ceil(days_left * (1 + scenario.cycle_change)).astype(int)
    deals["sim_close_date"] = (pd.Timestamp(as_of) + pd.to_timedelta(sim_days, unit="D")).dt.date
    deals["sim_in_quarter"] = (deals["sim_close_date"] > as_of) & (deals["sim_close_date"] < quarter_end)
    deals["sim_expected"] = np.where(deals["sim_in_quarter"], deals["amount"] * deals["sim_probability"], 0.0)
    return deals


def project_by_segment(
    open_deals: pd.DataFrame,
    targets: pd.DataFrame,
    scenario: Scenario,
    as_of: date,
    quarter_end: date,
) -> pd.DataFrame:
    """Baseline vs scenario projected bookings and attainment per rep segment, plus a Total row.

    The baseline uses the same probability basis with no other changes, so the
    delta isolates the effect of the levers.
    """
    baseline = apply_scenario(open_deals, Scenario(basis=scenario.basis), as_of, quarter_end)
    simulated = apply_scenario(open_deals, scenario, as_of, quarter_end)

    def by_segment(deals: pd.DataFrame, name: str) -> pd.Series:
        return deals.groupby("rep_segment")["sim_expected"].sum().rename(name)

    out = (
        targets.set_index("rep_segment")
        .join(by_segment(baseline, "baseline_pipeline"))
        .join(by_segment(simulated, "scenario_pipeline"))
        .join(simulated.groupby("rep_segment")["sim_in_quarter"].sum().rename("scenario_deals_in_quarter"))
        .join(baseline.groupby("rep_segment")["sim_in_quarter"].sum().rename("baseline_deals_in_quarter"))
        .fillna(0.0)
    )
    total = out.sum().rename("Total")
    out = pd.concat([out, total.to_frame().T])
    out["baseline_projected"] = out["won_qtd"] + out["baseline_pipeline"]
    out["scenario_projected"] = out["won_qtd"] + out["scenario_pipeline"]
    out["baseline_attainment"] = out["baseline_projected"] / out["quarterly_quota"]
    out["scenario_attainment"] = out["scenario_projected"] / out["quarterly_quota"]
    out["scenario_gap"] = out["quarterly_quota"] - out["scenario_projected"]
    out["deals_slipped"] = (out["baseline_deals_in_quarter"] - out["scenario_deals_in_quarter"]).astype(int)
    out.index.name = "rep_segment"
    return out.reset_index()


def required_win_rate_change(
    open_deals: pd.DataFrame, targets: pd.DataFrame, scenario: Scenario, as_of: date, quarter_end: date
) -> float | None:
    """Smallest uniform win-rate change (other levers held) that reaches 100% of quota in total.

    None if even certain wins (probability 1 for every deal in the quarter) fall short.
    """
    def total_at(change: float) -> float:
        s = Scenario(change, scenario.cycle_change, scenario.basis, scenario.write_off_stalled)
        row = project_by_segment(open_deals, targets, s, as_of, quarter_end).iloc[-1]
        return row["scenario_projected"] - row["quarterly_quota"]

    if total_at(0.0) >= 0:
        return 0.0
    # Probabilities clip at 1, so the reachable ceiling is a large multiplier.
    high = 50.0
    if total_at(high) < 0:
        return None
    low = 0.0
    for _ in range(40):  # bisection to well under 0.1 percentage points
        mid = (low + high) / 2
        if total_at(mid) >= 0:
            high = mid
        else:
            low = mid
    return math.ceil(high * 1000) / 1000
