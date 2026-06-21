import pytest
import pandas as pd
from unittest.mock import MagicMock
from dbcheck.config import AssignmentConfig, ViewConfig
from dbcheck.views.sql_rewriter import rewrite_sql_query, extract_select_body
from dbcheck.views.view_reporter import run_compare_rewritten_sql_on_answer_db
from dbcheck.views.result_comparator import compare_multisets

@pytest.fixture
def base_config_data():
    return {
        "assignment": {"name": "Test Assignment", "protected_answer_db": "ans_db"},
        "schema": {
            "matching_threshold": 0.8,
            "table_accept_threshold": 0.9,
            "table_ambiguous_threshold": 0.75,
            "column_accept_threshold": 0.88,
            "column_ambiguous_threshold": 0.75,
            "aliases": {"tables": {}, "columns": {"global": {}, "by_table": {}}},
            "abbreviations": {},
            "type_compatibility": {
                "mode": "group_with_warnings",
                "identifier_columns": {"global": [], "by_table": {}},
            },
        },
        "views": {
            "mode": "answer_snapshot",
            "execution_mode": "compare_rewritten_sql_on_answer_db",
            "export_outputs": True,
            "compare_as_multiset": True,
            "sql_rewrite": {
                "enabled": True,
                "use_existing_mapping_reports": True,
                "reject_unsafe_sql": True,
                "execute_on_answer_db": True,
                "allow_weak_column_aliases": False,
                "allow_weak_table_aliases": False,
                "max_execution_seconds": 10
            },
            "expected": [
                {
                    "answer_view": "Cau1",
                    "answer_required": True,
                    "student_required": True,
                    "check_mode": "full",
                    "order_sensitive": False,
                    "expected_output": {
                        "columns": [
                            {"canonical": "PhieuMuaHang", "type": "text", "aliases": []},
                            {"canonical": "TongTien", "type": "number", "aliases": []}
                        ],
                        "sort_by": ["PhieuMuaHang"]
                    }
                }
            ]
        }
    }

def test_extract_select_body():
    # Simple DDL
    ddl1 = "CREATE VIEW dbo.MyView AS SELECT * FROM dbo.T1"
    assert extract_select_body(ddl1) == "SELECT * FROM dbo.T1"
    
    # Case insensitivity and whitespace/newlines
    ddl2 = "\n  ALTER   VIEW  \n [MyView] \n AS \n\tSELECT col1, col2\nFROM T1;\n"
    assert extract_select_body(ddl2) == "SELECT col1, col2\nFROM T1"
    
    # DDL with parenthesis around view name/column list
    ddl3 = "CREATE VIEW dbo.MyView (ColA, ColB) AS SELECT 1, 2"
    assert extract_select_body(ddl3) == "SELECT 1, 2"
    
    # Raise error if no AS keyword found
    with pytest.raises(ValueError):
        extract_select_body("CREATE VIEW MyView SELECT * FROM T1")

def test_table_rewriting(base_config_data):
    config = AssignmentConfig(base_config_data)
    table_map = {"CT_MuaHang": "ChiTietMuaHang"}
    column_map = {}
    
    # Standard query
    sql = "SELECT * FROM dbo.CT_MuaHang"
    res = rewrite_sql_query(sql, table_map, column_map, config)
    assert res["status"] == "VIEW_SQL_REWRITE_SUCCESS"
    assert "dbo.[ChiTietMuaHang]" in res["rewritten_sql"] or "dbo.ChiTietMuaHang" in res["rewritten_sql"]

def test_qualified_column_rewriting_preserving_alias(base_config_data):
    config = AssignmentConfig(base_config_data)
    table_map = {"CT_MuaHang": "ChiTietMuaHang"}
    column_map = {("CT_MuaHang", "PMH"): "PhieuMuaHang"}
    
    sql = "SELECT c.PMH FROM dbo.CT_MuaHang c"
    res = rewrite_sql_query(sql, table_map, column_map, config)
    assert res["status"] == "VIEW_SQL_REWRITE_SUCCESS"
    # Preserves alias c, and maps PMH to PhieuMuaHang
    assert "c.PhieuMuaHang" in res["rewritten_sql"] or "c.[PhieuMuaHang]" in res["rewritten_sql"]

