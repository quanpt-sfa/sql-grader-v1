import pytest
from types import SimpleNamespace
from dbcheck.config import AssignmentConfig, ViewConfig
from dbcheck.snapshot.normalizer import NameNormalizer
from dbcheck.structure.view_matcher import match_views_structure
from dbcheck.views.view_reporter import run_view_testing
from dbcheck.structure.type_compatibility import compare_sql_types, is_identifier_column

@pytest.fixture
def base_config_data():
    return {
        "assignment": {
            "name": "Generic Grading Config",
            "protected_answer_db": "00000001"
        },
        "schema": {
            "matching_threshold": 0.8,
            "table_accept_threshold": 0.9,
            "table_ambiguous_threshold": 0.75,
            "column_accept_threshold": 0.88,
            "column_ambiguous_threshold": 0.75,
            "aliases": {
                "tables": {},
                "columns": {
                    "global": {},
                    "by_table": {}
                }
            },
            "abbreviations": {},
            "type_compatibility": {
                "mode": "group_with_warnings",
                "identifier_columns": {
                    "global": [],
                    "by_table": {}
                }
            }
        },
        "views": {
            "mode": "answer_snapshot",
            "expected": []
        }
    }

def test_regression_expected_views_remain_snapshot_driven(base_config_data):
    # Setup config that mentions Cau4 but views_mode is answer_snapshot
    base_config_data["views"] = {
        "mode": "answer_snapshot",
        "expected": [
            {"answer_view": "Cau4", "answer_required": True}
        ]
    }
    config = AssignmentConfig(base_config_data)
    assert config.views_mode == "answer_snapshot"
    
    # Snapshot has only Cau1, Cau2, Cau3
    ans_views = [
        {"view_name": "Cau1", "view_name_canonical": "Cau1", "execution_status": "OK"},
        {"view_name": "Cau2", "view_name_canonical": "Cau2", "execution_status": "OK"},
        {"view_name": "Cau3", "view_name_canonical": "Cau3", "execution_status": "OK"}
    ]
    ans_view_cols = []
    stud_views = []
    stud_view_cols = []
    
    # Run structure matching
    results = match_views_structure(ans_views, stud_views, ans_view_cols, stud_view_cols, config)
    
    # Expected: only Cau1, Cau2, Cau3 are reported as missing (since student has no views)
    # Cau4 must NOT be in the results
    missing_views = [r["answer_object"] for r in results if r["status"] == "MISSING"]
    assert "Cau1" in missing_views
    assert "Cau2" in missing_views
    assert "Cau3" in missing_views
    assert "Cau4" not in missing_views
    assert len(missing_views) == 3

def test_views_explicit_config_mode(base_config_data):
    # Setup explicit config mode
    base_config_data["views"] = {
        "mode": "explicit_config",
        "expected": [
            {"answer_view": "Cau4", "answer_required": True}
        ]
    }
    config = AssignmentConfig(base_config_data)
    assert config.views_mode == "explicit_config"
    
    ans_views = [
        {"view_name": "Cau1", "view_name_canonical": "Cau1", "execution_status": "OK"}
    ]
    ans_view_cols = []
    stud_views = []
    stud_view_cols = []
    
    results = match_views_structure(ans_views, stud_views, ans_view_cols, stud_view_cols, config)
    
    # Expected: only Cau4 is required (even though it's not in answer snapshot)
    missing_views = [r["answer_object"] for r in results if r["status"] == "MISSING"]
    assert "Cau4" in missing_views
    assert "Cau1" not in missing_views
    assert len(missing_views) == 1

def test_no_hardcoded_domain_matching(base_config_data):
    # Arbitrary table names A, B, C
    base_config_data["schema"]["aliases"]["tables"] = {
        "TableA": ["AliasA"],
        "TableB": ["AliasB"]
    }
    config = AssignmentConfig(base_config_data)
    normalizer = NameNormalizer(config)
    
    # Match using configured aliases
    assert normalizer.map_table("AliasA")["answer_table"] == "TableA"
    assert normalizer.map_table("AliasB")["answer_table"] == "TableB"
    
    # NCC and PC must not resolve without config
    res_ncc = normalizer.map_table("NCC")
    assert res_ncc["match_status"] == "TABLE_UNMAPPED"

