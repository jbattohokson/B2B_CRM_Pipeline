"""Tests for pipelines/generate_data.py: determinism, keys, and simulation invariants."""

from datetime import timedelta

import pandas as pd
import pytest

from pipelines.generate_data import (
    ALL_STAGES,
    CLOSED_LOST,
    CLOSED_WON,
    OPEN_STAGES,
    STAGE_PROBABILITY,
    TIER_EMPLOYEES,
    GeneratorConfig,
    generate,
    main,
    write_csvs,
)

CLOSED = {CLOSED_WON, CLOSED_LOST}


# --- Determinism -----------------------------------------------------------


def test_same_seed_gives_identical_csv_bytes(small_config, raw_dir, tmp_path):
    write_csvs(generate(small_config), tmp_path)
    for path in raw_dir.glob("*.csv"):
        assert (tmp_path / path.name).read_bytes() == path.read_bytes(), path.name


def test_different_seed_gives_different_data(small_config, tables):
    other = generate(GeneratorConfig(**{**small_config.__dict__, "seed": small_config.seed + 1}))
    assert not other["fct_opportunities"].equals(tables["fct_opportunities"])


def test_writes_all_six_csvs(raw_dir):
    assert sorted(p.stem for p in raw_dir.glob("*.csv")) == sorted(
        ["dim_accounts", "dim_reps", "dim_date", "fct_opportunities", "fct_stage_history", "fct_activities"]
    )


# --- Keys and referential integrity ---------------------------------------


@pytest.mark.parametrize(
    "table, key",
    [
        ("dim_accounts", "account_id"),
        ("dim_reps", "rep_id"),
        ("dim_date", "date_day"),
        ("fct_opportunities", "opportunity_id"),
        ("fct_activities", "activity_id"),
    ],
)
def test_primary_keys_unique_and_present(tables, table, key):
    col = tables[table][key]
    assert col.notna().all()
    assert col.is_unique


def test_stage_history_key_unique(tables):
    assert not tables["fct_stage_history"].duplicated(["opportunity_id", "stage_sequence"]).any()


def test_foreign_keys_resolve(tables):
    opps = tables["fct_opportunities"]
    assert opps["account_id"].isin(tables["dim_accounts"]["account_id"]).all()
    assert opps["rep_id"].isin(tables["dim_reps"]["rep_id"]).all()
    assert tables["fct_stage_history"]["opportunity_id"].isin(opps["opportunity_id"]).all()
    assert tables["fct_activities"]["opportunity_id"].isin(opps["opportunity_id"]).all()


def test_company_and_rep_names_unique(tables):
    assert tables["dim_accounts"]["company_name"].is_unique
    assert tables["dim_reps"]["rep_name"].is_unique


# --- Dimension rules -------------------------------------------------------


def test_tier_matches_employee_band(tables):
    for row in tables["dim_accounts"].itertuples():
        low, high = TIER_EMPLOYEES[row.tier]
        assert low <= row.employee_count < high, row


def test_arr_is_trailing_twelve_month_won_amount(tables, small_config):
    opps = tables["fct_opportunities"]
    cutoff = small_config.as_of - timedelta(days=365)
    won = opps[(opps["stage"] == CLOSED_WON) & (opps["close_date"] > cutoff)]
    expected = won.groupby("account_id")["amount"].sum()
    accounts = tables["dim_accounts"].set_index("account_id")
    assert (accounts["arr"] >= 0).all()
    pd.testing.assert_series_equal(
        accounts.loc[expected.index, "arr"], expected.astype(int), check_names=False
    )
    assert (accounts.drop(expected.index)["arr"] == 0).all()


def test_reps_route_to_their_segment(tables):
    """Every deal is worked by a rep whose segment matches the account's tier."""
    opps = tables["fct_opportunities"]
    merged = opps.merge(tables["dim_accounts"][["account_id", "tier"]], on="account_id").merge(
        tables["dim_reps"][["rep_id", "segment"]], on="rep_id"
    )
    assert (merged["tier"] == merged["segment"]).all()


def test_date_dim_is_contiguous_and_covers_all_dates(tables):
    dates = pd.to_datetime(tables["dim_date"]["date_day"])
    assert (dates.diff().dropna() == pd.Timedelta(days=1)).all()
    opps = tables["fct_opportunities"]
    for col in ("created_date", "close_date", "stage_entered_date"):
        assert pd.to_datetime(opps[col]).between(dates.min(), dates.max()).all(), col


def test_date_dim_attributes(tables):
    row = tables["dim_date"].set_index("date_key").loc[20250104]  # a Saturday
    assert row["day_name"] == "Saturday"
    assert row["day_of_week"] == 6
    assert bool(row["is_weekend"]) is True
    assert row["quarter_label"] == "2025-Q1"
    assert str(row["first_day_of_quarter"]) == "2025-01-01"


# --- Opportunity simulation invariants ------------------------------------


def test_stages_and_probabilities(tables):
    opps = tables["fct_opportunities"]
    assert set(opps["stage"]) <= set(ALL_STAGES)
    assert (opps["probability"] == opps["stage"].map(STAGE_PROBABILITY)).all()
    assert (opps["amount"] >= 1_000).all()


