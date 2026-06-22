from pathlib import Path
import re
import pandas as pd

PROJECT_ROOT = Path(r"D:\Good Measure\MentalWellbeingbyGeography")

MASTER_IN_PARQUET = PROJECT_ROOT / "data" / "processed" / "integrated" / "sa2_predictor_universe_v01.parquet"
MASTER_IN_CSV = PROJECT_ROOT / "data" / "processed" / "integrated" / "sa2_predictor_universe_v01.csv"

AIHW_IN_PARQUET = PROJECT_ROOT / "data" / "processed" / "sources" / "sa3_aihw_regional_profiles_selected_measures_2021_22.parquet"
AIHW_IN_CSV = PROJECT_ROOT / "data" / "processed" / "sources" / "sa3_aihw_regional_profiles_selected_measures_2021_22.csv"

INTEGRATED_DIR = PROJECT_ROOT / "data" / "processed" / "integrated"
AUDIT_DIR = PROJECT_ROOT / "outputs" / "audits"
DICT_DIR = PROJECT_ROOT / "docs" / "data_dictionaries"

MASTER_OUT_CSV = INTEGRATED_DIR / "sa2_predictor_universe_v02_with_aihw_sa3.csv"
MASTER_OUT_PARQUET = INTEGRATED_DIR / "sa2_predictor_universe_v02_with_aihw_sa3.parquet"

JOIN_AUDIT = AUDIT_DIR / "sa2_predictor_universe_v02_aihw_sa3_join_audit.csv"
UNMATCHED_SA3_AUDIT = AUDIT_DIR / "sa2_predictor_universe_v02_aihw_sa3_unmatched_sa3_audit.csv"
COLUMN_AUDIT = AUDIT_DIR / "sa2_predictor_universe_v02_aihw_sa3_column_audit.csv"
MISSINGNESS_AUDIT = AUDIT_DIR / "sa2_predictor_universe_v02_aihw_sa3_missingness.csv"

COLUMN_DICTIONARY_IN = DICT_DIR / "sa3_aihw_regional_profiles_selected_measure_dictionary_2021_22.csv"
COLUMN_DICTIONARY_OUT = DICT_DIR / "sa2_predictor_universe_v02_aihw_sa3_column_dictionary.csv"


def normalise_code(value):
    if pd.isna(value):
        return pd.NA

    text = str(value).strip()

    if text == "" or text.lower() in {"nan", "none", "null"}:
        return pd.NA

    text = re.sub(r"\.0$", "", text)
    return text


def read_table(parquet_path: Path, csv_path: Path, label: str) -> pd.DataFrame:
    if parquet_path.exists():
        print(f"Reading {label}:")
        print(f"  {parquet_path}")
        return pd.read_parquet(parquet_path)

    if csv_path.exists():
        print(f"Reading {label}:")
        print(f"  {csv_path}")
        return pd.read_csv(csv_path, dtype=str, low_memory=False)

    raise FileNotFoundError(f"Could not find {label}: {parquet_path} or {csv_path}")


def classify_aihw_columns(aihw: pd.DataFrame) -> list[str]:
    join_cols = ["sa3_code_2021"]

    candidate_cols = []

    for col in aihw.columns:
        if col in join_cols:
            continue

        # Do not bring duplicate geography labels into the SA2 master.
        if col in {"sa3_name_2021", "state_name_2021"}:
            continue

        if col.startswith("aihw_"):
            candidate_cols.append(col)
            continue

        if col in {"has_aihw_sa3_regional_profile_extract"}:
            candidate_cols.append(col)
            continue

    return join_cols + candidate_cols


def build_missingness_audit(df: pd.DataFrame, aihw_cols: list[str]) -> pd.DataFrame:
    rows = []

    for col in aihw_cols:
        if col == "sa3_code_2021":
            continue

        missing = int(df[col].isna().sum()) if col in df.columns else len(df)
        non_missing = int(df[col].notna().sum()) if col in df.columns else 0

        rows.append(
            {
                "column_name": col,
                "non_missing_count": non_missing,
                "missing_count": missing,
                "missing_pct": round(missing / len(df) * 100, 3) if len(df) else pd.NA,
                "source_family": "aihw_sa3_regional_profiles_2021_22",
                "native_geography": "SA3",
                "notes": "SA3-level value repeated across SA2 rows in same SA3 after join.",
            }
        )

    return pd.DataFrame(rows).sort_values(["missing_pct", "column_name"], ascending=[False, True])


