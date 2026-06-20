import pytest
from types import SimpleNamespace
from dbcheck.config import AssignmentConfig
from dbcheck.snapshot.normalizer import NameNormalizer
from dbcheck.structure.constraint_checker import match_constraints, is_surrogate_column
from dbcheck.structure.structure_reporter import run_structure_comparison

@pytest.fixture
def base_config_data():
    return {
        "assignment": {
            "name": "Key Adequacy Test",
            "protected_answer_db": "00000001"
        },
        "schema": {
            "matching_threshold": 0.8,
            "table_accept_threshold": 0.9,
            "table_ambiguous_threshold": 0.75,
            "column_accept_threshold": 0.88,
            "column_ambiguous_threshold": 0.75,
            "aliases": {
                "tables": {
                    "HangHoa": ["Hang"],
                    "MuaHang": ["Purchase"],
                    "ChiTietMuaHang": ["PurchaseDetail"]
                },
                "columns": {
                    "global": {},
                    "by_table": {
                        "HangHoa": {
                            "MaHangHoa": ["MaHang_Explicit"]
                        }
                    }
                }
            },
            "abbreviations": {},
            "key_grading": {
                "mode": "adequacy",
                "allow_surrogate_keys": True,
                "allow_natural_keys": True,
                "require_business_key_uniqueness": True,
                "surrogate_key_patterns": ["id", "{table}id", "{table}_id"],
                "natural_key_aliases": {
                    "HangHoa": {
                        "MaHangHoa": ["MaHang_KeyGrading"]
                    },
                    "MuaHang": {
                        "PhieuMuaHang": ["PMH", "SoHoaDon"]
                    }
                }
            }
        },
        "views": {
            "mode": "answer_snapshot",
            "expected": []
        }
    }

def test_alias_precedence(base_config_data):
    config = AssignmentConfig(base_config_data)
    normalizer = NameNormalizer(config)
    
    # Precedence: Explicit (1) > KeyGrading (2) > Generic (3)
    # 1. Explicit table alias matches MaHangHoa
    expected_cols = [{"column_name": "MaHangHoa"}]
    res = normalizer.map_column("MaHang_Explicit", "HangHoa", "HangHoa", expected_cols)
    assert res["answer_column"] == "MaHangHoa"
    assert res["match_method"] == "table_alias"

    # 2. Key grading alias matches MaHangHoa
    res2 = normalizer.map_column("MaHang_KeyGrading", "HangHoa", "HangHoa", expected_cols)
    assert res2["answer_column"] == "MaHangHoa"
    assert res2["match_method"] == "natural_key_alias"

def test_is_surrogate_column(base_config_data):
    config = AssignmentConfig(base_config_data)
    
    # MuaHangID -> matches MuaHang patterns
    assert is_surrogate_column("MuaHangID", "MuaHang", 0, config) is True
    assert is_surrogate_column("muahang_id", "MuaHang", 0, config) is True
    # id -> always surrogate
    assert is_surrogate_column("id", "SomeTable", 0, config) is True
    # identity column -> surrogate
    assert is_surrogate_column("col", "SomeTable", 1, config) is True
    # normal column -> not surrogate
    assert is_surrogate_column("MaHangHoa", "HangHoa", 0, config) is False

