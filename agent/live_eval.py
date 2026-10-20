"""Live evaluation of the local agent against ground truth computed directly in SQL.

For each question this prints whether the agent's answer was grounded (at least
one query succeeded), how many attempts it took, the latency, and the answer
next to the ground-truth figure. That makes it easy to see when a small model
gets the SQL right but garbles the numbers in its prose.

Slow by nature (minutes per question on a laptop CPU), so it isn't part of pytest.

Usage:
    .venv/bin/python -m agent.live_eval                    # default model (llama3.2)
    .venv/bin/python -m agent.live_eval --model llama3.1   # needs 16 GB+ RAM
"""

from __future__ import annotations

import argparse

from agent.crew_agent import DEFAULT_OLLAMA_MODEL, AgentError, OllamaConfig, ask
from agent.sql_tool import run_select

# (question, ground-truth SQL computing the answer directly from the marts)
EVAL_SET: list[tuple[str, str]] = [
    (
        "Which lead source has the highest MQL-to-deal conversion rate, and what is it?",
        "SELECT lead_source, conversion_rate FROM marts.agg_lead_source_conversion "
        "ORDER BY conversion_rate DESC LIMIT 1",
    ),
    (
        "What share of all MQLs became deals?",
        "SELECT avg(is_won::INT) AS conversion_rate FROM marts.fct_lead_funnel",
    ),
    (
        "Which SDR closed the most deals, and what was their median days to close?",
        "SELECT sdr_label, deals_won, median_days_to_close FROM marts.dim_sdrs ORDER BY deals_won DESC LIMIT 1",
    ),
    (
        "What is the median number of days from first contact to close for won deals, by business segment?",
        "SELECT business_segment, median_days_to_close FROM marts.agg_segment_summary ORDER BY deals_won DESC LIMIT 10",
    ),
]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--model", default=DEFAULT_OLLAMA_MODEL)
    args = parser.parse_args(argv)
    config = OllamaConfig(model=args.model)

    grounded = 0
    for i, (question, truth_sql) in enumerate(EVAL_SET, start=1):
        truth = run_select(truth_sql).dataframe.to_dict(orient="records")
        print(f"\n### Q{i}: {question}\nGround truth: {truth}")
        try:
            result = ask(question, config)
        except AgentError as exc:
            print(f"AGENT ERROR: {exc}")
            continue
        grounded += result.grounded
        print(
            f"grounded={result.grounded} attempts={result.attempts} "
            f"queries={len(result.queries)} ({len(result.successful_queries)} ok) secs={result.elapsed_seconds}"
        )
        for q in result.queries:
            print(f"  SQL ({'ok' if q.ok else 'error'}): {' '.join(q.sql.split())}")
        print("Answer:\n" + result.answer)
        print(f"Unverified figures: {result.unverified_figures or 'none'}")

    print(f"\n=== {args.model}: {grounded}/{len(EVAL_SET)} answers grounded in a successful query ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
