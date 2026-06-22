from pathlib import Path
import re
import pandas as pd

PROJECT_ROOT = Path(r"D:\Good Measure\MentalWellbeingbyGeography")

SPINE_PATH = PROJECT_ROOT / "data" / "processed" / "spines" / "sa2_2021_spine.parquet"
SEIFA_PATH = PROJECT_ROOT / "data" / "processed" / "sources" / "sa2_seifa_2021.parquet"
REMOTENESS_PATH = PROJECT_ROOT / "data" / "processed" / "sources" / "sa2_remoteness_2021.parquet"
NSMHW_PATH = PROJECT_ROOT / "data" / "processed" / "sources" / "sa2_nsmhw_modelled_estimates_2020_22_wide.parquet"

OUTPUT_DIR = PROJECT_ROOT / "data" / "processed" / "integrated"
AUDIT_DIR = PROJECT_ROOT / "outputs" / "audits"
DICT_DIR = PROJECT_ROOT / "docs" / "data_dictionaries"

OUTPUT_CSV = OUTPUT_DIR / "sa2_predictor_universe_pre_census_v01.csv"
OUTPUT_PARQUET = OUTPUT_DIR / "sa2_predictor_universe_pre_census_v01.parquet"

BUILD_AUDIT = AUDIT_DIR / "sa2_predictor_universe_pre_census_v01_build_audit.csv"
MISSINGNESS_AUDIT = AUDIT_DIR / "sa2_predictor_universe_pre_census_v01_missingness.csv"
COLUMN_SOURCE_AUDIT = AUDIT_DIR / "sa2_predictor_universe_pre_census_v01_column_sources.csv"
NSMHW_CLASSIFICATION_OUTPUT = DICT_DIR / "nsmhw_sa2_variable_classification_preliminary.csv"


def normalise_code(value):
    if pd.isna(value):
        return pd.NA

    value = str(value).strip()

    if value == "" or value.lower() in {"nan", "none", "null"}:
        return pd.NA

    if re.fullmatch(r"\d+\.0", value):
        value = value[:-2]

    return value


def read_table(path: Path) -> pd.DataFrame:
    if path.exists():
        if path.suffix.lower() == ".parquet":
            return pd.read_parquet(path)
        if path.suffix.lower() == ".csv":
            return pd.read_csv(path, dtype=str, low_memory=False)

    csv_path = path.with_suffix(".csv")
    parquet_path = path.with_suffix(".parquet")

    if parquet_path.exists():
        return pd.read_parquet(parquet_path)

    if csv_path.exists():
        return pd.read_csv(csv_path, dtype=str, low_memory=False)

    raise FileNotFoundError(f"Input file not found: {path}")


def load_source(path: Path, source_name: str) -> pd.DataFrame:
    df = read_table(path)

    if "sa2_code_2021" not in df.columns:
        raise ValueError(f"{source_name} missing sa2_code_2021 column.")

    df = df.copy()
    df["sa2_code_2021"] = df["sa2_code_2021"].map(normalise_code).astype("string")

    duplicate_count = int(df["sa2_code_2021"].duplicated().sum())

    if duplicate_count > 0:
        duplicates = (
            df.loc[df["sa2_code_2021"].duplicated(keep=False), ["sa2_code_2021"]]
            .head(20)
            .to_string(index=False)
        )
        raise ValueError(
            f"{source_name} contains duplicate SA2 rows: {duplicate_count}\n"
            f"Example duplicates:\n{duplicates}"
        )

    return df


def prepare_source_for_join(df: pd.DataFrame, source_name: str) -> pd.DataFrame:
    out = df.copy()

    # Keep the spine's SA2 name as the authority.
    if source_name != "spine":
        out = out.drop(columns=["sa2_name_2021"], errors="ignore")

    out[f"source_{source_name}_present_flag"] = True

    return out


