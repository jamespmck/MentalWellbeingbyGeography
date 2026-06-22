# Housing QuickStats clean context layer v08

This layer derives a compact set of interpretable housing context columns from Census QuickStats fields already present in the v07 master.

It does not scope the master for modelling and does not remove any source columns. It appends clean alias fields for rent, mortgage, tenure, dwelling structure, bedrooms, household composition and selected housing-related DSS context.

Base master:
`D:\Good Measure\MentalWellbeingbyGeography\data\processed\integrated\sa2_predictor_universe_v07_with_housing_affordability_context.parquet`

Derived feature table:
`D:\Good Measure\MentalWellbeingbyGeography\data\processed\sources\housing_quickstats_clean_derived_features_v08.csv`

Output master:
`D:\Good Measure\MentalWellbeingbyGeography\data\processed\integrated\sa2_predictor_universe_v08_with_clean_housing_context.parquet`

Method:
- identify selected Census QuickStats housing/dwelling columns by stable token matching
- convert selected source columns to numeric values
- preserve one row per SA2
- write mapping and candidate audits so every derived field is traceable to its original source column

Important limitations:
- these fields are Census QuickStats summary variables
- count fields should not be interpreted as rates without denominators
- percentage fields are preferable for area comparison where the source definition is clear
- the external RDH MAID/RAID housing affordability resource was not required for this derived layer
