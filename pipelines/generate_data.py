"""Generate a seeded, synthetic B2B SaaS CRM dataset as CSVs.

Writes six files to data/raw/:

    dim_accounts.csv        one row per customer/prospect account
    dim_reps.csv            one row per sales rep
    dim_date.csv            calendar dimension covering every date in the data
    fct_opportunities.csv   one row per opportunity (current state)
    fct_stage_history.csv   one row per opportunity per stage it entered
    fct_activities.csv      one row per logged sales activity

fct_stage_history is not in the original table list. It's what makes the
velocity metrics honest: stage durations and stage-to-stage conversion need
stage entry/exit dates, and a single "current stage" column can't give you
those.

The generator simulates each opportunity walking through the funnel
(Prospecting -> Discovery -> Demo -> Negotiation -> Closed-Won), with a chance
of being lost at every stage. Durations, conversion rates and deal sizes depend
on the account's tier and the rep's skill. A minority of deals stall in
Negotiation (a long tail of 60-180 days with activity falling off), and some
reps stall more than others. Every deal still moving as of `as_of` is left open
in its current stage.

The same seed and arguments always produce byte-identical CSVs.

Usage:
    .venv/bin/python pipelines/generate_data.py
    .venv/bin/python pipelines/generate_data.py --seed 7 --n-opportunities 8000
"""

from __future__ import annotations

import argparse
import itertools
import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Domain constants
# ---------------------------------------------------------------------------

OPEN_STAGES = ["Prospecting", "Discovery", "Demo", "Negotiation"]
CLOSED_WON = "Closed-Won"
CLOSED_LOST = "Closed-Lost"
ALL_STAGES = OPEN_STAGES + [CLOSED_WON, CLOSED_LOST]

# Default forecast probability attached to each stage (decimal, not percent).
STAGE_PROBABILITY = {
    "Prospecting": 0.10,
    "Discovery": 0.20,
    "Demo": 0.40,
    "Negotiation": 0.70,
    CLOSED_WON: 1.00,
    CLOSED_LOST: 0.00,
}

# Base probability of advancing out of each open stage (Negotiation -> Won).
BASE_ADVANCE_RATE = {
    "Prospecting": 0.65,
    "Discovery": 0.60,
    "Demo": 0.55,
    "Negotiation": 0.60,
}

TIERS = ["SMB", "Mid-Market", "Enterprise"]
TIER_WEIGHTS = [0.55, 0.30, 0.15]
# Employee-count band per tier: [low, high).
TIER_EMPLOYEES = {"SMB": (10, 200), "Mid-Market": (200, 2000), "Enterprise": (2000, 50000)}
# Additive tweak to every stage's advance rate: small deals close more easily.
TIER_ADVANCE_ADJ = {"SMB": 0.05, "Mid-Market": 0.0, "Enterprise": -0.05}
# Mean days spent in each open stage, by tier (Prospecting, Discovery, Demo, Negotiation).
TIER_STAGE_DAYS = {
    "SMB": [7, 10, 10, 14],
    "Mid-Market": [10, 14, 18, 21],
    "Enterprise": [14, 21, 30, 35],
}
# Deal size: lognormal with this median (USD) and sigma, by tier.
TIER_AMOUNT = {"SMB": (12_000, 0.5), "Mid-Market": (45_000, 0.5), "Enterprise": (150_000, 0.6)}

# Share of reps per selling segment, and annual quota range by segment.
REP_SEGMENT_WEIGHTS = {"SMB": 0.42, "Mid-Market": 0.33, "Enterprise": 0.25}
REP_QUOTA_RANGE = {
    "SMB": (450_000, 650_000),
    "Mid-Market": (700_000, 1_000_000),
    "Enterprise": (1_100_000, 1_600_000),
}

REGIONS = ["NA-East", "NA-West", "EMEA", "APAC"]
REGION_WEIGHTS = [0.32, 0.28, 0.25, 0.15]

INDUSTRIES = [
    "Software", "FinTech", "Healthcare", "Manufacturing",
    "Retail", "Logistics", "Media", "Education",
]

