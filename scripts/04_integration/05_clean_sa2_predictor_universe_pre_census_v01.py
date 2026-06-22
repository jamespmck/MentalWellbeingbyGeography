from pathlib import Path
import re
import pandas as pd

PROJECT_ROOT = Path(r"D:\Good Measure\MentalWellbeingbyGeography")

INPUT_PATH = PROJECT_ROOT / "data" / "processed" / "integrated" / "sa2_predictor_universe_pre_census_v01.parquet"

OUTPUT_CSV = PROJECT_ROOT / "data" / "processed" / "integrated" / "sa2_predictor_universe_pre_census_v01_clean.csv"
OUTPUT_PARQUET = PROJECT_ROOT / "data" / "processed" / "integrated" / "sa2_predictor_universe_pre_census_v01_clean.parquet"

AUDIT_OUTPUT = PROJECT_ROOT / "outputs" / "audits" / "sa2_predictor_universe_pre_census_v01_cleaning_audit.csv"
COLUMN_OUTPUT = PROJECT_ROOT / "outputs" / "audits" / "sa2_predictor_universe_pre_census_v01_clean_columns.csv"


def read_input() -> pd.DataFrame:
    if INPUT_PATH.exists():
        return pd.read_parquet(INPUT_PATH)

    csv_path = INPUT_PATH.with_suffix(".csv")

    if csv_path.exists():
        return pd.read_csv(csv_path, dtype=str, low_memory=False)

    raise FileNotFoundError(f"Input integrated table not found: {INPUT_PATH}")


def clean_nsmhw_column_name(col: str) -> str:
    if not col.startswith("nsmhw__"):
        return col

    # ABS confidence interval columns were split across two adjacent columns.
    if col.endswith("__95_confidence_interval_of_proportion"):
        return col.replace(
            "__95_confidence_interval_of_proportion",
            "__proportion_95ci_lower",
        )

    if col.endswith("__unnamed_2"):
        return col.replace("__unnamed_2", "__proportion_95ci_upper")

    # The first unnamed column contains ABS reliability markers such as * and **.
    if col.endswith("__unnamed"):
        return col.replace("__unnamed", "__relative_root_mean_square_error_flag")

    # Standardise RRMSE suffixes.
    if col.endswith("__relative_root_mean_square_error_a"):
        return col.replace(
            "__relative_root_mean_square_error_a",
            "__relative_root_mean_square_error_pct",
        )

    if col.endswith("__relative_root_mean_square_error_b"):
        return col.replace(
            "__relative_root_mean_square_error_b",
            "__relative_root_mean_square_error_pct",
        )

    return col


