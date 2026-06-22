# PHIDU Social Health Atlas deep key probe v11

Generated: 2026-06-21T22:48:48

This audit performs a cell-level scan of downloaded PHIDU Excel workbooks. It does not join PHIDU data into the SA2 master.

## Why this step was needed

PHIDU workbooks use multi-row and sometimes merged or duplicated headers. A conventional pandas header-based scan can miss geography key columns or create empty validation outputs.

## Decision rule

A sheet is only treated as a join candidate if one source column or embedded-code column matches at least 95% of unique source keys to the relevant v08 master key. PHN and LGA candidates remain contextual and require boundary/year review even after a high key match.

## Recommended actions summary

| recommended_action                      |   sheet_count |
|:----------------------------------------|--------------:|
| manual_review_key_match_below_threshold |           552 |

## Key caution

Do not broad-join PHIDU data. Select a small set of indicators, validate geography, validate denominator definitions, then add a source-specific context layer.