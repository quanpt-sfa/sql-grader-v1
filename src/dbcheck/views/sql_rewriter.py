import re
from typing import List, Dict, Any, Tuple, Optional, Set
from dbcheck.config import AssignmentConfig
from dbcheck.snapshot.normalizer import normalize_key

class Token:
    def __init__(self, type_: str, value: str, start: int, end: int):
        self.type = type_
        self.value = value
        self.start = start
        self.end = end

    def __repr__(self):
        return f"Token({self.type}, {repr(self.value)})"

def tokenize_sql(sql: str) -> List[Token]:
    """Tokenize a T-SQL query string into a list of Token objects."""
    token_pattern = re.compile(
        r'(?P<WHITESPACE>\s+)'
        r'|(?P<COMMENT_LINE>--[^\r\n]*)'
        r'|(?P<COMMENT_BLOCK>/\*(?:[^*]|\*[^/])*\*/)'
        r'|(?P<STRING>N?\'(?:[^\']|\'\')*\')'
        r'|(?P<IDENTIFIER_BRACKET>\[[^\]]*\])'
        r'|(?P<IDENTIFIER_QUOTE>"[^"]*")'
        r'|(?P<WORD>(?:[a-zA-Z_@#$]|[^\x00-\x7F])(/?[a-zA-Z0-9_@#$]|[^\x00-\x7F])*)'
        r'|(?P<OPERATOR><=|>=|!=|<>|::)'
        r'|(?P<SYMBOL>.)'
    )
    tokens = []
    for match in token_pattern.finditer(sql):
        kind = match.lastgroup
        value = match.group(kind)
        start = match.start()
        end = match.end()
        tokens.append(Token(kind, value, start, end))
    return tokens

def clean_identifier(name: str) -> str:
    """Remove surrounding brackets or double quotes from an identifier."""
    name = name.strip()
    if name.startswith('[') and name.endswith(']'):
        return name[1:-1].strip()
    if name.startswith('"') and name.endswith('"'):
        return name[1:-1].strip()
    return name

def extract_select_body(definition: str) -> str:
    """
    Remove CREATE VIEW / ALTER VIEW DDL wrapper and return SELECT/WITH statement.
    Returns stripped string or raises ValueError if AS wrapper delimiter is not found.
    """
    tokens = tokenize_sql(definition)
    
    # We want to scan and find CREATE/ALTER, then VIEW, then the top-level AS keyword.
    # We keep track of parenthesis depth.
    view_seen = False
    delimiter_token_idx = -1
    paren_depth = 0
    
    for i, t in enumerate(tokens):
        if t.type == "SYMBOL":
            if t.value == "(":
                paren_depth += 1
            elif t.value == ")":
                paren_depth -= 1
        elif t.type == "WORD" and paren_depth == 0:
            val_upper = t.value.upper()
            if val_upper == "VIEW":
                view_seen = True
            elif val_upper == "AS" and view_seen:
                delimiter_token_idx = i
                break
                
    if delimiter_token_idx == -1:
        raise ValueError("Could not find the AS keyword separating CREATE/ALTER VIEW DDL from the query body.")
        
    # Query body is everything after the AS keyword
    body_tokens = tokens[delimiter_token_idx + 1:]
    body_sql = "".join(t.value for t in body_tokens).strip()
    
    # Strip optional trailing semicolons
    while body_sql.endswith(';'):
        body_sql = body_sql[:-1].strip()
        
    return body_sql

REJECTED_KEYWORDS = {
    "INSERT", "UPDATE", "DELETE", "MERGE", "CREATE", "ALTER", "DROP", "TRUNCATE",
    "EXEC", "EXECUTE", "SP_EXECUTESQL", "USE", "WAITFOR", "BACKUP", "RESTORE",
    "OPENQUERY", "OPENROWSET", "OPENDATASOURCE", "BULK", "XP_CMDSHELL"
}

