# PHIDU Social Health Atlas join candidate validation v10

Generated: 2026-06-21T15:48:23

This validation step follows the PHIDU v09 inventory. It does not join PHIDU data into the SA2 master.

## Method

The script reads the PHIDU join-readiness audit, samples candidate SA2, PHN and LGA sheets, detects geography key columns, compares those keys with the v08 master, and inventories numeric indicator columns for prioritisation.

## Decision rule

A candidate is treated as join-ready only if geography key coverage is high and the join geography is methodologically acceptable. PHN and LGA candidates remain context candidates until boundary year and area-share issues are resolved.

## Summary

| recommended_action                       |   sheet_count |
|:-----------------------------------------|--------------:|
| hold_context_only_no_validated_key_match |            64 |
| hold_context_only                        |             4 |

## Key caution

Do not add broad PHIDU workbooks to the modelling table without indicator selection. PHIDU contains overlapping demographic, health-status, mortality, service-use and health-system indicators at mixed geographies.