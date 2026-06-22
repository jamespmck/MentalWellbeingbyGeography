# Housing affordability context layer v07

This layer attempts to add Census mortgage and rent affordability indicators to the SA2 master.

Source target:
- Dataset page: https://catalogue.data.infrastructure.gov.au/dataset/rdh-census-housing-affordability-data-for-lgas-and-sa2s-mortgage-and-rent-affordability-indicators
- Preferred resource: 2011-2021 Time Series MAID & RAID long format, resource id `a29d2ba0-ea5d-4495-a637-6b5521e7501e`
- Fallback resource: All Tables Excel workbook, resource id `b8cd5524-dd8d-4306-b388-c7ce264b8944`

Method:
1. Read the v06 master with DSS social security context.
2. Inventory housing-related columns already present in the master, mainly from Census QuickStats.
3. Attempt to acquire the Regional Data Hub/ABS-derived MAID and RAID affordability data.
4. Filter to 2021 and SA2-level records where detected.
5. Build numeric SA2 features and join by `sa2_code_2021`.
6. Preserve the SA2 spine; the join must not change row count.

Interpretation caveat:
Housing affordability indicators are usually household counts by affordability category. They should not be interpreted as rates until divided by a relevant denominator, such as renting households, mortgaged households or occupied private dwellings.

Modelling rule:
Treat these as context predictor candidates. Before primary modelling, derive proportions or include appropriate household/population denominators.


## v07 acquisition status

The external MAID/RAID housing affordability resource was not downloaded during this run. v07 was still created as a documented checkpoint because the v06 master already contains housing-related Census QuickStats variables. The relevant existing columns are listed in `outputs/audits/housing_existing_master_column_inventory_v07.csv`.

No external MAID/RAID fields were added. The two fallback metadata fields are:
- `source_housing_affordability_external_2021_present_flag`
- `housing_affordability_external_acquisition_status`

Before modelling, use the existing housing inventory to select rent, mortgage, tenure, dwelling and occupancy variables already available in the master.