# Stalling: chance a deal stalls on entering Negotiation, and how stalls behave.
BASE_STALL_RATE = 0.15
STALLER_REP_SHARE = 0.15          # share of reps who stall deals far more often
STALLER_REP_MULTIPLIER = 2.5
ENTERPRISE_STALL_BONUS = 0.05
STALL_DAYS_RANGE = (60, 181)      # [low, high) days spent stuck in Negotiation
STALLED_WIN_PENALTY = 0.5         # multiplier on the win rate once a deal has stalled
STALL_ACTIVITY_CUTOFF_DAYS = 30   # after this many days in a stall, activity drops off

# Activities: mean activities per week while in each stage, and the type mix.
ACTIVITY_RATE_PER_WEEK = {"Prospecting": 1.5, "Discovery": 2.0, "Demo": 2.5, "Negotiation": 2.0}
STALLED_ACTIVITY_RATE_PER_WEEK = 0.3
ACTIVITY_TYPE_MIX = {
    "Prospecting": {"Email": 0.55, "Call": 0.40, "Meeting": 0.05},
    "Discovery": {"Call": 0.40, "Meeting": 0.35, "Email": 0.25},
    "Demo": {"Demo": 0.45, "Meeting": 0.25, "Email": 0.20, "Call": 0.10},
    "Negotiation": {"Meeting": 0.30, "Email": 0.30, "Proposal Sent": 0.20, "Call": 0.20},
}

# Opportunity creation volume grows over the window: the creation rate at the
# end is (1 + PIPELINE_GROWTH) times the rate at the start.
PIPELINE_GROWTH = 1.5

COMPANY_PREFIXES = [
    "Apex", "Arbor", "Aster", "Atlas", "Beacon", "Birch", "Blue", "Bright", "Cedar",
    "Cinder", "Clear", "Cobalt", "Copper", "Crest", "Delta", "Echo", "Ember", "Fable",
    "Falcon", "Fern", "Flint", "Forge", "Granite", "Harbor", "Helix", "Horizon", "Iron",
    "Juniper", "Keystone", "Lumen", "Maple", "Meridian", "Nimbus", "North", "Nova",
    "Oak", "Onyx", "Orbit", "Pine", "Pioneer", "Prism", "Quarry", "Quartz", "Raven",
    "Ridge", "Sable", "Signal", "Summit", "Tidal", "Vertex",
]
COMPANY_SUFFIXES = [
    "Analytics", "Bio", "Capital", "Cloud", "Dynamics", "Systems", "Foods", "Freight",
    "Health", "Holdings", "Industries", "Labs", "Learning", "Logistics", "Media",
    "Networks", "Partners", "Retail", "Robotics", "Software", "Solutions", "Studios",
    "Technologies", "Works",
]
FIRST_NAMES = [
    "Alex", "Amara", "Ben", "Carmen", "Chen", "Dana", "Devon", "Elena", "Farah", "Gabe",
    "Hana", "Ivan", "Jordan", "Kai", "Leila", "Marco", "Maya", "Nate", "Noor", "Omar",
    "Priya", "Quinn", "Rafael", "Sam", "Sofia", "Taylor", "Uma", "Victor", "Yuki", "Zoe",
]
LAST_NAMES = [
    "Adams", "Bauer", "Castillo", "Diaz", "Edwards", "Fischer", "Garcia", "Hughes",
    "Ito", "Jensen", "Khan", "Lopez", "Morgan", "Nguyen", "Okafor", "Patel", "Quintero",
    "Rossi", "Silva", "Tanaka", "Usman", "Vega", "Walsh", "Xu", "Yilmaz", "Zhang",
    "Brooks", "Cohen", "Novak", "Reyes",
]