RESERVED_KEYWORDS = {
    "SELECT", "FROM", "JOIN", "ON", "WHERE", "GROUP", "ORDER", "HAVING", "UNION",
    "INTERSECT", "EXCEPT", "WITH", "AS", "INNER", "LEFT", "RIGHT", "FULL", "CROSS",
    "OUTER", "APPLY", "AND", "OR", "NOT", "BY", "TOP", "DISTINCT", "AS", "NULL",
    "CASE", "WHEN", "THEN", "ELSE", "END", "LIKE", "IN", "BETWEEN", "EXISTS", "IS"
}

def sql_safety_audit(tokens: List[Token]) -> Optional[str]:
    """
    Perform security and capability audit on the SQL token list.
    Returns error string if unsafe, else None.
    """
    paren_depth = 0
    for i, t in enumerate(tokens):
        if t.type == "SYMBOL":
            if t.value == "(":
                paren_depth += 1
            elif t.value == ")":
                paren_depth -= 1
            elif t.value == ";":
                # Multiple statements check: if semicolon is followed by any non-whitespace, non-comment token
                # Scan ahead to see if any real tokens follow
                has_subsequent = False
                for t_next in tokens[i+1:]:
                    if t_next.type not in ("WHITESPACE", "COMMENT_LINE", "COMMENT_BLOCK"):
                        has_subsequent = True
                        break
                if has_subsequent:
                    return "Multiple SQL statements separated by semicolon are not allowed."
                    
        elif t.type == "WORD":
            val_upper = t.value.upper()
            if val_upper in REJECTED_KEYWORDS:
                return f"Unsafe or unsupported keyword: {t.value}"
            if val_upper == "INTO":
                # Check if it is SELECT ... INTO
                # Search backward for SELECT keyword at same parenthesis level
                # To be conservative, reject any INTO keyword
                return "SELECT INTO is not allowed."
            if t.value.startswith("#"):
                return "Temporary table creation/usage is not allowed."
                
        elif t.type == "IDENTIFIER_BRACKET":
            val = clean_identifier(t.value)
            if val.startswith("#"):
                return "Temporary table creation/usage is not allowed."
                
    return None

def extract_cte_names(tokens: List[Token]) -> Set[str]:
    """Find all top-level CTE names defined in WITH clauses."""
    ctes = set()
    n = len(tokens)
    i = 0
    while i < n:
        t = tokens[i]
        if t.type == "WORD" and t.value.upper() == "WITH":
            # Scan for CTE definitions: WITH name AS ( ... ), name2 AS ( ... )
            i += 1
            while i < n:
                # Skip whitespace/comments
                while i < n and tokens[i].type in ("WHITESPACE", "COMMENT_LINE", "COMMENT_BLOCK"):
                    i += 1
                if i >= n:
                    break
                cte_name_token = tokens[i]
                if cte_name_token.type not in ("WORD", "IDENTIFIER_BRACKET", "IDENTIFIER_QUOTE"):
                    break
                cte_name = clean_identifier(cte_name_token.value)
                
                # Skip columns definition list if present, e.g. cte_name (col1, col2)
                i += 1
                while i < n and tokens[i].type in ("WHITESPACE", "COMMENT_LINE", "COMMENT_BLOCK"):
                    i += 1
                if i < n and tokens[i].type == "SYMBOL" and tokens[i].value == "(":
                    # Find matching closing paren
                    depth = 1
                    i += 1
                    while i < n and depth > 0:
                        if tokens[i].type == "SYMBOL":
                            if tokens[i].value == "(": depth += 1
                            elif tokens[i].value == ")": depth -= 1
                        i += 1
                
                # Next must be AS
                while i < n and tokens[i].type in ("WHITESPACE", "COMMENT_LINE", "COMMENT_BLOCK"):
                    i += 1
                if i >= n or tokens[i].type != "WORD" or tokens[i].value.upper() != "AS":
                    break
                
                # Next must be ( query_body )
                i += 1
                while i < n and tokens[i].type in ("WHITESPACE", "COMMENT_LINE", "COMMENT_BLOCK"):
                    i += 1
                if i < n and tokens[i].type == "SYMBOL" and tokens[i].value == "(":
                    ctes.add(cte_name.lower())
                    # Skip to closing paren of CTE body
                    depth = 1
                    i += 1
                    while i < n and depth > 0:
                        if tokens[i].type == "SYMBOL":
                            if tokens[i].value == "(": depth += 1
                            elif tokens[i].value == ")": depth -= 1
                        i += 1
                
                # Check if there is a comma for next CTE definition
                while i < n and tokens[i].type in ("WHITESPACE", "COMMENT_LINE", "COMMENT_BLOCK"):
                    i += 1
                if i < n and tokens[i].type == "SYMBOL" and tokens[i].value == ",":
                    i += 1
                else:
                    break
        i += 1
    return ctes