def test_quoted_table_column_rewriting(base_config_data):
    config = AssignmentConfig(base_config_data)
    table_map = {"CT_MuaHang": "ChiTietMuaHang"}
    column_map = {("CT_MuaHang", "PMH"): "PhieuMuaHang"}
    
    sql = "SELECT [c].[PMH] FROM [CT_MuaHang] [c]"
    res = rewrite_sql_query(sql, table_map, column_map, config)
    assert res["status"] == "VIEW_SQL_REWRITE_SUCCESS"
    assert "[ChiTietMuaHang]" in res["rewritten_sql"]
    assert "[PhieuMuaHang]" in res["rewritten_sql"]

def test_output_alias_preservation(base_config_data):
    config = AssignmentConfig(base_config_data)
    table_map = {"CT_MuaHang": "ChiTietMuaHang"}
    column_map = {("CT_MuaHang", "PMH"): "PhieuMuaHang"}
    
    # Alias defined as `AS SoPhieu` should not be rewritten
    sql = "SELECT c.PMH AS SoPhieu FROM dbo.CT_MuaHang c"
    res = rewrite_sql_query(sql, table_map, column_map, config)
    assert res["status"] == "VIEW_SQL_REWRITE_SUCCESS"
    assert "AS SoPhieu" in res["rewritten_sql"]
    # Check that it didn't rewrite SoPhieu
    assert "AS PhieuMuaHang" not in res["rewritten_sql"]

def test_string_literal_preservation(base_config_data):
    config = AssignmentConfig(base_config_data)
    table_map = {"CT_MuaHang": "ChiTietMuaHang"}
    column_map = {
        ("CT_MuaHang", "PMH"): "PhieuMuaHang",
        ("CT_MuaHang", "Note"): "Note"
    }
    
    sql = "SELECT c.PMH FROM dbo.CT_MuaHang c WHERE c.Note = 'PMH'"
    res = rewrite_sql_query(sql, table_map, column_map, config)
    assert res["status"] == "VIEW_SQL_REWRITE_SUCCESS"
    assert "'PMH'" in res["rewritten_sql"]  # string literal untouched

def test_unqualified_unambiguous_column(base_config_data):
    config = AssignmentConfig(base_config_data)
    table_map = {"CT_MuaHang": "ChiTietMuaHang"}
    column_map = {("CT_MuaHang", "PMH"): "PhieuMuaHang"}
    
    sql = "SELECT PMH FROM dbo.CT_MuaHang"
    res = rewrite_sql_query(sql, table_map, column_map, config)
    assert res["status"] == "VIEW_SQL_REWRITE_SUCCESS"
    assert "PhieuMuaHang" in res["rewritten_sql"]

def test_unqualified_ambiguous_column(base_config_data):
    config = AssignmentConfig(base_config_data)
    table_map = {"T1": "Table1", "T2": "Table2"}
    column_map = {
        ("T1", "ID"): "ID",
        ("T2", "ID"): "ID"
    }
    
    sql = "SELECT ID FROM dbo.T1 JOIN dbo.T2 ON T1.ID = T2.ID"
    res = rewrite_sql_query(sql, table_map, column_map, config)
    assert res["status"] == "VIEW_SQL_REWRITE_AMBIGUOUS_COLUMN"

