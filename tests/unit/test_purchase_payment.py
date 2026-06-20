import pytest
from types import SimpleNamespace
from dbcheck.config import load_config
from dbcheck.snapshot.normalizer import NameNormalizer
from dbcheck.structure.type_compatibility import (
    compare_sql_types,
    is_identifier_column,
    is_numeric_integer_like
)

def test_purchase_payment_config_and_aliases():
    # 1. Load config
    config = load_config("configs/assignment_purchase_payment_ca3.yaml")
    assert config.name == "Purchase-Payment REA Assignment Grade Config"
    assert config.protected_answer_db == "00000001"

    # 2. Check table exclusions
    assert config.schema.is_excluded("sysdiagrams")
    assert config.schema.is_excluded("Stage_MuaHang")
    assert config.schema.is_excluded("Stage_TraTien")
    assert not config.schema.is_excluded("NhaCungCap")

    # 3. Initialize normalizer
    normalizer = NameNormalizer(config)

    # A. Table alias tests
    assert normalizer.map_table("04.NCC")["answer_table"] == "NhaCungCap"
    assert normalizer.map_table("05.Muahangg")["answer_table"] == "MuaHang"
    assert normalizer.map_table("06.CT_MuaHang")["answer_table"] == "ChiTietMuaHang"
    assert normalizer.map_table("07.ChiTien")["answer_table"] == "TraTien"
    assert normalizer.map_table("08.CT_ChiTien")["answer_table"] == "ChiTietTraTien"
    assert normalizer.map_table("01.HangTonKho")["answer_table"] == "HangHoa"
    assert normalizer.map_table("02.Tien")["answer_table"] == "LoaiTien"

    # B. Table-scoped column alias tests
    # HangHoa
    assert normalizer.map_column("MaHang", "HangHoa", "01.HangTonKho")["answer_column"] == "MaHangHoa"
    assert normalizer.map_column("TenHang", "HangHoa", "01.HangTonKho")["answer_column"] == "TenHangHoa"

    # MuaHang
    assert normalizer.map_column("Ngay", "MuaHang", "05.Muahangg")["answer_column"] == "NgayMuaHang"
    assert normalizer.map_column("Loaitien", "MuaHang", "05.Muahangg")["answer_column"] == "MaLoaiTien"

    # TraTien
    assert normalizer.map_column("PC", "TraTien", "07.ChiTien")["answer_column"] == "SoPhieuTraTien"

    # ChiTietTraTien
    assert normalizer.map_column("PC", "ChiTietTraTien", "08.CT_ChiTien")["answer_column"] == "SoPhieuTraTien"


def test_identifier_compatible_types():
    config = load_config("configs/assignment_purchase_payment_ca3.yaml")

    # Helper tests
    global_ids = config.type_compatibility.identifier_columns_global
    assert is_identifier_column("MaNhanVien", global_identifiers=global_ids)
    assert is_identifier_column("SoPhieuTraTien", global_identifiers=global_ids)
    assert is_identifier_column("PC", global_identifiers=global_ids)
    assert is_identifier_column("PMH", global_identifiers=global_ids)
    assert is_identifier_column("MaNCC", participates_in_pk_fk=True, global_identifiers=global_ids)
    assert not is_identifier_column("DonGia", global_identifiers=global_ids)
    assert not is_identifier_column("SoLuong", global_identifiers=global_ids)

    assert is_numeric_integer_like("decimal(18,0)")
    assert is_numeric_integer_like("numeric(10,0)", scale=0)
    assert not is_numeric_integer_like("decimal(18,2)")
    assert not is_numeric_integer_like("int")

    # C. Type warning tests on identifier columns
    # MaNhanVien: int vs char -> TYPE_IDENTIFIER_COMPATIBLE_WARNING
    res = compare_sql_types("int", "char", config, column_name="MaNhanVien")
    assert res["type_status"] == "TYPE_IDENTIFIER_COMPATIBLE_WARNING"
    assert res["type_score"] == 0.75

    # MaNhaCungCap: int vs varchar -> TYPE_IDENTIFIER_COMPATIBLE_WARNING
    res = compare_sql_types("int", "varchar(50)", config, column_name="MaNhaCungCap")
    assert res["type_status"] == "TYPE_IDENTIFIER_COMPATIBLE_WARNING"

    # MaLoaiTien: int vs char -> TYPE_IDENTIFIER_COMPATIBLE_WARNING
    res = compare_sql_types("int", "char(3)", config, column_name="MaLoaiTien")
    assert res["type_status"] == "TYPE_IDENTIFIER_COMPATIBLE_WARNING"

    # DonGia: decimal vs char -> TYPE_MISMATCH (since DonGia is not an identifier)
    res = compare_sql_types("decimal(18,2)", "char(10)", config, column_name="DonGia")
    assert res["type_status"] == "TYPE_MISMATCH"

    # NgayMuaHang: datetime vs char -> TYPE_MISMATCH
    res = compare_sql_types("datetime", "char(10)", config, column_name="NgayMuaHang")
    assert res["type_status"] == "TYPE_MISMATCH"

    # DonGia: decimal vs money -> TYPE_MATCH_GROUP (both in fixed_decimal group)
    res = compare_sql_types("decimal(18,2)", "money", config, column_name="DonGia")
    assert res["type_status"] == "TYPE_MATCH_GROUP"


def test_view_configs():
    config = load_config("configs/assignment_purchase_payment_ca3.yaml")
    
    # D. View config checks
    assert len(config.views) == 3
    
    cau1 = config.views[0]
    assert cau1.answer_view == "Cau1"
    assert cau1.answer_required is True
    assert cau1.student_required is True
    assert cau1.check_mode == "full"

