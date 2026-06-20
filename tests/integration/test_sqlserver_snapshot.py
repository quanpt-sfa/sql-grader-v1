import pytest
from pathlib import Path
import tempfile
from dbcheck.config import AssignmentConfig
from dbcheck.sqlserver.connection import SQLServerConnection
from dbcheck.sqlserver.restore import restore_database, drop_database
from dbcheck.snapshot.normalizer import NameNormalizer
from dbcheck.sqlserver.introspection import get_tables, get_views
from dbcheck.snapshot.writer import write_full_snapshot
from dbcheck.snapshot.reader import read_full_snapshot

def test_sqlserver_introspection():
    workspace_dir = Path("d:/Works/sql-grader-v1")
    answer_bak = workspace_dir / "solution" / "dapan.bak"
    assert answer_bak.exists(), "dapan.bak must exist for integration tests"
    
    config_data = {
        "assignment": {
            "name": "Integration Test",
            "protected_answer_db": "00000001"
        },
        "schema": {
            "matching_threshold": 0.8,
            "aliases": {
                "tables": {
                    "NhanVien": ["Employee", "NhanVien"],
                    "KhachHang": ["Customer", "KH", "NhaCungCap"],
                    "Hang": ["Product", "HangHoa"],
                    "Tien": ["Currency", "LoaiTien"],
                    "BanHang": ["Sales", "MuaHang"],
                    "ChiTietBanHang": ["SalesDetail", "ChiTietMuaHang"],
                    "ThuTien": ["Receipt", "TraTien"],
                    "ChiTietThuTien": ["ReceiptDetail", "ChiTietTraTien"]
                },
                "columns": {
                    "MaKH": ["CustomerID", "MaKhachHang", "MaNhaCungCap"],
                    "TenKH": ["CustomerName", "TenKhachHang", "TenNhaCungCap"],
                    "MaNV": ["EmployeeID", "MaNhanVien"],
                    "TenNV": ["EmployeeName", "TenNhanVien"],
                    "MaHang": ["ProductID", "MaHangHoa"],
                    "TenHang": ["ProductName", "TenHangHoa"],
                    "MaHD": ["InvoiceID", "MaHD", "PhieuMuaHang"],
                    "NgayHD": ["DateCreated", "NgayLap", "NgayMuaHang"],
                    "MaTien": ["CurrencyID", "MaLoaiTien"],
                    "TenTien": ["CurrencyName", "TenLoaiTien"],
                    "SoLuong": ["Quantity", "SoLuong"],
                    "DonGia": ["UnitPrice", "DonGia"]
                }
            }
        },
        "views": []
    }
    config = AssignmentConfig(config_data)
    normalizer = NameNormalizer(config)
    
    db_conn = SQLServerConnection()
    temp_db = "grade_tmp_answer_int_test"
    
    try:
        restore_database(db_conn, answer_bak, temp_db)
        
        tables = get_tables(db_conn, temp_db, "test_ans", "answer", normalizer)
        views = get_views(db_conn, temp_db, "test_ans", normalizer)
        
        table_canons = [t["table_name_canonical"] for t in tables]
        assert "KhachHang" in table_canons
        assert "NhanVien" in table_canons
        assert "BanHang" in table_canons
        
        view_canons = [v["view_name_canonical"] for v in views]
        assert "Cau1" in view_canons or "cau1" in [vc.lower() for vc in view_canons]
        
        with tempfile.TemporaryDirectory() as tmp_dir:
            snap_path = Path(tmp_dir)
            snapshot_data = {
                "tables": tables,
                "columns": [],
                "primary_keys": [],
                "foreign_keys": [],
                "views": views,
                "view_columns": []
            }
            write_full_snapshot(snap_path, snapshot_data)
            
            read_snap = read_full_snapshot(snap_path)
            assert len(read_snap["tables"]) == len(tables)
            assert read_snap["tables"][0]["table_name"] == tables[0]["table_name"]
            
    finally:
        drop_database(db_conn, temp_db)
