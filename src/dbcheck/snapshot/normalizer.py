import re
import unicodedata
from typing import Dict, List, Any, Optional
from rapidfuzz import fuzz
from dbcheck.config import AssignmentConfig
from dbcheck.utils.logging import get_logger

def remove_accents(s: str) -> str:
    """Remove Vietnamese accents and diacritics from a string."""
    s = unicodedata.normalize('NFD', s)
    s = "".join([c for c in s if not unicodedata.combining(c)])
    s = s.replace('đ', 'd').replace('Đ', 'D')
    return s

def normalize_key(s: str) -> str:
    """Normalize a name to a compact key by removing accents, lowercasing, stripping prefixes, and removing all separators."""
    if not s:
        return ""
    # Remove Vietnamese accents
    s = remove_accents(s)
    # Lowercase
    s = s.lower().strip()
    # Strip leading numbering prefixes (e.g. "06. ", "06_", "06-", "06 ")
    s = re.sub(r'^\d+[\.\s_–\-]*', '', s).strip()
    # Strip leading cau/câu prefixes
    s = re.sub(r'^(câu|cau)\s*\d*[\.\s_–\-]*', '', s).strip()
    # Remove all internal spaces, underscores, hyphens, en-dashes
    s = re.sub(r'[\s_–\-]+', '', s)
    return s

def get_column_role(name: str) -> Optional[str]:
    """Identify the semantic role of a column based on name suffix/prefix."""
    name_l = name.lower()
    if any(k in name_l for k in ["ma", "id", "code", "key"]):
        return "ma"
    if any(k in name_l for k in ["ten", "name"]):
        return "ten"
    if any(k in name_l for k in ["ngay", "date", "time"]):
        return "ngay"
    if any(k in name_l for k in ["soluong", "qty", "quantity"]):
        return "soluong"
    if any(k in name_l for k in ["dongia", "price"]) and "tongtien" not in name_l:
        return "dongia"
    if any(k in name_l for k in ["tongtien", "amount", "thanhtien"]):
        return "tongtien"
    return None

def check_roles_compatible(role1: Optional[str], role2: Optional[str]) -> bool:
    """Check if two column semantic roles are compatible."""
    if role1 is not None and role2 is not None:
        return role1 == role2
    return True

