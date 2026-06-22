# Native geography processing note

Run timestamp: 20260622_130925
Version: v20

This run processed acquired raw and existing processed source files into native-geography staging tables.
It did not build scoped masters and did not join higher-level values to SA2.

## Interpretation rules

- Tables in `data/processed/native/sa2` can be candidates for the SA2 master after leakage and outcome-source review.
- Tables in `sa3`, `lga`, `phn`, `sa4` and `state` remain native higher-level context until joined through the foreign-key master.
- Tables in `unknown_review` require manual key review before any scoped master inclusion.
- Held PDFs, HTML pages, README files and raw foundation workbooks remain provenance/context, not model inputs.

## Run counts

Audit rows: 959

## Summary by source/geography/status

| source_family                           | native_geography   | status                                          |   tables |   rows |   columns_max |
|:----------------------------------------|:-------------------|:------------------------------------------------|---------:|-------:|--------------:|
| abs_census_2021_sa2                     | hold               | held_raw_foundation_source                      |        1 |      0 |             0 |
| abs_census_quickstats_sa2_2021          | sa2                | copied_existing_processed_native_table          |        1 |   2393 |          1678 |
| abs_geography_2021                      | hold               | held_raw_foundation_source                      |       13 |      0 |             0 |
| abs_homelessness                        | hold               | held_source_page_snapshot                       |        2 |      0 |             0 |
| abs_homelessness                        | lga                | processed_native_table                          |        1 |     99 |             7 |
| abs_homelessness                        | sa2                | processed_native_table                          |        2 |   5636 |             9 |
| abs_homelessness                        | sa3                | processed_native_table                          |        4 |   1086 |            17 |
| abs_homelessness                        | state              | processed_native_table                          |       54 |   5179 |            25 |
| abs_homelessness                        | unknown            | held_metadata_sheet                             |        9 |      0 |             0 |
| abs_homelessness                        | unknown            | processed_unknown_geography_review              |        9 |    512 |            15 |
| abs_nsmhw_sa2_modelled_2020_22          | sa2                | copied_existing_processed_native_table          |        1 |   2472 |            48 |
| abs_nsmhw_sa2_modelled_estimates        | hold               | held_raw_foundation_source                      |        6 |      0 |             0 |
| abs_remoteness_sa2_2021                 | sa2                | copied_existing_processed_native_table          |        1 |   2472 |            14 |
| abs_sa2_spine_2021                      | sa2                | copied_existing_processed_native_table          |        1 |   2472 |            13 |
| abs_seifa_2021                          | hold               | held_raw_foundation_source                      |        5 |      0 |             0 |
| abs_seifa_sa2_2021                      | sa2                | copied_existing_processed_native_table          |        1 |   2472 |            51 |
| aedc_child_development                  | hold               | held_source_page_snapshot                       |        3 |      0 |             0 |
| aedc_child_development                  | sa3                | processed_native_table                          |        7 |    142 |            22 |
| aedc_child_development                  | sa4                | processed_native_table                          |        3 |     60 |            11 |
| aedc_child_development                  | state              | processed_native_table                          |        8 |    340 |            22 |
| aedc_child_development                  | unknown            | held_metadata_sheet                             |        4 |      0 |             0 |
| aedc_child_development                  | unknown            | processed_unknown_geography_review              |       40 |    927 |            22 |
| aihw_mbs_primary_care_geography         | hold               | held_report_pdf_context                         |        2 |      0 |             0 |
| aihw_mbs_primary_care_geography         | hold               | held_source_page_snapshot                       |        2 |      0 |             0 |
| aihw_mental_health_data_tables          | hold               | held_source_page_snapshot                       |        2 |      0 |             0 |
| aihw_mental_health_regional_activity    | hold               | held_marker_file                                |        6 |      0 |             0 |
| aihw_mental_health_regional_activity    | hold               | held_readme_metadata                            |        2 |      0 |             0 |
| aihw_mental_health_regional_activity    | hold               | held_source_page_snapshot                       |        1 |      0 |             0 |
| aihw_mental_health_regional_activity    | phn                | processed_native_table                          |        6 |     32 |             9 |
| aihw_mental_health_regional_activity    | sa3                | processed_native_table                          |       18 | 317664 |            20 |
| aihw_mental_health_regional_activity    | sa4                | processed_native_table                          |       14 | 197568 |            20 |
| aihw_mental_health_regional_activity    | state              | processed_native_table                          |       66 |  89796 |            19 |
| aihw_mental_health_regional_activity    | unknown            | held_metadata_sheet                             |        2 |      0 |             0 |
| aihw_mental_health_regional_activity    | unknown            | held_zip_non_table_member                       |        2 |      0 |             0 |
| aihw_mental_health_regional_activity    | unknown            | processed_unknown_geography_review              |       28 |  93836 |            19 |
| aihw_regional_profiles_sa3              | hold               | held_already_processed_regional_profile_extract |      340 |      0 |             0 |
| aihw_regional_profiles_sa3_2021_22      | sa3                | copied_existing_processed_native_table          |        1 |    335 |            40 |
| aihw_regional_profiles_sa3_long_2021_22 | sa3                | copied_existing_processed_native_table          |        1 |  59074 |            18 |
| aihw_specialist_homelessness_services   | hold               | held_report_pdf_context                         |        2 |      0 |             0 |
| aihw_specialist_homelessness_services   | hold               | held_source_page_snapshot                       |        2 |      0 |             0 |
| dss                                     | state              | processed_native_table                          |        1 |  38964 |            33 |
| dss_social_security_sa2_2021            | sa2                | not_found_optional_processed_source             |        1 |      0 |             0 |
| geography_bridges                       | sa2                | processed_native_table                          |        1 |   2538 |            14 |
| housing_quickstats_sa2_derived          | sa2                | not_found_optional_processed_source             |        1 |      0 |             0 |
| ndia_public_poc_sa2_context_holdaside   | sa2                | not_found_optional_processed_source             |        1 |      0 |             0 |
| ndis_service_area_candidate             | hold               | held_source_page_snapshot                       |        1 |      0 |             0 |
| ndis_service_area_candidate             | ndis_service_area  | processed_native_table                          |        1 |    102 |            11 |
| ndis_service_area_candidate             | sa2                | processed_native_table                          |        1 |  60996 |             9 |
| ndis_service_area_candidate             | sa3                | processed_native_table                          |        5 | 150096 |            15 |
| ndis_service_area_candidate             | sa4                | processed_native_table                          |        9 | 411673 |            12 |
| ndis_service_area_candidate             | state              | processed_native_table                          |       14 |   1823 |            78 |
| ndis_service_area_candidate             | unknown            | processed_unknown_geography_review              |        6 |  18546 |            12 |
| phidu_lga_context_selected_v12          | lga                | copied_existing_processed_native_table          |        1 |    442 |            88 |
| phidu_phn_context_selected_v12          | phn                | copied_existing_processed_native_table          |        1 |     31 |           119 |
| phidu_raw                               | lga                | processed_native_table                          |       80 |  45881 |           199 |
| phidu_raw                               | phn                | processed_native_table                          |        3 |      9 |             6 |
| phidu_raw                               | sa3                | processed_native_table                          |      146 | 142404 |           199 |
| phidu_raw                               | state              | processed_native_table                          |        8 |   7802 |            54 |
| phidu_raw                               | unknown            | held_metadata_sheet                             |        3 |      0 |             0 |
| state_health_geography_inventory        | hold               | held_report_pdf_context                         |        1 |      0 |             0 |
| state_health_geography_inventory        | hold               | held_source_page_snapshot                       |        1 |      0 |             0 |