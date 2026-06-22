# Raw acquisition freeze note v15

Generated: 2026-06-22T13:01:40

## Purpose

This freeze records the cleaned active raw/source acquisition state for MentalWellbeingByGeography before native-geography processing and scoped master construction. It is a provenance checkpoint, not an analytical dataset.

## Freeze summary

- Active raw files hashed: 457
- Active raw size MB: 642.527550
- Raw file hash/read failures: 0
- Source families ready or active: 19
- Archived raw files excluded from active manifest: 0
- Archived raw size MB excluded from active manifest: 0.000000

## Processing principle

Raw data remains in its native geography. SA2, SA3, LGA, PHN and NDIS/service-area variables should be processed into separate native-geography source tables. SA2 modelling data should then be assembled through explicit foreign keys, not by permanently widening the SA2 master with every higher-level source.

## Current source-family treatment

- ABS homelessness: process next, with attention to whether usable SA2/SA3 tables are available.
- AEDC: inspect geography and suppression/release scope before use.
- AIHW mental health regional activity: unzip and process PHN/SA4/SA3/state tables separately.
- PHIDU: retain as LGA and PHN context only. Do not treat as SA2 measurement.
- AIHW MBS primary-care geography: inspect report/source tables before extraction.
- AIHW SHS: hold as report/context unless usable lower geography is found.
- NDIS service-area candidate: hold until a true service-area key is identified.
- State health geography: hold as state-specific context only.

## Key outputs

- outputs/audits/raw_acquisition_freeze_manifest_v15.csv
- outputs/audits/raw_acquisition_source_family_rollup_v15.csv
- outputs/audits/raw_acquisition_processing_sequence_v15.csv
- outputs/audits/raw_acquisition_archive_exclusion_summary_v15.csv
- docs/source_registers/raw_acquisition_freeze_manifest_v15.csv
- docs/source_registers/raw_acquisition_processing_sequence_v15.csv