def test_student_view_name_differences(base_config_data, tmp_path):
    config = AssignmentConfig(base_config_data)
    
    # Mock database connection
    db_conn = MagicMock()
    # Mock execute_query (for extracting views)
    db_conn.execute_query.return_value = [
        {
            "view_name": "DifferentName_Cau1",
            "definition": "CREATE VIEW DifferentName_Cau1 AS SELECT c.PMH, c.TongTien FROM dbo.CT_MuaHang c"
        }
    ]
    # Mock execute_query_df (for executing queries to get dataframes)
    db_conn.execute_query_df.side_effect = [
        # Expected view execute on answer DB: SELECT * FROM dbo.[Cau1]
        pd.DataFrame({"PhieuMuaHang": ["PMH01"], "TongTien": [100.0]}),
        # Rewritten query execute on answer DB
        pd.DataFrame({"PhieuMuaHang": ["PMH01"], "TongTien": [100.0]})
    ]
    
    # Write table/column mapping reports to temp path
    (tmp_path / "table_mapping_report.csv").write_text("student_table,answer_table,match_status\nCT_MuaHang,ChiTietMuaHang,TABLE_MATCHED_EXACT\n", encoding="utf-8")
    (tmp_path / "column_mapping_report.csv").write_text("student_table,student_column,answer_column,match_status\nCT_MuaHang,PMH,PhieuMuaHang,COLUMN_MATCHED_EXACT\nCT_MuaHang,TongTien,TongTien,COLUMN_MATCHED_EXACT\n", encoding="utf-8")
    
    results = run_compare_rewritten_sql_on_answer_db(
        db_conn=db_conn,
        ans_db="ans_db",
        stud_db="stud_db",
        submission_id="sub1",
        config=config,
        expected_views=config.views,
        output_report_path=tmp_path / "view_test_report.csv",
        diff_dir=tmp_path / "diffs",
        col_accept_threshold=0.88,
        export_outputs=False
    )
    
    assert len(results) == 1
    assert results[0]["answer_view"] == "Cau1"
    assert results[0]["matched_student_view"] == "DifferentName_Cau1"
    assert results[0]["status"] == "VIEW_OUTPUT_MATCH"

def test_wrong_logic_matching(base_config_data, tmp_path):
    config = AssignmentConfig(base_config_data)
    
    db_conn = MagicMock()
    # Mock execute_query
    db_conn.execute_query.return_value = [
        {
            "view_name": "Cau1",
            "definition": "CREATE VIEW Cau1 AS SELECT c.PMH, c.TongTien FROM dbo.CT_MuaHang c"
        }
    ]
    # Mock execute_query_df
    db_conn.execute_query_df.side_effect = [
        # Expected output
        pd.DataFrame({"PhieuMuaHang": ["PMH01"], "TongTien": [100.0]}),
        # Student output (wrong value)
        pd.DataFrame({"PhieuMuaHang": ["PMH01"], "TongTien": [999.0]})
    ]
    
    (tmp_path / "table_mapping_report.csv").write_text("student_table,answer_table,match_status\nCT_MuaHang,ChiTietMuaHang,TABLE_MATCHED_EXACT\n", encoding="utf-8")
    (tmp_path / "column_mapping_report.csv").write_text("student_table,student_column,answer_column,match_status\nCT_MuaHang,PMH,PhieuMuaHang,COLUMN_MATCHED_EXACT\nCT_MuaHang,TongTien,TongTien,COLUMN_MATCHED_EXACT\n", encoding="utf-8")
    
    results = run_compare_rewritten_sql_on_answer_db(
        db_conn=db_conn,
        ans_db="ans_db",
        stud_db="stud_db",
        submission_id="sub1",
        config=config,
        expected_views=config.views,
        output_report_path=tmp_path / "view_test_report.csv",
        diff_dir=tmp_path / "diffs",
        col_accept_threshold=0.88,
        export_outputs=False
    )
    
    assert len(results) == 1
    assert results[0]["status"] == "VIEW_VALUE_MISMATCH"

def test_cutoff_filters_part_c_contamination(base_config_data):
    ans_df = pd.DataFrame({"PhieuMuaHang": ["PMH01"], "TongTien": [100.0]})
    stud_df = pd.DataFrame({"PhieuMuaHang": ["PMH01", "PMH02"], "TongTien": [100.0, 200.0]})
    
    ans_minus, stud_minus, metrics = compare_multisets(ans_df, stud_df)
    assert ans_minus.empty
    assert len(stud_minus) == 1
    assert metrics["student_minus_answer_count"] == 1

