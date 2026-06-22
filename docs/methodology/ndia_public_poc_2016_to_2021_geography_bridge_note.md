# NDIA public POC 2016 to 2021 geography bridge note

This script builds the NDIA proof-of-concept geography bridge using the official ABS ASGS Edition 3 correspondence files:

- `CG_SA2_2016_SA2_2021.csv`
- `CG_SA3_2016_SA3_2021.csv`

The previous bridge attempt was rejected because it selected a Mesh Block-scale correspondence: the audit showed more than 350,000 unique 2016 codes, which is impossible for SA2 or SA3.

The corrected bridge requires the exact ABS correspondence columns for SA2 and SA3, validates reasonable geography counts, and refuses to write outputs if a Mesh Block or other misclassified source is detected.

NDIA public proof-of-concept participant files use 2016 ASGS codes (`SA2Cd2016`, `SA3Cd2016`). The active MentalWellbeingByGeography master uses 2021 ASGS codes. Counts should therefore be allocated from 2016 to 2021 using `ratio_from_to` where a 2016 geography maps to multiple 2021 geographies.

The NDIA public POC layer remains excluded from the primary 2021-aligned model and may only be used in a separate demonstration or sensitivity layer.