def test_configured_abbreviation_expansion(base_config_data):
    # Add configured abbreviation
    base_config_data["schema"]["abbreviations"] = {
        "ncc": "NhaCungCap",
        "pc": "PhieuChi"
    }
    base_config_data["schema"]["aliases"]["tables"] = {
        "NhaCungCap": ["NCC_Alias"]
    }
    config = AssignmentConfig(base_config_data)
    normalizer = NameNormalizer(config)
    
    # "NCC" should expand to "nhacungcap" and match NhaCungCap canonical table
    res_ncc = normalizer.map_table("NCC")
    assert res_ncc["match_status"] == "TABLE_MATCHED_ABBREVIATION"
    assert res_ncc["answer_table"] == "NhaCungCap"
    
    # Without abbreviation config, ncc would be unmapped (covered in test_no_hardcoded_domain_matching)

def test_column_scope_restriction(base_config_data):
    # Define table-scoped aliases for TableA and TableB
    base_config_data["schema"]["aliases"]["columns"]["by_table"] = {
        "TableA": {
            "ColA1": ["ma", "code"]
        },
        "TableB": {
            "ColB1": ["ma", "id"]
        }
    }
    config = AssignmentConfig(base_config_data)
    normalizer = NameNormalizer(config)
    
    # Under TableA, "ma" maps to ColA1
    expected_cols_a = [{"column_name": "ColA1"}, {"column_name": "ColA2"}]
    res_a = normalizer.map_column("ma", "TableA", "AliasA", expected_cols_a)
    assert res_a["answer_column"] == "ColA1"
    
    # Under TableB, "ma" maps to ColB1
    expected_cols_b = [{"column_name": "ColB1"}, {"column_name": "ColB2"}]
    res_b = normalizer.map_column("ma", "TableB", "AliasB", expected_cols_b)
    assert res_b["answer_column"] == "ColB1"

def test_type_compatibility_with_identifiers(base_config_data):
    # Configure identifier columns
    base_config_data["schema"]["type_compatibility"]["identifier_columns"] = {
        "global": ["DocNo"],
        "by_table": {
            "TableA": ["TableAPhieu"]
        }
    }
    config = AssignmentConfig(base_config_data)
    
    # 1. participates_in_pk_fk is True -> TYPE_IDENTIFIER_COMPATIBLE_WARNING
    res1 = compare_sql_types("int", "varchar(50)", config, column_name="AnyCol", participates_in_pk_fk=True)
    assert res1["type_status"] == "TYPE_IDENTIFIER_COMPATIBLE_WARNING"
    
    # 2. Global identifier -> TYPE_IDENTIFIER_COMPATIBLE_WARNING
    res2 = compare_sql_types("int", "varchar(50)", config, column_name="DocNo", participates_in_pk_fk=False)
    assert res2["type_status"] == "TYPE_IDENTIFIER_COMPATIBLE_WARNING"
    
    # 3. Table-scoped identifier under correct table -> TYPE_IDENTIFIER_COMPATIBLE_WARNING
    res3 = compare_sql_types("int", "varchar(50)", config, column_name="TableAPhieu", participates_in_pk_fk=False, table_name="TableA")
    assert res3["type_status"] == "TYPE_IDENTIFIER_COMPATIBLE_WARNING"
    
    # 4. Table-scoped identifier under wrong table -> TYPE_MISMATCH
    res4 = compare_sql_types("int", "varchar(50)", config, column_name="TableAPhieu", participates_in_pk_fk=False, table_name="TableB")
    assert res4["type_status"] == "TYPE_MISMATCH"
    
    # 5. Non-identifier type mismatch -> TYPE_MISMATCH
    res5 = compare_sql_types("int", "varchar(50)", config, column_name="SomeDataCol", participates_in_pk_fk=False)
    assert res5["type_status"] == "TYPE_MISMATCH"