@pytest.mark.parametrize("unsafe_sql, expected_error_part", [
    ("INSERT INTO dbo.T1 VALUES(1)", "keyword: INSERT"),
    ("CREATE TABLE #temp(id int)", "keyword: CREATE"),
    ("SELECT * FROM dbo.T1; DELETE FROM dbo.T2", "separated by semicolon"),
    ("SELECT * FROM Db.dbo.Table", "Three-part table name"),
    ("SELECT * FROM dbo.T1 WHERE val = #temp.val", "Temporary table"),
    ("EXEC sp_executesql N'SELECT 1'", "keyword: EXEC"),
    ("SELECT * INTO dbo.T2 FROM dbo.T1", "SELECT INTO"),
])
def test_unsafe_sql_rejection(base_config_data, unsafe_sql, expected_error_part):
    config = AssignmentConfig(base_config_data)
    table_map = {"T1": "Table1"}
    column_map = {}
    
    res = rewrite_sql_query(unsafe_sql, table_map, column_map, config)
    assert res["status"] in ("VIEW_SQL_UNSAFE_REVIEW", "VIEW_SQL_REWRITE_UNSUPPORTED_SQL")
    assert expected_error_part in res["error_message"]

def test_cte_query_rewriting(base_config_data):
    config = AssignmentConfig(base_config_data)
    table_map = {"CT_MuaHang": "ChiTietMuaHang"}
    column_map = {
        ("CT_MuaHang", "PMH"): "PhieuMuaHang",
    }
    
    sql = "WITH MyCTE AS (SELECT c.PMH FROM dbo.CT_MuaHang c) SELECT * FROM MyCTE"
    res = rewrite_sql_query(sql, table_map, column_map, config)
    assert res["status"] == "VIEW_SQL_REWRITE_SUCCESS"
    # The CTE definition itself is rewritten
    assert "dbo.ChiTietMuaHang" in res["rewritten_sql"]
    assert "c.PhieuMuaHang" in res["rewritten_sql"] or "c.[PhieuMuaHang]" in res["rewritten_sql"]
    # The CTE reference 'MyCTE' is NOT rewritten as a physical table
    assert "dbo.MyCTE" not in res["rewritten_sql"]

def test_order_sensitivity(base_config_data, tmp_path):
    # Set expected view to order_sensitive=True
    base_config_data["views"]["expected"][0]["order_sensitive"] = True
    config = AssignmentConfig(base_config_data)
    
    db_conn = MagicMock()
    # Mock execute_query
    db_conn.execute_query.return_value = [
        {
            "view_name": "Cau1",
            "definition": "CREATE VIEW Cau1 AS SELECT c.PMH, c.TongTien FROM dbo.CT_MuaHang c"
        }
    ]
    # Mock execute_query_df
    db_conn.execute_query_df.side_effect = [
        # Expected output (ordered: PMH01, PMH02)
        pd.DataFrame({"PhieuMuaHang": ["PMH01", "PMH02"], "TongTien": [100.0, 200.0]}),
        # Student output (wrong order: PMH02, PMH01)
        pd.DataFrame({"PhieuMuaHang": ["PMH02", "PMH01"], "TongTien": [200.0, 100.0]})
    ]
    
    (tmp_path / "table_mapping_report.csv").write_text("student_table,answer_table,match_status\nCT_MuaHang,ChiTietMuaHang,TABLE_MATCHED_EXACT\n", encoding="utf-8")
    (tmp_path / "column_mapping_report.csv").write_text("student_table,student_column,answer_column,match_status\nCT_MuaHang,PMH,PhieuMuaHang,COLUMN_MATCHED_EXACT\nCT_MuaHang,TongTien,TongTien,COLUMN_MATCHED_EXACT\n", encoding="utf-8")
    
    results = run_compare_rewritten_sql_on_answer_db(
        db_conn=db_conn,
        ans_db="ans_db",
        stud_db="stud_db",
        submission_id="sub1",
        config=config,
        expected_views=config.views,
        output_report_path=tmp_path / "view_test_report.csv",
        diff_dir=tmp_path / "diffs",
        col_accept_threshold=0.88,
        export_outputs=False
    )
    
    assert len(results) == 1
    assert results[0]["status"] == "VIEW_ORDER_MISMATCH"

