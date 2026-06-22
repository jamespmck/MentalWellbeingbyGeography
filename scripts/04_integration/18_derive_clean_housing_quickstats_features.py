#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
18_derive_clean_housing_quickstats_features.py

Derive a compact, explicitly named housing context layer from Census QuickStats
columns already present in the MentalWellbeingByGeography master.

This script does NOT scope the master for modelling. It preserves the full master
and appends a small set of clean derived housing feature aliases so downstream
modelling/auditing can refer to interpretable names instead of long QuickStats
column names.

Default input:
  data/processed/integrated/sa2_predictor_universe_v07_with_housing_affordability_context.parquet

Default output:
  data/processed/integrated/sa2_predictor_universe_v08_with_clean_housing_context.parquet
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

import pandas as pd


EXPECTED_ROWS = 2472


@dataclass(frozen=True)
class FeatureSpec:
    output_column: str
    required: tuple[str, ...]
    any_of: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()
    prefer: tuple[str, ...] = ()
    suffix: str | None = None
    notes: str = ""


FEATURE_SPECS: list[FeatureSpec] = [
    # Core affordability
    FeatureSpec(
        "housing_qs_median_weekly_rent",
        required=("census_qs_housing", "rent_weekly_payments", "median_rent"),
        suffix="_count",
        notes="Median weekly rent from Census QuickStats housing table.",
    ),
    FeatureSpec(
        "housing_qs_median_monthly_mortgage_repayments",
        required=("census_qs_housing", "mortgage_monthly_repayments", "median_mortgage_repayments"),
        suffix="_count",
        notes="Median monthly mortgage repayments from Census QuickStats housing table.",
    ),
    FeatureSpec(
        "housing_qs_renter_households_rent_gt_30_income_count",
        required=("census_qs_housing", "rent_weekly_payments", "renter_households_with_rent_payments_greater_than_30"),
        suffix="_count",
        notes="Count of renter households with rent payments greater than 30% of household income.",
    ),
    FeatureSpec(
        "housing_qs_renter_households_rent_gt_30_income_pct",
        required=("census_qs_housing", "rent_weekly_payments", "renter_households_with_rent_payments_greater_than_30"),
        suffix="_pct",
        notes="Percentage of renter households with rent payments greater than 30% of household income.",
    ),
    FeatureSpec(
        "housing_qs_renter_households_rent_le_30_income_count",
        required=("census_qs_housing", "rent_weekly_payments", "renter_households_where_rent_payments_are_less_than_or"),
        suffix="_count",
        notes="Count of renter households with rent payments less than or equal to 30% of household income.",
    ),
    FeatureSpec(
        "housing_qs_renter_households_rent_le_30_income_pct",
        required=("census_qs_housing", "rent_weekly_payments", "renter_households_where_rent_payments_are_less_than_or"),
        suffix="_pct",
        notes="Percentage of renter households with rent payments less than or equal to 30% of household income.",
    ),
    FeatureSpec(
        "housing_qs_mortgaged_households_repayment_gt_30_income_count",
        required=("census_qs_housing", "mortgage_monthly_repayments", "owner_with_mortgage_households_with_mortgage_repayments"),
        suffix="_count",
        notes="Count of owner-with-mortgage households with repayments greater than 30% of household income.",
    ),
    FeatureSpec(
        "housing_qs_mortgaged_households_repayment_gt_30_income_pct",
        required=("census_qs_housing", "mortgage_monthly_repayments", "owner_with_mortgage_households_with_mortgage_repayments"),
        suffix="_pct",
        notes="Percentage of owner-with-mortgage households with repayments greater than 30% of household income.",
    ),
    FeatureSpec(
        "housing_qs_mortgaged_households_repayment_le_30_income_count",
        required=("census_qs_housing", "mortgage_monthly_repayments", "owner_with_mortgage_households_where_mortgage_repayment"),
        suffix="_count",
        notes="Count of owner-with-mortgage households with repayments less than or equal to 30% of household income.",
    ),
    FeatureSpec(
        "housing_qs_mortgaged_households_repayment_le_30_income_pct",
        required=("census_qs_housing", "mortgage_monthly_repayments", "owner_with_mortgage_households_where_mortgage_repayment"),
        suffix="_pct",
        notes="Percentage of owner-with-mortgage households with repayments less than or equal to 30% of household income.",
    ),

    # Tenure
    FeatureSpec(
        "housing_qs_owned_outright_count",
        required=("census_qs_housing", "tenure_type", "owned_outright"),
        suffix="_count",
        notes="Occupied private dwellings owned outright.",
    ),
    FeatureSpec(
        "housing_qs_owned_outright_pct",
        required=("census_qs_housing", "tenure_type", "owned_outright"),
        suffix="_pct",
        notes="Percentage of occupied private dwellings owned outright.",
    ),
    FeatureSpec(
        "housing_qs_owned_with_mortgage_count",
        required=("census_qs_housing", "tenure_type", "owned_with_a_mortgage"),
        suffix="_count",
        notes="Occupied private dwellings owned with a mortgage.",
    ),
    FeatureSpec(
        "housing_qs_owned_with_mortgage_pct",
        required=("census_qs_housing", "tenure_type", "owned_with_a_mortgage"),
        suffix="_pct",
        notes="Percentage of occupied private dwellings owned with a mortgage.",
    ),
    FeatureSpec(
        "housing_qs_rented_count",
        required=("census_qs_housing", "tenure_type", "rented"),
        suffix="_count",
        notes="Occupied private dwellings rented.",
    ),
    FeatureSpec(
        "housing_qs_rented_pct",
        required=("census_qs_housing", "tenure_type", "rented"),
        suffix="_pct",
        notes="Percentage of occupied private dwellings rented.",
    ),

    # Dwelling structure
    FeatureSpec(
        "housing_qs_separate_house_pct",
        required=("census_qs_dwellings", "dwelling_structure", "separate_house"),
        suffix="_pct",
        notes="Percentage of occupied private dwellings that are separate houses.",
    ),
    FeatureSpec(
        "housing_qs_flat_or_apartment_pct",
        required=("census_qs_dwellings", "dwelling_structure", "flat_or_apartment"),
        suffix="_pct",
        notes="Percentage of occupied private dwellings that are flats or apartments.",
    ),
    FeatureSpec(
        "housing_qs_semi_detached_townhouse_pct",
        required=("census_qs_dwellings", "dwelling_structure", "semi_detached_row_or_terrace_house_townhouse"),
        suffix="_pct",
        notes="Percentage of occupied private dwellings that are semi-detached/row/terrace/townhouse.",
    ),

    # Dwelling occupancy and size
    FeatureSpec(
        "housing_qs_unoccupied_private_dwellings_pct",
        required=("census_qs_dwellings", "dwelling_count_private_dwellings", "unoccupied_private_dwellings"),
        suffix="_pct",
        notes="Percentage of private dwellings unoccupied.",
    ),
    FeatureSpec(
        "housing_qs_average_bedrooms_per_dwelling",
        required=("census_qs_dwellings", "number_of_bedrooms", "average_number_of_bedrooms_per_dwelling"),
        suffix="_count",
        notes="Average number of bedrooms per occupied private dwelling.",
    ),
    FeatureSpec(
        "housing_qs_average_people_per_household",
        required=("census_qs_dwellings", "number_of_bedrooms", "average_number_of_people_per_household"),
        suffix="_count",
        notes="Average number of people per household.",
    ),
    FeatureSpec(
        "housing_qs_1_bedroom_pct",
        required=("census_qs_dwellings", "number_of_bedrooms", "1_bedroom"),
        suffix="_pct",
        notes="Percentage of occupied private dwellings with one bedroom.",
    ),
    FeatureSpec(
        "housing_qs_4plus_bedrooms_pct",
        required=("census_qs_dwellings", "number_of_bedrooms", "4_or_more_bedrooms"),
        suffix="_pct",
        notes="Percentage of occupied private dwellings with four or more bedrooms.",
    ),

    # Household composition and income
    FeatureSpec(
        "housing_qs_single_person_households_pct",
        required=("census_qs_housing", "household_composition", "single_person_households"),
        suffix="_pct",
        notes="Percentage of occupied private dwellings that are single-person households.",
    ),
    FeatureSpec(
        "housing_qs_group_households_pct",
        required=("census_qs_housing", "household_composition", "group_households"),
        suffix="_pct",
        notes="Percentage of occupied private dwellings that are group households.",
    ),
    FeatureSpec(
        "housing_qs_family_households_pct",
        required=("census_qs_housing", "household_composition", "family_households"),
        suffix="_pct",
        notes="Percentage of occupied private dwellings that are family households.",
    ),
    FeatureSpec(
        "housing_qs_low_household_income_lt_650_pct",
        required=("census_qs_housing", "household_income", "less_than_650"),
        suffix="_pct",
        notes="Percentage of occupied private dwellings with total household weekly income less than $650.",
    ),
    FeatureSpec(
        "housing_qs_high_household_income_gt_3000_pct",
        required=("census_qs_housing", "household_income", "more_than_3_000"),
        suffix="_pct",
        notes="Percentage of occupied private dwellings with total household weekly income more than $3,000.",
    ),

    # DSS linked housing payment context already in v06
    FeatureSpec(
        "housing_dss_commonwealth_rent_assistance_recipients",
        required=("dss_sa2_2021_allocated_commonwealth_rent_assistance_recipients",),
        notes="Allocated DSS Commonwealth Rent Assistance recipient count. Count, not a rate.",
    ),
]