def parse_table_sources(tokens: List[Token], cte_names: Set[str]) -> List[Dict[str, Any]]:
    """
    Parse FROM, JOIN, and APPLY clauses to extract table references and their aliases.
    Handles schema-qualified names and nested parenthesis checks.
    """
    sources = []
    n = len(tokens)
    i = 0
    
    # Set of join keywords that prefix a table source
    join_keywords = {"FROM", "JOIN", "APPLY", "INNER", "LEFT", "RIGHT", "FULL", "CROSS", "OUTER"}
    
    while i < n:
        t = tokens[i]
        if t.type == "WORD" and t.value.upper() in join_keywords:
            val_upper = t.value.upper()
            
            # Skip join modifiers until we hit FROM, JOIN, or APPLY
            if val_upper in ("INNER", "LEFT", "RIGHT", "FULL", "CROSS", "OUTER"):
                i += 1
                continue
                
            # Now we are at FROM, JOIN, or APPLY. The next non-whitespace is the table source
            i += 1
            while i < n:
                # Skip whitespace/comments
                while i < n and tokens[i].type in ("WHITESPACE", "COMMENT_LINE", "COMMENT_BLOCK"):
                    i += 1
                if i >= n:
                    break
                
                # Is it a subquery? (starts with paren)
                if tokens[i].type == "SYMBOL" and tokens[i].value == "(":
                    # Find closing paren of the subquery
                    subquery_start = i
                    depth = 1
                    i += 1
                    while i < n and depth > 0:
                        if tokens[i].type == "SYMBOL":
                            if tokens[i].value == "(": depth += 1
                            elif tokens[i].value == ")": depth -= 1
                        i += 1
                    subquery_end = i - 1
                    
                    # Resolve alias of the subquery if it has one
                    alias = None
                    alias_tokens = []
                    while i < n and tokens[i].type in ("WHITESPACE", "COMMENT_LINE", "COMMENT_BLOCK"):
                        i += 1
                    if i < n and tokens[i].type == "WORD" and tokens[i].value.upper() == "AS":
                        i += 1
                        while i < n and tokens[i].type in ("WHITESPACE", "COMMENT_LINE", "COMMENT_BLOCK"):
                            i += 1
                    if i < n and tokens[i].type in ("WORD", "IDENTIFIER_BRACKET", "IDENTIFIER_QUOTE"):
                        if tokens[i].type == "WORD" and tokens[i].value.upper() in RESERVED_KEYWORDS:
                            pass
                        else:
                            alias = clean_identifier(tokens[i].value)
                            alias_tokens = [i]
                            i += 1
                            
                    sources.append({
                        "is_subquery": True,
                        "subquery_tokens": list(range(subquery_start, subquery_end + 1)),
                        "alias": alias,
                        "alias_tokens": alias_tokens,
                        "physical_name": None,
                        "is_cte": False,
                    })
                    break # break table source loop
                    
                else:
                    # It's an identifier name (schema + table name, dot-separated)
                    name_parts = []
                    start_idx = i
                    last_name_part_idx = i
                    while i < n:
                        if tokens[i].type in ("WORD", "IDENTIFIER_BRACKET", "IDENTIFIER_QUOTE"):
                            name_parts.append(tokens[i])
                            last_name_part_idx = i
                            i += 1
                        else:
                            break
                        # Check for dot separator
                        dot_found = False
                        while i < n and tokens[i].type in ("WHITESPACE", "COMMENT_LINE", "COMMENT_BLOCK"):
                            i += 1
                        if i < n and tokens[i].type == "SYMBOL" and tokens[i].value == ".":
                            dot_found = True
                            i += 1
                            while i < n and tokens[i].type in ("WHITESPACE", "COMMENT_LINE", "COMMENT_BLOCK"):
                                i += 1
                        if not dot_found:
                            break
                            
                    end_idx = last_name_part_idx
                    
                    if not name_parts:
                        break
                        
                    # Extract parts
                    cleaned_parts = [clean_identifier(p.value) for p in name_parts]
                    
                    # Resolve table name and alias
                    alias = None
                    alias_tokens = []
                    
                    # Skip optional AS
                    while i < n and tokens[i].type in ("WHITESPACE", "COMMENT_LINE", "COMMENT_BLOCK"):
                        i += 1
                    if i < n and tokens[i].type == "WORD" and tokens[i].value.upper() == "AS":
                        i += 1
                        while i < n and tokens[i].type in ("WHITESPACE", "COMMENT_LINE", "COMMENT_BLOCK"):
                            i += 1
                            
                    # Next token is alias if it's a word/bracket and not reserved keyword
                    if i < n and tokens[i].type in ("WORD", "IDENTIFIER_BRACKET", "IDENTIFIER_QUOTE"):
                        if tokens[i].type == "WORD" and tokens[i].value.upper() in RESERVED_KEYWORDS:
                            pass
                        else:
                            alias = clean_identifier(tokens[i].value)
                            alias_tokens = [i]
                            i += 1
                            
                    core_table_name = cleaned_parts[-1]
                    is_cte = core_table_name.lower() in cte_names
                    
                    sources.append({
                        "is_subquery": False,
                        "raw_parts": cleaned_parts,
                        "token_range": (start_idx, end_idx),
                        "alias": alias,
                        "alias_tokens": alias_tokens,
                        "physical_name": core_table_name,
                        "is_cte": is_cte,
                    })
                    
                    # Check if there is a comma indicating comma-join
                    while i < n and tokens[i].type in ("WHITESPACE", "COMMENT_LINE", "COMMENT_BLOCK"):
                        i += 1
                    if i < n and tokens[i].type == "SYMBOL" and tokens[i].value == ",":
                        i += 1
                        continue # loop to parse next table source
                    else:
                        break # break table source loop
            continue
            
        i += 1
        
    return sources

