"""Tests for agent/fact_check.py, using answer text the live llama3.2 eval actually produced."""

from decimal import Decimal

import pandas as pd
import pytest

from agent.fact_check import extract_figures, find_unverified_figures


def test_extract_figures_parses_money_percent_and_shorthand():
    figures = extract_figures("Pipeline is $1,373,500.00 (49%), about $1.4M, or 212k.")
    assert [(w, v, u) for w, v, u in figures] == [
        ("$1,373,500.00", 1373500.0, None),
        ("49%", 49.0, "%"),
        ("$1.4M", 1400000.0, "scaled"),
        ("212k", 212000.0, "scaled"),
    ]


def test_list_numbering_is_not_a_figure():
    assert extract_figures("1. Do this\n2) Do that") == []


def test_live_q1_answer_flags_invented_threshold():
    """Real llama3.2 output: the count and amount are right; the $1,000,000 'threshold' is invented."""
    result = pd.DataFrame({"stalled_deals": [41], "stalled_pipeline_amount": [Decimal("1373500.00")]})
    answer = (
        "There are 41 open deals in Negotiation that are stalled, holding $1,373,500.00.\n"
        "* 41: number of stalled deals\n* $1,373,500.00: total stalled pipeline amount\n"
        "* $1,000,000: threshold for stall threshold (45 days in Negotiation)"
    )
    question = "How many open deals are stalled in negotiation over 45 days?"
    assert find_unverified_figures(answer, [result], question) == ["$1,000,000"]


def test_live_q3_answer_flags_unqueried_tiers():
    """Real llama3.2 output: queried Enterprise only, then cited other tiers and an invented deal size."""
    result = pd.DataFrame({"stage_name": ["Demo"], "stage_order": [3], "conversion_rate": [0.49343832020997375]})
    answer = (
        "The conversion rate out of Demo for Enterprise accounts is 49%.\n"
        "* Mid-Market: 55%\n* SMB: 42%\n* Average days in Demo stage: 14 days\n* Average deal size: $250k"
    )
    assert find_unverified_figures(answer, [result]) == ["55%", "42%", "14", "$250k"]


def test_live_q4_answer_flags_uncomputed_overall_average():
    result = pd.DataFrame({"avg_sales_cycle_days": [71.87, 50.39, 108.35], "account_tier": ["MM", "SMB", "Ent"]})
    answer = "Mid-Market 71.87 days, SMB 50.39 days, Enterprise 108.35 days. All won deals: 77.81 days."
    assert find_unverified_figures(answer, [result]) == ["77.81"]


@pytest.mark.parametrize(
    "written, actual",
    [
        ("$212,400", 212400.0),
        ("$212.4k", 212400.0),
        ("$1.4M", 1373500.0),  # within 5% for a rounded shorthand figure
        ("50.4", 50.394),
        ("50", 50.394),
        ("49.3%", 0.4934),
        ("2.62x", 2.62),
    ],
)
def test_rounded_forms_are_accepted(written, actual):
    assert find_unverified_figures(f"Value: {written}", [pd.DataFrame({"v": [actual]})]) == []


def test_numbers_from_question_and_small_ints_are_allowed():
    answer = "Deals over 45 days: see the top 3 reps."
    assert find_unverified_figures(answer, [pd.DataFrame({"v": [99]})], question="stuck over 45 days") == []


def test_everything_unverified_without_results():
    assert find_unverified_figures("Gap is $212,400.", []) == ["$212,400"]


def test_live_q2_answer_flags_invented_quota_and_percentages():
    """Real llama3.2 output (second eval): gap figures are right; the quota and % are invented."""
    result = pd.DataFrame({"rep_name": ["Maya Brooks", "Yuki Silva"], "expected_revenue_gap": [212400.0, 182450.0]})
    answer = (
        "Maya Brooks has an expected revenue gap of $212,400.00, a shortfall of 12.5% of quota.\n"
        "* Expected revenue gap for Yuki Silva: $182,450.00\n* Quarterly quota for Maya Brooks: $1,736,000.00"
    )
    assert find_unverified_figures(answer, [result]) == ["12.5%", "$1,736,000.00"]


def test_prompt_constants_are_allowed():
    answer = "* 90: day window for early conversion\n* 180: longest window tracked"
    assert find_unverified_figures(answer, [], allowed_values=(90, 180)) == []
    assert find_unverified_figures(answer, []) == ["90", "180"]


def test_row_count_counts_as_verified():
    """Real llama3.2 answer in the app: '21 reps have stuck deals' = the result's row count."""
    result = pd.DataFrame({"rep_name": [f"Rep {i}" for i in range(21)], "max_stuck_days": range(100, 121)})
    assert find_unverified_figures("21: the total number of reps with stuck deals", [result]) == []
