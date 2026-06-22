# Remaining raw source validation note v14

Generated: 2026-06-22T11:50:36

This validation pass inspects raw files staged by `24_acquire_remaining_raw_source_register.py`.
It does not join sources and does not create modelling features.

## Outputs

- `outputs/audits/remaining_raw_source_file_inventory_v14.csv`
- `outputs/audits/remaining_raw_workbook_sheet_inventory_v14.csv`
- `outputs/audits/remaining_raw_zip_member_inventory_v14.csv`
- `outputs/audits/remaining_raw_source_validation_summary_v14.csv`
- `outputs/audits/remaining_raw_source_failed_register_rows_v14.csv`
- `docs/source_registers/remaining_raw_source_validation_register_v14.csv`

## Interpretation

Use the validation summary to decide which raw sources are ready for native-geography processing.
State-specific health geography sources should remain context only unless a validated crosswalk is available.
Higher-level sources such as LGA and PHN should be kept separate from the SA2 master and connected later through the scoped foreign-key model.
