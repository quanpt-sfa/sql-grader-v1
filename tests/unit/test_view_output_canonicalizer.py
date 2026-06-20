import pandas as pd
import numpy as np
import pytest
from dbcheck.config import AssignmentConfig, ViewConfig
from dbcheck.views.output_canonicalizer import canonicalize_view_output, resolve_view_columns, get_tolerance_decimals

@pytest.fixture
def sample_view_config():
    data = {
        "answer_view": "vw_DoanhThu",
        "expected_output": {
            "columns": [
                {"canonical": "MaKH", "type": "text", "aliases": ["CustomerID", "KH_ID"]},
                {"canonical": "TenKH", "type": "text", "aliases": ["CustomerName"]},
                {"canonical": "TongTien", "type": "decimal", "aliases": ["TotalAmount"]}
            ],
            "sort_by": ["MaKH"],
            "numeric_tolerance": 0.01
        }
    }
    return ViewConfig(data)

def test_get_tolerance_decimals():
    assert get_tolerance_decimals(0.01) == 2
    assert get_tolerance_decimals(0.001) == 3
    assert get_tolerance_decimals(0.1) == 1
    assert get_tolerance_decimals(1.0) == 0

def test_resolve_view_columns(sample_view_config):
    phys_cols = ["CustomerID", "CustomerName", "TotalAmount"]
    global_aliases = {"MaKH": ["IDKH"], "TenKH": ["Name"]}
    
    mapping = resolve_view_columns(phys_cols, sample_view_config, global_aliases)
    assert mapping["CustomerID"] == "MaKH"
    assert mapping["CustomerName"] == "TenKH"
    assert mapping["TotalAmount"] == "TongTien"

def test_canonicalize_view_output(sample_view_config):
    # Setup raw student output DataFrame
    raw_data = {
        "CustomerID": ["KH02", "KH01", "KH03"],
        "CustomerName": [" Bình ", "An", "Chưa mua"],
        "TotalAmount": [200.556, 100.123, np.nan]
    }
    df = pd.DataFrame(raw_data)
    
    global_aliases = {}
    canon_df = canonicalize_view_output(df, sample_view_config, global_aliases)
    
    # Check shape and columns
    assert list(canon_df.columns) == ["MaKH", "TenKH", "TongTien"]
    
    # Check values and types
    # Row 1 after sort should be KH01
    assert canon_df.iloc[0]["MaKH"] == "kh01"
    assert canon_df.iloc[0]["TenKH"] == "an" # lowercased and stripped
    assert canon_df.iloc[0]["TongTien"] == 100.12 # rounded to 2 decimal places (tolerance 0.01)
    
    # Row 2 should be KH02
    assert canon_df.iloc[1]["MaKH"] == "kh02"
    assert canon_df.iloc[1]["TenKH"] == "bình"
    assert canon_df.iloc[1]["TongTien"] == 200.56 # rounded up
    
    # Row 3 should be KH03
    assert canon_df.iloc[2]["MaKH"] == "kh03"
    assert pd.isna(canon_df.iloc[2]["TongTien"])

def test_canonicalize_datetime_columns():
    view_data = {
        "answer_view": "vw_Dates",
        "expected_output": {
            "columns": [
                {"canonical": "NgayLap", "type": "datetime"}
            ],
            "sort_by": ["NgayLap"]
        }
    }
    view_cfg = ViewConfig(view_data)
    
    df = pd.DataFrame({
        "NgayLap": ["2026-06-20 15:30:00", "2026-06-19 00:00:00"]
    })
    
    canon_df = canonicalize_view_output(df, view_cfg, {})
    
    # Verify string representation formatting
    # Note: 2026-06-19 00:00:00 has non-zero times? No, 00:00:00 is zero time.
    # If there is at least one non-zero time (15:30:00), the series will use HH:MM:SS formatting.
    assert canon_df.iloc[0]["NgayLap"] == "2026-06-19 00:00:00"
    assert canon_df.iloc[1]["NgayLap"] == "2026-06-20 15:30:00"