def left_join_source(base: pd.DataFrame, source: pd.DataFrame, source_name: str) -> tuple[pd.DataFrame, dict]:
    before_rows = len(base)

    source_prepped = prepare_source_for_join(source, source_name)

    overlap = [
        col for col in source_prepped.columns
        if col in base.columns and col != "sa2_code_2021"
    ]

    if overlap:
        raise ValueError(
            f"Column collision before joining {source_name}: {overlap[:30]}"
        )

    merged = base.merge(
        source_prepped,
        on="sa2_code_2021",
        how="left",
        validate="one_to_one",
    )

    present_col = f"source_{source_name}_present_flag"

    matched = int(merged[present_col].fillna(False).sum())
    unmatched = int(len(merged) - matched)

    merged[present_col] = merged[present_col].fillna(False).astype(bool)

    audit = {
        "source_name": source_name,
        "status": "pass" if len(merged) == before_rows else "fail",
        "rows_before": before_rows,
        "rows_after": len(merged),
        "source_rows": len(source),
        "matched_sa2_rows": matched,
        "unmatched_sa2_rows": unmatched,
        "notes": "Left joined to SA2 spine; output remains one row per SA2.",
    }

    return merged, audit


def classify_nsmhw_column(column_name: str) -> tuple[str, str, str]:
    c = column_name.lower()

    if not c.startswith("nsmhw__"):
        return "not_nsmhw", "not_nsmhw", ""

    if "any_12_month_mental_disorder_modelled_sa2" in c:
        if "estimated" in c or "estimate" in c or "proportion" in c or "percent" in c or "pct" in c:
            return (
                "primary_outcome_candidate",
                "outcome",
                "Likely primary outcome candidate: any 12-month mental disorder SA2 modelled estimate.",
            )
        return (
            "primary_outcome_source_related",
            "manual_review_required",
            "Same workbook as primary outcome; classify exact measure before modelling.",
        )

    if "any_12_month_mental_disorder_by_severity" in c:
        return (
            "severity_related_outcome",
            "sensitivity_or_related_outcome",
            "Use for severe-outcome sensitivity or descriptive analysis; do not use as predictor of any-disorder outcome.",
        )

    if "affective_disorders" in c:
        return (
            "disorder_subtype_related_outcome",
            "target_leakage_exclusion_for_any_disorder_model",
            "Affective disorder subtype overlaps conceptually with any-disorder outcome.",
        )

    if "anxiety_disorders" in c:
        return (
            "disorder_subtype_related_outcome",
            "target_leakage_exclusion_for_any_disorder_model",
            "Anxiety disorder subtype overlaps conceptually with any-disorder outcome.",
        )

    if "substance_use_disorders" in c:
        return (
            "disorder_subtype_related_outcome",
            "target_leakage_exclusion_for_any_disorder_model",
            "Substance-use disorder subtype overlaps conceptually with any-disorder outcome.",
        )

    if "comorbidity" in c:
        return (
            "comorbidity_related_outcome",
            "related_outcome_or_descriptive_context",
            "Comorbidity measure is outcome-adjacent; classify before modelling.",
        )

    return (
        "nsmhw_modelled_estimate_unclassified",
        "manual_review_required",
        "NSMHW SA2 modelled variable preserved; manual role classification required.",
    )


def infer_column_source_and_role(df: pd.DataFrame) -> pd.DataFrame:
    rows = []

    geography_cols = {
        "sa2_code_2021",
        "sa2_name_2021",
        "sa3_code_2021",
        "sa3_name_2021",
        "sa4_code_2021",
        "sa4_name_2021",
        "state_code_2021",
        "state_name_2021",
        "area_albers_sqkm",
    }

    for col in df.columns:
        if col in geography_cols:
            source_table = "sa2_2021_spine"
            field_role = "geography_identifier_or_context"
            modelling_use = "identifier_context_or_grouping"
            notes = "From validated SA2 2021 geography spine."

        elif col.startswith("seifa_"):
            source_table = "sa2_seifa_2021"
            field_role = "candidate_predictor_or_missingness_context"
            modelling_use = "candidate_predictor_after_screening"
            notes = "SEIFA variable or SEIFA missingness/exclusion flag."

        elif (
            col.startswith("remoteness_")
            or col in {
                "remoteness_area_share_of_sa2",
                "remoteness_sa1_count",
                "sa2_ra_category_count",
                "remoteness_assignment_method",
            }
        ):
            source_table = "sa2_remoteness_2021"
            field_role = "candidate_predictor_or_geographic_context"
            modelling_use = "candidate_predictor_after_screening"
            notes = "SA2-level remoteness classification derived through SA1 bridge."

        elif col.startswith("nsmhw__"):
            source_table = "sa2_nsmhw_modelled_estimates_2020_22_wide"
            field_role, modelling_use, notes = classify_nsmhw_column(col)

        elif col.startswith("source_") and col.endswith("_present_flag"):
            source_table = "integration_build"
            field_role = "source_presence_audit"
            modelling_use = "audit_only"
            notes = "Indicates whether the SA2 matched the source table during integration."

        else:
            source_table = "unknown_or_manual_review"
            field_role = "manual_review_required"
            modelling_use = "manual_review_required"
            notes = "Column requires manual source/role classification."

        rows.append(
            {
                "column_name": col,
                "source_table": source_table,
                "initial_field_role": field_role,
                "initial_modelling_use": modelling_use,
                "missing_count": int(df[col].isna().sum()),
                "missing_pct": round(float(df[col].isna().mean() * 100), 3),
                "dtype": str(df[col].dtype),
                "notes": notes,
            }
        )

    return pd.DataFrame(rows)


