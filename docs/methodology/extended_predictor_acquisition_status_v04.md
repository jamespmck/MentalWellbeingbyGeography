# Extended predictor acquisition status after NDIA POC join

Generated: 2026-06-19T13:26:08.232288+00:00

## Current master files

Primary aligned master remains:

`data/processed/integrated/sa2_predictor_universe_v02_with_aihw_sa3.parquet`

NDIA public proof-of-concept context master is:

`data/processed/integrated/sa2_predictor_universe_v03_with_ndia_public_poc_context.parquet`

If PHN or LGA bridges were validated locally, this script may also create:

`data/processed/integrated/sa2_predictor_universe_v04_with_ndia_phn_lga_context.parquet`

## Modelling rule

NDIA public POC variables use public participant-count data aligned to 2021-12-31 where available, bridged from ASGS 2016 to ASGS 2021. These variables remain a proof-of-concept/sensitivity layer unless explicitly included in a separate sensitivity model.

PHN and LGA fields are administrative/context geography fields. They should not be treated as individual-level predictors. They may support grouped summaries, local commissioning interpretation and service-system context.

The following source families remain pending until geography and source validity are confirmed:

- LHD / local health district / state health district boundaries
- DSS social-security data
- housing stress and homelessness data
- PHIDU Social Health Atlas indicators
- AEDC child development indicators
- carer, disability and psychosocial support demand sources beyond public NDIA participant counts

## Guardrail

This project should not join PHN, LGA, LHD, DSS, PHIDU, AEDC or housing/homelessness files unless the native geography is direct SA2/SA3 or a validated bridge exists. Where source geography is LGA/PHN/PHA/AEDC community/state/service district, hold as context-only or pending until bridge quality is documented.