def test_pk_adequacy_natural_key_exact_and_alias(base_config_data):
    config = AssignmentConfig(base_config_data)
    
    ans_pks = [{"table_name_canonical": "MuaHang", "column_name_canonical": "PhieuMuaHang", "key_ordinal": 1}]
    
    # A. Exact physical match
    stud_pks_exact = [{"table_name_canonical": "MuaHang", "column_name_canonical": "PhieuMuaHang", "key_ordinal": 1}]
    stud_cols = [
        {"table_name": "MuaHang", "column_name": "PhieuMuaHang", "column_name_canonical": "PhieuMuaHang", "is_nullable": 0, "is_identity": 0}
    ]
    ans_cols = [
        {"table_name_canonical": "MuaHang", "column_name_canonical": "PhieuMuaHang", "column_name": "PhieuMuaHang", "is_identity": 0}
    ]
    col_mappings = [
        {"answer_table": "MuaHang", "student_table": "MuaHang", "answer_column": "PhieuMuaHang", "student_column": "PhieuMuaHang"}
    ]
    
    res_exact, key_report, _, counts = match_constraints(
        ans_pks, stud_pks_exact, [], [], {"MuaHang": "MuaHang"},
        config, col_mappings, ans_cols, stud_cols, []
    )
    
    assert counts["pk_exact_match_count"] == 1
    assert key_report[0]["key_status"] == "PK_MATCH_EXACT"

    # B. Alias match
    stud_pks_alias = [{"table_name_canonical": "MuaHang", "column_name_canonical": "PhieuMuaHang", "key_ordinal": 1}]
    stud_cols_alias = [
        {"table_name": "MuaHang", "column_name": "PMH", "column_name_canonical": "PhieuMuaHang", "is_nullable": 0, "is_identity": 0}
    ]
    col_mappings_alias = [
        {"answer_table": "MuaHang", "student_table": "MuaHang", "answer_column": "PhieuMuaHang", "student_column": "PMH"}
    ]
    
    _, key_report_alias, _, counts_alias = match_constraints(
        ans_pks, stud_pks_alias, [], [], {"MuaHang": "MuaHang"},
        config, col_mappings_alias, ans_cols, stud_cols_alias, []
    )
    assert counts_alias["pk_alias_equivalent_count"] == 1
    assert key_report_alias[0]["key_status"] == "PK_MATCH_ALIAS_EQUIVALENT"

def test_pk_surrogate_accepted_and_review(base_config_data):
    config = AssignmentConfig(base_config_data)
    
    ans_pks = [{"table_name_canonical": "MuaHang", "column_name_canonical": "PhieuMuaHang", "key_ordinal": 1}]
    
    # Surrogate PK, expected business key exists and unique
    stud_pks = [{"table_name_canonical": "MuaHang", "column_name_canonical": "MuaHangID", "key_ordinal": 1}]
    stud_cols = [
        {"table_name": "MuaHang", "column_name": "MuaHangID", "column_name_canonical": "MuaHangID", "is_nullable": 0, "is_identity": 1},
        {"table_name": "MuaHang", "column_name": "PhieuMuaHang", "column_name_canonical": "PhieuMuaHang", "is_nullable": 0, "is_identity": 0}
    ]
    ans_cols = [
        {"table_name_canonical": "MuaHang", "column_name_canonical": "PhieuMuaHang", "column_name": "PhieuMuaHang", "is_identity": 0}
    ]
    col_mappings = [
        {"answer_table": "MuaHang", "student_table": "MuaHang", "answer_column": "PhieuMuaHang", "student_column": "PhieuMuaHang"},
        {"answer_table": "MuaHang", "student_table": "MuaHang", "answer_column": "MuaHangID", "student_column": "MuaHangID"}
    ]
    
    # 1. Verification fails if no unique index exists when require_business_key_uniqueness is True
    _, key_report1, _, counts1 = match_constraints(
        ans_pks, stud_pks, [], [], {"MuaHang": "MuaHang"},
        config, col_mappings, ans_cols, stud_cols, []
    )
    assert key_report1[0]["key_status"] == "PK_REVIEW_REQUIRED"
    assert counts1["pk_review_required_count"] == 1

    # 2. Verification passes with unique index
    stud_uniques = [
        {"table_name_canonical": "MuaHang", "constraint_name": "UQ_PMH", "column_name_canonical": "PhieuMuaHang", "key_ordinal": 1}
    ]
    _, key_report2, _, counts2 = match_constraints(
        ans_pks, stud_pks, [], [], {"MuaHang": "MuaHang"},
        config, col_mappings, ans_cols, stud_cols, stud_uniques
    )
    assert key_report2[0]["key_status"] == "PK_SURROGATE_ACCEPTED"
    assert counts2["pk_surrogate_accepted_count"] == 1

