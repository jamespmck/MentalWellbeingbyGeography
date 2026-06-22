# Raw register path reconciliation v17

Run timestamp: 20260622_115016
Mode: APPLY

This step reconciles stale raw_file_path values after the raw folder cleanup.
The cleanup moved files into clearer source-family folders and archives, but v13 acquisition registers still pointed to the old paths.
The reconciliation uses applied raw_folder_cleanup_plan_v16 CSV files plus a filename scan of data/raw.

After applying this reconciliation, rerun 25_validate_remaining_raw_source_inventory.py.

Summary file:
D:\Good Measure\MentalWellbeingbyGeography\outputs\audits\raw_register_path_reconciliation_summary_v17_20260622_115016.csv
