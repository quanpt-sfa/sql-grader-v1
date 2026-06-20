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


def test_hanghoa_table_context_aware_matching():
    from dbcheck.config import load_config
    config = load_config("configs/assignment_purchase_payment_ca3.yaml")
    normalizer = NameNormalizer(config)

    # 1. normalize_key assertions
    assert normalize_key("Hàng Hóa") == normalize_key("HangHoa")
    assert normalize_key("Hàng Hóa") == "hanghoa"

    # 2. In HangHoa table, column "Hàng Hóa" must resolve to TenHangHoa
    res_hanghoa = normalizer.map_column(
        raw_column="Hàng Hóa",
        canonical_table="HangHoa",
        physical_table="HangHoa"
    )
    assert res_hanghoa["answer_column"] == "TenHangHoa"
    assert res_hanghoa["match_status"] == "COLUMN_MATCHED_ALIAS"

    # 3. In ChiTietMuaHang table, column "Hàng Hóa" must resolve to MaHangHoa
    res_ctmh = normalizer.map_column(
        raw_column="Hàng Hóa",
        canonical_table="ChiTietMuaHang",
        physical_table="ChiTietMuaHang"
    )
    assert res_ctmh["answer_column"] == "MaHangHoa"
    assert res_ctmh["match_status"] == "COLUMN_MATCHED_ALIAS"

