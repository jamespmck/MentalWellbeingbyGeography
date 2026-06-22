# NDIA public proof-of-concept participant context layer

Generated: 2026-06-19T10:07:28.024407+00:00

This layer stages public NDIA participant-count files found in the project discovery workflow and selects the best available reference period for alignment with the 2021 Census, the 2021-22 AIHW service-system layer and the NSMHW 2020-22 outcome window.

## Method

- NDIA participant files use ASGS 2016 geography codes.
- The active MentalWellbeingByGeography master uses ASGS 2021 geography codes.
- Participant counts are allocated from 2016 geography to 2021 geography using the official ABS correspondence field `RATIO_FROM_TO`.
- SA2 participant counts are joined to the SA2 master by `sa2_code_2021`.
- SA3 participant counts are joined by `sa3_code_2021` and repeat across SA2s within the same SA3.

## Reference periods selected

- SA2 participant file: `2021-12-31`
- SA3 participant file: `2021-12-31`

## Modelling rule

This NDIA layer is a public proof-of-concept context layer. The period selection targets the 2021/2022 evidence window where available, but the public NDIA extracts remain structurally limited and must be excluded from the primary model unless the modelling plan explicitly treats them as a separate sensitivity/context layer.

It may be used only in a separate proof-of-concept, sensitivity or demonstration model with explicit caveats. It should not be interpreted as a complete measure of NDIS access, psychosocial support investment or service availability.

## Outputs

- `data/processed/sources/ndia_public_poc_participants_sa2_2021_allocated.csv`
- `data/processed/sources/ndia_public_poc_participants_sa3_2021_allocated.csv`
- `data/processed/integrated/sa2_predictor_universe_v03_with_ndia_public_poc_context.csv`
