import re

def clean_sql_name(name: str) -> str:
    """Sanitize names for safe SQL identifiers, removing brackets, quotes, and whitespace."""
    if not name:
        return ""
    cleaned = re.sub(r'[\[\]\"\'\s]', '', name)
    cleaned = re.sub(r'[^a-zA-Z0-9_\.]', '', cleaned)
    return cleaned

def is_safe_db_name(name: str) -> bool:
    """Validate database names against SQL Server restrictions and injection risks."""
    if not name:
        return False
    # Must only contain alphanumerics and underscores, max 128 characters
    pattern = r'^[a-zA-Z0-9_]{1,128}$'
    return bool(re.match(pattern, name))
