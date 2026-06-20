import os
import pyodbc
import pandas as pd
from typing import List, Dict, Any, Optional
from dbcheck.utils.logging import get_logger

def get_best_driver() -> str:
    """Find the best installed SQL Server ODBC driver."""
    available = pyodbc.drivers()
    for driver in ["ODBC Driver 18 for SQL Server", "ODBC Driver 17 for SQL Server", "SQL Server Native Client 11.0", "SQL Server"]:
        if driver in available:
            return driver
    # Fallback to first available if none match
    return available[0] if available else "SQL Server"

class SQLServerConnection:
    def __init__(self, server: str = ".", default_db: str = "master"):
        self.server = os.environ.get("DB_SERVER", server)
        self.default_db = default_db
        env_driver = os.environ.get("DB_DRIVER")
        if env_driver:
            self.driver = env_driver
        else:
            self.driver = get_best_driver()
        self.logger = get_logger()
        self.logger.info(f"Using SQL Server ODBC driver: {self.driver}")

    def get_conn_str(self, db_name: Optional[str] = None) -> str:
        db = db_name if db_name else self.default_db
        
        server = os.environ.get("DB_SERVER", self.server)
        driver = os.environ.get("DB_DRIVER", self.driver)
        auth_mode = os.environ.get("DB_AUTH_MODE", "windows").lower()
        trust_cert = os.environ.get("DB_TRUST_CERT", "").lower()
        
        parts = [
            f"DRIVER={{{driver}}}",
            f"SERVER={server}",
            f"DATABASE={db}"
        ]
        
        if auth_mode == "sql":
            user = os.environ.get("DB_USER", "")
            pwd = os.environ.get("DB_PASSWORD", "")
            parts.append(f"UID={user}")
            parts.append(f"PWD={pwd}")
        else:
            parts.append("Trusted_Connection=yes")
            
        # Handle TrustServerCertificate & Encrypt
        if trust_cert == "yes":
            parts.append("TrustServerCertificate=yes")
            parts.append("Encrypt=no")
        elif trust_cert == "no":
            parts.append("TrustServerCertificate=no")
        else:
            # Fallback to default ODBC Driver 18 logic
            if "ODBC Driver 18" in driver:
                parts.append("TrustServerCertificate=yes")
                parts.append("Encrypt=no")
                
        return ";".join(parts) + ";"

    def get_connection(self, db_name: Optional[str] = None, autocommit: bool = False) -> pyodbc.Connection:
        conn_str = self.get_conn_str(db_name)
        conn = pyodbc.connect(conn_str)
        conn.autocommit = autocommit
        return conn

    def execute_query(self, sql: str, params: Optional[List[Any]] = None, db_name: Optional[str] = None) -> List[Dict[str, Any]]:
        """Execute a query and return a list of dictionaries (rows)."""
        conn = self.get_connection(db_name)
        cursor = conn.cursor()
        try:
            if params:
                cursor.execute(sql, params)
            else:
                cursor.execute(sql)
            
            # Fetch column names
            columns = [column[0] for column in cursor.description] if cursor.description else []
            rows = cursor.fetchall()
            
            result = []
            for row in rows:
                result.append(dict(zip(columns, row)))
            return result
        finally:
            cursor.close()
            conn.close()

    def execute_non_query(self, sql: str, params: Optional[List[Any]] = None, db_name: Optional[str] = None, autocommit: bool = True) -> None:
        """Execute a non-query command (e.g. CREATE, DROP, UPDATE, RESTORE)."""
        conn = self.get_connection(db_name, autocommit=autocommit)
        cursor = conn.cursor()
        try:
            if params:
                cursor.execute(sql, params)
            else:
                cursor.execute(sql)
            # Drain all message/result sets to ensure the command finishes execution
            while cursor.nextset():
                pass
            if not autocommit:
                conn.commit()
        finally:
            cursor.close()
            conn.close()

    def execute_query_df(self, sql: str, params: Optional[List[Any]] = None, db_name: Optional[str] = None) -> pd.DataFrame:
        """Execute a query and return a pandas DataFrame."""
        conn_str = self.get_conn_str(db_name)
        # Using a context manager with pyodbc connection is recommended
        with pyodbc.connect(conn_str) as conn:
            # pandas read_sql can take parameters
            return pd.read_sql(sql, conn, params=params)
