# PHIDU official LGA/PHN context extraction v12

This run targeted the PHIDU Social Health Atlas workbooks explicitly published for Local Government Areas and Primary Health Networks.

Outputs are source context tables, not an SA2 master join. Any later SA2 join must be treated as a higher-level contextual join, because LGA/PHN values will repeat across SA2s.

Base master: `data\processed\integrated\sa2_predictor_universe_v08_with_clean_housing_context.parquet`

Official source URLs:

- LGA Australia: `https://phidu.torrens.edu.au/current/data/sha-aust/lga/phidu_data_lga_aust.xlsx`
- PHN with component PHAs: `https://phidu.torrens.edu.au/current/data/sha-aust/phn_pha_parts/phidu_data_phn_pha_parts_aust.xlsx`
- PHN with component LGAs: `https://phidu.torrens.edu.au/current/data/sha-aust/phn_lga_parts/phidu_data_phn_lga_aust.xlsx`

Key caveats:

- LGA context uses `dominant_lga_code_2021` from the SA2 master if later joined. This is a dominant-area assignment, not a population-weighted LGA allocation.
- PHN context uses `phn_2017_code` from the SA2 master if later joined. Boundary year should be checked against PHIDU metadata.
- These fields should be excluded from primary SA2-only feature sets unless model validation groups by LGA/PHN or explicitly treats them as higher-level contextual predictors.
