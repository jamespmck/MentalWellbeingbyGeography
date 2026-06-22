# Raw folder cleanup note v16

Run timestamp: 2026-06-22T11:45:37
Mode: APPLIED
Project root: D:\Good Measure\MentalWellbeingbyGeography
Raw root: D:\Good Measure\MentalWellbeingbyGeography\data\raw

This cleanup was conservative. It moved duplicated, exploratory, browser-capture and generic-discovery artefacts out of the active raw source layer. It did not delete raw data.

Active top-level raw folders after this run or dry-run baseline:

- _archive
- abs_homelessness
- aedc
- aihw
- dss
- geography_bridges
- ndia
- phidu
- state_health_geography

Key design decisions:

- Keep source-page HTML as provenance, but rehome it under source_pages.
- Keep official PHIDU LGA/PHN workbooks active under raw\phidu\official_lga_phn_context.
- Archive exploratory PHIDU workbooks and exact duplicates.
- Consolidate AIHW source-family folders under raw\aihw.
- Keep AIHW regional activity extracted data active and archive duplicate zip downloads from the second acquisition path.
- Keep raw\ndia\public_poc_selected active.
- Move NDIA browser/network captures to raw\_archive\browser_captures.
- Move ndis_service_area into raw\ndia\service_area_candidate.
- Do not touch the large raw\abs Census folder, which may be absent from shared archives.

Plan/audit file:

D:\Good Measure\MentalWellbeingbyGeography\outputs\audits\raw_folder_cleanup_plan_v16_20260622_114537.csv

Summary file:

D:\Good Measure\MentalWellbeingbyGeography\outputs\audits\raw_folder_cleanup_summary_v16_20260622_114537.csv