def test_one_to_one_assignment_and_ambiguity(base_config_data, tmp_path):
    # Two expected views: Cau1 and Cau2
    base_config_data["views"]["expected"] = [
        {
            "answer_view": "Cau1",
            "answer_required": True,
            "student_required": True,
            "check_mode": "full",
            "expected_output": {"columns": [{"canonical": "PhieuMuaHang", "type": "text", "aliases": []}]}
        },
        {
            "answer_view": "Cau2",
            "answer_required": True,
            "student_required": True,
            "check_mode": "full",
            "expected_output": {"columns": [{"canonical": "TongTien", "type": "number", "aliases": []}]}
        }
    ]
    config = AssignmentConfig(base_config_data)
    
    db_conn = MagicMock()
    # Mock execute_query
    db_conn.execute_query.return_value = [
        {
            "view_name": "Stud1",
            "definition": "CREATE VIEW Stud1 AS SELECT PMH FROM dbo.CT_MuaHang"
        },
        {
            "view_name": "Stud2",
            "definition": "CREATE VIEW Stud2 AS SELECT PMH FROM dbo.CT_MuaHang"
        }
    ]
    # Mock execute_query_df
    db_conn.execute_query_df.side_effect = [
        # Expected outputs for Cau1 and Cau2
        pd.DataFrame({"PhieuMuaHang": ["PMH01"]}), # Cau1
        pd.DataFrame({"TongTien": [100.0]}),        # Cau2
        
        # execution of Stud1
        pd.DataFrame({"PhieuMuaHang": ["PMH01"]}),
        # execution of Stud2
        pd.DataFrame({"PhieuMuaHang": ["PMH01"]})
    ]
    
    (tmp_path / "table_mapping_report.csv").write_text("student_table,answer_table,match_status\nCT_MuaHang,ChiTietMuaHang,TABLE_MATCHED_EXACT\n", encoding="utf-8")
    (tmp_path / "column_mapping_report.csv").write_text("student_table,student_column,answer_column,match_status\nCT_MuaHang,PMH,PhieuMuaHang,COLUMN_MATCHED_EXACT\n", encoding="utf-8")
    
    results = run_compare_rewritten_sql_on_answer_db(
        db_conn=db_conn,
        ans_db="ans_db",
        stud_db="stud_db",
        submission_id="sub1",
        config=config,
        expected_views=config.views,
        output_report_path=tmp_path / "view_test_report.csv",
        diff_dir=tmp_path / "diffs",
        col_accept_threshold=0.88,
        export_outputs=False
    )
    
    # Since both Stud1 and Stud2 match Cau1 perfectly (score 1.0), and neither matches Cau2,
    # Cau1 should get VIEW_MAPPING_AMBIGUOUS status.
    cau1_res = next(r for r in results if r["answer_view"] == "Cau1")
    assert cau1_res["status"] == "VIEW_MAPPING_AMBIGUOUS"


# ---------------------------------------------------------------------------
# SQL file export tests
# ---------------------------------------------------------------------------

