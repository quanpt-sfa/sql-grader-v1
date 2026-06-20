# Key Adequacy and Relationship-Level Foreign Key Grading Model

This document explains the key adequacy grading model used in the checker to support natural keys, surrogate keys, and alternative database designs.

---

## 1. Primary Key Adequacy

Primary key checks determine whether the student database defines an adequate table row identifier, moving away from strict physical column matching.

### Key Status Classification

*   `PK_MATCH_EXACT`
    The student primary key matches the answer primary key columns exactly (case-insensitive names).

*   `PK_MATCH_ALIAS_EQUIVALENT`
    The student primary key columns differ in name but map to the expected business key columns after applying config-defined name aliases.

*   `PK_SURROGATE_ACCEPTED`
    The student uses a single-column surrogate key (such as an `IDENTITY` integer column, an `ID` column, or columns matching configured surrogate patterns like `{table}id`), and:
    1.  The expected natural business key columns exist in the student table and are defined as `NOT NULL`.
    2.  If `require_business_key_uniqueness` is enabled in config, there is a verified `UNIQUE` constraint or `UNIQUE` index on the natural business key columns in the student table.
    3.  For detail/child tables, all required parent relationship foreign keys are present and valid.

*   `PK_NATURAL_ACCEPTED`
    The student uses a natural business key design while the answer uses a surrogate primary key.

*   `PK_ALTERNATIVE_ACCEPTED`
    Other valid alternative key designs.

*   `PK_REVIEW_REQUIRED`
    A primary key exists but its adequacy cannot be verified automatically (e.g. missing `UNIQUE` constraints on business keys when surrogate PK is used, or missing parent relationships on detail tables).

*   `PK_MISSING`
    No primary key constraint exists on the student table.

*   `PK_INVALID`
    Primary key exists but contains nullable columns or clearly invalid definitions.

---

## 2. Foreign Key Relationship-Level Matching

Foreign key checks match constraints at the logical relationship level rather than matching exact physical column names or types.

### FK Status Classification

*   `FK_MATCH_EXACT`
    Physical column names and tables of the foreign key match the answer database exactly.

*   `FK_ALIAS_EQUIVALENT`
    Foreign key exists between the correct tables, and columns match after alias expansion.

*   `FK_SURROGATE_ACCEPTED`
    Foreign key references the parent table via its surrogate key (e.g., `ChildTable.ParentID -> ParentTable.ParentID`) instead of the business key (e.g., `ChildTable.ParentCode -> ParentTable.ParentCode`).

*   `FK_NATURAL_ACCEPTED`
    Foreign key references the parent table via its business key instead of the surrogate key.

*   `FK_RELATIONSHIP_MATCH`
    The child table references the correct parent table through mapped column pairs representing a valid relationship.

*   `FK_IMPLIED_REVIEW_REQUIRED`
    No declared foreign key constraint exists, but the child table contains columns (like `MaNCC` or `NhaCungCapID`) that strongly imply the relationship. This is flagged for manual review and does not count as a pass.

*   `FK_MISSING`
    No foreign key or column evidence exists representing the parent-child relationship.

*   `FK_WRONG_TARGET`
    The child table declares a foreign key referencing an incorrect parent table.

---

## 3. Configuration Options

Configure key grading options under `schema.key_grading` in `assignment.yaml`:

```yaml
schema:
  key_grading:
    mode: adequacy                           # exact | adequacy
    allow_surrogate_keys: true               # allow student to use IDENTITY / ID columns
    allow_natural_keys: true                 # allow natural business key fallbacks
    require_business_key_uniqueness: false   # if true, requires UNIQUE index on business key under surrogate PK
    surrogate_key_patterns:                  # name patterns matching surrogate keys
      - id
      - "{table}id"
      - "{table}_id"
    business_key_patterns:                   # name patterns matching business key codes
      - ma
      - code
      - so
      - phieu
    natural_key_aliases:                     # table-scoped key aliases
      HangHoa:
        MaHangHoa: [MaHang, MaHTK]
```
