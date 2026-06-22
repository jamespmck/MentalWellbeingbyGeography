from pathlib import Path
import re
import zipfile
import pandas as pd

PROJECT_ROOT = Path(r"D:\Good Measure\MentalWellbeingbyGeography")

RAW_GEO_DIR = PROJECT_ROOT / "data" / "raw" / "abs" / "geography"
PROCESSED_SPINE_DIR = PROJECT_ROOT / "data" / "processed" / "spines"
AUDIT_DIR = PROJECT_ROOT / "outputs" / "audits"

OUTPUT_CSV = PROCESSED_SPINE_DIR / "sa2_2021_spine.csv"
OUTPUT_PARQUET = PROCESSED_SPINE_DIR / "sa2_2021_spine.parquet"
AUDIT_OUTPUT = AUDIT_DIR / "sa2_sa3_spine_build_audit.csv"
SOURCE_CANDIDATE_AUDIT = AUDIT_DIR / "sa2_sa3_source_candidate_audit.csv"

REQUIRED_OUTPUT_COLUMNS = [
    "sa2_code_2021",
    "sa2_name_2021",
    "sa3_code_2021",
    "sa3_name_2021",
    "sa4_code_2021",
    "sa4_name_2021",
    "state_code_2021",
    "state_name_2021",
]


def clean_col_name(col: str) -> str:
    """Normalise source column names."""
    col = str(col).strip().lower()
    col = re.sub(r"[^a-z0-9]+", "_", col)
    col = re.sub(r"_+", "_", col)
    return col.strip("_")


def normalise_code(value) -> str | pd.NA:
    """Normalise geography codes while preserving leading zeroes if present."""
    if pd.isna(value):
        return pd.NA

    value = str(value).strip()

    if value == "" or value.lower() in {"nan", "none", "null"}:
        return pd.NA

    # Excel sometimes reads codes as floats, e.g. 101021007.0
    if re.fullmatch(r"\d+\.0", value):
        value = value[:-2]

    return value


def list_candidate_files(raw_dir: Path) -> list[Path]:
    """Find downloaded geography files, including files inside ZIPs."""
    if not raw_dir.exists():
        raise FileNotFoundError(f"Raw geography folder not found: {raw_dir}")

    candidates = []

    for pattern in ["*.csv", "*.xlsx", "*.xls", "*.zip"]:
        candidates.extend(raw_dir.glob(pattern))

    if not candidates:
        raise FileNotFoundError(
            f"No CSV, XLSX, XLS or ZIP files found in {raw_dir}"
        )

    return sorted(candidates)


def extract_zip_if_needed(path: Path) -> list[Path]:
    """Extract ZIP files and return contained CSV/XLSX/XLS files."""
    if path.suffix.lower() != ".zip":
        return [path]

    extract_dir = path.parent / path.stem
    extract_dir.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(path, "r") as zf:
        zf.extractall(extract_dir)

    extracted = []
    for pattern in ["**/*.csv", "**/*.xlsx", "**/*.xls"]:
        extracted.extend(extract_dir.glob(pattern))

    return sorted(extracted)


def read_table(path: Path) -> pd.DataFrame:
    """Read a table from CSV/XLSX/XLS."""
    suffix = path.suffix.lower()

    if suffix == ".csv":
        return pd.read_csv(path, dtype=str, low_memory=False)

    if suffix in {".xlsx", ".xls"}:
        return pd.read_excel(path, dtype=str)

    raise ValueError(f"Unsupported extracted file type: {path}")


