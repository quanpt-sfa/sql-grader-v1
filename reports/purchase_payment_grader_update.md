# Grader Update Walkthrough: Purchase-Payment Exam (DeThiCa3.docx)

This report details the updates made to the grading pipeline for the **Purchase-Payment REA Assignment (DeThiCa3)** exam context, and explains how to run and verify the grader.

---

## 1. Context and Updates

The exam assignment has transitioned from a Sales-Payment process to a **Purchase-Payment REA process**. The core database schema models the following:

- **Resources**: `HangHoa` (Goods/Inventory), `LoaiTien` (Currency)
- **Events**: `MuaHang` (Purchase), `TraTien` (Payment/Cash Disbursement)
- **Agents**: `NhaCungCap` (Supplier), `NhanVien` (Employee)
- **Associations**: `ChiTietMuaHang` (Purchase Details), `ChiTietTraTien` (Payment Details)

### Main Grader Updates
1. **Config File (`configs/assignment_purchase_payment_ca3.yaml`)**:
   - Mapped all Purchase-Payment REA concepts with canonical names and standard Vietnamese/English abbreviations and aliases (e.g. `CT_ChiTien`, `PhieuChi`, `NguoiBan`, etc.).
   - Configured `views` to grade **Câu 1**, **Câu 2**, and **Câu 3** using full answer-backed checks, and **Câu 4** as student-required but answer-optional (`check_mode: execution_only` and `answer_required: false`).
2. **Identifier Type Warnings (`TYPE_IDENTIFIER_COMPATIBLE_WARNING`)**:
   - Implemented warning-level widening matches on identifier columns (such as `MaNhanVien` or `SoPhieuTraTien`). If a student uses `char` for an identifier that is `int` in the answer schema, it triggers a warning instead of a hard type mismatch.
   - Preserves actual PK/FK missing or target mismatched constraints as hard structural errors.
3. **Separation of Row Count Mismatches**:
   - Row count mismatch is now reported as a data import warning under `data_rowcount_mismatch_count` in the summary metrics, rather than a schema mismatch.
4. **CSV Seeding Patches (`test_data_loader.py`)**:
   - Normalized headers dynamically against canonical mappings before resolving `identity` properties. This prevents failures during `IDENTITY_INSERT` on answer and student tables.

---

## 2. Verification Run Summary

The full pipeline has been executed on the test runs (`runs/run_test`) containing student submissions:
- **`23701621`** (Restored as `grade_tmp_23701621_*`)
- **`23708511`** (Restored as `grade_tmp_23708511_*`)

### Pipeline Execution Outputs
- **Snapshot Extraction**: Extracted and normalized schemas. Mapped `Stage_MuaHang` and `Stage_TraTien` correctly.
- **Structure Comparison**:
  - `23701621`: 39 PASS, 10 MISSING, 3 EXTRA, 9 Identifier Type Warnings, 5 Row Count Warnings, 1 PK Mismatch.
  - `23708511`: 37 PASS, 11 MISSING, 2 EXTRA, 9 Identifier Type Warnings, 5 Row Count Warnings, 1 PK Mismatch.
- **View Testing**: Seeding completed successfully. Verified execution of all views. (Both submissions did not contain any views in their snapshots, yielding 4 `VIEW_NOT_FOUND` warnings as expected).

Results are summarized in:
- [summary.csv](file:///D:/Works/sql-grader-v1/runs/run_test/summary.csv)
- [execution.log](file:///D:/Works/sql-grader-v1/runs/run_test/execution.log)

---

## 3. How to Rerun the Grader

Run these commands sequentially from the repository root directory. Ensure that you set `PYTHONPATH=src` or run via `python -m dbcheck.cli.main`:

### Step 1: Snapshot Extraction
```powershell
python -m dbcheck.cli.main snapshot --answer-bak solution/dapan.bak --submissions exams --run-dir runs/run_test --config configs/assignment_purchase_payment_ca3.yaml
```

### Step 2: Structural Comparison
```powershell
python -m dbcheck.cli.main compare-structure --run-dir runs/run_test --config configs/assignment_purchase_payment_ca3.yaml
```

### Step 3: View Behavioral Testing
```powershell
python -m dbcheck.cli.main test-views --run-dir runs/run_test --test-data test_data --config configs/assignment_purchase_payment_ca3.yaml
```