def coerce_numeric_columns(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()

    keep_text_patterns = [
        "sa2_code_2021",
        "sa2_name_2021",
        "sa3_code_2021",
        "sa3_name_2021",
        "sa4_code_2021",
        "sa4_name_2021",
        "state_code_2021",
        "state_name_2021",
        "remoteness_area_code_2021",
        "remoteness_area_name_2021",
        "remoteness_area_short_2021",
        "remoteness_assignment_method",
        "relative_root_mean_square_error_flag",
    ]

    for col in out.columns:
        if col.startswith("source_") and col.endswith("_present_flag"):
            out[col] = out[col].astype("string").str.lower().map(
                {
                    "true": True,
                    "false": False,
                    "1": True,
                    "0": False,
                }
            )
            continue

        if any(pattern == col or pattern in col for pattern in keep_text_patterns):
            continue

        numeric = pd.to_numeric(out[col], errors="coerce")

        original_non_missing = int(out[col].notna().sum())
        numeric_non_missing = int(numeric.notna().sum())

        if original_non_missing == 0:
            continue

        if numeric_non_missing / original_non_missing >= 0.9:
            out[col] = numeric

    return out


def add_nsmhw_availability_flags(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()

    source_prefixes = sorted(
        {
            col.split("__")[1]
            for col in out.columns
            if col.startswith("nsmhw__") and len(col.split("__")) >= 3
        }
    )

    for source in source_prefixes:
        source_cols = [
            col for col in out.columns
            if col.startswith(f"nsmhw__{source}__")
            and not col.endswith("_available_flag")
        ]

        numeric_or_text_signal_cols = [
            col for col in source_cols
            if not col.endswith("relative_root_mean_square_error_flag")
        ]

        flag_col = f"nsmhw__{source}__available_flag"

        out[flag_col] = out[numeric_or_text_signal_cols].notna().any(axis=1)

    # The old source_nsmhw_present_flag is misleading because the NSMHW wide table
    # was already padded to the full SA2 spine. Keep it, but add a clearer flag.
    nsmhw_available_cols = [
        col for col in out.columns
        if col.startswith("nsmhw__") and col.endswith("__available_flag")
    ]

    out["source_nsmhw_modelled_estimates_available_flag"] = (
        out[nsmhw_available_cols].any(axis=1)
        if nsmhw_available_cols
        else False
    )

    return out


def build_audit(before: pd.DataFrame, after: pd.DataFrame) -> pd.DataFrame:
    rows = []

    rows.extend(
        [
            {
                "check_name": "row_count_before",
                "value": len(before),
                "status": "info",
                "notes": "Rows before cleaning.",
            },
            {
                "check_name": "row_count_after",
                "value": len(after),
                "status": "pass" if len(after) == len(before) else "fail",
                "notes": "Rows after cleaning should match input.",
            },
            {
                "check_name": "column_count_before",
                "value": len(before.columns),
                "status": "info",
                "notes": "Columns before cleaning.",
            },
            {
                "check_name": "column_count_after",
                "value": len(after.columns),
                "status": "info",
                "notes": "Columns after cleaning; availability flags may increase this.",
            },
            {
                "check_name": "duplicate_sa2_rows",
                "value": int(after["sa2_code_2021"].duplicated().sum()),
                "status": "pass" if int(after["sa2_code_2021"].duplicated().sum()) == 0 else "fail",
                "notes": "Cleaned table must remain one row per SA2.",
            },
            {
                "check_name": "missing_sa2_code",
                "value": int(after["sa2_code_2021"].isna().sum()),
                "status": "pass" if int(after["sa2_code_2021"].isna().sum()) == 0 else "fail",
                "notes": "SA2 code must not be missing.",
            },
        ]
    )

    unnamed_after = [
        col for col in after.columns
        if col.startswith("nsmhw__") and "__unnamed" in col
    ]

    rows.append(
        {
            "check_name": "remaining_nsmhw_unnamed_columns",
            "value": len(unnamed_after),
            "status": "pass" if len(unnamed_after) == 0 else "review",
            "notes": "NSMHW columns should have explicit names.",
        }
    )

    availability_flag = "source_nsmhw_modelled_estimates_available_flag"

    if availability_flag in after.columns:
        rows.append(
            {
                "check_name": "nsmhw_available_sa2_count",
                "value": int(after[availability_flag].sum()),
                "status": "info",
                "notes": "SA2s with at least one public NSMHW modelled estimate.",
            }
        )

        rows.append(
            {
                "check_name": "nsmhw_unavailable_sa2_count",
                "value": int((~after[availability_flag]).sum()),
                "status": "info",
                "notes": "SA2s retained in spine but without public NSMHW modelled estimates.",
            }
        )

    return pd.DataFrame(rows)


def build_column_audit(before_cols: list[str], after: pd.DataFrame) -> pd.DataFrame:
    rename_map = {
        old: clean_nsmhw_column_name(old)
        for old in before_cols
    }

    rows = []

    for old, new in rename_map.items():
        rows.append(
            {
                "old_column_name": old,
                "new_column_name": new,
                "renamed": old != new,
                "missing_count": int(after[new].isna().sum()) if new in after.columns else "",
                "dtype": str(after[new].dtype) if new in after.columns else "",
            }
        )

    # Add new columns created by this script.
    for col in after.columns:
        if col not in rename_map.values():
            rows.append(
                {
                    "old_column_name": "",
                    "new_column_name": col,
                    "renamed": "created",
                    "missing_count": int(after[col].isna().sum()),
                    "dtype": str(after[col].dtype),
                }
            )

    return pd.DataFrame(rows)


def main() -> None:
    before = read_input()

    rename_map = {
        col: clean_nsmhw_column_name(col)
        for col in before.columns
    }

    after = before.rename(columns=rename_map).copy()

    if after.columns.duplicated().any():
        dupes = after.columns[after.columns.duplicated()].tolist()
        raise ValueError(f"Cleaning created duplicate columns: {dupes}")

    after = coerce_numeric_columns(after)
    after = add_nsmhw_availability_flags(after)

    audit = build_audit(before, after)
    column_audit = build_column_audit(list(before.columns), after)

    OUTPUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    AUDIT_OUTPUT.parent.mkdir(parents=True, exist_ok=True)

    after.to_csv(OUTPUT_CSV, index=False, encoding="utf-8-sig")

    parquet_status = "written"
    try:
        after.to_parquet(OUTPUT_PARQUET, index=False)
    except Exception as exc:
        parquet_status = f"not written: {exc}"

    audit.to_csv(AUDIT_OUTPUT, index=False, encoding="utf-8-sig")
    column_audit.to_csv(COLUMN_OUTPUT, index=False, encoding="utf-8-sig")

    print("Created cleaned pre-Census integrated table:")
    print(f"  {OUTPUT_CSV}")
    print(f"  {OUTPUT_PARQUET} ({parquet_status})")

    print("\nCreated audits:")
    print(f"  {AUDIT_OUTPUT}")
    print(f"  {COLUMN_OUTPUT}")

    print("\nCleaning audit:")
    print(audit.to_string(index=False))

    print("\nCleaned table shape:")
    print(f"  rows: {len(after)}")
    print(f"  columns: {len(after.columns)}")

    failed = audit.query("status == 'fail'")
    if not failed.empty:
        raise RuntimeError(f"Cleaning failed. Review {AUDIT_OUTPUT}")

    print("\nPre-Census table cleaned.")


if __name__ == "__main__":
    main()
