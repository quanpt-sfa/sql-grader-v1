"""
Unit tests for:
1. Canonical table deduplication (fix A)
2. Excluded table logic (fix B)
3. Type normalization, grouping, and compatibility (fix C)
"""
import pytest
from unittest.mock import MagicMock, patch
from pathlib import Path
from dbcheck.structure.type_compatibility import (
    normalize_sql_type,
    get_type_group,
    compare_sql_types,
)


# ---------------------------------------------------------------------------
# Helper builders
# ---------------------------------------------------------------------------

def _make_config(
    tables=None,
    excluded_tables=None,
    mode="group_with_warnings",
    allow_int_decimal=True,
    allow_dec_float=True,
    allow_bit_int=False,
):
    """Create a minimal AssignmentConfig-like mock."""
    from types import SimpleNamespace

    if tables is None:
        tables = {
            "Hang": ["HangHoa", "HTK"],
            "KhachHang": ["NhaCungCap", "KH"],
            "BanHang": ["MuaHang"],
        }
    if excluded_tables is None:
        excluded_tables = []

    from dbcheck.config import SchemaConfig, AssignmentConfig, TypeCompatibilityConfig
    schema_raw = {
        "aliases": {"tables": tables, "columns": {}},
        "excluded_tables": excluded_tables,
        "table_accept_threshold": 0.90,
        "table_ambiguous_threshold": 0.75,
        "column_accept_threshold": 0.88,
        "column_ambiguous_threshold": 0.75,
    }
    schema = SchemaConfig(schema_raw)

    tc = TypeCompatibilityConfig({
        "mode": mode,
        "allow_integer_decimal_compatibility": allow_int_decimal,
        "allow_decimal_float_compatibility": allow_dec_float,
        "allow_bit_integer_compatibility": allow_bit_int,
    })

    cfg = SimpleNamespace(schema=schema, type_compatibility=tc)
    return cfg


# ===========================================================================
# A. Canonical table deduplication
# ===========================================================================

class TestCanonicalDeduplication:
    """Answer snapshot may have both raw ('HangHoa') and canonical ('Hang').
    After deduplication by table_name_canonical, only one required table
    should be generated for 'Hang'."""

    def _run_comparison(self, ans_tables, stud_tables, config):
        """Minimal reproduction of the structure_reporter dedup logic."""
        from dbcheck.snapshot.normalizer import NameNormalizer
        normalizer = NameNormalizer(config)

        # Simulate _build_excluded_set and ans_tables_active
        excluded = set()
        for t in ans_tables:
            if config.schema.is_excluded(t["table_name"]) or config.schema.is_excluded(
                t.get("table_name_canonical", t["table_name"])
            ):
                excluded.add(t["table_name"])
        ans_active = [t for t in ans_tables if t["table_name"] not in excluded]

        excluded_stu = set()
        for t in stud_tables:
            if config.schema.is_excluded(t["table_name"]) or config.schema.is_excluded(
                t.get("table_name_canonical", t["table_name"])
            ):
                excluded_stu.add(t["table_name"])
        stu_active = [t for t in stud_tables if t["table_name"] not in excluded_stu]

        # Build required_answer_canons (the dedup dict)
        required_answer_canons = {}
        for t in ans_active:
            raw = t["table_name"]
            canon = t.get("table_name_canonical") or raw
            if canon not in required_answer_canons:
                required_answer_canons[canon] = raw

        return required_answer_canons

    def test_raw_and_canonical_not_double_counted(self):
        """HangHoa and Hang should both normalize to canon 'Hang'; only one required."""
        config = _make_config(tables={"Hang": ["HangHoa", "HTK"]})
        ans_tables = [
            {"table_name": "HangHoa", "table_name_canonical": "Hang"},
            {"table_name": "Hang",    "table_name_canonical": "Hang"},  # dup canonical
        ]
        stud_tables = []
        canons = self._run_comparison(ans_tables, stud_tables, config)
        assert list(canons.keys()) == ["Hang"], f"Expected only 'Hang', got {list(canons.keys())}"

    def test_all_eight_canonical_tables_deduplicated(self):
        """Simulate the typical 8-table answer set where raw names differ from canonical."""
        tables_alias = {
            "NhanVien":         ["Employee"],
            "KhachHang":        ["Customer", "NhaCungCap"],
            "Hang":             ["Product", "HangHoa", "HTK"],
            "Tien":             ["Currency", "LoaiTien"],
            "BanHang":          ["Sales", "MuaHang"],
            "ChiTietBanHang":   ["SalesDetail", "ChiTietMuaHang"],
            "ThuTien":          ["Receipt", "TraTien"],
            "ChiTietThuTien":   ["ReceiptDetail", "ChiTietTraTien"],
        }
        config = _make_config(tables=tables_alias)
        # Answer DB has BOTH raw and canonical rows
        ans_tables = [
            # raw rows
            {"table_name": "NhaCungCap",      "table_name_canonical": "KhachHang"},
            {"table_name": "HangHoa",         "table_name_canonical": "Hang"},
            {"table_name": "LoaiTien",        "table_name_canonical": "Tien"},
            {"table_name": "MuaHang",         "table_name_canonical": "BanHang"},
            {"table_name": "ChiTietMuaHang",  "table_name_canonical": "ChiTietBanHang"},
            {"table_name": "TraTien",         "table_name_canonical": "ThuTien"},
            {"table_name": "ChiTietTraTien",  "table_name_canonical": "ChiTietThuTien"},
            # canonical rows (duplicates of the same concept)
            {"table_name": "Hang",            "table_name_canonical": "Hang"},
            {"table_name": "Tien",            "table_name_canonical": "Tien"},
        ]
        canons = self._run_comparison(ans_tables, [], config)
        assert len(canons) == 7, f"Expected 7 unique canonicals, got {len(canons)}: {list(canons.keys())}"
        assert "Hang" in canons
        assert "Tien" in canons
        assert "KhachHang" in canons

    def test_student_table_maps_to_canonical_not_raw(self):
        """Student table 'HangHoa' should map to canonical 'Hang', not create a second entry."""
        from dbcheck.snapshot.normalizer import NameNormalizer
        config = _make_config(tables={"Hang": ["HangHoa", "HTK"]})
        normalizer = NameNormalizer(config)
        result = normalizer.map_table("HangHoa")
        assert result["answer_table"] == "Hang"
        assert result["match_status"] in {
            "TABLE_MATCHED_EXACT", "TABLE_MATCHED_ALIAS",
            "TABLE_MATCHED_ABBREVIATION", "TABLE_MATCHED_FUZZY_HIGH"
        }


