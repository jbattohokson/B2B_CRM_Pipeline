"""Flag numbers in the agent's answer that don't appear in any of its query results.

Small local models get the SQL right but then pad the write-up with figures they
never queried. In the live eval, llama3.2 cited conversion rates for tiers it
had filtered out, an invented "$1,000,000 threshold", and an overall average it
never computed. This check is deterministic and needs no LLM. It extracts every
figure from the answer and looks for a matching value in the result DataFrames,
allowing for:

- rounding (relative tolerance, plus +/-0.5 for whole-number rounding),
- percentages (49% matches 0.4934),
- shorthand money ($1.4M or $212k match 1,373,500 / 212,400 at 1-2 significant digits).

Figures that also appear in the question (e.g. "over 45 days") are allowed, as
are known constants from the schema briefing (the stall threshold, the 365-day
TTM window) and small integers (<= 10), which are mostly list numbering and
counts too noisy to judge.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable

import pandas as pd

# A number with optional $ prefix, thousands separators, decimals, and a %, k, M or B suffix.
_FIGURE = re.compile(
    r"(?<![\w.])(\$)?\s?(\d{1,3}(?:,\d{3})+|\d+)(\.\d+)?\s?(%|[kKmMbB]\b)?"
)
# Markdown list numbering at line start ("1. ", "2) "): not a figure.
_LIST_MARKER = re.compile(r"(?m)^\s*\d+[.)]\s")
_SCALE = {"k": 1e3, "m": 1e6, "b": 1e9}
SMALL_INT_LIMIT = 10


def extract_figures(text: str) -> list[tuple[str, float, str | None]]:
    """[(as written, numeric value, unit)] for each figure; unit is '%', 'scaled' or None."""
    text = _LIST_MARKER.sub(" ", text)
    figures = []
    for match in _FIGURE.finditer(text):
        dollar, whole, frac, suffix = match.groups()
        value = float(whole.replace(",", "") + (frac or ""))
        unit = None
        if suffix == "%":
            unit = "%"
        elif suffix:
            value *= _SCALE[suffix.lower()]
            unit = "scaled"
        figures.append((match.group(0).strip(), value, unit))
    return figures


def _numeric_values(frames: Iterable[pd.DataFrame]) -> list[float]:
    values = []
    for df in frames:
        for col in df.columns:
            for v in df[col].tolist():
                try:
                    f = float(v)
                except (TypeError, ValueError):
                    continue
                if not math.isnan(f):
                    values.append(f)
    return values


def _close(figure: float, actual: float, unit: str | None) -> bool:
    if unit == "%":
        # Written as a percent; the data may hold a 0-1 rate or a percentage already.
        return any(abs(figure - candidate) <= 0.51 for candidate in (actual * 100, actual))
    if unit == "scaled":
        # "$1.4M" is a rounded figure: accept anything within 5%.
        return actual != 0 and abs(figure - actual) / abs(actual) <= 0.05
    # Plain figures: allow rounding to whole numbers or to two decimals.
    return abs(figure - actual) <= 0.51 or (actual != 0 and abs(figure - actual) / abs(actual) <= 0.005)


def find_unverified_figures(
    answer: str,
    frames: Iterable[pd.DataFrame],
    question: str = "",
    allowed_values: Iterable[float] = (),
) -> list[str]:
    """Figures in the answer with no matching value in any query result (in order, deduplicated).

    allowed_values: constants the model was told in its prompt (e.g. the stall threshold).
    """
    actual_values = _numeric_values(frames)
    question_values = {value for _, value, _ in extract_figures(question)} | {float(v) for v in allowed_values}
    unverified: list[str] = []
    for written, value, unit in extract_figures(answer):
        if unit is None and value.is_integer() and value <= SMALL_INT_LIMIT:
            continue
        if value in question_values:
            continue
        if any(_close(value, actual, unit) for actual in actual_values):
            continue
        if written not in unverified:
            unverified.append(written)
    return unverified
