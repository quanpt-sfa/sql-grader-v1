# SQL Server Schema & View Checker

A lightweight, offline-first, deterministic SQL Server assignment checker. 

## Features
1. **Structure Snapshot**: Extracts a snapshot of the database structure (tables, columns, primary keys, foreign keys, views, view columns) into CSV files.
2. **Structure Comparison**: Compares a student database structure snapshot against an instructor's answer snapshot using mapping rules (exact, alias, and fuzzy matching).
3. **View Behavioral Test**: Seeds test data into copies of the answer and student databases, runs views, canonicalizes the outputs (types, sorting, columns), and performs multiset comparisons (catching duplicate rows).

---

## CLI Commands

### 1. Extract Snapshot
```powershell
dbcheck snapshot \
  --answer-db 00000001 \
  --submissions "D:\exam\baks" \
  --run-dir "D:\exam\runs\run001" \
  --config "configs\assignment.yaml"
```
Or if using an answer backup file:
```powershell
dbcheck snapshot \
  --answer-bak "D:\exam\dapan.bak" \
  --submissions "D:\exam\baks" \
  --run-dir "D:\exam\runs\run001" \
  --config "configs\assignment.yaml"
```

### 2. Compare Structure
```powershell
dbcheck compare-structure \
  --run-dir "D:\exam\runs\run001" \
  --config "configs\assignment.yaml"
```

### 3. Test Views
```powershell
dbcheck test-views \
  --run-dir "D:\exam\runs\run001" \
  --test-data "D:\exam\test_data" \
  --config "configs\assignment.yaml"
```