def test_sql_files_written_on_success(base_config_data, tmp_path):
    """Raw, select_body, rewritten, and diff SQL files are written on rewrite success."""
    config = AssignmentConfig(base_config_data)

    db_conn = MagicMock()
    raw_ddl = "CREATE VIEW Cau1 AS SELECT c.PMH, c.TongTien FROM dbo.CT_MuaHang c"
    db_conn.execute_query.return_value = [
        {"view_name": "Cau1", "definition": raw_ddl}
    ]
    db_conn.execute_query_df.side_effect = [
        pd.DataFrame({"PhieuMuaHang": ["PMH01"], "TongTien": [100.0]}),
        pd.DataFrame({"PhieuMuaHang": ["PMH01"], "TongTien": [100.0]}),
    ]

    (tmp_path / "table_mapping_report.csv").write_text(
        "student_table,answer_table,match_status\nCT_MuaHang,ChiTietMuaHang,TABLE_MATCHED_EXACT\n",
        encoding="utf-8",
    )
    (tmp_path / "column_mapping_report.csv").write_text(
        "student_table,student_column,answer_column,match_status\n"
        "CT_MuaHang,PMH,PhieuMuaHang,COLUMN_MATCHED_EXACT\n"
        "CT_MuaHang,TongTien,TongTien,COLUMN_MATCHED_EXACT\n",
        encoding="utf-8",
    )

    run_compare_rewritten_sql_on_answer_db(
        db_conn=db_conn,
        ans_db="ans_db",
        stud_db="stud_db",
        submission_id="sub1",
        config=config,
        expected_views=config.views,
        output_report_path=tmp_path / "view_test_report.csv",
        diff_dir=tmp_path / "diffs",
        col_accept_threshold=0.88,
        export_outputs=False,
    )

    view_sql_dir = tmp_path.parent / "view_sql"

    raw_file = view_sql_dir / "raw" / "Cau1.sql"
    body_file = view_sql_dir / "select_body" / "Cau1.sql"
    rw_file = view_sql_dir / "rewritten" / "Cau1.sql"
    diff_file = view_sql_dir / "diff" / "Cau1.diff.txt"

    assert raw_file.exists(), f"Expected raw SQL file at {raw_file}"
    assert body_file.exists(), f"Expected select_body SQL file at {body_file}"
    assert rw_file.exists(), f"Expected rewritten SQL file at {rw_file}"
    assert diff_file.exists(), f"Expected diff file at {diff_file}"

    # raw file must contain the original DDL
    raw_content = raw_file.read_text(encoding="utf-8")
    assert "CREATE VIEW" in raw_content

    # select_body file must not contain CREATE/ALTER VIEW wrapper
    body_content = body_file.read_text(encoding="utf-8")
    assert "CREATE VIEW" not in body_content
    assert "SELECT" in body_content.upper()

    # rewritten file must contain canonical table name (not failure comment)
    rw_content = rw_file.read_text(encoding="utf-8")
    assert "-- Rewrite failed" not in rw_content
    assert "ChiTietMuaHang" in rw_content


def test_sql_files_written_on_rewrite_failure(base_config_data, tmp_path):
    """When rewrite fails, raw and diagnostic rewritten files are still written."""
    config = AssignmentConfig(base_config_data)

    db_conn = MagicMock()
    # DDL references an unmapped table so rewrite will fail
    raw_ddl = "CREATE VIEW Cau1 AS SELECT x.SomeCol FROM dbo.UnknownTable x"
    db_conn.execute_query.return_value = [
        {"view_name": "Cau1", "definition": raw_ddl}
    ]
    db_conn.execute_query_df.return_value = pd.DataFrame({"PhieuMuaHang": ["PMH01"], "TongTien": [100.0]})

    (tmp_path / "table_mapping_report.csv").write_text(
        "student_table,answer_table,match_status\n", encoding="utf-8"
    )
    (tmp_path / "column_mapping_report.csv").write_text(
        "student_table,student_column,answer_column,match_status\n", encoding="utf-8"
    )

    run_compare_rewritten_sql_on_answer_db(
        db_conn=db_conn,
        ans_db="ans_db",
        stud_db="stud_db",
        submission_id="sub1",
        config=config,
        expected_views=config.views,
        output_report_path=tmp_path / "view_test_report.csv",
        diff_dir=tmp_path / "diffs",
        col_accept_threshold=0.88,
        export_outputs=False,
    )

    view_sql_dir = tmp_path.parent / "view_sql"
    raw_file = view_sql_dir / "raw" / "Cau1.sql"
    rw_file = view_sql_dir / "rewritten" / "Cau1.sql"
    diff_file = view_sql_dir / "diff" / "Cau1.diff.txt"

    assert raw_file.exists(), f"Expected raw SQL file even on failure: {raw_file}"
    assert rw_file.exists(), f"Expected diagnostic rewritten file: {rw_file}"
    assert diff_file.exists(), f"Expected diff file even on failure: {diff_file}"

    rw_content = rw_file.read_text(encoding="utf-8")
    assert "-- Rewrite failed" in rw_content
    assert "-- Status:" in rw_content