def test_dataset_contains_every_stage(tables):
    """The small config should still exercise every stage, open and closed."""
    assert set(tables["fct_opportunities"]["stage"]) == set(ALL_STAGES)


def test_dates_ordered_and_bounded(tables, small_config):
    opps = tables["fct_opportunities"]
    assert (opps["created_date"] >= small_config.start_date).all()
    assert (opps["created_date"] <= small_config.as_of).all()
    assert (opps["close_date"] >= opps["created_date"]).all()
    assert (opps["stage_entered_date"] >= opps["created_date"]).all()

    closed = opps["stage"].isin(CLOSED)
    # Closed deals: close date is the real terminal date, in the past.
    assert (opps.loc[closed, "close_date"] == opps.loc[closed, "stage_entered_date"]).all()
    assert (opps.loc[closed, "close_date"] <= small_config.as_of).all()
    # Open deals: expected close date is still in the future.
    assert (opps.loc[~closed, "close_date"] > small_config.as_of).all()


def test_stage_history_is_a_valid_funnel_walk(tables):
    history = tables["fct_stage_history"].sort_values(["opportunity_id", "stage_sequence"])
    current = tables["fct_opportunities"].set_index("opportunity_id")

    for opp_id, steps in history.groupby("opportunity_id", sort=False):
        stages = steps["stage"].tolist()
        # Open stages are entered in funnel order starting at Prospecting, with none skipped.
        open_part = [s for s in stages if s in OPEN_STAGES]
        assert open_part == OPEN_STAGES[: len(open_part)], (opp_id, stages)
        # A closed stage can only be the final step.
        assert all(s not in CLOSED for s in stages[:-1]), (opp_id, stages)
        # Won requires passing through Negotiation.
        if stages[-1] == CLOSED_WON:
            assert open_part == OPEN_STAGES, (opp_id, stages)

        # Contiguous: each step's exit is the next step's entry. Only the last step is open.
        entered = steps["entered_date"].tolist()
        exited = steps["exited_date"].tolist()
        assert exited[:-1] == entered[1:], opp_id
        assert exited[-1] is None, opp_id

        # The opportunity row reflects the last step.
        assert current.at[opp_id, "stage"] == stages[-1]
        assert current.at[opp_id, "stage_entered_date"] == entered[-1]
        assert entered[0] == current.at[opp_id, "created_date"]


def test_stalled_negotiations_exist(tables, small_config):
    """The stalled-deal flag needs real examples: open Negotiation deals older than 45 days."""
    opps = tables["fct_opportunities"]
    neg = opps[opps["stage"] == "Negotiation"]
    days_in_stage = neg["stage_entered_date"].map(lambda d: (small_config.as_of - d).days)
    assert (days_in_stage > 45).sum() >= 3


def test_enterprise_cycles_longer_than_smb(tables):
    opps = tables["fct_opportunities"].merge(tables["dim_accounts"][["account_id", "tier"]])
    won = opps[opps["stage"] == CLOSED_WON]
    cycle = (pd.to_datetime(won["close_date"]) - pd.to_datetime(won["created_date"])).dt.days
    by_tier = cycle.groupby(won["tier"]).median()
    assert by_tier["Enterprise"] > by_tier["Mid-Market"] > by_tier["SMB"]


# --- Activities ------------------------------------------------------------


def test_activities_fall_inside_open_stage_windows(tables, small_config):
    """No activity is logged before a deal was created, after it closed, or after as_of."""
    acts = tables["fct_activities"].merge(
        tables["fct_opportunities"][["opportunity_id", "created_date", "stage", "close_date"]]
    )
    day = acts["activity_timestamp"].dt.date
    assert (day >= acts["created_date"]).all()
    assert (day <= small_config.as_of).all()
    closed = acts["stage"].isin(CLOSED)
    assert (day[closed] <= acts.loc[closed, "close_date"]).all()


def test_activity_ids_are_chronological(tables):
    acts = tables["fct_activities"]
    assert acts["activity_timestamp"].is_monotonic_increasing
    assert acts["activity_id"].tolist() == sorted(acts["activity_id"])


def test_every_opportunity_has_activity(tables):
    assert set(tables["fct_activities"]["opportunity_id"]) == set(
        tables["fct_opportunities"]["opportunity_id"]
    )


# --- Config and CLI --------------------------------------------------------


def test_config_rejects_bad_dates(small_config):
    with pytest.raises(ValueError):
        GeneratorConfig(start_date=small_config.as_of, as_of=small_config.start_date)


def test_config_rejects_too_many_accounts():
    with pytest.raises(ValueError):
        GeneratorConfig(n_accounts=10_000)


def test_cli_writes_to_out_dir(tmp_path, capsys):
    code = main(["--n-accounts", "20", "--n-reps", "6", "--n-opportunities", "50", "--out-dir", str(tmp_path)])
    assert code == 0
    assert len(list(tmp_path.glob("*.csv"))) == 6
    assert "fct_opportunities.csv" in capsys.readouterr().out


def test_cli_reports_invalid_config(tmp_path, capsys):
    code = main(["--n-reps", "1", "--out-dir", str(tmp_path)])
    assert code == 2
    assert "Invalid configuration" in capsys.readouterr().out