@dataclass(frozen=True)
class GeneratorConfig:
    """Knobs for one generation run. Defaults produce the committed-to dataset."""

    seed: int = 42
    n_accounts: int = 600
    n_reps: int = 36
    n_opportunities: int = 9000
    start_date: date = date(2024, 1, 1)
    as_of: date = date(2026, 9, 30)

    def __post_init__(self) -> None:
        if self.as_of <= self.start_date:
            raise ValueError("as_of must be after start_date")
        if self.n_accounts > len(COMPANY_PREFIXES) * len(COMPANY_SUFFIXES):
            raise ValueError("n_accounts exceeds the number of unique company names available")
        if self.n_reps > len(FIRST_NAMES) * len(LAST_NAMES):
            raise ValueError("n_reps exceeds the number of unique rep names available")
        if self.n_reps < len(TIERS):
            raise ValueError("need at least one rep per segment")
        if min(self.n_accounts, self.n_opportunities) < 1:
            raise ValueError("n_accounts and n_opportunities must be positive")


# ---------------------------------------------------------------------------
# Dimensions
# ---------------------------------------------------------------------------


def _unique_names(rng: np.random.Generator, left: list[str], right: list[str], n: int) -> list[str]:
    """Pick n distinct "left right" combinations without replacement."""
    combos = [f"{a} {b}" for a, b in itertools.product(left, right)]
    picks = rng.choice(len(combos), size=n, replace=False)
    return [combos[i] for i in picks]


def generate_accounts(rng: np.random.Generator, cfg: GeneratorConfig) -> pd.DataFrame:
    """Accounts with tier, industry, size and region. ARR is filled in later from won deals."""
    tiers = rng.choice(TIERS, size=cfg.n_accounts, p=TIER_WEIGHTS)
    employees = []
    for tier in tiers:
        low, high = TIER_EMPLOYEES[tier]
        # Log-uniform within the band, so small companies dominate each tier.
        employees.append(int(math.exp(rng.uniform(math.log(low), math.log(high)))))
    return pd.DataFrame(
        {
            "account_id": [f"ACC-{i:05d}" for i in range(1, cfg.n_accounts + 1)],
            "company_name": _unique_names(rng, COMPANY_PREFIXES, COMPANY_SUFFIXES, cfg.n_accounts),
            "industry": rng.choice(INDUSTRIES, size=cfg.n_accounts),
            "employee_count": employees,
            "tier": tiers,
            "region": rng.choice(REGIONS, size=cfg.n_accounts, p=REGION_WEIGHTS),
        }
    )


def generate_reps(rng: np.random.Generator, cfg: GeneratorConfig) -> tuple[pd.DataFrame, dict]:
    """Reps with region, segment and quota, plus hidden performance traits.

    The traits (skill multiplier, staller flag) drive the simulation but are not
    written to the CSV, because a real CRM wouldn't record them either. The
    analysis has to find them in the outcomes.
    """
    # Guarantee every segment has at least one rep, then fill the rest by weight.
    segments = list(TIERS) + list(
        rng.choice(TIERS, size=cfg.n_reps - len(TIERS), p=list(REP_SEGMENT_WEIGHTS.values()))
    )
    rng.shuffle(segments)
    quotas = []
    for segment in segments:
        low, high = REP_QUOTA_RANGE[segment]
        quotas.append(int(round(rng.uniform(low, high), -4)))  # round to nearest 10k

    reps = pd.DataFrame(
        {
            "rep_id": [f"REP-{i:03d}" for i in range(1, cfg.n_reps + 1)],
            "rep_name": _unique_names(rng, FIRST_NAMES, LAST_NAMES, cfg.n_reps),
            "region": rng.choice(REGIONS, size=cfg.n_reps, p=REGION_WEIGHTS),
            "segment": segments,
            "annual_quota": quotas,
        }
    )
    traits = {
        rep_id: {
            "skill": float(np.clip(rng.normal(1.0, 0.12), 0.7, 1.3)),
            "staller": bool(rng.random() < STALLER_REP_SHARE),
        }
        for rep_id in reps["rep_id"]
    }
    return reps, traits


