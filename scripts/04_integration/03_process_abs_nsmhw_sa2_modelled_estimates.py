from pathlib import Path
import re
import pandas as pd

PROJECT_ROOT = Path(r"D:\Good Measure\MentalWellbeingbyGeography")

SPINE_PATH = PROJECT_ROOT / "data" / "processed" / "spines" / "sa2_2021_spine.parquet"

RAW_DIR = PROJECT_ROOT / "data" / "raw" / "abs" / "nsmhw"
PROCESSED_SOURCE_DIR = PROJECT_ROOT / "data" / "processed" / "sources"
AUDIT_DIR = PROJECT_ROOT / "outputs" / "audits"

MANIFEST_PATH = AUDIT_DIR / "abs_nsmhw_sa2_modelled_estimates_download_manifest.csv"

WIDE_CSV = PROCESSED_SOURCE_DIR / "sa2_nsmhw_modelled_estimates_2020_22_wide.csv"
WIDE_PARQUET = PROCESSED_SOURCE_DIR / "sa2_nsmhw_modelled_estimates_2020_22_wide.parquet"

LONG_CSV = PROCESSED_SOURCE_DIR / "sa2_nsmhw_modelled_estimates_2020_22_long.csv"
LONG_PARQUET = PROCESSED_SOURCE_DIR / "sa2_nsmhw_modelled_estimates_2020_22_long.parquet"

PROCESSING_AUDIT = AUDIT_DIR / "sa2_nsmhw_modelled_estimates_2020_22_processing_audit.csv"
SOURCE_CANDIDATE_AUDIT = AUDIT_DIR / "sa2_nsmhw_modelled_estimates_2020_22_source_candidate_audit.csv"
COLUMN_DICTIONARY = AUDIT_DIR / "sa2_nsmhw_modelled_estimates_2020_22_column_dictionary.csv"


def clean_text(value) -> str:
    if pd.isna(value):
        return ""

    text = str(value).strip()
    text = re.sub(r"\s+", " ", text)
    return text


def clean_col_name(value) -> str:
    value = clean_text(value).lower()
    value = value.replace("12-month", "12_month")
    value = re.sub(r"[^a-z0-9]+", "_", value)
    value = re.sub(r"_+", "_", value)
    return value.strip("_")


def normalise_code(value):
    if pd.isna(value):
        return pd.NA

    value = str(value).strip()

    if value == "" or value.lower() in {"nan", "none", "null"}:
        return pd.NA

    if re.fullmatch(r"\d+\.0", value):
        value = value[:-2]

    return value


def slugify(value: str) -> str:
    value = clean_col_name(value)
    return value or "unnamed"


def load_spine() -> pd.DataFrame:
    if not SPINE_PATH.exists():
        raise FileNotFoundError(f"SA2 spine not found: {SPINE_PATH}")

    spine = pd.read_parquet(SPINE_PATH)

    required = ["sa2_code_2021", "sa2_name_2021"]

    missing = [col for col in required if col not in spine.columns]
    if missing:
        raise ValueError(f"SA2 spine missing required columns: {missing}")

    out = spine[required].copy()
    out["sa2_code_2021"] = out["sa2_code_2021"].map(normalise_code).astype("string")
    out["sa2_name_2021"] = out["sa2_name_2021"].astype("string").str.strip()

    if out["sa2_code_2021"].duplicated().any():
        raise ValueError("SA2 spine contains duplicate SA2 codes.")

    return out


def load_manifest() -> pd.DataFrame:
    if not MANIFEST_PATH.exists():
        raise FileNotFoundError(
            f"Download manifest not found: {MANIFEST_PATH}\n"
            "Run scripts\\03_acquisition\\03_fetch_abs_nsmhw_sa2_modelled_estimates.py first."
        )

    manifest = pd.read_csv(MANIFEST_PATH, dtype=str)

    required = ["downloaded_path", "download_status", "source_slug", "filename"]

    missing = [col for col in required if col not in manifest.columns]
    if missing:
        raise ValueError(f"Manifest missing required columns: {missing}")

    manifest = manifest[manifest["download_status"].eq("downloaded")].copy()

    if manifest.empty:
        raise RuntimeError("Manifest contains no successfully downloaded NSMHW files.")

    return manifest


