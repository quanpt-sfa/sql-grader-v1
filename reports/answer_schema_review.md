# Answer Schema Review Note: ChiTietTraTien Primary Key

## Issue Description
During grading configuration, a mismatch was identified in the primary key design of the payment detail table `ChiTietTraTien` (or student table `CT_ChiTien`).

- **Answer Schema Key Pattern**: `[SoPhieuTraTien, MaLoaiTien]` (or `[SoPhieuChi, MaTien]`)
- **Student Schema Key Pattern**: `[SoPhieuTraTien, PhieuMuaHang]` (or `[PC, MaHD]`)

## Conceptual REA Analysis
1. In the Purchase-Payment business process, a single payment voucher (`TraTien` / `PhieuChi`) may be used to settle outstanding balances on multiple purchase invoices (`MuaHang` / `PhieuMuaHang`).
2. If `ChiTietTraTien` (the junction table linking payments to purchases) has the primary key `[SoPhieuTraTien, MaLoaiTien]`, it assumes that each payment details voucher can only pay for different currencies, which is highly unlikely (and typically one voucher settles in one currency).
3. If one payment voucher pays for multiple purchase invoices (e.g. paying invoice 1 and invoice 2 at the same time), then the primary key of `ChiTietTraTien` **must** include the purchase invoice identifier (such as `PhieuMuaHang` / `SoHoaDon` / `MaHD`) to identify which invoice is being settled.
4. Therefore, the student's design of `CT_ChiTien(PC, MaHD, SoTienTra)` with primary key `[PC, MaHD]` is conceptually and practically more correct than the answer schema's key structure.

## Recommendation for Human Reviewers
- For future exam cycles, update the answer database schema design so that `ChiTietTraTien`'s primary key includes the purchase invoice number (`PhieuMuaHang` / `SoHoaDon` / `MaHD`).
- For the current grading run, the grading behavior remains configured per the default snapshot. However, any manual override or alternative key configurations in `assignment_purchase_payment_ca3.yaml` should take this note into consideration.
