import pytest
from pathlib import Path
from dbcheck.utils.names import clean_sql_name, is_safe_db_name
from dbcheck.sqlserver.safety import get_protected_dbs, is_db_protected, check_quarantine, extract_submission_id

def test_clean_sql_name():
    assert clean_sql_name("[dbo].[KhachHang]") == "dbo.KhachHang"
    assert clean_sql_name("Khach Hang") == "KhachHang"
    assert clean_sql_name("table_123; DROP TABLE x;") == "table_123DROPTABLEx"

def test_is_safe_db_name():
    assert is_safe_db_name("grade_tmp_23701621_20260620_153000") is True
    assert is_safe_db_name("master") is True
    assert is_safe_db_name("db-name") is False  # contains hyphen
    assert is_safe_db_name("db name") is False  # contains space
    assert is_safe_db_name("db;drop") is False  # contains semicolon

def test_protected_dbs():
    protected = get_protected_dbs("00000001")
    assert "master" in protected
    assert "tempdb" in protected
    assert "00000001" in protected
    
    assert is_db_protected("master", "00000001") is True
    assert is_db_protected("00000001", "00000001") is True
    assert is_db_protected("00000001 ", "00000001") is True
    assert is_db_protected("my_student_db", "00000001") is False

def test_extract_submission_id():
    assert extract_submission_id(Path("C1_01_AN_23701621.BAK")) == "23701621"
    assert extract_submission_id(Path("23690001.bak")) == "23690001"
    assert extract_submission_id(Path("no_digits_name.bak")) == "no_digits_name"

def test_check_quarantine():
    # 1. Filename contains protected DB name
    q, reason = check_quarantine(Path("C1_00000001_AN.BAK"), "00000001")
    assert q is True
    assert "contains protected database name" in reason
    
    # 2. Extracted ID matches protected DB
    q, reason = check_quarantine(Path("00000001.bak"), "00000001")
    assert q is True
    assert "protected database name" in reason
    
    # 3. SQL injection pattern
    q, reason = check_quarantine(Path("C1; DROP DATABASE master--.bak"), "00000001")
    assert q is True
    assert "potential SQL injection" in reason
    
    # 4. Safe filename
    q, reason = check_quarantine(Path("C1_01_AN_23701621.BAK"), "00000001")
    assert q is False
    assert reason == ""