def generate_date_dim(start: date, end: date) -> pd.DataFrame:
    """Standard calendar dimension, one row per day from start to end inclusive."""
    days = pd.date_range(start, end, freq="D")
    iso = days.isocalendar()
    return pd.DataFrame(
        {
            "date_key": days.strftime("%Y%m%d").astype(int),
            "date_day": days.date,
            "year": days.year,
            "quarter": days.quarter,
            "quarter_label": days.year.astype(str) + "-Q" + days.quarter.astype(str),
            "month": days.month,
            "month_name": days.strftime("%B"),
            "day_of_month": days.day,
            "day_of_week": iso["day"].to_numpy(),  # ISO: 1 = Monday ... 7 = Sunday
            "day_name": days.strftime("%A"),
            "iso_week": iso["week"].to_numpy(),
            "is_weekend": iso["day"].to_numpy() >= 6,
            "first_day_of_month": days.to_period("M").start_time.date,
            "first_day_of_quarter": days.to_period("Q").start_time.date,
        }
    )


# ---------------------------------------------------------------------------
# Opportunity simulation
# ---------------------------------------------------------------------------


def _creation_dates(rng: np.random.Generator, cfg: GeneratorConfig) -> list[date]:
    """Sorted creation dates whose density grows linearly across the window.

    Inverse-CDF sampling of density f(t) proportional to 1 + g*t on t in [0, 1].
    """
    g = PIPELINE_GROWTH
    u = rng.random(cfg.n_opportunities)
    t = (-1 + np.sqrt(1 + g * (2 + g) * u)) / g if g > 0 else u
    span = (cfg.as_of - cfg.start_date).days
    offsets = np.floor(t * (span + 1)).clip(0, span).astype(int)
    return sorted(cfg.start_date + timedelta(days=int(d)) for d in offsets)


def _stage_days(rng: np.random.Generator, mean_days: float) -> int:
    """Days spent in a stage: gamma-distributed around the mean, at least 1."""
    return max(1, int(round(rng.gamma(shape=3.0, scale=mean_days / 3.0))))


def _pick_rep(rng: np.random.Generator, reps: pd.DataFrame, tier: str, region: str) -> str:
    """A rep who sells to this tier in this region, falling back to any rep in the tier."""
    pool = reps[(reps["segment"] == tier) & (reps["region"] == region)]
    if pool.empty:
        pool = reps[reps["segment"] == tier]
    return str(pool["rep_id"].iloc[rng.integers(len(pool))])


def simulate_opportunity(
    rng: np.random.Generator,
    created: date,
    tier: str,
    rep_traits: dict,
    as_of: date,
) -> tuple[list[dict], bool]:
    """Walk one deal through the funnel.

    Returns (history, stalled): the stages entered, in order, each with
    entered/exited dates (exited is None for the current stage), and whether the
    deal stalled in Negotiation.
    """
    history: list[dict] = []
    stalled = False
    entered = created

    for idx, stage in enumerate(OPEN_STAGES):
        days = _stage_days(rng, TIER_STAGE_DAYS[tier][idx])
        advance_rate = (BASE_ADVANCE_RATE[stage] + TIER_ADVANCE_ADJ[tier]) * rep_traits["skill"]

        if stage == "Negotiation":
            stall_rate = BASE_STALL_RATE + (ENTERPRISE_STALL_BONUS if tier == "Enterprise" else 0.0)
            if rep_traits["staller"]:
                stall_rate *= STALLER_REP_MULTIPLIER
            if rng.random() < stall_rate:
                stalled = True
                days = int(rng.integers(*STALL_DAYS_RANGE))
                advance_rate *= STALLED_WIN_PENALTY

        exit_date = entered + timedelta(days=days)
        if exit_date > as_of:
            # Still sitting in this stage today: leave it open.
            history.append({"stage": stage, "entered_date": entered, "exited_date": None})
            return history, stalled

        history.append({"stage": stage, "entered_date": entered, "exited_date": exit_date})
        entered = exit_date
        if rng.random() >= min(advance_rate, 0.95):
            history.append({"stage": CLOSED_LOST, "entered_date": entered, "exited_date": None})
            return history, stalled

    history.append({"stage": CLOSED_WON, "entered_date": entered, "exited_date": None})
    return history, stalled


def _expected_close(rng: np.random.Generator, created: date, tier: str, as_of: date) -> date:
    """Rep-entered close date for an open deal.

    Starts from the typical full cycle for the tier. If that date has already
    passed, it gets pushed 1-6 weeks out, which is what reps do in a real CRM.
    """
    planned = created + timedelta(days=int(sum(TIER_STAGE_DAYS[tier]) * rng.uniform(0.8, 1.3)))
    if planned <= as_of:
        planned = as_of + timedelta(days=int(rng.integers(7, 43)))
    return planned