# ===========================================================================
# B. Excluded table logic
# ===========================================================================

class TestExcludedTables:
    def test_sysdiagrams_always_excluded(self):
        config = _make_config()
        assert config.schema.is_excluded("sysdiagrams")
        assert config.schema.is_excluded("SYSDIAGRAMS")
        assert config.schema.is_excluded("SysDiagrams")

    def test_configured_excluded_table(self):
        config = _make_config(excluded_tables=["Stage_MuaHang", "Stage_TraTien"])
        assert config.schema.is_excluded("Stage_MuaHang")
        assert config.schema.is_excluded("stage_muahang")   # case-insensitive
        assert config.schema.is_excluded("Stage_TraTien")

    def test_accented_excluded_table(self):
        config = _make_config(excluded_tables=["BảngTạm"])
        assert config.schema.is_excluded("BảngTạm")
        assert config.schema.is_excluded("bangtam")  # accent-stripped lowercase

    def test_normal_table_not_excluded(self):
        config = _make_config(excluded_tables=["Stage_MuaHang"])
        assert not config.schema.is_excluded("Hang")
        assert not config.schema.is_excluded("BanHang")
        assert not config.schema.is_excluded("KhachHang")

    def test_empty_excluded_list_only_has_default(self):
        config = _make_config(excluded_tables=[])
        # Only sysdiagrams should be excluded
        assert config.schema.is_excluded("sysdiagrams")
        assert not config.schema.is_excluded("BanHang")


# ===========================================================================
# C. Type normalization
# ===========================================================================

class TestNormalizeSqlType:
    @pytest.mark.parametrize("raw,expected", [
        ("varchar(50)",   "varchar"),
        ("VARCHAR(50)",   "varchar"),
        ("decimal(18,2)", "decimal"),
        ("NUMERIC(10,0)", "numeric"),
        ("nvarchar(255)", "nvarchar"),
        ("[nvarchar](255)", "nvarchar"),
        ("int",           "int"),
        ("INT",           "int"),
        ("datetime2(7)",  "datetime2"),
        ("",              ""),
        ("  float  ",     "float"),
    ])
    def test_normalize(self, raw, expected):
        assert normalize_sql_type(raw) == expected


# ===========================================================================
# D. Type grouping
# ===========================================================================

class TestGetTypeGroup:
    @pytest.mark.parametrize("sql_type,group", [
        ("int",          "integer"),
        ("bigint",       "integer"),
        ("smallint",     "integer"),
        ("tinyint",      "integer"),
        ("decimal",      "fixed_decimal"),
        ("numeric",      "fixed_decimal"),
        ("money",        "fixed_decimal"),
        ("smallmoney",   "fixed_decimal"),
        ("float",        "floating"),
        ("real",         "floating"),
        ("varchar",      "text"),
        ("nvarchar",     "text"),
        ("char",         "text"),
        ("nchar",        "text"),
        ("text",         "text"),
        ("ntext",        "text"),
        ("date",         "date_time"),
        ("datetime",     "date_time"),
        ("datetime2",    "date_time"),
        ("smalldatetime","date_time"),
        ("time",         "date_time"),
        ("bit",          "boolean"),
        ("uniqueidentifier", "guid"),
        ("xml",          "xml_json"),
        ("binary",       "binary"),
        ("varbinary",    "binary"),
        ("image",        "binary"),
    ])
    def test_group(self, sql_type, group):
        assert get_type_group(sql_type) == group

    def test_unknown_type(self):
        assert get_type_group("geometry") == "unknown"
        assert get_type_group("") == "unknown"


