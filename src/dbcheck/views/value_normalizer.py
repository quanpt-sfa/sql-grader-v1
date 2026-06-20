"""
value_normalizer.py — Per-cell and per-DataFrame value normalization for view output comparison.

Rules applied before multiset/ordered comparison:
- NULL / NaN  → sentinel "<NULL>"
- Strings     → strip whitespace, Unicode-normalize (NFC), lowercase
- Dates       → parse to datetime, format as "yyyy-mm-dd" (or "yyyy-mm-dd HH:MM:SS")
- Numbers     → convert via Decimal, round to tolerance decimal places
- Booleans    → normalize to "true" / "false" string (consistent with text downstream)
"""

import math
import unicodedata
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation
from typing import Any, Optional, List, Dict
import pandas as pd
import numpy as np

from dbcheck.config import ViewConfig
from dbcheck.utils.logging import get_logger

_NULL_SENTINEL = "<NULL>"


def _get_tolerance_decimals(tolerance: float) -> int:
    """Return decimal places implied by a tolerance value (e.g. 0.01 → 2)."""
    if tolerance <= 0:
        return 0
    try:
        return max(0, int(round(-math.log10(tolerance))))
    except Exception:
        return 2


def normalize_value(val: Any, col_type: str, tolerance: float = 0.01) -> Any:
    """Normalize a single cell value for canonical comparison.

    Returns a Python primitive (str, Decimal, or the sentinel string "<NULL>").
    """
    # --- NULL handling ---
    if val is None:
        return _NULL_SENTINEL
    if isinstance(val, float) and math.isnan(val):
        return _NULL_SENTINEL
    # pd.NA or pd.NaT
    try:
        if pd.isna(val):
            return _NULL_SENTINEL
    except (TypeError, ValueError):
        pass

    col_type_l = col_type.lower()

    # --- Numeric ---
    if col_type_l in ("number", "decimal", "float", "numeric", "int", "integer"):
        try:
            dec = Decimal(str(val))
            places = _get_tolerance_decimals(tolerance)
            quantize_str = "1" if places == 0 else ("0." + "0" * places)
            return dec.quantize(Decimal(quantize_str), rounding=ROUND_HALF_UP)
        except (InvalidOperation, ValueError):
            return _NULL_SENTINEL

    # --- Date / DateTime ---
    if col_type_l in ("date", "datetime", "time"):
        try:
            ts = pd.to_datetime(val, errors="raise")
            if ts.hour == 0 and ts.minute == 0 and ts.second == 0:
                return ts.strftime("%Y-%m-%d")
            return ts.strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            # Fall through to string normalization if parsing fails
            pass

    # --- String (default) ---
    s = str(val).strip()
    # Unicode NFC normalization (handles Vietnamese composed forms)
    s = unicodedata.normalize("NFC", s)
    return s.lower()


def normalize_dataframe(df: pd.DataFrame, view_cfg: ViewConfig) -> pd.DataFrame:
    """Apply per-column normalization to a DataFrame that has already been column-renamed
    to canonical names.

    Returns a new DataFrame with every value replaced by its normalized form.
    The returned DataFrame has the same column order as `view_cfg.columns`.
    All values are Python primitives suitable for hashing and equality comparison.
    """
    logger = get_logger()
    if df is None or df.empty:
        expected_cols = [c["canonical"] for c in view_cfg.columns]
        return pd.DataFrame(columns=expected_cols)

    df = df.copy()
    col_meta_by_name: Dict[str, Dict] = {c["canonical"]: c for c in view_cfg.columns}

    for canon_name in df.columns:
        meta = col_meta_by_name.get(canon_name)
        if meta is None:
            continue
        col_type = meta.get("type", "text")
        tol = view_cfg.numeric_tolerance

        df[canon_name] = df[canon_name].apply(
            lambda v: normalize_value(v, col_type, tol)
        )

    return df


def compare_ordered(
    ans_df: pd.DataFrame,
    stud_df: pd.DataFrame,
    view_cfg: ViewConfig,
) -> Dict[str, Any]:
    """Order-sensitive comparison: both DataFrames must match row-for-row after normalization.

    Returns a metrics dict compatible with the multiset metrics format plus
    a 'diff_df' key containing rows that differ (None if identical).
    """
    ans_norm = normalize_dataframe(ans_df, view_cfg)
    stud_norm = normalize_dataframe(stud_df, view_cfg)

    n_ans = len(ans_norm)
    n_stud = len(stud_norm)

    metrics: Dict[str, Any] = {
        "row_count_answer": n_ans,
        "row_count_student": n_stud,
        "answer_minus_student_count": 0,
        "student_minus_answer_count": 0,
        "value_mismatch_count": 0,
        "order_mismatch": False,
    }

    if n_ans != n_stud:
        diff = abs(n_ans - n_stud)
        metrics["answer_minus_student_count"] = max(0, n_ans - n_stud)
        metrics["student_minus_answer_count"] = max(0, n_stud - n_ans)
        return metrics, None

    # Compare row by row
    mismatch_rows = []
    for i in range(n_ans):
        a_row = ans_norm.iloc[i]
        s_row = stud_norm.iloc[i] if i < n_stud else None
        if s_row is None or not a_row.equals(s_row):
            metrics["value_mismatch_count"] += 1
            mismatch_rows.append({
                "row_index": i,
                **{f"answer_{c}": a_row[c] for c in ans_norm.columns},
                **{f"student_{c}": (s_row[c] if s_row is not None else _NULL_SENTINEL)
                   for c in ans_norm.columns},
            })

    if mismatch_rows:
        metrics["order_mismatch"] = True
        diff_df = pd.DataFrame(mismatch_rows)
    else:
        diff_df = None

    return metrics, diff_df