def _activities_for_stage(
    rng: np.random.Generator, opp_id: str, row: dict, stalled: bool, as_of: date
) -> list[dict]:
    """Activities logged while a deal sat in one open stage."""
    start = row["entered_date"]
    end = row["exited_date"] or as_of
    span_days = max((end - start).days, 1)
    stage = row["stage"]

    # Active period at the normal rate. A stalled Negotiation goes quiet after the cutoff.
    active_days = span_days
    quiet_days = 0
    if stage == "Negotiation" and stalled and span_days > STALL_ACTIVITY_CUTOFF_DAYS:
        active_days = STALL_ACTIVITY_CUTOFF_DAYS
        quiet_days = span_days - STALL_ACTIVITY_CUTOFF_DAYS

    windows = [(0, active_days, ACTIVITY_RATE_PER_WEEK[stage])]
    if quiet_days:
        windows.append((active_days, span_days, STALLED_ACTIVITY_RATE_PER_WEEK))

    mix = ACTIVITY_TYPE_MIX[stage]
    types, weights = list(mix), list(mix.values())
    out = []
    for i, (lo, hi, rate) in enumerate(windows):
        count = int(rng.poisson(rate * (hi - lo) / 7))
        if i == 0:
            count = max(count, 1)  # every stage a deal reaches gets at least one touch
        for _ in range(count):
            day_offset = int(rng.integers(lo, hi))
            # Never log an activity after the stage ended or after today.
            day = min(start + timedelta(days=day_offset), end, as_of)
            ts = datetime(day.year, day.month, day.day, int(rng.integers(8, 18)), int(rng.integers(0, 60)))
            out.append(
                {
                    "opportunity_id": opp_id,
                    "activity_type": str(rng.choice(types, p=weights)),
                    "activity_timestamp": ts,
                }
            )
    return out


