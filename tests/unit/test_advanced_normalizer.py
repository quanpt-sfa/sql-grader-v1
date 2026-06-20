import pytest
from dbcheck.config import AssignmentConfig
from dbcheck.snapshot.normalizer import NameNormalizer, remove_accents, get_column_role, check_roles_compatible

@pytest.fixture
def advanced_config():
    data = {
        "assignment": {
            "name": "Advanced Test Assignment",
            "protected_answer_db": "00000001"
        },
        "schema": {
            "matching_threshold": 0.8,
            "table_accept_threshold": 0.90,
            "table_ambiguous_threshold": 0.75,
            "column_accept_threshold": 0.88,
            "column_ambiguous_threshold": 0.75,
            "aliases": {
                "tables": {
                    "NhaCungCap": ["NCC", "Supplier"],
                    "KhachHang": ["KH", "Customer"]
                },
                "columns": {
                    "global": {
                        "GhiChu": ["ghichu", "note"]
                    },
                    "by_table": {
                        "NhaCungCap": {
                            "MaNCC": ["mancc", "ma"],
                            "TenNCC": ["tenncc", "ten"]
                        },
                        "KhachHang": {
                            "MaKH": ["makh", "ma"],
                            "TenKH": ["tenkh", "ten"]
                        }
                    }
                }
            }
        },
        "views": []
    }
    return AssignmentConfig(data)

def test_vietnamese_accent_removal():
    assert remove_accents("Nhân Viên") == "Nhan Vien"
    assert remove_accents("Khách Hàng") == "Khach Hang"
    assert remove_accents("Đại Lý") == "Dai Ly"

def test_column_role_guard():
    assert get_column_role("MaNV") == "ma"
    assert get_column_role("TenNV") == "ten"
    assert get_column_role("NgayLap") == "ngay"
    assert get_column_role("SoLuong") == "soluong"
    assert get_column_role("DonGia") == "dongia"
    assert get_column_role("TongTien") == "tongtien"
    
    assert check_roles_compatible("ma", "ma") is True
    assert check_roles_compatible("ma", "ten") is False
    assert check_roles_compatible("ma", None) is True
    assert check_roles_compatible(None, "ten") is True

def test_abbreviation_candidate_length(advanced_config):
    normalizer = NameNormalizer(advanced_config)
    
    # "NCC" has length 3 -> abbreviation candidate, must be resolved by dictionary
    res = normalizer.map_table("NCC")
    assert res["match_status"] == "TABLE_MATCHED_ABBREVIATION"
    assert res["answer_table"] == "NhaCungCap"
    
    # "ABC" has length 3 but is not in the dictionary -> must NOT fuzzy match, status TABLE_UNMAPPED
    res2 = normalizer.map_table("ABC")
    assert res2["match_status"] == "TABLE_UNMAPPED"

def test_fuzzy_matching_dual_thresholds(advanced_config):
    normalizer = NameNormalizer(advanced_config)
    
    # "NhaCungCaps" (length > 3) - similarity to "NhaCungCap" is high
    res = normalizer.map_table("NhaCungCaps")
    assert res["match_status"] == "TABLE_MATCHED_FUZZY_HIGH"
    assert res["answer_table"] == "NhaCungCap"
    
    # "NhaCung" (length > 3) - similarity to "NhaCungCap" is medium/ambiguous
    res2 = normalizer.map_table("NhaCung")
    assert res2["match_status"] == "TABLE_AMBIGUOUS"

def test_table_scoped_vs_global_columns(advanced_config):
    normalizer = NameNormalizer(advanced_config)
    
    # Under NhaCungCap table: "ma" maps to MaNCC
    expected_ncc = [{"column_name": "MaNCC"}, {"column_name": "TenNCC"}, {"column_name": "GhiChu"}]
    res_ncc = normalizer.map_column("ma", "NhaCungCap", "NCC", expected_ncc)
    assert res_ncc["match_status"] == "COLUMN_MATCHED_ALIAS"
    assert res_ncc["answer_column"] == "MaNCC"
    
    # Under KhachHang table: "ma" maps to MaKH
    expected_kh = [{"column_name": "MaKH"}, {"column_name": "TenKH"}, {"column_name": "GhiChu"}]
    res_kh = normalizer.map_column("ma", "KhachHang", "KH", expected_kh)
    assert res_kh["match_status"] == "COLUMN_MATCHED_ALIAS"
    assert res_kh["answer_column"] == "MaKH"
    
    # Global alias "note" maps to GhiChu under both tables
    res_note_ncc = normalizer.map_column("note", "NhaCungCap", "NCC", expected_ncc)
    assert res_note_ncc["match_status"] == "COLUMN_MATCHED_ALIAS"
    assert res_note_ncc["answer_column"] == "GhiChu"
    
    res_note_kh = normalizer.map_column("note", "KhachHang", "KH", expected_kh)
    assert res_note_kh["match_status"] == "COLUMN_MATCHED_ALIAS"
    assert res_note_kh["answer_column"] == "GhiChu"