def build_missingness_table(df: pd.DataFrame) -> pd.DataFrame:
    rows = []

    for col in df.columns:
        rows.append(
            {
                "column_name": col,
                "missing_count": int(df[col].isna().sum()),
                "missing_pct": round(float(df[col].isna().mean() * 100), 3),
                "non_missing_count": int(df[col].notna().sum()),
                "dtype": str(df[col].dtype),
            }
        )

    return (
        pd.DataFrame(rows)
        .sort_values(["missing_pct", "column_name"], ascending=[False, True])
        .reset_index(drop=True)
    )


def build_nsmhw_classification(df: pd.DataFrame) -> pd.DataFrame:
    rows = []

    for col in df.columns:
        if not col.startswith("nsmhw__"):
            continue

        field_role, modelling_use, notes = classify_nsmhw_column(col)

        rows.append(
            {
                "column_name": col,
                "field_role_preliminary": field_role,
                "modelling_use_preliminary": modelling_use,
                "manual_review_required": modelling_use == "manual_review_required",
                "notes": notes,
            }
        )

    return pd.DataFrame(rows)


def build_final_audit(df: pd.DataFrame, source_audits: list[dict]) -> pd.DataFrame:
    rows = list(source_audits)

    duplicate_sa2 = int(df["sa2_code_2021"].duplicated().sum())
    missing_sa2 = int(df["sa2_code_2021"].isna().sum())
    nsmhw_cols = [col for col in df.columns if col.startswith("nsmhw__")]
    seifa_cols = [col for col in df.columns if col.startswith("seifa_")]
    remoteness_cols = [col for col in df.columns if col.startswith("remoteness_")]

    rows.extend(
        [
            {
                "source_name": "final_integrated_table",
                "status": "pass" if len(df) == 2472 else "review",
                "rows_before": "",
                "rows_after": len(df),
                "source_rows": "",
                "matched_sa2_rows": "",
                "unmatched_sa2_rows": "",
                "notes": "Expected 2472 rows from the current validated ABS SA2 spine.",
            },
            {
                "source_name": "final_duplicate_sa2_check",
                "status": "pass" if duplicate_sa2 == 0 else "fail",
                "rows_before": "",
                "rows_after": len(df),
                "source_rows": "",
                "matched_sa2_rows": "",
                "unmatched_sa2_rows": duplicate_sa2,
                "notes": "Integrated table must remain one row per SA2.",
            },
            {
                "source_name": "final_missing_sa2_check",
                "status": "pass" if missing_sa2 == 0 else "fail",
                "rows_before": "",
                "rows_after": len(df),
                "source_rows": "",
                "matched_sa2_rows": "",
                "unmatched_sa2_rows": missing_sa2,
                "notes": "SA2 code must not be missing.",
            },
            {
                "source_name": "final_column_count",
                "status": "info",
                "rows_before": "",
                "rows_after": len(df),
                "source_rows": "",
                "matched_sa2_rows": "",
                "unmatched_sa2_rows": "",
                "notes": f"Integrated table has {len(df.columns)} columns.",
            },
            {
                "source_name": "final_nsmhw_column_count",
                "status": "pass" if len(nsmhw_cols) > 0 else "fail",
                "rows_before": "",
                "rows_after": len(df),
                "source_rows": "",
                "matched_sa2_rows": "",
                "unmatched_sa2_rows": "",
                "notes": f"Integrated table has {len(nsmhw_cols)} NSMHW columns.",
            },
            {
                "source_name": "final_seifa_column_count",
                "status": "pass" if len(seifa_cols) > 0 else "fail",
                "rows_before": "",
                "rows_after": len(df),
                "source_rows": "",
                "matched_sa2_rows": "",
                "unmatched_sa2_rows": "",
                "notes": f"Integrated table has {len(seifa_cols)} SEIFA columns.",
            },
            {
                "source_name": "final_remoteness_column_count",
                "status": "pass" if len(remoteness_cols) > 0 else "fail",
                "rows_before": "",
                "rows_after": len(df),
                "source_rows": "",
                "matched_sa2_rows": "",
                "unmatched_sa2_rows": "",
                "notes": f"Integrated table has {len(remoteness_cols)} remoteness columns.",
            },
        ]
    )

    return pd.DataFrame(rows)


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    AUDIT_DIR.mkdir(parents=True, exist_ok=True)
    DICT_DIR.mkdir(parents=True, exist_ok=True)

    print("Loading source tables.")

    spine = load_source(SPINE_PATH, "sa2_2021_spine")
    seifa = load_source(SEIFA_PATH, "sa2_seifa_2021")
    remoteness = load_source(REMOTENESS_PATH, "sa2_remoteness_2021")
    nsmhw = load_source(NSMHW_PATH, "sa2_nsmhw_modelled_estimates_2020_22_wide")

    integrated = spine.copy()
    source_audits = []

    print("Joining SEIFA.")
    integrated, audit = left_join_source(integrated, seifa, "seifa")
    source_audits.append(audit)

    print("Joining remoteness.")
    integrated, audit = left_join_source(integrated, remoteness, "remoteness")
    source_audits.append(audit)

    print("Joining all public SA2-level NSMHW variables.")
    integrated, audit = left_join_source(integrated, nsmhw, "nsmhw")
    source_audits.append(audit)

    final_audit = build_final_audit(integrated, source_audits)
    missingness = build_missingness_table(integrated)
    column_sources = infer_column_source_and_role(integrated)
    nsmhw_classification = build_nsmhw_classification(integrated)

    integrated.to_csv(OUTPUT_CSV, index=False, encoding="utf-8-sig")

    parquet_status = "written"
    try:
        integrated.to_parquet(OUTPUT_PARQUET, index=False)
    except Exception as exc:
        parquet_status = f"not written: {exc}"

    final_audit.to_csv(BUILD_AUDIT, index=False, encoding="utf-8-sig")
    missingness.to_csv(MISSINGNESS_AUDIT, index=False, encoding="utf-8-sig")
    column_sources.to_csv(COLUMN_SOURCE_AUDIT, index=False, encoding="utf-8-sig")
    nsmhw_classification.to_csv(NSMHW_CLASSIFICATION_OUTPUT, index=False, encoding="utf-8-sig")

    print("\nCreated integrated pre-Census SA2 table:")
    print(f"  {OUTPUT_CSV}")
    print(f"  {OUTPUT_PARQUET} ({parquet_status})")

    print("\nCreated audits and dictionaries:")
    print(f"  {BUILD_AUDIT}")
    print(f"  {MISSINGNESS_AUDIT}")
    print(f"  {COLUMN_SOURCE_AUDIT}")
    print(f"  {NSMHW_CLASSIFICATION_OUTPUT}")

    print("\nBuild audit:")
    print(final_audit.to_string(index=False))

    print("\nIntegrated table shape:")
    print(f"  rows: {len(integrated)}")
    print(f"  columns: {len(integrated.columns)}")

    print("\nVariable group counts:")
    print(f"  NSMHW columns: {sum(col.startswith('nsmhw__') for col in integrated.columns)}")
    print(f"  SEIFA columns: {sum(col.startswith('seifa_') for col in integrated.columns)}")
    print(f"  Remoteness columns: {sum(col.startswith('remoteness_') for col in integrated.columns)}")

    failed = final_audit.query("status == 'fail'")
    if not failed.empty:
        raise RuntimeError(f"Integrated table build failed. Review {BUILD_AUDIT}")

    print("\nPre-Census SA2 predictor universe created with all public SA2-level NSMHW variables preserved.")


if __name__ == "__main__":
    main()
