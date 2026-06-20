import pandas as pd
from collections import Counter
from typing import Tuple, Dict, Any, List

def df_to_multiset(df: pd.DataFrame) -> Tuple[Counter, List[str]]:
    """Convert a DataFrame into a Counter of row tuples, replacing NaNs with '<NULL>'."""
    if df is None or df.empty:
        return Counter(), []
        
    columns = list(df.columns)
    # Fill NaN values with a standard sentinel string to make them hashable
    df_filled = df.fillna("<NULL>")
    
    # Extract rows as tuples of string representations or standard Python types
    rows = []
    for row in df_filled.values:
        # Convert all elements to standard types or string to make them strictly comparable
        row_tuple = tuple(row)
        rows.append(row_tuple)
        
    return Counter(rows), columns

def compare_multisets(
    ans_df: pd.DataFrame, stud_df: pd.DataFrame
) -> Tuple[pd.DataFrame, pd.DataFrame, Dict[str, Any]]:
    """Compare two DataFrames as multisets.
    
    Returns (ans_minus_stud_df, stud_minus_ans_df, metrics_dict).
    """
    ans_counter, ans_cols = df_to_multiset(ans_df)
    stud_counter, stud_cols = df_to_multiset(stud_df)
    
    # Compute multiset differences
    ans_minus_stud = ans_counter - stud_counter
    stud_minus_ans = stud_counter - ans_counter
    
    ans_minus_stud_count = sum(ans_minus_stud.values())
    stud_minus_ans_count = sum(stud_minus_ans.values())
    
    # Construct difference DataFrames
    ans_minus_stud_rows = []
    for row, count in ans_minus_stud.items():
        ans_minus_stud_rows.extend([row] * count)
        
    stud_minus_ans_rows = []
    for row, count in stud_minus_ans.items():
        stud_minus_ans_rows.extend([row] * count)
        
    # Standardize columns to expected canonical columns
    columns = ans_cols if ans_cols else stud_cols
    
    ans_minus_stud_df = pd.DataFrame(ans_minus_stud_rows, columns=columns) if ans_minus_stud_rows else pd.DataFrame(columns=columns)
    stud_minus_ans_df = pd.DataFrame(stud_minus_ans_rows, columns=columns) if stud_minus_ans_rows else pd.DataFrame(columns=columns)
    
    # Calculate value mismatch count:
    # Under a multiset model, the number of row values that mismatch (where counts overlap but values differ)
    # is the min of the extra rows in answer and student. The rest are size mismatches.
    value_mismatch_count = min(ans_minus_stud_count, stud_minus_ans_count)
    
    metrics = {
        "row_count_answer": len(ans_df) if ans_df is not None else 0,
        "row_count_student": len(stud_df) if stud_df is not None else 0,
        "answer_minus_student_count": ans_minus_stud_count,
        "student_minus_answer_count": stud_minus_ans_count,
        "value_mismatch_count": value_mismatch_count
    }
    
    return ans_minus_stud_df, stud_minus_ans_df, metrics
