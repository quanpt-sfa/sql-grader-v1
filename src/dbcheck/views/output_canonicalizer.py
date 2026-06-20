import math
import numpy as np
import pandas as pd
from typing import List, Dict, Any, Tuple, Optional
from rapidfuzz import fuzz
from dbcheck.config import AssignmentConfig, ViewConfig
from dbcheck.utils.logging import get_logger
from dbcheck.snapshot.normalizer import normalize_key

def get_tolerance_decimals(tolerance: float) -> int:
    """Calculate the number of decimal places for a given tolerance."""
    if tolerance <= 0:
        return 0
    try:
        return max(0, int(round(-math.log10(tolerance))))
    except Exception:
        return 2

def resolve_view_columns(
    phys_cols: List[str],
    view_config: ViewConfig,
    accepted_table_col_mappings: Optional[Dict[str, Any]] = None,
    column_accept_threshold: float = 0.88
) -> Dict[str, str]:
    """Resolve physical column names in student view output to canonical column names.
    
    Canonicalization priority:
    1. view.expected_output aliases;
    2. accepted table-scoped column mappings;
    3. fuzzy fallback within the expected output columns of that view only.
    """
    logger = get_logger()
    mapping = {}
    
    # Expected canonicals in this view
    expected_canonicals = {normalize_key(col["canonical"]): col for col in view_config.columns}
    
    # Handle backward compatibility: check if accepted_table_col_mappings is actually global_col_aliases (dict of lists)
    global_col_aliases = {}
    mappings_dict = {}
    if accepted_table_col_mappings:
        sample_val = next(iter(accepted_table_col_mappings.values()), None)
        if isinstance(sample_val, (list, tuple)):
            global_col_aliases = accepted_table_col_mappings
        else:
            mappings_dict = {normalize_key(k): v for k, v in accepted_table_col_mappings.items()}
            
    for p_col in phys_cols:
        p_col_norm = normalize_key(p_col)
        matched_canon = None
        
        # 1. view.expected_output aliases & exact match
        for canon_norm, col_meta in expected_canonicals.items():
            if p_col_norm == canon_norm:
                matched_canon = col_meta["canonical"]
                break
            aliases = [normalize_key(a) for a in col_meta.get("aliases", [])]
            if p_col_norm in aliases:
                matched_canon = col_meta["canonical"]
                break
                
        # 2. accepted table-scoped column mappings
        if not matched_canon:
            if p_col_norm in mappings_dict:
                canon_name = mappings_dict[p_col_norm]
                if normalize_key(canon_name) in expected_canonicals:
                    matched_canon = canon_name
                    
        # 2b. Check global column aliases (old format backward compatibility)
        if not matched_canon and global_col_aliases:
            for canon_norm, col_meta in expected_canonicals.items():
                canon_name = col_meta["canonical"]
                global_aliases = [normalize_key(a) for a in global_col_aliases.get(canon_name, [])]
                if p_col_norm in global_aliases:
                    matched_canon = canon_name
                    break
                    
        # 3. fuzzy fallback within expected output columns of this view only
        if not matched_canon:
            fuzzy_candidates = []
            for canon_norm, col_meta in expected_canonicals.items():
                canon_name = col_meta["canonical"]
                score_raw = fuzz.ratio(p_col_norm, normalize_key(canon_name))
                
                # Check aliases from view config
                aliases = [normalize_key(a) for a in col_meta.get("aliases", [])]
                for alias in aliases:
                    score_raw = max(score_raw, fuzz.ratio(p_col_norm, alias))
                    
                if score_raw >= column_accept_threshold * 100.0:
                    fuzzy_candidates.append((canon_name, score_raw))
                    
            if fuzzy_candidates:
                fuzzy_candidates.sort(key=lambda x: x[1], reverse=True)
                top_score = fuzzy_candidates[0][1]
                best = [c for c in fuzzy_candidates if c[1] == top_score]
                if len(best) > 1:
                    raise ValueError(f"Ambiguous fuzzy column mapping for view output: '{p_col}' matches {best}")
                matched_canon = best[0][0]
                
        if matched_canon:
            mapping[p_col] = matched_canon
            
    # Check for mapping ambiguity (multiple physical columns mapped to the same canonical)
    canon_to_phys = {}
    for p_col, c_col in mapping.items():
        canon_to_phys.setdefault(c_col, []).append(p_col)
        
    ambiguous = {c: p for c, p in canon_to_phys.items() if len(p) > 1}
    if ambiguous:
        raise ValueError(f"Ambiguous column mapping: multiple output columns match the same canonical name. {ambiguous}")
        
    return mapping

