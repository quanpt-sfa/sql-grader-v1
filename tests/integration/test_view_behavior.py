import pytest
from pathlib import Path
import tempfile
import pandas as pd
from dbcheck.config import AssignmentConfig, ViewConfig
from dbcheck.sqlserver.connection import SQLServerConnection
from dbcheck.sqlserver.restore import restore_database, drop_database
from dbcheck.sqlserver.test_data_loader import seed_database
from dbcheck.snapshot.normalizer import NameNormalizer
from dbcheck.sqlserver.introspection import get_tables, get_columns, get_foreign_keys, get_views
from dbcheck.views.view_reporter import run_view_testing

def test_view_behavior_end_to_end():
    workspace_dir = Path("d:/Works/sql-grader-v1")
    ans_bak = workspace_dir / "solution" / "dapan.bak"
    stud_bak = workspace_dir / "exams" / "C1_01_AN_23701621.BAK"
    
    assert ans_bak.exists()
    assert stud_bak.exists()
    
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
                    "KhachHang": ["Customer", "KH", "NhaCungCap", "NCC"],
                    "Hang": ["Product", "HangHoa", "HTK", "HangTonKho"],
                    "Tien": ["Currency", "LoaiTien"],
                    "BanHang": ["Sales", "MuaHang", "Muahangg"],
                    "ChiTietBanHang": ["SalesDetail", "ChiTietMuaHang", "CT_MuaHang"],
                    "ThuTien": ["Receipt", "TraTien", "ChiTien"],
                    "ChiTietThuTien": ["ReceiptDetail", "ChiTietTraTien", "CT_ChiTien"]
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
        "views": [
            {
                "answer_view": "Cau1",
                "expected_output": {
                    "columns": [
                        {"canonical": "MaKH", "type": "text", "aliases": ["MaNhaCungCap"]},
                        {"canonical": "TenKH", "type": "text", "aliases": ["TenNhaCungCap"]}
                    ],
                    "sort_by": ["MaKH"],
                    "numeric_tolerance": 0.01
                }
            }
        ]
    }
    config = AssignmentConfig(config_data)
    normalizer = NameNormalizer(config)
    
    db_conn = SQLServerConnection()
    temp_ans_db = "grade_tmp_ans_view_test"
    temp_stud_db = "grade_tmp_stud_view_test"
    
    try:
        # Restore both
        restore_database(db_conn, ans_bak, temp_ans_db)
        restore_database(db_conn, stud_bak, temp_stud_db)
        
        # Introspect schemas
        ans_snap = {
            "tables": get_tables(db_conn, temp_ans_db, "ans", "answer", normalizer),
            "columns": get_columns(db_conn, temp_ans_db, "ans", normalizer),
            "foreign_keys": get_foreign_keys(db_conn, temp_ans_db, "ans", normalizer),
            "views": get_views(db_conn, temp_ans_db, "ans", normalizer)
        }
        
        stud_snap = {
            "tables": get_tables(db_conn, temp_stud_db, "stud", "student", normalizer),
            "columns": get_columns(db_conn, temp_stud_db, "stud", normalizer),
            "foreign_keys": get_foreign_keys(db_conn, temp_stud_db, "stud", normalizer),
            "views": get_views(db_conn, temp_stud_db, "stud", normalizer)
        }
        
        # 3. Create test data folder & CSVs using canonical headers
        with tempfile.TemporaryDirectory() as tmp_dir:
            test_data_path = Path(tmp_dir)
            
            # Parent tables
            pd.DataFrame({"MaKH": [1, 2], "TenKH": ["An", "Binh"]}).to_csv(test_data_path / "KhachHang.csv", index=False)
            pd.DataFrame({"MaNV": [1], "TenNV": ["Emp One"]}).to_csv(test_data_path / "NhanVien.csv", index=False)
            pd.DataFrame({"MaHang": [1], "TenHang": ["Item One"]}).to_csv(test_data_path / "Hang.csv", index=False)
            pd.DataFrame({"MaTien": [1], "TenTien": ["Vietnamese Dong"]}).to_csv(test_data_path / "Tien.csv", index=False)
            
            # Child tables
            pd.DataFrame({
                "MaHD": [1],
                "MaKH": [1],
                "MaNV": [1],
                "NgayHD": ["2023-05-15"] # should trigger view filtering for May 2023
            }).to_csv(test_data_path / "BanHang.csv", index=False)
            
            pd.DataFrame({
                "MaHD": [1],
                "MaHang": [1],
                "SoLuong": [10],
                "DonGia": [15000]
            }).to_csv(test_data_path / "ChiTietBanHang.csv", index=False)
            
            # Seed them
            seed_database(db_conn, temp_ans_db, test_data_path, ans_snap["tables"], ans_snap["columns"], ans_snap["foreign_keys"], normalizer)
            seed_database(db_conn, temp_stud_db, test_data_path, stud_snap["tables"], stud_snap["columns"], stud_snap["foreign_keys"], normalizer)
            
            # Run view tests
            report_csv = Path(tmp_dir) / "reports" / "view_report.csv"
            diff_path = Path(tmp_dir) / "diffs"
            
            view_results = run_view_testing(
                db_conn, temp_ans_db, temp_stud_db, "23701621", config,
                ans_snap["views"], stud_snap["views"], stud_snap["columns"],
                report_csv, diff_path
            )
            
            assert len(view_results) == 1
            # Status should be VIEW_NOT_FOUND because student backup has no views
            assert view_results[0]["status"] == "VIEW_NOT_FOUND"
            
    finally:
        # Drop temporary databases
        drop_database(db_conn, temp_ans_db)
        drop_database(db_conn, temp_stud_db)
