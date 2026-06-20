import pandas as pd
import numpy as np
from dbcheck.views.result_comparator import compare_multisets

def test_compare_multisets_identical():
    ans_df = pd.DataFrame({
        "MaKH": ["KH01", "KH02"],
        "TongTien": [100.0, 200.0]
    })
    stud_df = pd.DataFrame({
        "MaKH": ["KH01", "KH02"],
        "TongTien": [100.0, 200.0]
    })
    
    ans_minus, stud_minus, metrics = compare_multisets(ans_df, stud_df)
    
    assert ans_minus.empty
    assert stud_minus.empty
    assert metrics["row_count_answer"] == 2
    assert metrics["row_count_student"] == 2
    assert metrics["answer_minus_student_count"] == 0
    assert metrics["student_minus_answer_count"] == 0
    assert metrics["value_mismatch_count"] == 0

def test_compare_multisets_row_multiplicity():
    # Multiset test: student returns duplicate row, answer only has it once
    ans_df = pd.DataFrame({
        "MaKH": ["KH01", "KH02"],
        "TongTien": [100.0, 200.0]
    })
    stud_df = pd.DataFrame({
        "MaKH": ["KH01", "KH02", "KH02"], # KH02 duplicated
        "TongTien": [100.0, 200.0, 200.0]
    })
    
    ans_minus, stud_minus, metrics = compare_multisets(ans_df, stud_df)
    
    # Answer minus student: empty (student has all rows that answer has)
    assert ans_minus.empty
    # Student minus answer: has 1 extra row of KH02
    assert len(stud_minus) == 1
    assert stud_minus.iloc[0]["MaKH"] == "KH02"
    assert stud_minus.iloc[0]["TongTien"] == 200.0
    
    assert metrics["answer_minus_student_count"] == 0
    assert metrics["student_minus_answer_count"] == 1
    assert metrics["value_mismatch_count"] == 0 # size difference, not value mismatch

def test_compare_multisets_value_mismatch():
    ans_df = pd.DataFrame({
        "MaKH": ["KH01", "KH02"],
        "TongTien": [100.0, 200.0]
    })
    stud_df = pd.DataFrame({
        "MaKH": ["KH01", "KH02"],
        "TongTien": [100.0, 250.0] # Value mismatch on KH02
    })
    
    ans_minus, stud_minus, metrics = compare_multisets(ans_df, stud_df)
    
    # 1 mismatch row in each diff
    assert len(ans_minus) == 1
    assert ans_minus.iloc[0]["MaKH"] == "KH02"
    assert ans_minus.iloc[0]["TongTien"] == 200.0
    
    assert len(stud_minus) == 1
    assert stud_minus.iloc[0]["MaKH"] == "KH02"
    assert stud_minus.iloc[0]["TongTien"] == 250.0
    
    assert metrics["answer_minus_student_count"] == 1
    assert metrics["student_minus_answer_count"] == 1
    assert metrics["value_mismatch_count"] == 1 # overlaps