def main():
    INTEGRATED_DIR.mkdir(parents=True, exist_ok=True)
    AUDIT_DIR.mkdir(parents=True, exist_ok=True)
    DICT_DIR.mkdir(parents=True, exist_ok=True)

    master = read_table(MASTER_IN_PARQUET, MASTER_IN_CSV, "SA2 master v01")
    aihw = read_table(AIHW_IN_PARQUET, AIHW_IN_CSV, "AIHW SA3 selected measures")

    if "sa3_code_2021" not in master.columns:
        raise ValueError("SA2 master is missing sa3_code_2021.")

    if "sa3_code_2021" not in aihw.columns:
        raise ValueError("AIHW selected-measures table is missing sa3_code_2021.")

    master = master.copy()
    aihw = aihw.copy()

    master["sa3_code_2021"] = master["sa3_code_2021"].map(normalise_code).astype("string")
    aihw["sa3_code_2021"] = aihw["sa3_code_2021"].map(normalise_code).astype("string")

    before_rows = len(master)
    before_cols = len(master.columns)

    source_rows = len(aihw)
    source_unique_sa3 = int(aihw["sa3_code_2021"].nunique(dropna=True))
    source_duplicate_sa3 = int(aihw.duplicated(subset=["sa3_code_2021"]).sum())

    if source_duplicate_sa3 > 0:
        duplicated = (
            aihw.loc[aihw.duplicated(subset=["sa3_code_2021"], keep=False), ["sa3_code_2021"]]
            .drop_duplicates()
            .sort_values("sa3_code_2021")
        )
        raise ValueError(
            f"AIHW selected-measures table has duplicate SA3 rows. "
            f"First duplicates: {duplicated.head(20)['sa3_code_2021'].tolist()}"
        )

    keep_cols = classify_aihw_columns(aihw)

    aihw_join = aihw[keep_cols].copy()
    aihw_join["source_aihw_sa3_regional_profiles_2021_22_present_flag"] = True

    collision_cols = [
        col for col in aihw_join.columns
        if col in master.columns and col != "sa3_code_2021"
    ]

    if collision_cols:
        raise ValueError(f"Column collisions before join: {collision_cols[:50]}")

    joined = master.merge(
        aihw_join,
        on="sa3_code_2021",
        how="left",
        validate="many_to_one",
    )

    joined["source_aihw_sa3_regional_profiles_2021_22_present_flag"] = (
        joined["source_aihw_sa3_regional_profiles_2021_22_present_flag"]
        .fillna(False)
        .astype(bool)
    )

    after_rows = len(joined)
    after_cols = len(joined.columns)

    matched_sa2_rows = int(joined["source_aihw_sa3_regional_profiles_2021_22_present_flag"].sum())
    unmatched_sa2_rows = int(after_rows - matched_sa2_rows)

    master_unique_sa3 = int(master["sa3_code_2021"].nunique(dropna=True))
    matched_unique_sa3 = int(
        joined.loc[
            joined["source_aihw_sa3_regional_profiles_2021_22_present_flag"],
            "sa3_code_2021",
        ].nunique(dropna=True)
    )
    unmatched_unique_sa3 = int(master_unique_sa3 - matched_unique_sa3)

    unmatched = (
        joined.loc[
            ~joined["source_aihw_sa3_regional_profiles_2021_22_present_flag"],
            [
                "sa3_code_2021",
                "sa3_name_2021",
                "state_name_2021",
            ],
        ]
        .drop_duplicates()
        .sort_values(["state_name_2021", "sa3_code_2021"])
        .reset_index(drop=True)
    )

    unmatched["reason_inferred"] = "No AIHW SA3 selected-measures row joined. Likely special/non-standard geography or no profile rows."

    unmatched.to_csv(UNMATCHED_SA3_AUDIT, index=False, encoding="utf-8-sig")

    aihw_added_cols = [col for col in joined.columns if col not in master.columns]

    column_audit = pd.DataFrame(
        [
            {
                "column_name": col,
                "source_family": "aihw_sa3_regional_profiles_2021_22",
                "native_geography": "SA3",
                "join_key": "sa3_code_2021",
                "field_role": (
                    "source_presence_flag"
                    if col == "source_aihw_sa3_regional_profiles_2021_22_present_flag"
                    else "candidate_predictor_aihw_sa3_service_activity"
                ),
                "modelling_warning": (
                    "SA3-level predictor repeated across SA2 rows in the same SA3. "
                    "Use grouped validation by sa3_code_2021."
                ),
            }
            for col in aihw_added_cols
        ]
    )

    column_audit.to_csv(COLUMN_AUDIT, index=False, encoding="utf-8-sig")

    missingness = build_missingness_audit(joined, aihw_added_cols)
    missingness.to_csv(MISSINGNESS_AUDIT, index=False, encoding="utf-8-sig")

    audit = pd.DataFrame(
        [
            {
                "check_name": "master_rows_before_join",
                "value": before_rows,
                "status": "pass" if before_rows == 2472 else "review",
                "notes": "Expected current SA2 master row count.",
            },
            {
                "check_name": "master_columns_before_join",
                "value": before_cols,
                "status": "info",
                "notes": "",
            },
            {
                "check_name": "aihw_source_rows",
                "value": source_rows,
                "status": "pass" if source_rows == 335 else "review",
                "notes": "Expected 335 SA3s with AIHW selected-measure rows from completed 2021-22 scrape.",
            },
            {
                "check_name": "aihw_source_unique_sa3",
                "value": source_unique_sa3,
                "status": "pass" if source_unique_sa3 == source_rows else "fail",
                "notes": "AIHW selected-measures table should have one row per SA3.",
            },
            {
                "check_name": "aihw_source_duplicate_sa3_rows",
                "value": source_duplicate_sa3,
                "status": "pass" if source_duplicate_sa3 == 0 else "fail",
                "notes": "",
            },
            {
                "check_name": "master_unique_sa3",
                "value": master_unique_sa3,
                "status": "info",
                "notes": "Current SA2 spine includes all SA3s, including special/non-standard geographies.",
            },
            {
                "check_name": "matched_sa2_rows",
                "value": matched_sa2_rows,
                "status": "info",
                "notes": "SA2 rows whose SA3 matched an AIHW SA3 profile row.",
            },
            {
                "check_name": "unmatched_sa2_rows",
                "value": unmatched_sa2_rows,
                "status": "review",
                "notes": "Expected for special/non-standard SA3s or SA3s with no AIHW profile rows.",
            },
            {
                "check_name": "matched_unique_sa3",
                "value": matched_unique_sa3,
                "status": "pass" if matched_unique_sa3 == 335 else "review",
                "notes": "",
            },
            {
                "check_name": "unmatched_unique_sa3",
                "value": unmatched_unique_sa3,
                "status": "review",
                "notes": "Review unmatched SA3 audit.",
            },
            {
                "check_name": "master_rows_after_join",
                "value": after_rows,
                "status": "pass" if after_rows == before_rows else "fail",
                "notes": "Join must not change SA2 row count.",
            },
            {
                "check_name": "master_columns_after_join",
                "value": after_cols,
                "status": "info",
                "notes": "",
            },
            {
                "check_name": "aihw_columns_added",
                "value": len(aihw_added_cols),
                "status": "info",
                "notes": "",
            },
            {
                "check_name": "duplicate_sa2_rows_after_join",
                "value": int(joined.duplicated(subset=["sa2_code_2021"]).sum()) if "sa2_code_2021" in joined.columns else pd.NA,
                "status": (
                    "pass"
                    if "sa2_code_2021" in joined.columns and int(joined.duplicated(subset=["sa2_code_2021"]).sum()) == 0
                    else "review"
                ),
                "notes": "",
            },
        ]
    )

    audit.to_csv(JOIN_AUDIT, index=False, encoding="utf-8-sig")

    if COLUMN_DICTIONARY_IN.exists():
        source_dict = pd.read_csv(COLUMN_DICTIONARY_IN, dtype=str)
        source_dict["joined_to_sa2_master"] = "yes"
        source_dict["joined_master_table"] = "sa2_predictor_universe_v02_with_aihw_sa3"
        source_dict["join_method"] = "left join from SA2 master to AIHW SA3 selected measures by sa3_code_2021"
        source_dict["native_geography"] = "SA3"
        source_dict.to_csv(COLUMN_DICTIONARY_OUT, index=False, encoding="utf-8-sig")
    else:
        column_audit.to_csv(COLUMN_DICTIONARY_OUT, index=False, encoding="utf-8-sig")

    joined.to_csv(MASTER_OUT_CSV, index=False, encoding="utf-8-sig")
    joined.to_parquet(MASTER_OUT_PARQUET, index=False)

    print("\nCreated AIHW SA3 joined master:")
    print(f"  {MASTER_OUT_CSV}")
    print(f"  {MASTER_OUT_PARQUET}")

    print("\nCreated audits:")
    print(f"  {JOIN_AUDIT}")
    print(f"  {UNMATCHED_SA3_AUDIT}")
    print(f"  {COLUMN_AUDIT}")
    print(f"  {MISSINGNESS_AUDIT}")
    print(f"  {COLUMN_DICTIONARY_OUT}")

    print("\nJoin audit:")
    print(audit.to_string(index=False))

    print("\nUnmatched SA3s:")
    if unmatched.empty:
        print("  None")
    else:
        print(unmatched.to_string(index=False))


if __name__ == "__main__":
    main()