def standardise_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Rename common ABS ASGS columns into project-standard names."""
    out = df.copy()
    out.columns = [clean_col_name(c) for c in out.columns]

    rename_candidates = {
        "sa2_code_2021": [
            "sa2_code_2021",
            "sa2_maincode_2021",
            "sa2_main_code_2021",
            "sa2_code",
            "sa2_maincode",
            "sa2_main_code",
            "sa2_2021_code",
            "sa2_2021_maincode",
        ],
        "sa2_name_2021": [
            "sa2_name_2021",
            "sa2_name",
            "sa2_2021_name",
        ],
        "sa3_code_2021": [
            "sa3_code_2021",
            "sa3_code",
            "sa3_2021_code",
        ],
        "sa3_name_2021": [
            "sa3_name_2021",
            "sa3_name",
            "sa3_2021_name",
        ],
        "sa4_code_2021": [
            "sa4_code_2021",
            "sa4_code",
            "sa4_2021_code",
        ],
        "sa4_name_2021": [
            "sa4_name_2021",
            "sa4_name",
            "sa4_2021_name",
        ],
        "state_code_2021": [
            "state_code_2021",
            "state_code",
            "ste_code_2021",
            "ste_code",
            "ste_2021_code",
        ],
        "state_name_2021": [
            "state_name_2021",
            "state_name",
            "ste_name_2021",
            "ste_name",
            "ste_2021_name",
        ],
        "area_albers_sqkm": [
            "area_albers_sqkm",
            "areasqkm",
            "area_sqkm",
            "area_albers_sq_km",
            "albers_area_sqkm",
        ],
    }

    rename_map = {}

    for standard_name, possible_names in rename_candidates.items():
        for possible_name in possible_names:
            if possible_name in out.columns:
                rename_map[possible_name] = standard_name
                break

    return out.rename(columns=rename_map)


def score_candidate(path: Path, df: pd.DataFrame) -> dict:
    """Score candidate files based on whether they contain SA2-SA3 hierarchy fields."""
    standardised = standardise_columns(df)
    cols = set(standardised.columns)
    name = path.name.lower()

    score = 0
    reasons = []

    if "sa2_code_2021" in cols:
        score += 10
        reasons.append("has_sa2_code")
    if "sa3_code_2021" in cols:
        score += 10
        reasons.append("has_sa3_code")
    if "sa2_name_2021" in cols:
        score += 3
        reasons.append("has_sa2_name")
    if "sa3_name_2021" in cols:
        score += 3
        reasons.append("has_sa3_name")
    if "sa4_code_2021" in cols:
        score += 2
        reasons.append("has_sa4_code")
    if "state_code_2021" in cols:
        score += 2
        reasons.append("has_state_code")

    if "allocation" in name:
        score += 4
        reasons.append("filename_allocation")
    if "main" in name and "structure" in name:
        score += 4
        reasons.append("filename_main_structure")
    if "sa2" in name:
        score += 2
        reasons.append("filename_sa2")
    if "sa3" in name:
        score += 2
        reasons.append("filename_sa3")
    if "2021" in name:
        score += 2
        reasons.append("filename_2021")

    return {
        "file_path": str(path),
        "file_name": path.name,
        "row_count": len(df),
        "column_count": len(df.columns),
        "score": score,
        "reasons": "; ".join(reasons),
        "columns": "; ".join(df.columns.astype(str)),
    }


def find_best_source() -> tuple[Path, pd.DataFrame, pd.DataFrame]:
    """Find the best downloaded ABS file to build the SA2-SA3 spine."""
    raw_candidates = list_candidate_files(RAW_GEO_DIR)

    scored_rows = []
    loaded_candidates = []

    for raw_path in raw_candidates:
        extracted_paths = extract_zip_if_needed(raw_path)

        for candidate_path in extracted_paths:
            if candidate_path.suffix.lower() not in {".csv", ".xlsx", ".xls"}:
                continue

            try:
                df = read_table(candidate_path)
                score_row = score_candidate(candidate_path, df)
                scored_rows.append(score_row)
                loaded_candidates.append((score_row["score"], candidate_path, df))
            except Exception as exc:
                scored_rows.append(
                    {
                        "file_path": str(candidate_path),
                        "file_name": candidate_path.name,
                        "row_count": "",
                        "column_count": "",
                        "score": -1,
                        "reasons": f"read_error: {exc}",
                        "columns": "",
                    }
                )

    if not loaded_candidates:
        raise RuntimeError("No readable CSV/XLSX/XLS geography files found.")

    candidate_audit = pd.DataFrame(scored_rows).sort_values(
        by=["score", "file_name"],
        ascending=[False, True],
    )

    best_score, best_path, best_df = sorted(
        loaded_candidates,
        key=lambda item: item[0],
        reverse=True,
    )[0]

    if best_score < 20:
        raise RuntimeError(
            "No strong SA2-SA3 source file found. "
            f"Best file was {best_path} with score {best_score}. "
            f"Review {SOURCE_CANDIDATE_AUDIT} after it is written."
        )

    return best_path, best_df, candidate_audit


def build_sa2_sa3_spine(source_df: pd.DataFrame) -> pd.DataFrame:
    """Build one-row-per-SA2 spine with SA3 fields."""
    df = standardise_columns(source_df)

    required = ["sa2_code_2021", "sa3_code_2021"]
    missing = [col for col in required if col not in df.columns]

    if missing:
        raise ValueError(
            f"Required columns missing after standardisation: {missing}"
        )

    available_cols = [
        col for col in REQUIRED_OUTPUT_COLUMNS + ["area_albers_sqkm"]
        if col in df.columns
    ]

    spine = df[available_cols].copy()

    for col in spine.columns:
        spine[col] = spine[col].map(normalise_code).astype("string")

    spine = spine.drop_duplicates()

    # Remove non-SA2 aggregate rows if any slipped in.
    # 2021 SA2 codes are usually 9 digits. This filter is conservative:
    # keep only non-missing numeric SA2 codes with length >= 8.
    spine = spine[
        spine["sa2_code_2021"].notna()
        & spine["sa2_code_2021"].str.fullmatch(r"\d{8,12}", na=False)
    ].copy()

    return spine


def validate_spine(spine: pd.DataFrame, source_path: Path) -> pd.DataFrame:
    """Create build audit."""
    audit_rows = []

    n_rows = len(spine)
    n_sa2 = spine["sa2_code_2021"].nunique(dropna=True)
    n_sa3 = spine["sa3_code_2021"].nunique(dropna=True)

    missing_sa2 = int(spine["sa2_code_2021"].isna().sum())
    missing_sa3 = int(spine["sa3_code_2021"].isna().sum())

    sa2_to_sa3 = (
        spine
        .dropna(subset=["sa2_code_2021"])
        .groupby("sa2_code_2021")["sa3_code_2021"]
        .nunique(dropna=True)
        .reset_index(name="n_sa3")
    )

    duplicate_sa2_rows = int(spine["sa2_code_2021"].duplicated().sum())
    multi_sa3_count = int((sa2_to_sa3["n_sa3"] > 1).sum())

    audit_rows.extend(
        [
            {
                "check_name": "source_file",
                "value": str(source_path),
                "status": "info",
                "notes": "Selected source file.",
            },
            {
                "check_name": "row_count",
                "value": n_rows,
                "status": "info",
                "notes": "Rows in output spine.",
            },
            {
                "check_name": "unique_sa2_count",
                "value": n_sa2,
                "status": "info",
                "notes": "Unique SA2 codes in output spine.",
            },
            {
                "check_name": "unique_sa3_count",
                "value": n_sa3,
                "status": "info",
                "notes": "Unique SA3 codes in output spine.",
            },
            {
                "check_name": "missing_sa2_code",
                "value": missing_sa2,
                "status": "pass" if missing_sa2 == 0 else "fail",
                "notes": "SA2 code must not be missing.",
            },
            {
                "check_name": "missing_sa3_code",
                "value": missing_sa3,
                "status": "pass" if missing_sa3 == 0 else "fail",
                "notes": "SA3 code must not be missing.",
            },
            {
                "check_name": "duplicate_sa2_rows",
                "value": duplicate_sa2_rows,
                "status": "pass" if duplicate_sa2_rows == 0 else "fail",
                "notes": "Output should have one row per SA2.",
            },
            {
                "check_name": "sa2_maps_to_single_sa3",
                "value": multi_sa3_count,
                "status": "pass" if multi_sa3_count == 0 else "fail",
                "notes": "Each SA2 should map to one SA3 in the 2021 ASGS hierarchy.",
            },
        ]
    )

    return pd.DataFrame(audit_rows)


def main() -> None:
    PROCESSED_SPINE_DIR.mkdir(parents=True, exist_ok=True)
    AUDIT_DIR.mkdir(parents=True, exist_ok=True)

    source_path, source_df, candidate_audit = find_best_source()

    candidate_audit.to_csv(
        SOURCE_CANDIDATE_AUDIT,
        index=False,
        encoding="utf-8-sig",
    )

    print(f"Selected SA2-SA3 source file:")
    print(source_path)

    spine = build_sa2_sa3_spine(source_df)
    audit = validate_spine(spine, source_path)

    failed_checks = audit.query("status == 'fail'")

    spine.to_csv(OUTPUT_CSV, index=False, encoding="utf-8-sig")

    try:
        spine.to_parquet(OUTPUT_PARQUET, index=False)
        parquet_status = "written"
    except Exception as exc:
        parquet_status = f"not written: {exc}"

    audit.to_csv(AUDIT_OUTPUT, index=False, encoding="utf-8-sig")

    print("\nCreated:")
    print(f"  {OUTPUT_CSV}")
    print(f"  {OUTPUT_PARQUET} ({parquet_status})")
    print(f"  {AUDIT_OUTPUT}")
    print(f"  {SOURCE_CANDIDATE_AUDIT}")

    print("\nAudit:")
    print(audit.to_string(index=False))

    if not failed_checks.empty:
        raise RuntimeError(
            "SA2-SA3 spine build completed but validation failed. "
            f"Review {AUDIT_OUTPUT}"
        )

    print("\nSA2-SA3 spine validated.")


if __name__ == "__main__":
    main()