def test_fk_adequacy_exact_alias_and_surrogate(base_config_data):
    config = AssignmentConfig(base_config_data)
    
    ans_fks = [{
        "parent_table_canonical": "ChiTietMuaHang", "parent_column_canonical": "PhieuMuaHang",
        "referenced_table_canonical": "MuaHang", "referenced_column_canonical": "PhieuMuaHang",
        "fk_name": "FK_Detail_Parent", "delete_rule": "NO_ACTION", "update_rule": "NO_ACTION"
    }]
    
    ans_cols = [
        {"table_name_canonical": "ChiTietMuaHang", "column_name_canonical": "PhieuMuaHang", "column_name": "PhieuMuaHang", "is_identity": 0},
        {"table_name_canonical": "MuaHang", "column_name_canonical": "PhieuMuaHang", "column_name": "PhieuMuaHang", "is_identity": 0}
    ]
    
    # 1. Exact match
    stud_fks_exact = [{
        "parent_table_canonical": "ChiTietMuaHang", "parent_column_canonical": "PhieuMuaHang",
        "referenced_table_canonical": "MuaHang", "referenced_column_canonical": "PhieuMuaHang",
        "fk_name": "FK_Detail_Parent", "delete_rule": "NO_ACTION", "update_rule": "NO_ACTION"
    }]
    stud_cols = [
        {"table_name": "ChiTietMuaHang", "column_name": "PhieuMuaHang", "column_name_canonical": "PhieuMuaHang", "is_nullable": 0, "is_identity": 0},
        {"table_name": "MuaHang", "column_name": "PhieuMuaHang", "column_name_canonical": "PhieuMuaHang", "is_nullable": 0, "is_identity": 0}
    ]
    
    _, _, fk_report1, counts1 = match_constraints(
        [], [], ans_fks, stud_fks_exact, {"ChiTietMuaHang": "ChiTietMuaHang", "MuaHang": "MuaHang"},
        config, [], ans_cols, stud_cols, []
    )
    assert fk_report1[0]["fk_status"] == "FK_MATCH_EXACT"
    assert counts1["fk_exact_match_count"] == 1

    # 2. Surrogate match
    stud_fks_surrogate = [{
        "parent_table_canonical": "ChiTietMuaHang", "parent_column_canonical": "MuaHangID",
        "referenced_table_canonical": "MuaHang", "referenced_column_canonical": "MuaHangID",
        "fk_name": "FK_Detail_Parent_Surr", "delete_rule": "NO_ACTION", "update_rule": "NO_ACTION"
    }]
    stud_cols_surr = [
        {"table_name": "ChiTietMuaHang", "column_name": "MuaHangID", "column_name_canonical": "MuaHangID", "is_nullable": 0, "is_identity": 0},
        {"table_name": "MuaHang", "column_name": "MuaHangID", "column_name_canonical": "MuaHangID", "is_nullable": 0, "is_identity": 1}
    ]
    
    _, _, fk_report2, counts2 = match_constraints(
        [], [], ans_fks, stud_fks_surrogate, {"ChiTietMuaHang": "ChiTietMuaHang", "MuaHang": "MuaHang"},
        config, [], ans_cols, stud_cols_surr, []
    )
    assert fk_report2[0]["fk_status"] == "FK_SURROGATE_ACCEPTED"
    assert counts2["fk_surrogate_accepted_count"] == 1

def test_fk_implied_review_required(base_config_data):
    config = AssignmentConfig(base_config_data)
    
    ans_fks = [{
        "parent_table_canonical": "ChiTietMuaHang", "parent_column_canonical": "PhieuMuaHang",
        "referenced_table_canonical": "MuaHang", "referenced_column_canonical": "PhieuMuaHang",
        "fk_name": "FK_Detail_Parent", "delete_rule": "NO_ACTION", "update_rule": "NO_ACTION"
    }]
    ans_cols = [
        {"table_name_canonical": "ChiTietMuaHang", "column_name_canonical": "PhieuMuaHang", "column_name": "PhieuMuaHang", "is_identity": 0},
        {"table_name_canonical": "MuaHang", "column_name_canonical": "PhieuMuaHang", "column_name": "PhieuMuaHang", "is_identity": 0}
    ]
    
    # Student has no declared FK, but has column 'PhieuMuaHang' (or 'MuaHangID') in ChiTietMuaHang
    stud_cols = [
        {"table_name": "ChiTietMuaHang", "column_name": "PhieuMuaHang", "column_name_canonical": "PhieuMuaHang", "is_nullable": 0, "is_identity": 0},
        {"table_name": "MuaHang", "column_name": "PhieuMuaHang", "column_name_canonical": "PhieuMuaHang", "is_nullable": 0, "is_identity": 0}
    ]
    
    _, _, fk_report, counts = match_constraints(
        [], [], ans_fks, [], {"ChiTietMuaHang": "ChiTietMuaHang", "MuaHang": "MuaHang"},
        config, [], ans_cols, stud_cols, []
    )
    assert fk_report[0]["fk_status"] == "FK_IMPLIED_REVIEW_REQUIRED"
    assert counts["fk_review_required_count"] == 1