def identify_select_output_aliases(tokens: List[Token]) -> Set[int]:
    """
    Scan SELECT lists to identify tokens representing output aliases.
    Returns a set of token indices that should NOT be rewritten.
    """
    output_alias_indices = set()
    n = len(tokens)
    i = 0
    paren_depth = 0
    
    operators = {".", "+", "-", "*", "/", "=", "<", ">", "!", "%", "&", "|", "^", "~", "(", ","}
    
    while i < n:
        t = tokens[i]
        if t.type == "SYMBOL":
            if t.value == "(": paren_depth += 1
            elif t.value == ")": paren_depth -= 1
            
        elif t.type == "WORD" and t.value.upper() == "SELECT" and paren_depth == 0:
            # We are inside SELECT block at depth 0. Scan up to FROM or UNION or end of query
            i += 1
            select_item_tokens = []
            
            while i < n:
                t_sel = tokens[i]
                if t_sel.type == "SYMBOL":
                    if t_sel.value == "(":
                        paren_depth += 1
                    elif t_sel.value == ")":
                        paren_depth -= 1
                        
                # End of SELECT block
                if paren_depth < 0:
                    break
                if t_sel.type == "WORD" and paren_depth == 0 and t_sel.value.upper() in ("FROM", "UNION", "INTERSECT", "EXCEPT"):
                    break
                    
                if t_sel.type == "SYMBOL" and t_sel.value == "," and paren_depth == 0:
                    # End of a select list item. Process accumulated tokens
                    _process_select_item(select_item_tokens, tokens, operators, output_alias_indices)
                    select_item_tokens = []
                else:
                    select_item_tokens.append(i)
                i += 1
                
            # Process last item
            if select_item_tokens:
                _process_select_item(select_item_tokens, tokens, operators, output_alias_indices)
                
            # Decrement loop counter by 1 since outer loop will increment
            i -= 1
            
        i += 1
        
    return output_alias_indices