def normalise_text(value: object) -> str:
    s = str(value).strip().lower()
    s = re.sub(r"[^a-z0-9]+", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return s


def setup_logger(project_root: Path, debug: bool) -> logging.Logger:
    log_dir = project_root / "outputs" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"18_derive_clean_housing_quickstats_features_{datetime.now():%Y%m%d_%H%M%S}.log"

    logger = logging.getLogger("housing_derived_features")
    logger.handlers.clear()
    logger.setLevel(logging.DEBUG if debug else logging.INFO)

    fmt = logging.Formatter("[%(asctime)s] [%(levelname)s] %(message)s", "%Y-%m-%d %H:%M:%S")

    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(fmt)
    stream.setLevel(logging.DEBUG if debug else logging.INFO)
    logger.addHandler(stream)

    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(fmt)
    file_handler.setLevel(logging.DEBUG)
    logger.addHandler(file_handler)

    logger.info("Log path: %s", log_path)
    return logger


def read_table(path: Path) -> pd.DataFrame:
    suffix = path.suffix.lower()
    if suffix == ".parquet":
        return pd.read_parquet(path)
    if suffix in {".csv", ".txt"}:
        return pd.read_csv(path, dtype=str, low_memory=False)
    raise ValueError(f"Unsupported input format: {path}")


def write_table(df: pd.DataFrame, csv_path: Path, parquet_path: Path, logger: logging.Logger) -> None:
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    parquet_path.parent.mkdir(parents=True, exist_ok=True)

    logger.info("Writing CSV: %s", csv_path)
    df.to_csv(csv_path, index=False)

    logger.info("Writing parquet: %s", parquet_path)
    df.to_parquet(parquet_path, index=False)


def find_source_column(columns: Iterable[str], spec: FeatureSpec) -> tuple[str | None, pd.DataFrame]:
    cols = list(columns)
    rows = []

    for col in cols:
        norm = normalise_text(col)
        score = 0
        required_ok = all(req in norm for req in spec.required)
        any_ok = True if not spec.any_of else any(token in norm for token in spec.any_of)
        exclude_hit = any(token in norm for token in spec.exclude)
        suffix_ok = True if spec.suffix is None else norm.endswith(normalise_text(spec.suffix))

        if required_ok and any_ok and not exclude_hit and suffix_ok:
            score += 100
            if col.startswith("census_qs_housing"):
                score += 25
            if col.startswith("census_qs_dwellings"):
                score += 20
            if col.startswith("dss_"):
                score += 15
            for pref in spec.prefer:
                if pref in norm:
                    score += 5
            # prefer columns without "not_stated" or "unable_to_determine" unless explicitly required
            if "not_stated" in norm and "not_stated" not in spec.required:
                score -= 20
            if "unable_to_determine" in norm and "unable_to_determine" not in spec.required:
                score -= 20

            rows.append(
                {
                    "candidate_column": col,
                    "normalised_column": norm,
                    "score": score,
                    "required_terms": " | ".join(spec.required),
                    "suffix": spec.suffix or "",
                }
            )

    audit = pd.DataFrame(rows).sort_values(["score", "candidate_column"], ascending=[False, True]) if rows else pd.DataFrame(
        columns=["candidate_column", "normalised_column", "score", "required_terms", "suffix"]
    )

    if audit.empty:
        return None, audit

    top_score = audit.iloc[0]["score"]
    top = audit[audit["score"] == top_score]
    if len(top) > 1:
        # Ambiguous. Return first deterministically but flag via audit.
        return str(top.iloc[0]["candidate_column"]), audit.assign(ambiguous_top_match=len(top) > 1)

    return str(audit.iloc[0]["candidate_column"]), audit.assign(ambiguous_top_match=False)


def to_numeric_series(s: pd.Series) -> pd.Series:
    if pd.api.types.is_numeric_dtype(s):
        return pd.to_numeric(s, errors="coerce")
    return pd.to_numeric(
        s.astype(str)
        .str.replace(",", "", regex=False)
        .str.replace("$", "", regex=False)
        .str.replace("%", "", regex=False)
        .str.strip()
        .replace({"": pd.NA, "nan": pd.NA, "None": pd.NA}),
        errors="coerce",
    )


def build_features(master: pd.DataFrame, logger: logging.Logger) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    feature_df = pd.DataFrame()
    if "sa2_code_2021" not in master.columns:
        raise ValueError("Input master does not contain sa2_code_2021.")

    feature_df["sa2_code_2021"] = master["sa2_code_2021"].astype(str)

    mapping_rows = []
    candidate_rows = []

    for spec in FEATURE_SPECS:
        source_col, candidates = find_source_column(master.columns, spec)
        if not candidates.empty:
            candidates = candidates.copy()
            candidates.insert(0, "output_column", spec.output_column)
            candidate_rows.append(candidates)

        if source_col is None:
            logger.warning("No source column matched for %s", spec.output_column)
            feature_df[spec.output_column] = pd.NA
            mapping_rows.append(
                {
                    "output_column": spec.output_column,
                    "source_column": "",
                    "matched": 0,
                    "non_missing_count": 0,
                    "numeric_parse_rate": 0.0,
                    "min_numeric": pd.NA,
                    "max_numeric": pd.NA,
                    "notes": spec.notes,
                }
            )
            continue

        values = to_numeric_series(master[source_col])
        feature_df[spec.output_column] = values
        non_missing = int(values.notna().sum())
        parse_rate = float(non_missing / len(values)) if len(values) else 0.0
        mapping_rows.append(
            {
                "output_column": spec.output_column,
                "source_column": source_col,
                "matched": 1,
                "non_missing_count": non_missing,
                "numeric_parse_rate": parse_rate,
                "min_numeric": values.min(skipna=True),
                "max_numeric": values.max(skipna=True),
                "notes": spec.notes,
            }
        )

    source_feature_cols = [c for c in feature_df.columns if c != "sa2_code_2021"]
    feature_df["source_housing_quickstats_derived_present_flag"] = feature_df[source_feature_cols].notna().any(axis=1).astype(int)

    mapping = pd.DataFrame(mapping_rows)
    candidates_all = pd.concat(candidate_rows, ignore_index=True) if candidate_rows else pd.DataFrame()
    return feature_df, mapping, candidates_all


def join_features(master: pd.DataFrame, features: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    before_rows = len(master)
    before_cols = len(master.columns)

    drop_cols = [c for c in features.columns if c in master.columns and c != "sa2_code_2021"]
    if drop_cols:
        master = master.drop(columns=drop_cols)

    out = master.merge(features, on="sa2_code_2021", how="left", validate="one_to_one")

    audit = pd.DataFrame(
        [
            {"check_name": "master_rows_before_join", "value": before_rows, "status": "pass" if before_rows == EXPECTED_ROWS else "review", "notes": "Expected SA2 row count."},
            {"check_name": "master_columns_before_join", "value": before_cols, "status": "info", "notes": ""},
            {"check_name": "derived_housing_feature_rows", "value": len(features), "status": "pass" if len(features) == EXPECTED_ROWS else "review", "notes": "One row per SA2 expected."},
            {"check_name": "master_rows_after_join", "value": len(out), "status": "pass" if len(out) == before_rows else "fail", "notes": "Join must not change SA2 row count."},
            {"check_name": "master_columns_after_join", "value": len(out.columns), "status": "info", "notes": ""},
            {"check_name": "duplicate_sa2_rows_after_join", "value": int(out["sa2_code_2021"].duplicated().sum()), "status": "pass" if int(out["sa2_code_2021"].duplicated().sum()) == 0 else "fail", "notes": ""},
            {
                "check_name": "sa2_rows_with_derived_housing_context",
                "value": int(out["source_housing_quickstats_derived_present_flag"].fillna(0).astype(int).sum()),
                "status": "info",
                "notes": "Rows with at least one derived housing feature.",
            },
        ]
    )
    return out, audit


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", default=r"D:\Good Measure\MentalWellbeingbyGeography")
    parser.add_argument(
        "--base-master",
        default=r"data\processed\integrated\sa2_predictor_universe_v07_with_housing_affordability_context.parquet",
        help="Path to base master, relative to project root unless absolute.",
    )
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    project_root = Path(args.project_root)
    logger = setup_logger(project_root, args.debug)
    logger.info("Deriving clean housing QuickStats/context features")
    logger.info("Project root: %s", project_root)

    base_master = Path(args.base_master)
    if not base_master.is_absolute():
        base_master = project_root / base_master
    logger.info("Base master: %s", base_master)

    if not base_master.exists():
        raise FileNotFoundError(f"Base master not found: {base_master}")

    master = read_table(base_master)
    logger.info("Base master rows: %s; columns: %s", len(master), len(master.columns))

    features, mapping, candidates = build_features(master, logger)
    logger.info("Derived housing feature table rows: %s; columns: %s", len(features), len(features.columns))

    out, join_audit = join_features(master, features)
    logger.info("Output master rows: %s; columns: %s", len(out), len(out.columns))

    processed_sources = project_root / "data" / "processed" / "sources"
    integrated = project_root / "data" / "processed" / "integrated"
    audits = project_root / "outputs" / "audits"
    dictionaries = project_root / "docs" / "data_dictionaries"
    methodology = project_root / "docs" / "methodology"

    for p in [processed_sources, integrated, audits, dictionaries, methodology]:
        p.mkdir(parents=True, exist_ok=True)

    feature_csv = processed_sources / "housing_quickstats_clean_derived_features_v08.csv"
    feature_parquet = processed_sources / "housing_quickstats_clean_derived_features_v08.parquet"
    write_table(features, feature_csv, feature_parquet, logger)

    mapping_path = audits / "housing_quickstats_clean_feature_mapping_audit_v08.csv"
    candidate_path = audits / "housing_quickstats_clean_feature_candidate_audit_v08.csv"
    join_audit_path = audits / "sa2_predictor_universe_v08_clean_housing_join_audit.csv"
    dictionary_path = dictionaries / "housing_quickstats_clean_context_field_dictionary_v08.csv"
    note_path = methodology / "housing_quickstats_clean_context_layer_note_v08.md"

    logger.info("Writing CSV: %s", mapping_path)
    mapping.to_csv(mapping_path, index=False)

    logger.info("Writing CSV: %s", candidate_path)
    candidates.to_csv(candidate_path, index=False)

    logger.info("Writing CSV: %s", join_audit_path)
    join_audit.to_csv(join_audit_path, index=False)

    dictionary = mapping[["output_column", "source_column", "notes"]].rename(columns={"output_column": "column_name"})
    dictionary.insert(1, "source_family", "Census QuickStats housing context")
    dictionary.insert(2, "native_geography", "SA2 2021")
    dictionary.insert(3, "field_role", "derived_context_predictor_candidate")
    dictionary.insert(
        4,
        "primary_model_use",
        "candidate_context_predictor_after_scope_review; prefer pct/rate fields over raw counts where available",
    )
    logger.info("Writing CSV: %s", dictionary_path)
    dictionary.to_csv(dictionary_path, index=False)

    note = f"""# Housing QuickStats clean context layer v08

This layer derives a compact set of interpretable housing context columns from Census QuickStats fields already present in the v07 master.

It does not scope the master for modelling and does not remove any source columns. It appends clean alias fields for rent, mortgage, tenure, dwelling structure, bedrooms, household composition and selected housing-related DSS context.

Base master:
`{base_master}`

Derived feature table:
`{feature_csv}`

Output master:
`{integrated / 'sa2_predictor_universe_v08_with_clean_housing_context.parquet'}`

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
"""
    logger.info("Writing methodology note: %s", note_path)
    note_path.write_text(note, encoding="utf-8")

    out_csv = integrated / "sa2_predictor_universe_v08_with_clean_housing_context.csv"
    out_parquet = integrated / "sa2_predictor_universe_v08_with_clean_housing_context.parquet"
    write_table(out, out_csv, out_parquet, logger)

    logger.info("Created v08 clean housing master:")
    logger.info("  %s", out_parquet)
    logger.info("  %s", out_csv)
    logger.info("Next action: review housing_quickstats_clean_feature_mapping_audit_v08.csv before using derived fields in modelling.")


if __name__ == "__main__":
    main()
