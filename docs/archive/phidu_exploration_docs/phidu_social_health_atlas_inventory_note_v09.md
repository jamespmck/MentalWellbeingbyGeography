# PHIDU Social Health Atlas inventory note v09

Generated: 2026-06-21T14:37:59

## Purpose

This step inventories PHIDU Social Health Atlas workbooks before any join to the SA2 master.

It does not join PHIDU indicators to the master. PHIDU data are published across multiple geographies, including Population Health Area, Local Government Area and Primary Health Network. These are not automatically equivalent to SA2.

## Base master

`D:\Good Measure\MentalWellbeingbyGeography\data\processed\integrated\sa2_predictor_universe_v08_with_clean_housing_context.parquet`

## Source pages

- `https://phidu.torrens.edu.au/social-health-atlases/data`
- `https://phidu.torrens.edu.au/social-health-atlases/indicators-and-notes-on-the-data/social-health-atlases-of-australia-contents`

## Outputs

- `outputs/audits/phidu_source_candidate_audit_v09.csv`
- `outputs/audits/phidu_download_audit_v09.csv`
- `outputs/audits/phidu_workbook_sheet_inventory_v09.csv`
- `outputs/audits/phidu_schema_audit_v09.csv`
- `outputs/audits/phidu_indicator_inventory_v09.csv`
- `outputs/audits/phidu_join_readiness_audit_v09.csv`
- `docs/source_registers/phidu_social_health_atlas_inventory_register_v09.csv`

## Summary

Candidate links discovered: 139

Downloaded or cached files: 9

## Interpretation rules

- Direct SA2 joins are acceptable only if SA2 codes and ASGS year are explicit.
- SA3 indicators may be joined as SA3 context only after year and definition review.
- LGA indicators may be joined only after LGA code/year validation and with dominant-LGA caveats.
- PHN indicators require PHN boundary-year validation. Current master contains PHN 2017 context.
- PHA indicators require a validated PHA-to-SA2/SA3 bridge or should be held as context-only.
- Indigenous Area data require separate bridge review and First Nations data governance review.

## Log

`D:\Good Measure\MentalWellbeingbyGeography\outputs\logs\19_inventory_phidu_social_health_atlas_20260621_141409.log`