def _process_select_item(item_indices: List[int], all_tokens: List[Token], operators: Set[str], alias_set: Set[int]) -> None:
    """Helper to detect output alias token in a single select list item."""
    # Filter out whitespace/comments
    real_indices = [idx for idx in item_indices if all_tokens[idx].type not in ("WHITESPACE", "COMMENT_LINE", "COMMENT_BLOCK")]
    if len(real_indices) < 2:
        return
        
    # Check for AS alias
    # Look for AS keyword at depth 0 in this item
    for k in range(len(real_indices) - 1):
        idx_k = real_indices[k]
        if all_tokens[idx_k].type == "WORD" and all_tokens[idx_k].value.upper() == "AS":
            alias_set.add(real_indices[k+1])
            return
            
    # Check for alias = expr
    first_idx = real_indices[0]
    second_idx = real_indices[1]
    if all_tokens[second_idx].type == "SYMBOL" and all_tokens[second_idx].value == "=":
        alias_set.add(first_idx)
        return
        
    # Check for expr alias (no AS)
    last_idx = real_indices[-1]
    prev_idx = real_indices[-2]
    last_token = all_tokens[last_idx]
    prev_token = all_tokens[prev_idx]
    
    if last_token.type in ("WORD", "IDENTIFIER_BRACKET", "IDENTIFIER_QUOTE"):
        # Make sure preceding token is not an operator
        if prev_token.type != "SYMBOL" or prev_token.value not in operators:
            alias_set.add(last_idx)

