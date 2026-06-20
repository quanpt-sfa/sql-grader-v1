import pytest
from dbcheck.config import AssignmentConfig
from dbcheck.snapshot.normalizer import NameNormalizer, normalize_key

@pytest.fixture
def test_config():
    data = {
        "assignment": {
            "name": "Test Assignment",
            "protected_answer_db": "00000001"
        },
        "schema": {
            "matching_threshold": 0.8,
            "aliases": {
                "tables": {
                    "KhachHang": ["Customer", "KH", "DMKhachHang"],
                    "HoaDon": ["Invoice", "Bill"]
                },
                "columns": {
                    "MaKH": ["CustomerID", "CustomerCode", "IDKH"],
                    "TenKH": ["CustomerName", "Name"],
                    "NgayLap": ["DateCreated", "NgayHD"]
                }
            }
        },
        "views": []
    }
    return AssignmentConfig(data)

def test_exact_and_alias_table_matching(test_config):
    normalizer = NameNormalizer(test_config)
    # Exact match
    assert normalizer.get_canonical_table("KhachHang") == "KhachHang"
    assert normalizer.get_canonical_table("khachhang") == "KhachHang"  # case-insensitive
    
    # Alias match
    assert normalizer.get_canonical_table("Customer") == "KhachHang"
    assert normalizer.get_canonical_table("KH") == "KhachHang"
    assert normalizer.get_canonical_table("DMKhachHang") == "KhachHang"
    assert normalizer.get_canonical_table("Invoice") == "HoaDon"

def test_fuzzy_table_matching_fallback(test_config):
    normalizer = NameNormalizer(test_config)
    # Fuzzy match "Customers" (close to "Customer")
    assert normalizer.get_canonical_table("Customers") == "KhachHang"
    # Fuzzy match "Invoices" (close to "Invoice")
    assert normalizer.get_canonical_table("Invoices") == "HoaDon"
    
    # Match not found: return original
    assert normalizer.get_canonical_table("NonExistentTable") == "NonExistentTable"

def test_exact_and_alias_column_matching(test_config):
    normalizer = NameNormalizer(test_config)
    # Exact
    assert normalizer.get_canonical_column("MaKH") == "MaKH"
    # Alias
    assert normalizer.get_canonical_column("CustomerID") == "MaKH"
    assert normalizer.get_canonical_column("CustomerName") == "TenKH"

def test_ambiguity_detection(test_config):
    # Setup a configuration that will lead to fuzzy ambiguity
    data = {
        "assignment": {"protected_answer_db": "00000001"},
        "schema": {
            "matching_threshold": 0.5,  # Low threshold to trigger matches
            "aliases": {
                "tables": {
                    "TableABC": ["XYZ"],
                    "TableABD": ["XYZ"]
                }
            }
        },
        "views": []
    }
    config = AssignmentConfig(data)
    normalizer = NameNormalizer(config)
    
    # "XYZ" is ambiguous because it is aliased to both TableABC and TableABD
    with pytest.raises(ValueError) as excinfo:
        normalizer.get_canonical_table("XYZ")
    assert "Ambiguous table mapping" in str(excinfo.value)


