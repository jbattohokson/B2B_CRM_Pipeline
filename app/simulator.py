"""Lead mix simulator: how many deals would a different MQL volume and source mix produce?

Expected deals for a source = planned MQLs from that source x its historical
conversion rate. The baseline is the historical volume and mix, so a zero-change
scenario reproduces the deals actually won (a test checks this).

The levers:
- monthly_mqls: total MQLs per month.
- mix_shift: points of the MQL mix moved from one source to another
  (e.g. move 10 points of volume from social to paid search).
- conversion_change: relative change to every source's conversion rate,
  for example better SDR follow-up (+10% turns 12% into 13.2%).

Uncertainty comes from each source's 95% Wilson interval. The low and high
totals assume every source lands at its bound together, so the range is wide on
purpose: it's a planning band, not a forecast interval.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import pandas as pd


@dataclass(frozen=True)
class Scenario:
    monthly_mqls: float
    mix_shift: dict[tuple[str, str], float] = field(default_factory=dict)  # (from, to) -> share points (0-1)
    conversion_change: float = 0.0  # relative, -0.5 .. +1.0

    def __post_init__(self) -> None:
        if self.monthly_mqls < 0:
            raise ValueError("monthly_mqls can't be negative")
        if self.conversion_change <= -1:
            raise ValueError("conversion_change must be above -100%")
        if any(v < 0 for v in self.mix_shift.values()):
            raise ValueError("mix shifts are amounts to move, so they must be >= 0")


def historical_monthly_mqls(sources: pd.DataFrame, months: int) -> float:
    return float(sources["mqls"].sum()) / months


def apply_mix_shift(shares: pd.Series, mix_shift: dict[tuple[str, str], float]) -> pd.Series:
    """Move share points between sources. A source can't give away more than it has."""
    out = shares.astype(float).copy()
    for (source, target), amount in mix_shift.items():
        if source not in out.index or target not in out.index:
            raise ValueError(f"Unknown lead source in mix shift: {source!r} -> {target!r}")
        moved = min(amount, out[source])
        out[source] -= moved
        out[target] += moved
    return out


def project(sources: pd.DataFrame, scenario: Scenario) -> pd.DataFrame:
    """Per-source baseline vs scenario MQLs and expected deals, plus a Total row.

    sources needs lead_source, mqls, conversion_rate, ci_low, ci_high (as from
    app.data.load_source_conversion). The baseline keeps the historical mix at the
    scenario's monthly volume, so the delta isolates the mix and conversion levers.
    """
    df = sources.set_index("lead_source")
    hist_share = df["mqls"] / df["mqls"].sum()
    scen_share = apply_mix_shift(hist_share, scenario.mix_shift)
    lift = 1 + scenario.conversion_change

    out = pd.DataFrame(index=df.index)
    out["baseline_mqls"] = hist_share * scenario.monthly_mqls
    out["scenario_mqls"] = scen_share * scenario.monthly_mqls
    out["conversion_rate"] = df["conversion_rate"]
    out["scenario_conversion_rate"] = (df["conversion_rate"] * lift).clip(upper=1.0)
    out["baseline_deals"] = out["baseline_mqls"] * df["conversion_rate"]
    out["scenario_deals"] = out["scenario_mqls"] * out["scenario_conversion_rate"]
    out["scenario_deals_low"] = out["scenario_mqls"] * (df["ci_low"] * lift).clip(upper=1.0)
    out["scenario_deals_high"] = out["scenario_mqls"] * (df["ci_high"] * lift).clip(upper=1.0)

    total = out.sum()
    total["conversion_rate"] = total["baseline_deals"] / total["baseline_mqls"] if total["baseline_mqls"] else 0.0
    total["scenario_conversion_rate"] = (
        total["scenario_deals"] / total["scenario_mqls"] if total["scenario_mqls"] else 0.0
    )
    out = pd.concat([out, total.rename("Total").to_frame().T])
    out.index.name = "lead_source"
    return out.reset_index()


def mqls_needed(projection: pd.DataFrame, target_deals: float) -> float | None:
    """MQLs per month the scenario's mix and conversion need to reach target_deals (None if conversion is 0)."""
    rate = float(projection.set_index("lead_source").loc["Total", "scenario_conversion_rate"])
    if rate <= 0:
        return None
    return math.ceil(target_deals / rate)