def canonicalize_view_output(
    df: pd.DataFrame,
    view_config: ViewConfig,
    accepted_table_col_mappings: Optional[Dict[str, Any]] = None,
    column_accept_threshold: float = 0.88
) -> pd.DataFrame:
    """Rename columns, normalize data types, apply rounding/sorting, and handle nulls consistently."""
    if df is None or df.empty:
        return pd.DataFrame()
        
    # Copy to avoid modifying the original DataFrame
    df = df.copy()
    
    # 1. Resolve and rename columns
    col_mapping = resolve_view_columns(list(df.columns), view_config, accepted_table_col_mappings, column_accept_threshold)
    df = df.rename(columns=col_mapping)
    
    # Filter to keep only columns that mapped to expected canonical columns
    expected_names = [col["canonical"] for col in view_config.columns]
    keep_cols = [c for c in df.columns if c in expected_names]
    df = df[keep_cols]
    
    # If any expected columns are completely missing, add them as null columns so DataFrame shape aligns
    for col_name in expected_names:
        if col_name not in df.columns:
            df[col_name] = np.nan
            
    # Reorder columns to match expected order
    df = df[expected_names]
    
    # 2. Normalize data types for each column
    for col_meta in view_config.columns:
        canon_name = col_meta["canonical"]
        col_type = col_meta.get("type", "text").lower()
        
        if col_type in ("decimal", "float", "int", "numeric"):
            # Convert to numeric
            df[canon_name] = pd.to_numeric(df[canon_name], errors="coerce")
            
            # Apply rounding
            decimals = get_tolerance_decimals(view_config.numeric_tolerance)
            df[canon_name] = df[canon_name].round(decimals)
            
            if col_type == "int":
                # Keep as float due to NaNs, but round to 0 decimal places
                df[canon_name] = df[canon_name].round(0)
                
        elif col_type in ("date", "datetime", "time"):
            # Convert to datetime and then to standard ISO string format
            try:
                datetime_series = pd.to_datetime(df[canon_name], errors="coerce")
                
                # If it has only dates (all times are 00:00:00), format as YYYY-MM-DD
                # Otherwise, format as YYYY-MM-DD HH:MM:SS
                if datetime_series.dt.time.eq(pd.Timestamp("00:00:00").time()).all():
                    df[canon_name] = datetime_series.dt.strftime("%Y-%m-%d")
                else:
                    df[canon_name] = datetime_series.dt.strftime("%Y-%m-%d %H:%M:%S")
            except Exception:
                # Fallback to string stripping if datetime parsing fails
                df[canon_name] = df[canon_name].astype(str).str.strip()
                
        else: # Text/fallback string type
            # Convert to string, strip whitespace, lowercase, and normalize spaces.
            # Build the full column with nulls preserved, then assign at once (avoids FutureWarning).
            series = df[canon_name].astype(object)
            normalized = series.where(
                series.isna(),
                series.astype(str)
                    .str.strip()
                    .str.lower()
                    .str.replace(r"\s+", " ", regex=True)
            )
            df[canon_name] = normalized

            
    # 3. Sort rows deterministically
    sort_cols = view_config.sort_by if view_config.sort_by else expected_names
    # Sort columns that are present in the dataframe
    sort_cols_present = [c for c in sort_cols if c in df.columns]
    
    if sort_cols_present:
        # Sort and reset index. We use na_position='last' to be consistent
        df = df.sort_values(by=sort_cols_present, na_position="last").reset_index(drop=True)
        
    return df
