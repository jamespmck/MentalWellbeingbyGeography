# Raw ABS folder cleanup note

Run timestamp: 20260622_121918
Mode: APPLIED
Project root: D:\Good Measure\MentalWellbeingbyGeography
Target directory: D:\Good Measure\MentalWellbeingbyGeography\data\raw\abs

This cleanup is conservative. It moves files into data\raw\abs\_archive and does not delete raw source files.

Actions included:
- Archive exact duplicate files using the latest raw_abs_duplicate_groups_v18 audit.
- Archive QuickStats HTML source-page snapshots unless -KeepHtmlSnapshotsActive was supplied.
- Archive generated extraction marker files.
- Optionally archive the extracted Census pack if -ArchiveExtractedCensusPack is supplied.
- Optionally archive selected ZIP files if -ArchiveZipFiles is supplied.

Outputs:
- D:\Good Measure\MentalWellbeingbyGeography\outputs\audits\raw_abs_cleanup_plan_v19_20260622_121918.csv
- D:\Good Measure\MentalWellbeingbyGeography\outputs\audits\raw_abs_cleanup_summary_v19_20260622_121918.csv
- D:\Good Measure\MentalWellbeingbyGeography\docs\methodology\raw_abs_cleanup_note_v19_20260622_121918.md

Recommended next step:
Re-run the ABS raw audit and check that duplicate groups and active file count have reduced while archive folders preserve provenance.
