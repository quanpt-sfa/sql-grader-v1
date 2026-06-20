import pytest
from dbcheck.config import AssignmentConfig
from dbcheck.snapshot.normalizer import NameNormalizer

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