class NameNormalizer:
    def __init__(self, config: AssignmentConfig):
        self.config = config
        self.logger = get_logger()

    def _clean_name(self, name: str) -> str:
        """Strip sequence number/dot/space prefixes, câu prefixes, remove accents, and lowercase."""
        name_no_accents = remove_accents(name)
        cleaned = name_no_accents.lower().strip()
        
        # Remove leading numbers, dots, spaces, hyphens, underscores (e.g., "01. HTK" -> "htk")
        cleaned = re.sub(r'^\d+[\.\s_–\-]*', '', cleaned).strip()
        # Remove leading "câu/cau" followed by digit prefix
        cleaned = re.sub(r'^(câu|cau)\s*\d+[\.\s_–\-]*', '', cleaned).strip()
        return cleaned

    def _expand_abbreviations(self, cleaned_name: str) -> str:
        """Expand standard generic database abbreviations and configured abbreviations."""
        expanded = cleaned_name
        expanded = re.sub(r'\bct[_\s]+', 'chitiet', expanded)
        expanded = re.sub(r'\bct\b', 'chitiet', expanded)
        expanded = re.sub(r'^(ct)(?=[a-z])', 'chitiet', expanded)

        # Expand configured domain abbreviations
        abbs = getattr(self.config.schema, "abbreviations", {}) if hasattr(self.config, "schema") else {}
        for abb, full in abbs.items():
            abb_l = abb.lower()
            full_l = full.lower()
            if expanded == abb_l:
                expanded = full_l
            else:
                expanded = re.sub(rf'\b{re.escape(abb_l)}\b', full_l, expanded)
                expanded = re.sub(rf'{re.escape(abb_l)}$', full_l, expanded)
                expanded = re.sub(rf'^{re.escape(abb_l)}', full_l, expanded)

        # Remove all separators for unified match
        expanded = re.sub(r'[_\s\-]+', '', expanded)
        return expanded

    def map_table(self, raw_table: str) -> Dict[str, Any]:
        """Perform multi-phase table mapping and return metadata dictionary."""
        normalized = self._clean_name(raw_table)
        expanded = self._expand_abbreviations(normalized)
        
        candidates = []
        student_key = normalize_key(raw_table)
        
        # 1. Exact match on canonical table name
        for canon in self.config.schema.tables.keys():
            if student_key == normalize_key(canon):
                candidates.append((canon, "TABLE_MATCHED_EXACT", "exact", 100.0))
                
        # 2. Alias match
        if not candidates:
            for canon, aliases in self.config.schema.tables.items():
                for alias in aliases:
                    if student_key == normalize_key(alias):
                        status = "TABLE_MATCHED_ALIAS"
                        candidates.append((canon, status, "alias", 100.0))
                        
        # 3. Abbreviation match
        if not candidates:
            # A. Configured abbreviation expansion match
            if expanded != normalized:
                for canon in self.config.schema.tables.keys():
                    if expanded == normalize_key(canon):
                        candidates.append((canon, "TABLE_MATCHED_ABBREVIATION", "abbreviation", 100.0))
                for canon, aliases in self.config.schema.tables.items():
                    for alias in aliases:
                        if expanded == normalize_key(alias):
                            candidates.append((canon, "TABLE_MATCHED_ABBREVIATION", "abbreviation", 100.0))
            
            # B. Legacy abbreviation match (length <= 3)
            if not candidates and len(normalized) <= 3:
                for canon, aliases in self.config.schema.tables.items():
                    if expanded == normalize_key(canon):
                        candidates.append((canon, "TABLE_MATCHED_ABBREVIATION", "abbreviation", 100.0))
                    for alias in aliases:
                        if expanded == normalize_key(alias):
                            candidates.append((canon, "TABLE_MATCHED_ABBREVIATION", "abbreviation", 100.0))

        # Group and select best mapping method per canonical table
        grouped = {}
        for canon, status, method, score in candidates:
            if canon not in grouped:
                grouped[canon] = (status, method, score)
            else:
                pref = {"exact": 3, "alias": 2, "abbreviation": 1}
                current_method = grouped[canon][1]
                if pref.get(method, 0) > pref.get(current_method, 0):
                    grouped[canon] = (status, method, score)
                    
        candidates = [(k, v[0], v[1], v[2]) for k, v in grouped.items()]
        
        # 4. Fuzzy fallback (only if length > 3 and no exact/alias/abbreviation match)
        if not candidates and len(normalized) > 3:
            expanded = self._expand_abbreviations(normalized)
            fuzzy_candidates = []
            for canon, aliases in self.config.schema.tables.items():
                s_raw = fuzz.ratio(normalized, canon.lower())
                s_exp = fuzz.ratio(expanded, canon.lower())
                max_s = max(s_raw, s_exp)
                
                for alias in aliases:
                    s_alias_raw = fuzz.ratio(normalized, alias.lower())
                    s_alias_exp = fuzz.ratio(expanded, alias.lower())
                    max_s = max(max_s, s_alias_raw, s_alias_exp)
                    
                if max_s >= self.config.schema.table_ambiguous_threshold * 100.0:
                    status = "TABLE_MATCHED_FUZZY_HIGH" if max_s >= self.config.schema.table_accept_threshold * 100.0 else "TABLE_AMBIGUOUS"
                    fuzzy_candidates.append((canon, status, "fuzzy", max_s))
            
            if fuzzy_candidates:
                fuzzy_candidates.sort(key=lambda x: x[3], reverse=True)
                top_score = fuzzy_candidates[0][3]
                candidates = [c for c in fuzzy_candidates if c[3] == top_score]

                
        # 5. Output resolution
        if not candidates:
            return {
                "answer_table": "",
                "student_table": raw_table,
                "raw_student_table": raw_table,
                "normalized_student_table": normalized,
                "expanded_student_table": expanded,
                "compact_student_table": student_key,
                "match_status": "TABLE_UNMAPPED",
                "match_method": "",
                "match_score": 0.0,
                "candidate_tables": "",
                "review_required": True,
                "suggested_alias_entry": f"{raw_table}: []"
            }
        elif len(candidates) > 1:
            cand_names = [c[0] for c in candidates]
            suggested = f"{cand_names[0]}: [{raw_table}]"
            return {
                "answer_table": "",
                "student_table": raw_table,
                "raw_student_table": raw_table,
                "normalized_student_table": normalized,
                "expanded_student_table": expanded,
                "compact_student_table": student_key,
                "match_status": "TABLE_AMBIGUOUS",
                "match_method": "multiple_matches",
                "match_score": candidates[0][3],
                "candidate_tables": ";".join(cand_names),
                "review_required": True,
                "suggested_alias_entry": suggested
            }
        else:
            canon, status, method, score = candidates[0]
            review = status in ["TABLE_AMBIGUOUS"]
            suggested = ""
            if status == "TABLE_MATCHED_FUZZY_HIGH":
                suggested = f"{canon}: [{raw_table}]"
            return {
                "answer_table": canon,
                "student_table": raw_table,
                "raw_student_table": raw_table,
                "normalized_student_table": normalized,
                "expanded_student_table": expanded,
                "compact_student_table": student_key,
                "match_status": status,
                "match_method": method,
                "match_score": score,
                "candidate_tables": canon,
                "review_required": review,
                "suggested_alias_entry": suggested
            }

    def get_canonical_table(self, physical_name: str) -> str:
        """Resolve physical table name to canonical name. Raises ValueError on ambiguity."""
        res = self.map_table(physical_name)
        if res["match_status"] in ["TABLE_MATCHED_EXACT", "TABLE_MATCHED_ALIAS", "TABLE_MATCHED_ABBREVIATION", "TABLE_MATCHED_FUZZY_HIGH"]:
            return res["answer_table"]
        if res["match_status"] == "TABLE_AMBIGUOUS":
            raise ValueError(f"Ambiguous table mapping for '{physical_name}': matches {res['candidate_tables']}")
        return physical_name

    def map_column(
        self,
        raw_column: str,
        canonical_table: str,
        physical_table: str,
        expected_cols: Optional[List[Dict[str, Any]]] = None
    ) -> Dict[str, Any]:
        """Perform multi-phase column mapping within the mapped table context and return metadata dictionary."""
        normalized = self._clean_name(raw_column)
        expanded = self._expand_abbreviations(normalized)
        
        # Build expected columns from config if not provided
        if not expected_cols:
            expected_names = set(self.config.schema.columns_global.keys())
            if canonical_table:
                expected_names.update(self.config.schema.columns_by_table.get(canonical_table, {}).keys())
            expected_cols = [{"column_name": c} for c in expected_names]
            
        table_aliases = self.config.schema.columns_by_table.get(canonical_table, {}) if canonical_table else {}
        global_aliases = self.config.schema.columns_global
        
        natural_aliases = {}
        if hasattr(self.config.schema, "key_grading"):
            natural_aliases = self.config.schema.key_grading.natural_key_aliases.get(canonical_table, {})
            
        candidates = []
        student_key = normalize_key(raw_column)
        
        # 1. Exact match with canonical column name
        for col_meta in expected_cols:
            canon_col = col_meta["column_name"]
            if student_key == normalize_key(canon_col):
                candidates.append((canon_col, "COLUMN_MATCHED_EXACT", "exact", 100.0))
                
        # 2. Alias match (Table-scoped, then Natural Key, then Global)
        for col_meta in expected_cols:
            canon_col = col_meta["column_name"]
            
            # A. Explicit table alias
            t_aliases = table_aliases.get(canon_col, [])
            for alias in t_aliases:
                if student_key == normalize_key(alias):
                    candidates.append((canon_col, "COLUMN_MATCHED_ALIAS", "table_alias", 100.0))
            
            # B. Natural key alias
            nk_aliases = natural_aliases.get(canon_col, [])
            for alias in nk_aliases:
                if student_key == normalize_key(alias):
                    candidates.append((canon_col, "COLUMN_MATCHED_ALIAS", "natural_key_alias", 100.0))
                    
            # C. Global alias
            g_aliases = global_aliases.get(canon_col, [])
            for alias in g_aliases:
                if student_key == normalize_key(alias):
                    candidates.append((canon_col, "COLUMN_MATCHED_ALIAS", "global_alias", 100.0))
                        
        # 3. Abbreviation match
        expanded = self._expand_abbreviations(normalized)
        if expanded != normalized:
            for col_meta in expected_cols:
                canon_col = col_meta["column_name"]
                if expanded == normalize_key(canon_col):
                    candidates.append((canon_col, "COLUMN_MATCHED_ABBREVIATION", "abbreviation", 100.0))
                t_aliases = table_aliases.get(canon_col, [])
                for alias in t_aliases:
                    if expanded == normalize_key(alias):
                        candidates.append((canon_col, "COLUMN_MATCHED_ABBREVIATION", "abbreviation", 100.0))
                nk_aliases = natural_aliases.get(canon_col, [])
                for alias in nk_aliases:
                    if expanded == normalize_key(alias):
                        candidates.append((canon_col, "COLUMN_MATCHED_ABBREVIATION", "abbreviation", 100.0))
                g_aliases = global_aliases.get(canon_col, [])
                for alias in g_aliases:
                    if expanded == normalize_key(alias):
                        candidates.append((canon_col, "COLUMN_MATCHED_ABBREVIATION", "abbreviation", 100.0))
        
        # Legacy abbreviation match (length <= 3)
        if len(normalized) <= 3:
            for col_meta in expected_cols:
                canon_col = col_meta["column_name"]
                if expanded == normalize_key(canon_col):
                    candidates.append((canon_col, "COLUMN_MATCHED_ABBREVIATION", "abbreviation", 100.0))
                t_aliases = table_aliases.get(canon_col, [])
                for alias in t_aliases:
                    if expanded == normalize_key(alias):
                        candidates.append((canon_col, "COLUMN_MATCHED_ABBREVIATION", "abbreviation", 100.0))
                nk_aliases = natural_aliases.get(canon_col, [])
                for alias in nk_aliases:
                    if expanded == normalize_key(alias):
                        candidates.append((canon_col, "COLUMN_MATCHED_ABBREVIATION", "abbreviation", 100.0))
                g_aliases = global_aliases.get(canon_col, [])
                for alias in g_aliases:
                    if expanded == normalize_key(alias):
                        candidates.append((canon_col, "COLUMN_MATCHED_ABBREVIATION", "abbreviation", 100.0))

        # Group and select best mapping method per column
        grouped = {}
        for canon, status, method, score in candidates:
            if canon not in grouped:
                grouped[canon] = (status, method, score)
            else:
                pref = {
                    "table_alias": 5,
                    "natural_key_alias": 4,
                    "exact": 3,
                    "global_alias": 2,
                    "abbreviation": 1,
                    "fuzzy": 0
                }
                current_method = grouped[canon][1]
                if pref.get(method, 0) > pref.get(current_method, 0):
                    grouped[canon] = (status, method, score)
                    
        candidates = [(k, v[0], v[1], v[2]) for k, v in grouped.items()]

        
        # 4. Fuzzy fallback (only if length > 3 and no exact/alias/abbreviation match)
        if not candidates and len(normalized) > 3:
            expanded = self._expand_abbreviations(normalized)
            fuzzy_candidates = []
            for col_meta in expected_cols:
                canon_col = col_meta["column_name"]
                
                s_raw = fuzz.ratio(normalized, canon_col.lower())
                s_exp = fuzz.ratio(expanded, canon_col.lower())
                max_s = max(s_raw, s_exp)
                
                t_aliases = table_aliases.get(canon_col, [])
                for alias in t_aliases:
                    s_alias_raw = fuzz.ratio(normalized, alias.lower())
                    s_alias_exp = fuzz.ratio(expanded, alias.lower())
                    max_s = max(max_s, s_alias_raw, s_alias_exp)
                    
                g_aliases = global_aliases.get(canon_col, [])
                for alias in g_aliases:
                    s_alias_raw = fuzz.ratio(normalized, alias.lower())
                    s_alias_exp = fuzz.ratio(expanded, alias.lower())
                    max_s = max(max_s, s_alias_raw, s_alias_exp)
                    
                if max_s >= self.config.schema.column_ambiguous_threshold * 100.0:
                    status = "COLUMN_MATCHED_FUZZY_HIGH" if max_s >= self.config.schema.column_accept_threshold * 100.0 else "COLUMN_AMBIGUOUS"
                    fuzzy_candidates.append((canon_col, status, "fuzzy", max_s))
                    
            if fuzzy_candidates:
                fuzzy_candidates.sort(key=lambda x: x[3], reverse=True)
                top_score = fuzzy_candidates[0][3]
                candidates = [c for c in fuzzy_candidates if c[3] == top_score]
                
        # 5. Role Guard check on candidates
        guarded_candidates = []
        for canon, status, method, score in candidates:
            c_role = get_column_role(canon)
            s_role = get_column_role(raw_column)
            
            if check_roles_compatible(c_role, s_role):
                guarded_candidates.append((canon, status, method, score, "compatible"))
            else:
                guarded_candidates.append((canon, "COLUMN_INCOMPATIBLE_ROLE", method, score, f"incompatible ({s_role} vs {c_role})"))
                
        # 6. Output resolution
        if not guarded_candidates:
            return {
                "answer_table": canonical_table,
                "student_table": physical_table,
                "answer_column": "",
                "student_column": raw_column,
                "raw_student_column": raw_column,
                "normalized_student_column": normalized,
                "expanded_student_column": expanded,
                "compact_student_column": student_key,
                "match_status": "COLUMN_UNMAPPED",
                "match_method": "",
                "match_score": 0.0,
                "role_guard_result": "",
                "review_required": True,
                "suggested_alias_entry": f"by_table:\n  {canonical_table}:\n    (expected_col): [{raw_column}]"
            }
            
        compatibles = [c for c in guarded_candidates if c[4] == "compatible"]
        if not compatibles:
            canon, status, method, score, role_res = guarded_candidates[0]
            return {
                "answer_table": canonical_table,
                "student_table": physical_table,
                "answer_column": canon,
                "student_column": raw_column,
                "raw_student_column": raw_column,
                "normalized_student_column": normalized,
                "expanded_student_column": expanded,
                "compact_student_column": student_key,
                "match_status": status,
                "match_method": method,
                "match_score": score,
                "role_guard_result": role_res,
                "review_required": True,
                "suggested_alias_entry": ""
            }
            
        if len(compatibles) > 1:
            cand_names = [c[0] for c in compatibles]
            suggested = f"by_table:\n  {canonical_table}:\n    {cand_names[0]}: [{raw_column}]"
            return {
                "answer_table": canonical_table,
                "student_table": physical_table,
                "answer_column": "",
                "student_column": raw_column,
                "raw_student_column": raw_column,
                "normalized_student_column": normalized,
                "expanded_student_column": expanded,
                "compact_student_column": student_key,
                "match_status": "COLUMN_AMBIGUOUS",
                "match_method": "multiple_matches",
                "match_score": compatibles[0][3],
                "role_guard_result": "compatible",
                "review_required": True,
                "suggested_alias_entry": suggested
            }
            
        canon, status, method, score, role_res = compatibles[0]
        review = status in ["COLUMN_AMBIGUOUS"]
        suggested = ""
        if status == "COLUMN_MATCHED_FUZZY_HIGH":
            suggested = f"by_table:\n  {canonical_table}:\n    {canon}: [{raw_column}]"
        return {
            "answer_table": canonical_table,
            "student_table": physical_table,
            "answer_column": canon,
            "student_column": raw_column,
            "raw_student_column": raw_column,
            "normalized_student_column": normalized,
            "expanded_student_column": expanded,
            "compact_student_column": student_key,
            "match_status": status,
            "match_method": method,
            "match_score": score,
            "role_guard_result": role_res,
            "review_required": review,
            "suggested_alias_entry": suggested
        }

    def get_canonical_column(self, physical_name: str, canonical_table: str = None) -> str:
        """Resolve physical column name to canonical name. Raises ValueError on ambiguity."""
        res = self.map_column(physical_name, canonical_table, "")
        if res["match_status"] in ["COLUMN_MATCHED_EXACT", "COLUMN_MATCHED_ALIAS", "COLUMN_MATCHED_ABBREVIATION", "COLUMN_MATCHED_FUZZY_HIGH"]:
            return res["answer_column"]
        if res["match_status"] == "COLUMN_AMBIGUOUS":
            raise ValueError(f"Ambiguous column mapping for '{physical_name}' in table '{canonical_table}': matches {res['suggested_alias_entry']}")
        return physical_name
