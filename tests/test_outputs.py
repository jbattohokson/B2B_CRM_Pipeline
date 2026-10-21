"""The portfolio chart script runs on the fixture warehouse and writes every file it promises."""

import pandas as pd

from reports import make_outputs

EXPECTED_CHARTS = {
    "chart1_conversion_by_source.png", "chart2_mql_mix_vs_deal_mix.png", "chart3_conversion_by_cohort.png",
    "chart4_days_to_close_by_cohort.png", "chart5_days_to_close_distribution.png", "chart6_rep_output.png",
    "chart7_segments.png",
}


def test_outputs_are_written_and_match_the_marts(marts_db, tmp_path, monkeypatch):
    monkeypatch.setattr(make_outputs, "OUT", tmp_path)
    assert make_outputs.main(["--db", str(marts_db)]) == 0
    written = {p.name for p in tmp_path.iterdir()}
    assert EXPECTED_CHARTS <= written
    assert all((tmp_path / c).stat().st_size > 10_000 for c in EXPECTED_CHARTS)

    sources = pd.read_csv(tmp_path / "summary_by_lead_source.csv")
    assert sources["mqls"].sum() == 8000 and sources["deals_won"].sum() == 842
    assert pd.read_csv(tmp_path / "summary_by_sdr.csv")["deals_won"].sum() == 842
    assert pd.read_csv(tmp_path / "summary_by_closer.csv")["deals_won"].sum() == 842
