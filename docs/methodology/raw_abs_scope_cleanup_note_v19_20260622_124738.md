# Raw ABS scope cleanup note

Run timestamp: 20260622_124738
Mode: APPLIED
Project root: D:\Good Measure\MentalWellbeingbyGeography
Target folder: D:\Good Measure\MentalWellbeingbyGeography\data\raw\abs
Archive folder: D:\Good Measure\MentalWellbeingbyGeography\data\raw\_archive\abs_not_active_for_v01_scope

Purpose: archive ABS raw files that are not active inputs for the 2021-aligned SA2/SA3/LGA/PHN/NDIS modelling project.

The script archives rather than deletes. It keeps core 2021 geography files, SA2/SA3/SA4 2016-to-2021 bridges used for historical allocation reproducibility, active NSMHW workbooks, Census GCP SA2 ZIP, and core SEIFA SA2/LGA inputs.

Use -ArchiveOptionalSA1AreaFile only if the SA1 2021 area workbook is no longer needed for remoteness or provenance.

Plan CSV: D:\Good Measure\MentalWellbeingbyGeography\outputs\audits\raw_abs_scope_cleanup_plan_v19_20260622_124738.csv
Summary CSV: D:\Good Measure\MentalWellbeingbyGeography\outputs\audits\raw_abs_scope_cleanup_summary_v19_20260622_124738.csv