def generate_opportunities(
    rng: np.random.Generator,
    cfg: GeneratorConfig,
    accounts: pd.DataFrame,
    reps: pd.DataFrame,
    rep_traits: dict,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Simulate every opportunity. Returns (opportunities, stage_history, activities)."""
    opp_rows, history_rows, activity_rows = [], [], []

    for n, created in enumerate(_creation_dates(rng, cfg), start=1):
        opp_id = f"OPP-{n:06d}"
        account = accounts.iloc[rng.integers(len(accounts))]
        tier = account["tier"]
        rep_id = _pick_rep(rng, reps, tier, account["region"])

        median, sigma = TIER_AMOUNT[tier]
        amount = round(float(rng.lognormal(math.log(median), sigma)) / 500) * 500
        amount = max(amount, 1_000)

        history, stalled = simulate_opportunity(rng, created, tier, rep_traits[rep_id], cfg.as_of)
        current = history[-1]
        stage = current["stage"]
        if stage in (CLOSED_WON, CLOSED_LOST):
            close_date = current["entered_date"]
        else:
            close_date = _expected_close(rng, created, tier, cfg.as_of)

        opp_rows.append(
            {
                "opportunity_id": opp_id,
                "account_id": account["account_id"],
                "rep_id": rep_id,
                "created_date": created,
                "close_date": close_date,
                "stage": stage,
                "stage_entered_date": current["entered_date"],
                "amount": amount,
                "probability": STAGE_PROBABILITY[stage],
            }
        )
        for seq, row in enumerate(history, start=1):
            history_rows.append({"opportunity_id": opp_id, "stage_sequence": seq, **row})
            if row["stage"] in OPEN_STAGES:
                activity_rows.extend(_activities_for_stage(rng, opp_id, row, stalled, cfg.as_of))

    opportunities = pd.DataFrame(opp_rows)
    stage_history = pd.DataFrame(history_rows)

    # Activity IDs go in chronological order (ties broken by opportunity) so they're stable.
    activities = (
        pd.DataFrame(activity_rows)
        .sort_values(["activity_timestamp", "opportunity_id"], kind="mergesort")
        .reset_index(drop=True)
    )
    activities.insert(0, "activity_id", [f"ACT-{i:07d}" for i in range(1, len(activities) + 1)])
    return opportunities, stage_history, activities


def compute_account_arr(accounts: pd.DataFrame, opportunities: pd.DataFrame, as_of: date) -> pd.Series:
    """Current ARR per account: Closed-Won amounts closed in the trailing 365 days.

    Assumes one-year subscription contracts. A deal won more than a year ago is
    treated as up for renewal (renewals aren't modeled), so it no longer counts.
    Accounts with no active contract get ARR = 0 (open prospects).
    """
    window_start = as_of - timedelta(days=365)
    won = opportunities[
        (opportunities["stage"] == CLOSED_WON)
        & (opportunities["close_date"] > window_start)
        & (opportunities["close_date"] <= as_of)
    ]
    arr = won.groupby("account_id")["amount"].sum()
    return accounts["account_id"].map(arr).fillna(0).astype(int)


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def generate(cfg: GeneratorConfig | None = None) -> dict[str, pd.DataFrame]:
    """Build every table in memory. Keys are the output CSV stems."""
    cfg = cfg or GeneratorConfig()
    rng = np.random.default_rng(cfg.seed)

    accounts = generate_accounts(rng, cfg)
    reps, rep_traits = generate_reps(rng, cfg)
    opportunities, stage_history, activities = generate_opportunities(rng, cfg, accounts, reps, rep_traits)

    accounts.insert(3, "arr", compute_account_arr(accounts, opportunities, cfg.as_of))

    # Calendar runs through the end of the year containing the latest date in the data.
    latest = max(opportunities["close_date"].max(), cfg.as_of)
    dates = generate_date_dim(cfg.start_date, date(latest.year, 12, 31))

    return {
        "dim_accounts": accounts,
        "dim_reps": reps,
        "dim_date": dates,
        "fct_opportunities": opportunities,
        "fct_stage_history": stage_history,
        "fct_activities": activities,
    }


def write_csvs(tables: dict[str, pd.DataFrame], out_dir: Path) -> dict[str, Path]:
    """Write each table to <out_dir>/<name>.csv with stable formatting."""
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = {}
    for name, df in tables.items():
        path = out_dir / f"{name}.csv"
        df.to_csv(path, index=False, lineterminator="\n", date_format="%Y-%m-%d %H:%M:%S")
        paths[name] = path
    return paths


def _parse_args(argv: list[str] | None = None) -> tuple[GeneratorConfig, Path]:
    defaults = GeneratorConfig()
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--seed", type=int, default=defaults.seed)
    parser.add_argument("--n-accounts", type=int, default=defaults.n_accounts)
    parser.add_argument("--n-reps", type=int, default=defaults.n_reps)
    parser.add_argument("--n-opportunities", type=int, default=defaults.n_opportunities)
    parser.add_argument("--start-date", type=date.fromisoformat, default=defaults.start_date)
    parser.add_argument(
        "--as-of", type=date.fromisoformat, default=defaults.as_of,
        help="'Today' for the simulation: open deals are open as of this date (fixed so runs are reproducible).",
    )
    parser.add_argument(
        "--out-dir", type=Path, default=Path(__file__).resolve().parent.parent / "data" / "raw"
    )
    args = parser.parse_args(argv)
    cfg = GeneratorConfig(
        seed=args.seed,
        n_accounts=args.n_accounts,
        n_reps=args.n_reps,
        n_opportunities=args.n_opportunities,
        start_date=args.start_date,
        as_of=args.as_of,
    )
    return cfg, args.out_dir


def main(argv: list[str] | None = None) -> int:
    try:
        cfg, out_dir = _parse_args(argv)
    except ValueError as exc:
        print(f"Invalid configuration: {exc}")
        return 2
    tables = generate(cfg)
    paths = write_csvs(tables, out_dir)
    print(f"Generated synthetic CRM data (seed={cfg.seed}, as_of={cfg.as_of}) in {out_dir}")
    for name, path in paths.items():
        print(f"  {path.name:<24} {len(tables[name]):>7,} rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