def rewrite_sql_query(
    sql: str,
    table_map: Dict[str, str],
    column_map: Dict[Tuple[str, str], str],
    config: AssignmentConfig
) -> Dict[str, Any]:
    """
    Conservative clause-aware SQL identifier rewriter.
    Returns dict with rewritten query or failure diagnostics.
    """
    result = {
        "status": "VIEW_SQL_REWRITE_SUCCESS",
        "rewritten_sql": "",
        "table_mappings_used": [],
        "column_mappings_used": [],
        "unmapped_tables": [],
        "unmapped_columns": [],
        "ambiguous_columns": [],
    }
    
    # 1. Tokenize
    tokens = tokenize_sql(sql)
    
    # 2. Safety Audit
    safety_error = sql_safety_audit(tokens)
    if safety_error:
        result["status"] = "VIEW_SQL_UNSAFE_REVIEW"
        result["error_message"] = safety_error
        return result
        
    # 3. CTE Names
    cte_names = extract_cte_names(tokens)
    
    # 4. Table sources
    try:
        table_sources = parse_table_sources(tokens, cte_names)
    except Exception as e:
        result["status"] = "VIEW_SQL_REWRITE_PARSE_ERROR"
        result["error_message"] = f"Failed to parse table sources: {e}"
        return result
        
    # Verify three-part names and reject
    for src in table_sources:
        if not src["is_subquery"] and not src["is_cte"]:
            if len(src["raw_parts"]) >= 3:
                result["status"] = "VIEW_SQL_REWRITE_UNSUPPORTED_SQL"
                result["error_message"] = f"Three-part table name '{'.'.join(src['raw_parts'])}' is not supported."
                return result
                
    # Build alias context and tables mappings
    alias_context = {}  # alias_lower -> {physical_name, canonical_name, is_cte}
    in_scope_physical_tables = []
    
    for src in table_sources:
        if src["is_subquery"]:
            alias = src["alias"]
            if alias:
                alias_context[alias.lower()] = {
                    "physical_name": None,
                    "canonical_name": None,
                    "is_cte": True, # treat as CTE for column lookup fallback
                    "is_subquery": True
                }
        elif src["is_cte"]:
            alias = src["alias"] or src["physical_name"]
            alias_context[alias.lower()] = {
                "physical_name": src["physical_name"],
                "canonical_name": src["physical_name"],
                "is_cte": True,
                "is_subquery": False
            }
        else:
            # Physical table
            phys_t = src["physical_name"]
            phys_t_norm = normalize_key(phys_t)
            
            # Map physical to canonical
            canon_t = None
            for p_t, c_t in table_map.items():
                if normalize_key(p_t) == phys_t_norm:
                    canon_t = c_t
                    break
                    
            if not canon_t:
                result["status"] = "VIEW_SQL_REWRITE_UNMAPPED_TABLE"
                result["unmapped_tables"].append(phys_t)
                return result
                
            result["table_mappings_used"].append(f"{phys_t}->{canon_t}")
            in_scope_physical_tables.append((phys_t, canon_t))
            
            alias = src["alias"] or phys_t
            alias_context[alias.lower()] = {
                "physical_name": phys_t,
                "canonical_name": canon_t,
                "is_cte": False,
                "is_subquery": False
            }
            
    # Gather tokens that must NOT be rewritten as columns (table names & aliases in FROM/JOIN)
    table_join_indices = set()
    for src in table_sources:
        if not src["is_subquery"]:
            start, end = src["token_range"]
            for idx in range(start, end + 1):
                table_join_indices.add(idx)
        for idx in src["alias_tokens"]:
            table_join_indices.add(idx)
            
    # 5. Output aliases
    output_alias_indices = identify_select_output_aliases(tokens)
    
    # 6. Rewrite physical tables in FROM/JOIN in-place
    table_sources_to_rewrite = [src for src in table_sources if not src["is_subquery"] and not src["is_cte"]]
    table_sources_to_rewrite.sort(key=lambda x: x["token_range"][0], reverse=True)
    
    for src in table_sources_to_rewrite:
        start, end = src["token_range"]
        canon_t = alias_context[(src["alias"] or src["physical_name"]).lower()]["canonical_name"]
        
        # Replace the table name tokens with "dbo.[CanonicalTable]"
        use_brackets = any('[' in tokens[idx].value for idx in range(start, end + 1))
        replacement_val = f"dbo.[{canon_t}]" if use_brackets else f"dbo.{canon_t}"
        
        # In-place range replacement
        tokens[start : end + 1] = [Token("WORD", replacement_val, tokens[start].start, tokens[end].end)]
        
    temp_sql = "".join(t.value for t in tokens)
    tokens = tokenize_sql(temp_sql)
    
    # Re-run DDL CTE/table analysis to get new token indices
    cte_names = extract_cte_names(tokens)
    table_sources = parse_table_sources(tokens, cte_names)
    table_join_indices = set()
    for src in table_sources:
        if not src["is_subquery"]:
            start, end = src["token_range"]
            for idx in range(start, end + 1):
                table_join_indices.add(idx)
        for idx in src["alias_tokens"]:
            table_join_indices.add(idx)
            
    output_alias_indices = identify_select_output_aliases(tokens)
    
    # Re-build alias context and in_scope_physical_tables
    alias_context = {}
    in_scope_physical_tables = []
    
    for src in table_sources:
        if src["is_subquery"]:
            alias = src["alias"]
            if alias:
                alias_context[alias.lower()] = {
                    "physical_name": None,
                    "canonical_name": None,
                    "is_cte": True,
                    "is_subquery": True
                }
        elif src["is_cte"]:
            alias = src["alias"] or src["physical_name"]
            alias_context[alias.lower()] = {
                "physical_name": src["physical_name"],
                "canonical_name": src["physical_name"],
                "is_cte": True,
                "is_subquery": False
            }
        else:
            # Already rewritten to dbo.CanonTable or dbo.[CanonTable]
            # Core name is after dbo.
            full_name = src["physical_name"]
            canon_t = full_name.split('.')[-1].replace('[','').replace(']','')
            
            # Find the original student table name by checking which physical name maps to this canon_t
            student_t = None
            for p_t, c_t in table_map.items():
                if c_t == canon_t:
                    student_t = p_t
                    break
            if not student_t:
                student_t = canon_t
                
            in_scope_physical_tables.append((student_t, canon_t))
            alias = src["alias"] or full_name
            alias_context[alias.lower()] = {
                "physical_name": student_t,
                "canonical_name": canon_t,
                "is_cte": False,
                "is_subquery": False
            }
            
    # 7. Identify and rewrite columns
    replacements = {}
    qualified_indices = set()
    
    # Scan for qualified column references: alias.column or dbo.table.column
    i = 0
    n = len(tokens)
    while i < n - 2:
        t1 = tokens[i]
        t2 = tokens[i+1]
        t3 = tokens[i+2]
        
        if t1.type in ("WORD", "IDENTIFIER_BRACKET", "IDENTIFIER_QUOTE") and t2.type == "SYMBOL" and t2.value == "." and t3.type in ("WORD", "IDENTIFIER_BRACKET", "IDENTIFIER_QUOTE"):
            alias_val = clean_identifier(t1.value)
            if alias_val.lower() in alias_context:
                # Standard alias.column
                qualified_indices.update([i, i+1, i+2])
                
                alias_meta = alias_context[alias_val.lower()]
                col_name = clean_identifier(t3.value)
                col_name_norm = normalize_key(col_name)
                
                # Resolve
                resolved_col = None
                if alias_meta["is_cte"]:
                    # Search globally or in physical tables
                    candidates = []
                    for s_t, c_t in in_scope_physical_tables:
                        for (st_t, st_c), ans_c in column_map.items():
                            if normalize_key(st_t) == normalize_key(s_t) and normalize_key(st_c) == col_name_norm:
                                if ans_c not in candidates:
                                    candidates.append(ans_c)
                    if len(candidates) == 1:
                        resolved_col = candidates[0]
                    elif len(candidates) > 1:
                        result["status"] = "VIEW_SQL_REWRITE_AMBIGUOUS_COLUMN"
                        result["ambiguous_columns"].append(col_name)
                        return result
                    else:
                        # Search entire column map
                        global_cands = []
                        for (st_t, st_c), ans_c in column_map.items():
                            if normalize_key(st_c) == col_name_norm:
                                if ans_c not in global_cands:
                                    global_cands.append(ans_c)
                        if len(global_cands) == 1:
                            resolved_col = global_cands[0]
                        elif len(global_cands) > 1:
                            result["status"] = "VIEW_SQL_REWRITE_AMBIGUOUS_COLUMN"
                            result["ambiguous_columns"].append(col_name)
                            return result
                else:
                    # Physical table
                    phys_t = alias_meta["physical_name"]
                    for (st_t, st_c), ans_c in column_map.items():
                        if normalize_key(st_t) == normalize_key(phys_t) and normalize_key(st_c) == col_name_norm:
                            resolved_col = ans_c
                            break
                            
                if not resolved_col:
                    result["status"] = "VIEW_SQL_REWRITE_UNMAPPED_COLUMN"
                    result["unmapped_columns"].append(col_name)
                    return result
                    
                result["column_mappings_used"].append(f"{alias_val}.{col_name}->{resolved_col}")
                
                # Rewrite column token
                use_brackets = '[' in t3.value
                replacements[i+2] = f"[{resolved_col}]" if use_brackets else resolved_col
                
                # If t1 was a physical table name and no alias was used, rewrite t1 too!
                if alias_meta["physical_name"] and alias_val.lower() == alias_meta["physical_name"].lower():
                    # Rewrite table qualifier to canonical name
                    replacements[i] = f"dbo.[{alias_meta['canonical_name']}]" if '[' in t1.value else f"dbo.{alias_meta['canonical_name']}"
                
                i += 3
                continue
                
            elif alias_val.lower() == "dbo" and i < n - 4:
                # Check for dbo.table.column
                t4 = tokens[i+3]
                t5 = tokens[i+4]
                if t4.type == "SYMBOL" and t4.value == "." and t5.type in ("WORD", "IDENTIFIER_BRACKET", "IDENTIFIER_QUOTE"):
                    table_val = clean_identifier(t3.value)
                    if table_val.lower() in alias_context:
                        qualified_indices.update([i, i+1, i+2, i+3, i+4])
                        alias_meta = alias_context[table_val.lower()]
                        col_name = clean_identifier(t5.value)
                        col_name_norm = normalize_key(col_name)
                        
                        resolved_col = None
                        phys_t = alias_meta["physical_name"]
                        if phys_t:
                            for (st_t, st_c), ans_c in column_map.items():
                                if normalize_key(st_t) == normalize_key(phys_t) and normalize_key(st_c) == col_name_norm:
                                    resolved_col = ans_c
                                    break
                                    
                        if not resolved_col:
                            result["status"] = "VIEW_SQL_REWRITE_UNMAPPED_COLUMN"
                            result["unmapped_columns"].append(col_name)
                            return result
                            
                        result["column_mappings_used"].append(f"dbo.{table_val}.{col_name}->{resolved_col}")
                        
                        # Rewrite table name and column
                        use_brackets = '[' in t5.value
                        replacements[i+4] = f"[{resolved_col}]" if use_brackets else resolved_col
                        
                        replacements[i+2] = f"[{alias_meta['canonical_name']}]" if '[' in t3.value else alias_meta['canonical_name']
                        
                        i += 5
                        continue
                        
        i += 1
        
    # Unqualified columns
    for idx, t in enumerate(tokens):
        if idx in qualified_indices or idx in table_join_indices or idx in output_alias_indices:
            continue
            
        if t.type in ("WORD", "IDENTIFIER_BRACKET"):
            # Check if keyword or function
            val = clean_identifier(t.value)
            val_upper = val.upper()
            
            if val_upper in RESERVED_KEYWORDS:
                continue
            if val.lower() in cte_names:
                continue
                
            # Check if function name (followed by whitespace/comment + '(')
            is_func = False
            next_real = None
            for idx_next in range(idx + 1, n):
                if tokens[idx_next].type not in ("WHITESPACE", "COMMENT_LINE", "COMMENT_BLOCK"):
                    next_real = tokens[idx_next]
                    break
            if next_real and next_real.type == "SYMBOL" and next_real.value == "(":
                is_func = True
            if is_func:
                continue
                
            # It's an unqualified column reference!
            col_name_norm = normalize_key(val)
            candidates = []
            
            for s_t, c_t in in_scope_physical_tables:
                for (st_t, st_c), ans_c in column_map.items():
                    if normalize_key(st_t) == normalize_key(s_t) and normalize_key(st_c) == col_name_norm:
                        if (s_t, ans_c) not in candidates:
                            candidates.append((s_t, ans_c))
                            
            if len(candidates) == 1:
                resolved_col = candidates[0][1]
                result["column_mappings_used"].append(f"{val}->{resolved_col}")
                use_brackets = '[' in t.value
                replacements[idx] = f"[{resolved_col}]" if use_brackets else resolved_col
            elif len(candidates) > 1:
                result["status"] = "VIEW_SQL_REWRITE_AMBIGUOUS_COLUMN"
                result["ambiguous_columns"].append(val)
                return result
            else:
                # Check global column map fallback if no match in physical tables
                global_cands = []
                for (st_t, st_c), ans_c in column_map.items():
                    if normalize_key(st_c) == col_name_norm:
                        if ans_c not in global_cands:
                            global_cands.append(ans_c)
                if len(global_cands) == 1:
                    resolved_col = global_cands[0]
                    result["column_mappings_used"].append(f"{val}->{resolved_col}")
                    use_brackets = '[' in t.value
                    replacements[idx] = f"[{resolved_col}]" if use_brackets else resolved_col
                elif len(global_cands) > 1:
                    result["status"] = "VIEW_SQL_REWRITE_AMBIGUOUS_COLUMN"
                    result["ambiguous_columns"].append(val)
                    return result
                else:
                    # Unmapped column
                    result["status"] = "VIEW_SQL_REWRITE_UNMAPPED_COLUMN"
                    result["unmapped_columns"].append(val)
                    return result
                    
    # Apply replacements
    for idx, new_val in replacements.items():
        tokens[idx].value = new_val
        
    result["rewritten_sql"] = "".join(t.value for t in tokens)
    return result