def test_rewrite_csv_contains_file_paths(base_config_data, tmp_path):
    """view_sql_rewrite_report.csv must include raw_select_sql_path and rewritten_sql_path columns."""
    import csv as csv_module

    config = AssignmentConfig(base_config_data)

    db_conn = MagicMock()
    raw_ddl = "CREATE VIEW Cau1 AS SELECT c.PMH FROM dbo.CT_MuaHang c"
    db_conn.execute_query.return_value = [
        {"view_name": "Cau1", "definition": raw_ddl}
    ]
    db_conn.execute_query_df.side_effect = [
        pd.DataFrame({"PhieuMuaHang": ["PMH01"], "TongTien": [100.0]}),
        pd.DataFrame({"PhieuMuaHang": ["PMH01"], "TongTien": [100.0]}),
    ]

    (tmp_path / "table_mapping_report.csv").write_text(
        "student_table,answer_table,match_status\nCT_MuaHang,ChiTietMuaHang,TABLE_MATCHED_EXACT\n",
        encoding="utf-8",
    )
    (tmp_path / "column_mapping_report.csv").write_text(
        "student_table,student_column,answer_column,match_status\n"
        "CT_MuaHang,PMH,PhieuMuaHang,COLUMN_MATCHED_EXACT\n",
        encoding="utf-8",
    )

    run_compare_rewritten_sql_on_answer_db(
        db_conn=db_conn,
        ans_db="ans_db",
        stud_db="stud_db",
        submission_id="sub1",
        config=config,
        expected_views=config.views,
        output_report_path=tmp_path / "view_test_report.csv",
        diff_dir=tmp_path / "diffs",
        col_accept_threshold=0.88,
        export_outputs=False,
    )

    rewrite_report = tmp_path / "view_sql_rewrite_report.csv"
    assert rewrite_report.exists()
    with open(rewrite_report, "r", encoding="utf-8") as f:
        rows = list(csv_module.DictReader(f))
    assert rows, "view_sql_rewrite_report.csv is empty"
    row = rows[0]
    assert "raw_select_sql_path" in row, "Missing raw_select_sql_path column"
    assert "rewritten_sql_path" in row, "Missing rewritten_sql_path column"
    # Paths should be non-empty for a successful rewrite
    assert row["raw_select_sql_path"], "raw_select_sql_path should not be empty"
    assert row["rewritten_sql_path"], "rewritten_sql_path should not be empty"


def test_extraction_csv_contains_raw_definition_path(base_config_data, tmp_path):
    """view_sql_extraction_report.csv must include raw_definition_path column."""
    import csv as csv_module

    config = AssignmentConfig(base_config_data)

    db_conn = MagicMock()
    raw_ddl = "CREATE VIEW Cau1 AS SELECT c.PMH FROM dbo.CT_MuaHang c"
    db_conn.execute_query.return_value = [
        {"view_name": "Cau1", "definition": raw_ddl}
    ]
    db_conn.execute_query_df.side_effect = [
        pd.DataFrame({"PhieuMuaHang": ["PMH01"], "TongTien": [100.0]}),
        pd.DataFrame({"PhieuMuaHang": ["PMH01"], "TongTien": [100.0]}),
    ]

    (tmp_path / "table_mapping_report.csv").write_text(
        "student_table,answer_table,match_status\nCT_MuaHang,ChiTietMuaHang,TABLE_MATCHED_EXACT\n",
        encoding="utf-8",
    )
    (tmp_path / "column_mapping_report.csv").write_text(
        "student_table,student_column,answer_column,match_status\n"
        "CT_MuaHang,PMH,PhieuMuaHang,COLUMN_MATCHED_EXACT\n",
        encoding="utf-8",
    )

    run_compare_rewritten_sql_on_answer_db(
        db_conn=db_conn,
        ans_db="ans_db",
        stud_db="stud_db",
        submission_id="sub1",
        config=config,
        expected_views=config.views,
        output_report_path=tmp_path / "view_test_report.csv",
        diff_dir=tmp_path / "diffs",
        col_accept_threshold=0.88,
        export_outputs=False,
    )

    extract_report = tmp_path / "view_sql_extraction_report.csv"
    assert extract_report.exists()
    with open(extract_report, "r", encoding="utf-8") as f:
        rows = list(csv_module.DictReader(f))
    assert rows, "view_sql_extraction_report.csv is empty"
    row = rows[0]
    assert "raw_definition_path" in row, "Missing raw_definition_path column"
    assert row["raw_definition_path"], "raw_definition_path should not be empty for a found definition"