# ===========================================================================
# E. Type compatibility comparison
# ===========================================================================

class TestCompareSqlTypes:
    def _cfg(self, **kwargs):
        return _make_config(**kwargs)

    def test_exact_match(self):
        cfg = self._cfg()
        r = compare_sql_types("varchar", "varchar", cfg)
        assert r["type_status"] == "TYPE_MATCH_EXACT"
        assert r["type_score"] == 1.0

    def test_exact_match_with_length_params(self):
        cfg = self._cfg()
        r = compare_sql_types("varchar(50)", "varchar(100)", cfg)
        # Both normalize to 'varchar' -> exact
        assert r["type_status"] == "TYPE_MATCH_EXACT"

    def test_same_group_text(self):
        cfg = self._cfg()
        r = compare_sql_types("varchar", "nvarchar", cfg)
        assert r["type_status"] == "TYPE_MATCH_GROUP"
        assert r["answer_type_group"] == "text"
        assert r["student_type_group"] == "text"

    def test_same_group_integer(self):
        cfg = self._cfg()
        r = compare_sql_types("int", "bigint", cfg)
        assert r["type_status"] == "TYPE_MATCH_GROUP"
        assert r["answer_type_group"] == "integer"

    def test_same_group_decimal(self):
        cfg = self._cfg()
        r = compare_sql_types("decimal", "numeric", cfg)
        assert r["type_status"] == "TYPE_MATCH_GROUP"

    def test_integer_decimal_warning_allowed(self):
        cfg = self._cfg(allow_int_decimal=True)
        r = compare_sql_types("int", "decimal", cfg)
        assert r["type_status"] == "TYPE_COMPATIBLE_WARNING"

    def test_integer_decimal_disallowed(self):
        cfg = self._cfg(allow_int_decimal=False)
        r = compare_sql_types("int", "decimal", cfg)
        assert r["type_status"] == "TYPE_MISMATCH"

    def test_decimal_float_warning_allowed(self):
        cfg = self._cfg(allow_dec_float=True)
        r = compare_sql_types("decimal", "float", cfg)
        assert r["type_status"] == "TYPE_COMPATIBLE_WARNING"

    def test_decimal_float_disallowed(self):
        cfg = self._cfg(allow_dec_float=False)
        r = compare_sql_types("decimal", "float", cfg)
        assert r["type_status"] == "TYPE_MISMATCH"

    def test_bit_integer_disallowed_by_default(self):
        cfg = self._cfg(allow_bit_int=False)
        r = compare_sql_types("bit", "int", cfg)
        assert r["type_status"] == "TYPE_MISMATCH"

    def test_bit_integer_allowed_when_configured(self):
        cfg = self._cfg(allow_bit_int=True)
        r = compare_sql_types("bit", "int", cfg)
        assert r["type_status"] == "TYPE_COMPATIBLE_WARNING"

    def test_text_vs_integer_hard_mismatch(self):
        cfg = self._cfg()
        r = compare_sql_types("varchar", "int", cfg)
        assert r["type_status"] == "TYPE_MISMATCH"
        assert r["type_score"] == 0.0

    def test_datetime_vs_text_hard_mismatch(self):
        cfg = self._cfg()
        r = compare_sql_types("datetime", "varchar", cfg)
        assert r["type_status"] == "TYPE_MISMATCH"

    def test_exact_mode_same_group_is_mismatch(self):
        cfg = self._cfg(mode="exact")
        r = compare_sql_types("varchar", "nvarchar", cfg)
        assert r["type_status"] == "TYPE_MISMATCH"

    def test_exact_mode_same_type_is_match(self):
        cfg = self._cfg(mode="exact")
        r = compare_sql_types("int", "int", cfg)
        assert r["type_status"] == "TYPE_MATCH_EXACT"

    def test_none_config(self):
        """compare_sql_types should work even without a config object."""
        r = compare_sql_types("varchar", "nvarchar", None)
        assert r["type_status"] == "TYPE_MATCH_GROUP"

    def test_money_smallmoney_same_group(self):
        cfg = self._cfg()
        r = compare_sql_types("money", "smallmoney", cfg)
        assert r["type_status"] == "TYPE_MATCH_GROUP"
        assert r["answer_type_group"] == "fixed_decimal"

    def test_date_datetime_same_group(self):
        cfg = self._cfg()
        r = compare_sql_types("date", "datetime2", cfg)
        assert r["type_status"] == "TYPE_MATCH_GROUP"
        assert r["answer_type_group"] == "date_time"
