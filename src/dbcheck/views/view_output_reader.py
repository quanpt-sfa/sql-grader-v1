import pandas as pd
from typing import Tuple, Optional
from dbcheck.sqlserver.connection import SQLServerConnection
from dbcheck.utils.logging import get_logger

def read_view_output(db_conn: SQLServerConnection, db_name: str, view_name: str) -> Tuple[Optional[pd.DataFrame], Optional[str]]:
    """Execute 'SELECT * FROM [view]' on target database.
    
    Returns (DataFrame, error_message).
    If execution fails, DataFrame is None and error_message contains details.
    """
    logger = get_logger()
    sql = f"SELECT * FROM [{view_name}]"
    try:
        df = db_conn.execute_query_df(sql, db_name=db_name)
        logger.debug(f"Successfully retrieved output for view '{view_name}' ({len(df)} rows)")
        return df, None
    except Exception as e:
        error_msg = str(e)
        logger.warning(f"Failed to execute view '{view_name}' on database '{db_name}': {error_msg}")
        return None, error_msg