def identify_sa2_code_column(columns: list[str]) -> str | None:
    for col in columns:
        c = clean_col_name(col)

        if c in {
            "sa2_code_2021",
            "sa2_maincode_2021",
            "sa2_main_code_2021",
            "sa2_code",
            "sa2_maincode",
            "sa2_main_code",
        }:
            return col

    for col in columns:
        c = clean_col_name(col)
        if "sa2" in c and "code" in c:
            return col

    for col in columns:
        c = clean_col_name(col)
        if "statistical_area_level_2" in c and "code" in c:
            return col

    return None


def identify_sa2_name_column(columns: list[str]) -> str | None:
    for col in columns:
        c = clean_col_name(col)

        if c in {"sa2_name_2021", "sa2_name", "sa2_2021_name"}:
            return col

    for col in columns:
        c = clean_col_name(col)
        if "sa2" in c and "name" in c:
            return col

    for col in columns:
        c = clean_col_name(col)
        if "statistical_area_level_2" in c and "name" in c:
            return col

    return None


def standardise_candidate_columns(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()

    original_columns = list(out.columns)
    sa2_code_col = identify_sa2_code_column(original_columns)
    sa2_name_col = identify_sa2_name_column(original_columns)

    new_cols = []
    used = {}

    for col in original_columns:
        if col == sa2_code_col:
            base = "sa2_code_2021"
        elif col == sa2_name_col:
            base = "sa2_name_2021"
        else:
            base = clean_col_name(col)

            if not base or base.startswith("unnamed"):
                base = "unnamed"

        if base in used:
            used[base] += 1
            base = f"{base}_{used[base]}"
        else:
            used[base] = 1

        new_cols.append(base)

    out.columns = new_cols

    return out


def score_candidate(df: pd.DataFrame, filename: str, sheet_name: str, header_row: int, spine_codes: set[str]) -> dict:
    std = standardise_candidate_columns(df)
    cols = set(std.columns)

    score = 0
    reasons = []

    if "sa2_code_2021" in cols:
        score += 40
        reasons.append("has_sa2_code")

        codes = (
            std["sa2_code_2021"]
            .map(normalise_code)
            .astype("string")
        )

        matched_spine = int(codes.isin(spine_codes).sum())
        sa2_like = int(codes.str.fullmatch(r"\d{8,12}", na=False).sum())

        if matched_spine > 2000:
            score += 80
            reasons.append(f"matched_spine_{matched_spine}")
        elif matched_spine > 1000:
            score += 40
            reasons.append(f"matched_spine_{matched_spine}")

        if sa2_like > 2000:
            score += 30
            reasons.append(f"sa2_like_{sa2_like}")

    else:
        matched_spine = 0
        sa2_like = 0

    if "sa2_name_2021" in cols:
        score += 10
        reasons.append("has_sa2_name")

    useful_cols = [
        col for col in std.columns
        if col not in {"sa2_code_2021", "sa2_name_2021"}
        and not col.startswith("unnamed")
    ]

    score += min(len(useful_cols), 50)
    reasons.append(f"useful_cols_{len(useful_cols)}")

    if "modelled" in filename.lower():
        score += 10
        reasons.append("filename_modelled")

    if "sa2" in filename.lower():
        score += 10
        reasons.append("filename_sa2")

    return {
        "filename": filename,
        "sheet_name": sheet_name,
        "header_row": header_row,
        "row_count": len(df),
        "column_count": len(df.columns),
        "score": score,
        "matched_spine_count": matched_spine,
        "sa2_like_count": sa2_like,
        "reasons": "; ".join(reasons),
        "standardised_columns": " | ".join(std.columns),
    }


def find_best_table(path: Path, source_slug: str, spine_codes: set[str]) -> tuple[pd.DataFrame, pd.DataFrame]:
    xls = pd.ExcelFile(path)

    candidate_rows = []
    loaded = []

    for sheet_name in xls.sheet_names:
        for header_row in range(0, 20):
            try:
                df = pd.read_excel(
                    path,
                    sheet_name=sheet_name,
                    dtype=str,
                    header=header_row,
                )
            except Exception:
                continue

            if len(df.columns) < 2:
                continue

            score_row = score_candidate(
                df=df,
                filename=path.name,
                sheet_name=sheet_name,
                header_row=header_row,
                spine_codes=spine_codes,
            )

            candidate_rows.append(score_row)
            loaded.append((score_row["score"], sheet_name, header_row, df, score_row))

    if not loaded:
        raise RuntimeError(f"No readable tables found in {path}")

    best_score, best_sheet, best_header, best_df, best_row = sorted(
        loaded,
        key=lambda item: item[0],
        reverse=True,
    )[0]

    candidate_audit = pd.DataFrame(candidate_rows).sort_values(
        by=["score", "filename", "sheet_name", "header_row"],
        ascending=[False, True, True, True],
    )

    if best_score < 120:
        raise RuntimeError(
            f"No strong SA2 table found in {path.name}. "
            f"Best score={best_score}, sheet={best_sheet}, header={best_header}."
        )

    out = standardise_candidate_columns(best_df)

    out["sa2_code_2021"] = out["sa2_code_2021"].map(normalise_code).astype("string")

    if "sa2_name_2021" in out.columns:
        out["sa2_name_2021"] = out["sa2_name_2021"].astype("string").str.strip()

    out = out[out["sa2_code_2021"].isin(spine_codes)].copy()

    # Drop fully empty columns.
    empty_cols = [
        col for col in out.columns
        if out[col].isna().all()
    ]
    out = out.drop(columns=empty_cols)

    out["nsmhw_source_slug"] = source_slug
    out["nsmhw_source_file"] = path.name
    out["nsmhw_source_sheet"] = best_sheet
    out["nsmhw_source_header_row"] = best_header

    return out, candidate_audit


def prefix_source_columns(df: pd.DataFrame, source_slug: str) -> pd.DataFrame:
    out = df.copy()

    id_cols = {
        "sa2_code_2021",
        "sa2_name_2021",
        "nsmhw_source_slug",
        "nsmhw_source_file",
        "nsmhw_source_sheet",
        "nsmhw_source_header_row",
    }

    rename_map = {}

    for col in out.columns:
        if col in id_cols:
            continue

        rename_map[col] = f"nsmhw__{source_slug}__{col}"

    out = out.rename(columns=rename_map)

    return out


def coerce_values(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()

    for col in out.columns:
        if col in {"sa2_code_2021", "sa2_name_2021"}:
            continue

        if col.startswith("nsmhw_source_"):
            continue

        original = out[col]
        numeric = pd.to_numeric(original, errors="coerce")

        non_missing_original = int(original.notna().sum())
        non_missing_numeric = int(numeric.notna().sum())

        if non_missing_original > 0 and non_missing_numeric / non_missing_original >= 0.8:
            out[col] = numeric
        else:
            out[col] = original.astype("string")

    return out


def build_outputs() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    spine = load_spine()
    spine_codes = set(spine["sa2_code_2021"].astype(str))

    manifest = load_manifest()

    wide = spine.copy()
    long_parts = []
    candidate_audits = []
    processing_rows = []
    column_rows = []

    for _, item in manifest.iterrows():
        path = Path(item["downloaded_path"])
        source_slug = str(item["source_slug"])

        if not path.exists():
            raise FileNotFoundError(f"Downloaded file missing: {path}")

        print(f"Processing: {path.name}")

        source_df, source_candidates = find_best_table(path, source_slug, spine_codes)
        source_candidates["source_slug"] = source_slug
        candidate_audits.append(source_candidates)

        source_df = coerce_values(source_df)

        row_count = len(source_df)
        unique_sa2 = source_df["sa2_code_2021"].nunique(dropna=True)
        duplicate_sa2 = int(source_df["sa2_code_2021"].duplicated().sum())

        processing_rows.append(
            {
                "source_slug": source_slug,
                "filename": path.name,
                "row_count": row_count,
                "unique_sa2_count": unique_sa2,
                "duplicate_sa2_rows": duplicate_sa2,
                "status": "pass" if duplicate_sa2 == 0 and unique_sa2 > 2000 else "review",
                "notes": "Source workbook parsed and filtered to SA2 spine rows.",
            }
        )

        long_parts.append(source_df.copy())

        prefixed = prefix_source_columns(source_df, source_slug)

        # Keep source metadata out of wide table except through the audit.
        prefixed = prefixed.drop(
            columns=[
                "sa2_name_2021",
                "nsmhw_source_slug",
                "nsmhw_source_file",
                "nsmhw_source_sheet",
                "nsmhw_source_header_row",
            ],
            errors="ignore",
        )

        if duplicate_sa2 > 0:
            raise RuntimeError(
                f"{path.name} produced duplicate SA2 rows. "
                "This source needs long-to-wide reshaping rules before merging."
            )

        wide = wide.merge(
            prefixed,
            on="sa2_code_2021",
            how="left",
            validate="one_to_one",
        )

        for col in prefixed.columns:
            if col == "sa2_code_2021":
                continue

            column_rows.append(
                {
                    "source_slug": source_slug,
                    "source_file": path.name,
                    "wide_column_name": col,
                    "field_role_initial": "nsmhw_modelled_estimate_unclassified",
                    "modelling_use_initial": "manual_review_required",
                    "notes": (
                        "Preserved from ABS NSMHW SA2 modelled estimate workbook. "
                        "Classify later as primary outcome, sensitivity outcome, related outcome, "
                        "target-leakage exclusion, or descriptive context."
                    ),
                }
            )

    long_df = pd.concat(long_parts, ignore_index=True, sort=False)
    candidate_audit = pd.concat(candidate_audits, ignore_index=True, sort=False)
    processing_audit = pd.DataFrame(processing_rows)
    column_dictionary = pd.DataFrame(column_rows)

    return wide, long_df, processing_audit, candidate_audit, column_dictionary


def main() -> None:
    PROCESSED_SOURCE_DIR.mkdir(parents=True, exist_ok=True)
    AUDIT_DIR.mkdir(parents=True, exist_ok=True)

    wide, long_df, processing_audit, candidate_audit, column_dictionary = build_outputs()

    wide.to_csv(WIDE_CSV, index=False, encoding="utf-8-sig")
    long_df.to_csv(LONG_CSV, index=False, encoding="utf-8-sig")

    wide_parquet_status = "written"
    long_parquet_status = "written"

    try:
        wide.to_parquet(WIDE_PARQUET, index=False)
    except Exception as exc:
        wide_parquet_status = f"not written: {exc}"

    try:
        long_df.to_parquet(LONG_PARQUET, index=False)
    except Exception as exc:
        long_parquet_status = f"not written: {exc}"

    processing_audit.to_csv(PROCESSING_AUDIT, index=False, encoding="utf-8-sig")
    candidate_audit.to_csv(SOURCE_CANDIDATE_AUDIT, index=False, encoding="utf-8-sig")
    column_dictionary.to_csv(COLUMN_DICTIONARY, index=False, encoding="utf-8-sig")

    print("\nCreated NSMHW processed source tables:")
    print(f"  {WIDE_CSV}")
    print(f"  {WIDE_PARQUET} ({wide_parquet_status})")
    print(f"  {LONG_CSV}")
    print(f"  {LONG_PARQUET} ({long_parquet_status})")

    print("\nCreated audits:")
    print(f"  {PROCESSING_AUDIT}")
    print(f"  {SOURCE_CANDIDATE_AUDIT}")
    print(f"  {COLUMN_DICTIONARY}")

    print("\nWide table shape:")
    print(f"  rows: {len(wide)}")
    print(f"  columns: {len(wide.columns)}")

    print("\nProcessing audit:")
    print(processing_audit.to_string(index=False))

    failed = processing_audit.query("status == 'fail'")
    if not failed.empty:
        raise RuntimeError(f"NSMHW processing failed. Review {PROCESSING_AUDIT}")

    print("\nNSMHW SA2 modelled estimate source table created with all parsed variables preserved.")


if __name__ == "__main__":
    main()