def test_comprehensive_required_normalization_and_mapping():
    from dbcheck.config import load_config
    config = load_config("configs/assignment_purchase_payment_ca3.yaml")
    normalizer = NameNormalizer(config)

    # 1. Direct normalizer output tests
    assert normalize_key("Hàng Hóa") == "hanghoa"
    assert normalize_key("Hang Hoa") == normalize_key("HangHoa")

    # 2. Column mapping tests
    # HangHoa.Hàng Hóa -> TenHangHoa
    res = normalizer.map_column("Hàng Hóa", "HangHoa", "HangHoa")
    assert res["answer_column"] == "TenHangHoa"

    # ChiTietMuaHang.Hàng Hóa -> MaHangHoa
    res = normalizer.map_column("Hàng Hóa", "ChiTietMuaHang", "ChiTietMuaHang")
    assert res["answer_column"] == "MaHangHoa"

    # HangHoa.DVT and HangHoa.ĐVT -> DonViTinh
    res1 = normalizer.map_column("DVT", "HangHoa", "HangHoa")
    res2 = normalizer.map_column("ĐVT", "HangHoa", "HangHoa")
    assert res1["answer_column"] == "DonViTinh"
    assert res2["answer_column"] == "DonViTinh"

    # TraTien.SoPTT -> SoPhieuTraTien
    res = normalizer.map_column("SoPTT", "TraTien", "TraTien")
    assert res["answer_column"] == "SoPhieuTraTien"

    # ChiTietTraTien.SoPhieuTra -> SoPhieuTraTien
    res = normalizer.map_column("SoPhieuTra", "ChiTietTraTien", "ChiTietTraTien")
    assert res["answer_column"] == "SoPhieuTraTien"

    # ChiTietTraTien.LoaiTT -> MaLoaiTien
    res = normalizer.map_column("LoaiTT", "ChiTietTraTien", "ChiTietTraTien")
    assert res["answer_column"] == "MaLoaiTien"

    # LoaiTien.tenlt -> TenLoaiTien
    res = normalizer.map_column("tenlt", "LoaiTien", "LoaiTien")
    assert res["answer_column"] == "TenLoaiTien"

    # MuaHang.Nhà cung cấp -> MaNhaCungCap
    res = normalizer.map_column("Nhà cung cấp", "MuaHang", "MuaHang")
    assert res["answer_column"] == "MaNhaCungCap"

    # NhaCungCap.Nhà cung cấp -> TenNhaCungCap
    res = normalizer.map_column("Nhà cung cấp", "NhaCungCap", "NhaCungCap")
    assert res["answer_column"] == "TenNhaCungCap"

    # MuaHang.NV mua hàng -> MaNhanVien
    res = normalizer.map_column("NV mua hàng", "MuaHang", "MuaHang")
    assert res["answer_column"] == "MaNhanVien"

    # NhanVien.NhanVien -> TenNhanVien
    res = normalizer.map_column("NhanVien", "NhanVien", "NhanVien")
    assert res["answer_column"] == "TenNhanVien"

    # 3. Table mapping test
    # CTCT table maps as weak/review alias, not clean alias.
    res_table = normalizer.map_table("CTCT")
    assert res_table["answer_table"] == "ChiTietTraTien"
    assert res_table["match_status"] == "TABLE_MATCHED_WEAK_ALIAS"
    assert res_table["review_required"] is True

    # 4. Duplicate mapping behavior verification using run_structure_comparison
    # PMH + SoHD duplicate mapping does not produce COLUMN_AMBIGUOUS.
    from unittest.mock import patch
    from pathlib import Path
    import csv
    import tempfile

    ans_snap = {
        "tables": [{"table_name": "ChiTietMuaHang", "table_name_canonical": "ChiTietMuaHang"}],
        "columns": [
            {"table_name": "ChiTietMuaHang", "table_name_canonical": "ChiTietMuaHang", "column_name": "PhieuMuaHang", "column_name_canonical": "PhieuMuaHang", "data_type": "int", "is_identity": 0}
        ],
        "primary_keys": [],
        "foreign_keys": [],
        "views": [],
        "view_columns": [],
        "unique_constraints": []
    }

    stud_snap = {
        "tables": [{"table_name": "ChiTietMuaHang"}],
        "columns": [
            {"table_name": "ChiTietMuaHang", "column_name": "PMH", "data_type": "int", "is_identity": 0},
            {"table_name": "ChiTietMuaHang", "column_name": "SoHD", "data_type": "int", "is_identity": 0}
        ],
        "primary_keys": [],
        "foreign_keys": [],
        "views": [],
        "view_columns": [],
        "unique_constraints": []
    }

    def mock_read(path):
        if "answer" in str(path):
            return ans_snap
        else:
            return stud_snap

    with tempfile.TemporaryDirectory() as tmp_dir:
        report_file = Path(tmp_dir) / "structure_report.csv"
        col_report_file = Path(tmp_dir) / "column_mapping_report.csv"
        
        from dbcheck.structure.structure_reporter import run_structure_comparison
        with patch("dbcheck.structure.structure_reporter.read_full_snapshot", side_effect=mock_read):
            run_structure_comparison(Path("answer_dir"), Path("student_dir"), report_file, config)
            
        assert col_report_file.exists()
        with open(col_report_file, "r", encoding="utf-8") as f:
            mappings = list(csv.DictReader(f))
            
        assert len(mappings) == 2
        pmh_map = next((m for m in mappings if m["student_column"] == "PMH"), None)
        sohd_map = next((m for m in mappings if m["student_column"] == "SoHD"), None)
        
        # Verify that duplicate column mapping resolved by priority (PMH > SoHD)
        # PMH maps as COLUMN_MATCHED_ALIAS, SoHD maps as DUPLICATE_MAPPING_REVIEW, no COLUMN_AMBIGUOUS emitted.
        assert pmh_map is not None
        assert pmh_map["answer_column"] == "PhieuMuaHang"
        assert pmh_map["match_status"] == "COLUMN_MATCHED_ALIAS"
        
        assert sohd_map is not None
        assert sohd_map["answer_column"] == "PhieuMuaHang"
        assert sohd_map["match_status"] == "DUPLICATE_MAPPING_REVIEW"
        assert sohd_map["duplicate_resolution"] == "demoted_duplicate"

