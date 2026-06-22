# Remaining raw source acquisition register v13

Run timestamp: 2026-06-22 10:48:34

This note documents the conservative raw acquisition pass for the remaining MentalWellbeingByGeography source candidates. The script downloads stable direct files where available, caches source pages, and discovers candidate downloadable files from those pages. It does not join, reshape or model any source.

## Scope

The acquisition pass focuses on source families still requiring raw/source staging after the existing SA2/SA3 foundation layers and PHIDU LGA/PHN context extraction:

- AEDC child development data
- ABS Estimating Homelessness: Census data cubes and access pages
- AIHW Specialist Homelessness Services annual report data page
- AIHW mental health regional activity and data tables
- AIHW Medicare-subsidised primary care/service-use geography pages
- NDIS service-area candidate discovery
- selected state health geography inventory pages

## Interpretation

A successful download or cached page is not a validated modelling source. Every file still needs native-geography validation, reference-period validation, denominator review and source-specific limitations before inclusion in a scoped master table.

## Modelling rule

Do not join higher-level geographies into the SA2 modelling table until the scoped master architecture is built. Native LGA, PHN, SA3 and service-area tables should stay separate and connect through a foreign-key master during feature-matrix assembly.
