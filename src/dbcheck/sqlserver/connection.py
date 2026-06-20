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
        self.server = server
        self.default_db = default_db
        self.driver = get_best_driver()
        self.logger = get_logger()
        self.logger.info(f"Using SQL Server ODBC driver: {self.driver}")

    def get_conn_str(self, db_name: Optional[str] = None) -> str:
        db = db_name if db_name else self.default_db
        # ODBC Driver 18 requires TrustServerCertificate=yes and Encrypt=no for local dev servers without certs
        trust = ";TrustServerCertificate=yes;Encrypt=no" if "ODBC Driver 18" in self.driver else ""
        return f"DRIVER={{{self.driver}}};SERVER={self.server};DATABASE={db};Trusted_Connection=yes{trust};"

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
