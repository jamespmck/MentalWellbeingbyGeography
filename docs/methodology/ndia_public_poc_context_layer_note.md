# NDIA public proof-of-concept context layer

Generated: 2026-06-19T02:44:18.268141+00:00

## Status

This project uses the earliest publicly available NDIA files located in the project downloads as a proof-of-concept context layer. These sources are not treated as time-aligned 2021 predictors in the primary model.

## Core time-aligned analytical layer

- ABS Census 2021 and QuickStats SA2 variables
- ABS SEIFA 2021
- ABS remoteness 2021
- AIHW Regional Profiles 2021-22 at SA3
- ABS NSMHW modelled SA2 estimates 2020-22

## NDIA public proof-of-concept rule

NDIA public files are used to demonstrate the Good Measure source discovery, staging and later integration method. They must be clearly labelled as a time-misaligned service-system context layer unless a future tailored NDIA request supplies 2021-22 SA2/SA3 data.

## Modelling rule

- Exclude NDIA public POC variables from the primary 2021-aligned model.
- Use NDIA public POC variables only in a separate proof-of-concept sensitivity or demonstration model.
- Preserve source period fields in all processed outputs.
- Prefer SA2 and SA3 public sources. Hold LGA, service district, PHN and state-only sources aside unless a validated bridge is used.

## Selected source families

- active_providers: active_providers__Active_providers_data_June_2024_2_.csv (2024-06-30 to 2024-06-30; context_only_unbridged)
- baseline_outcomes: other_download__Baseline_Outcomes_data.csv (2026-03-31 to 2026-03-31; context_only_state)
- cald_participants: participant_other__Culturally_and_linguistically_diverse_participants.csv (2021-12-31 to 2026-03-31; context_only_state)
- diagnosis: participants_count_by_diagnosis__Participants_count_by_diagnosis_data.csv (2025-12-31 to 2026-03-31; context_only_state)
- first_nations_participants: participant_other__First_Nations_participants.csv (2020-03-31 to 2026-03-31; context_only_state)
- market_concentration: market_concentration__Market_Concentration_data.csv (2025-03-31 to 2026-03-31; context_only_unbridged)
- market_insights: 202112_01_The_NDIS_Market_Insights_Dashboard_-_data.csv (2021-12-31 to 2021-12-31; context_only_review)
- other_ndia_public: participant_other__Participants_by_CED_data.csv (2023-03-31 to 2026-03-31; context_only_state)
- participant_numbers_plan_budgets: participant_numbers_plan_budgets__Participant_numbers_and_plan_budgets_data._June_2024.csv (2024-06-30 to 2024-06-30; context_only_unbridged)
- participants_by_sa2: participants_by_sa2__Participants_by_SA2_data.csv (2026-03-31 to 2026-03-31; joinable_now_sa2)
- participants_by_sa3: participants_by_sa3__Participants_by_SA3_data.csv (2024-12-31 to 2026-03-31; joinable_now_sa3)
- payments: payments__Payments_data_June_2024.csv (2024-06-30 to 2024-06-30; context_only_unbridged)
- plan_management: plan_management_types__Plan_Management_types_data_December_2024.csv (2024-12-31 to 2024-12-31; context_only_unbridged)
- sda_dwellings_demand: sda_dwellings_demand__SDA_Enrolled_dwellings_and_NDIS_demand_data_.xlsx (2023-03-31 to 2026-03-30; context_only_state)
- sda_participants: sda_participants__SDA_participants_data.csv (2020-03-31 to 2026-03-31; context_only_unbridged)
- sil_participants: sil_participants__SIL_participants_data.csv (2019-12-31 to 2026-03-31; context_only_unbridged)
