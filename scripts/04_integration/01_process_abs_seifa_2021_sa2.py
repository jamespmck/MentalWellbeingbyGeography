from pathlib import Path
from urllib.parse import quote
import re
import requests
import pandas as pd

PROJECT_ROOT = Path(r"D:\Good Measure\MentalWellbeingbyGeography")

RAW_SEIFA_DIR = PROJECT_ROOT / "data" / "raw" / "abs" / "seifa"
PROCESSED_SOURCE_DIR = PROJECT_ROOT / "data" / "processed" / "sources"
AUDIT_DIR = PROJECT_ROOT / "outputs" / "audits"

SPINE_PATH = PROJECT_ROOT / "data" / "processed" / "spines" / "sa2_2021_spine.parquet"

TARGET_SEIFA_FILE = RAW_SEIFA_DIR / "Statistical_Area_Level_2_Indexes_SEIFA_2021.xlsx"

OUTPUT_CSV = PROCESSED_SOURCE_DIR / "sa2_seifa_2021.csv"
OUTPUT_PARQUET = PROCESSED_SOURCE_DIR / "sa2_seifa_2021.parquet"

AUDIT_OUTPUT = AUDIT_DIR / "sa2_seifa_2021_processing_audit.csv"
SOURCE_CANDIDATE_AUDIT = AUDIT_DIR / "sa2_seifa_2021_source_candidate_audit.csv"
COLUMN_DIAGNOSTIC_OUTPUT = AUDIT_DIR / "sa2_seifa_2021_column_diagnostic.csv"

SEIFA_URL = (
    "https://www.abs.gov.au/statistics/people/people-and-communities/"
    "socio-economic-indexes-areas-seifa-australia/2021/"
    + quote("Statistical Area Level 2, Indexes, SEIFA 2021.xlsx")
)

INDEX_SHEETS = {
    "irsd": "Table 2",
    "irsad": "Table 3",
    "ier": "Table 4",
    "ieo": "Table 5",
}

INDEX_LABELS = {
    "irsd": "Index of Relative Socio-economic Disadvantage",
    "irsad": "Index of Relative Socio-economic Advantage and Disadvantage",
    "ier": "Index of Economic Resources",
    "ieo": "Index of Education and Occupation",
}


def clean_text(value) -> str:
    if pd.isna(value):
        return ""
    text = str(value).strip()
    text = re.sub(r"\s+", " ", text)
    return text


def clean_col_name(value) -> str:
    value = clean_text(value).lower()
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


def ensure_target_file() -> None:
    RAW_SEIFA_DIR.mkdir(parents=True, exist_ok=True)

    if TARGET_SEIFA_FILE.exists() and TARGET_SEIFA_FILE.stat().st_size > 10_000:
        print(f"Target SA2 SEIFA workbook exists: {TARGET_SEIFA_FILE}")
        return

    print("Downloading ABS SA2 SEIFA workbook:")
    print(SEIFA_URL)

    response = requests.get(
        SEIFA_URL,
        timeout=180,
        headers={"User-Agent": "Mozilla/5.0"},
    )
    response.raise_for_status()

    TARGET_SEIFA_FILE.write_bytes(response.content)

    print(f"Downloaded: {TARGET_SEIFA_FILE}")
    print(f"Size: {TARGET_SEIFA_FILE.stat().st_size:,} bytes")


def load_spine() -> pd.DataFrame:
    if not SPINE_PATH.exists():
        raise FileNotFoundError(f"SA2 spine not found: {SPINE_PATH}")

    spine = pd.read_parquet(SPINE_PATH)

    required = ["sa2_code_2021", "sa2_name_2021"]

    missing = [col for col in required if col not in spine.columns]
    if missing:
        raise ValueError(f"SA2 spine missing required columns: {missing}")

    spine = spine[required].copy()
    spine["sa2_code_2021"] = spine["sa2_code_2021"].map(normalise_code).astype("string")
    spine["sa2_name_2021"] = spine["sa2_name_2021"].astype("string").str.strip()

    if spine["sa2_code_2021"].duplicated().any():
        raise ValueError("SA2 spine contains duplicate sa2_code_2021 values.")

    return spine


def find_sheet_name(xls: pd.ExcelFile, wanted: str) -> str:
    wanted_clean = clean_col_name(wanted)

    for sheet in xls.sheet_names:
        if clean_col_name(sheet) == wanted_clean:
            return sheet

    for sheet in xls.sheet_names:
        if wanted_clean in clean_col_name(sheet):
            return sheet

    raise ValueError(f"Could not find sheet matching {wanted}. Available sheets: {xls.sheet_names}")


def build_combined_headers(raw: pd.DataFrame, header_end_row: int, window: int = 5) -> list[str]:
    start = max(0, header_end_row - window + 1)
    block = raw.iloc[start:header_end_row + 1, :].copy()

    # Merged Excel cells often appear once then blank. Forward fill horizontally.
    block = block.ffill(axis=1)

    headers = []

    for col_idx in range(block.shape[1]):
        parts = []

        for row_idx in range(block.shape[0]):
            text = clean_text(block.iat[row_idx, col_idx])

            if not text:
                continue

            if text.lower() in {"nan", "none", "null"}:
                continue

            if not parts or parts[-1] != text:
                parts.append(text)

        headers.append(" | ".join(parts))

    return headers


def classify_header(header: str, index_key: str | None) -> str | None:
    h = clean_col_name(header)

    if ("sa2" in h or "statistical_area_level_2" in h) and "code" in h:
        return "sa2_code_2021"

    if ("sa2" in h or "statistical_area_level_2" in h) and "name" in h:
        return "sa2_name_2021"

    if index_key is None:
        return None

    measure = None

    if "score" in h:
        measure = "score"
    elif "rank" in h:
        if "state" in h or "within_state" in h:
            measure = "rank_state"
        else:
            measure = "rank_australia"
    elif "decile" in h:
        if "state" in h or "within_state" in h:
            measure = "decile_state"
        else:
            measure = "decile_australia"
    elif "percentile" in h:
        if "state" in h or "within_state" in h:
            measure = "percentile_state"
        else:
            measure = "percentile_australia"

    if measure is None:
        return None

    return f"seifa_{index_key}_{measure}"


def parse_candidate(raw: pd.DataFrame, header_end_row: int, index_key: str | None) -> tuple[pd.DataFrame, dict]:
    headers = build_combined_headers(raw, header_end_row)

    standard_names = []
    used = {}

    for header in headers:
        standard = classify_header(header, index_key)

        if standard is None:
            standard = clean_col_name(header) or "unnamed"

        if standard in used:
            used[standard] += 1
            standard = f"{standard}_{used[standard]}"
        else:
            used[standard] = 1

        standard_names.append(standard)

    data = raw.iloc[header_end_row + 1:, :].copy()
    data.columns = standard_names

    keep_cols = [
        col for col in data.columns
        if col in {"sa2_code_2021", "sa2_name_2021"}
        or col.startswith("seifa_")
    ]

    parsed = data[keep_cols].copy() if keep_cols else pd.DataFrame()

    info = {
        "header_end_row": header_end_row,
        "kept_columns": " | ".join(keep_cols),
        "standard_names": " | ".join(standard_names),
        "combined_headers": " || ".join(headers),
    }

    return parsed, info


def score_candidate(parsed: pd.DataFrame, file_name: str, sheet_name: str, index_key: str | None) -> tuple[int, str]:
    if parsed.empty:
        return 0, "empty_or_no_kept_columns"

    cols = set(parsed.columns)
    score = 0
    reasons = []

    if "sa2_code_2021" in cols:
        score += 40
        reasons.append("has_sa2_code")

    if "sa2_name_2021" in cols:
        score += 10
        reasons.append("has_sa2_name")

    if index_key is not None:
        expected_score_col = f"seifa_{index_key}_score"
        expected_decile_col = f"seifa_{index_key}_decile_australia"

        if expected_score_col in cols:
            score += 40
            reasons.append(f"has_{expected_score_col}")

        if expected_decile_col in cols:
            score += 30
            reasons.append(f"has_{expected_decile_col}")

        seifa_cols = [col for col in cols if col.startswith(f"seifa_{index_key}_")]
        if seifa_cols:
            score += len(seifa_cols) * 10
            reasons.append(f"has_{len(seifa_cols)}_{index_key}_columns")

    if "sa2_code_2021" in parsed.columns:
        sa2_like = (
            parsed["sa2_code_2021"]
            .map(normalise_code)
            .astype("string")
            .str.fullmatch(r"\d{8,12}", na=False)
            .sum()
        )

        if sa2_like > 2000:
            score += 50
            reasons.append(f"many_sa2_rows_{sa2_like}")
        elif sa2_like > 1000:
            score += 25
            reasons.append(f"some_sa2_rows_{sa2_like}")

    if "statistical_area_level_2" in file_name.lower() or "sa2" in file_name.lower():
        score += 20
        reasons.append("file_is_sa2")

    if sheet_name.lower().startswith("table"):
        score += 5
        reasons.append("table_sheet")

    return score, "; ".join(reasons)


def parse_index_sheet(xls: pd.ExcelFile, index_key: str, table_name: str) -> tuple[pd.DataFrame, list[dict]]:
    sheet_name = find_sheet_name(xls, table_name)
    raw = pd.read_excel(TARGET_SEIFA_FILE, sheet_name=sheet_name, header=None, dtype=str)

    candidate_rows = []
    loaded = []

    for header_end_row in range(0, min(30, len(raw) - 1)):
        parsed, info = parse_candidate(raw, header_end_row, index_key)
        score, reasons = score_candidate(parsed, TARGET_SEIFA_FILE.name, sheet_name, index_key)

        row = {
            "index_key": index_key,
            "index_label": INDEX_LABELS[index_key],
            "sheet_name": sheet_name,
            "header_end_row": header_end_row,
            "score": score,
            "reasons": reasons,
            "row_count_parsed": len(parsed),
            "column_count_parsed": len(parsed.columns),
            "kept_columns": info["kept_columns"],
            "standard_names": info["standard_names"],
        }

        candidate_rows.append(row)
        loaded.append((score, header_end_row, parsed, row))

    best_score, best_header, best_parsed, best_row = sorted(
        loaded,
        key=lambda item: item[0],
        reverse=True,
    )[0]

    if best_score < 120:
        raise RuntimeError(
            f"No strong SEIFA table found for {index_key}. "
            f"Best score={best_score}, sheet={sheet_name}, header_end_row={best_header}. "
            f"Review {SOURCE_CANDIDATE_AUDIT}."
        )

    out = best_parsed.copy()

    out["sa2_code_2021"] = out["sa2_code_2021"].map(normalise_code).astype("string")

    out = out[
        out["sa2_code_2021"].notna()
        & out["sa2_code_2021"].str.fullmatch(r"\d{8,12}", na=False)
    ].copy()

    if "sa2_name_2021" in out.columns:
        out["sa2_name_2021"] = out["sa2_name_2021"].astype("string").str.strip()

    seifa_cols = [col for col in out.columns if col.startswith(f"seifa_{index_key}_")]

    for col in seifa_cols:
        out[col] = pd.to_numeric(out[col], errors="coerce")

    keep_cols = ["sa2_code_2021"]

    if "sa2_name_2021" in out.columns:
        keep_cols.append("sa2_name_2021")

    keep_cols.extend(sorted(seifa_cols))

    out = out[keep_cols].drop_duplicates(subset=["sa2_code_2021"], keep="first")

    return out, candidate_rows


def parse_exclusion_sheet(xls: pd.ExcelFile) -> pd.DataFrame:
    try:
        sheet_name = find_sheet_name(xls, "Table 6")
    except Exception:
        return pd.DataFrame(columns=["sa2_code_2021", "seifa_excluded_table6_flag"])

    raw = pd.read_excel(TARGET_SEIFA_FILE, sheet_name=sheet_name, header=None, dtype=str)

    loaded = []

    for header_end_row in range(0, min(30, len(raw) - 1)):
        parsed, _ = parse_candidate(raw, header_end_row, None)

        if "sa2_code_2021" not in parsed.columns:
            continue

        parsed = parsed.copy()
        parsed["sa2_code_2021"] = parsed["sa2_code_2021"].map(normalise_code).astype("string")
        parsed = parsed[
            parsed["sa2_code_2021"].notna()
            & parsed["sa2_code_2021"].str.fullmatch(r"\d{8,12}", na=False)
        ].copy()

        score = len(parsed)

        loaded.append((score, header_end_row, parsed))

    if not loaded:
        return pd.DataFrame(columns=["sa2_code_2021", "seifa_excluded_table6_flag"])

    _, _, exclusions = sorted(loaded, key=lambda item: item[0], reverse=True)[0]

    out = exclusions[["sa2_code_2021"]].copy()
    out["seifa_excluded_table6_flag"] = True
    out = out.drop_duplicates(subset=["sa2_code_2021"], keep="first")

    return out


def build_seifa_source() -> tuple[pd.DataFrame, pd.DataFrame]:
    ensure_target_file()

    spine = load_spine()

    xls = pd.ExcelFile(TARGET_SEIFA_FILE)

    all_candidate_rows = []
    output = spine.copy()

    for index_key, table_name in INDEX_SHEETS.items():
        index_df, candidate_rows = parse_index_sheet(xls, index_key, table_name)
        all_candidate_rows.extend(candidate_rows)

        drop_cols = [col for col in ["sa2_name_2021"] if col in index_df.columns]
        index_df = index_df.drop(columns=drop_cols)

        output = output.merge(
            index_df,
            on="sa2_code_2021",
            how="left",
            validate="one_to_one",
        )

        score_col = f"seifa_{index_key}_score"
        if score_col in output.columns:
            output[f"seifa_{index_key}_score_missing_flag"] = output[score_col].isna()

    exclusions = parse_exclusion_sheet(xls)

    if not exclusions.empty:
        output = output.merge(
            exclusions,
            on="sa2_code_2021",
            how="left",
            validate="one_to_one",
        )
        output["seifa_excluded_table6_flag"] = output["seifa_excluded_table6_flag"].fillna(False)
    else:
        output["seifa_excluded_table6_flag"] = pd.NA

    candidate_audit = pd.DataFrame(all_candidate_rows).sort_values(
        by=["index_key", "score"],
        ascending=[True, False],
    )

    return output, candidate_audit


def build_processing_audit(seifa: pd.DataFrame, candidate_audit: pd.DataFrame) -> pd.DataFrame:
    rows = []

    seifa_cols = [col for col in seifa.columns if col.startswith("seifa_")]
    score_cols = [col for col in seifa_cols if col.endswith("_score")]

    rows.extend(
        [
            {
                "check_name": "source_file",
                "value": str(TARGET_SEIFA_FILE),
                "status": "info",
                "notes": "ABS SA2 SEIFA workbook used.",
            },
            {
                "check_name": "row_count",
                "value": len(seifa),
                "status": "info",
                "notes": "Rows in processed SEIFA source table.",
            },
            {
                "check_name": "unique_sa2_count",
                "value": seifa["sa2_code_2021"].nunique(dropna=True),
                "status": "info",
                "notes": "Unique SA2 codes.",
            },
            {
                "check_name": "missing_sa2_code",
                "value": int(seifa["sa2_code_2021"].isna().sum()),
                "status": "pass" if int(seifa["sa2_code_2021"].isna().sum()) == 0 else "fail",
                "notes": "SA2 code must not be missing.",
            },
            {
                "check_name": "duplicate_sa2_rows",
                "value": int(seifa["sa2_code_2021"].duplicated().sum()),
                "status": "pass" if int(seifa["sa2_code_2021"].duplicated().sum()) == 0 else "fail",
                "notes": "SEIFA source table must remain one row per SA2.",
            },
            {
                "check_name": "seifa_column_count",
                "value": len(seifa_cols),
                "status": "pass" if len(seifa_cols) > 0 else "fail",
                "notes": "Recognised SEIFA columns.",
            },
        ]
    )

    for score_col in score_cols:
        index_key = score_col.replace("seifa_", "").replace("_score", "")
        non_missing = int(seifa[score_col].notna().sum())
        missing = int(seifa[score_col].isna().sum())

        rows.append(
            {
                "check_name": f"{index_key}_score_non_missing",
                "value": non_missing,
                "status": "info",
                "notes": f"Non-missing {index_key.upper()} scores.",
            }
        )

        rows.append(
            {
                "check_name": f"{index_key}_score_missing",
                "value": missing,
                "status": "info",
                "notes": f"Missing {index_key.upper()} scores; expected for excluded areas.",
            }
        )

    for index_key in INDEX_SHEETS:
        subset = candidate_audit[candidate_audit["index_key"] == index_key]
        if not subset.empty:
            best = subset.sort_values("score", ascending=False).iloc[0]
            rows.append(
                {
                    "check_name": f"{index_key}_selected_header_row",
                    "value": int(best["header_end_row"]),
                    "status": "info",
                    "notes": f"Selected {index_key.upper()} parse candidate from {best['sheet_name']}.",
                }
            )

    return pd.DataFrame(rows)


def main() -> None:
    PROCESSED_SOURCE_DIR.mkdir(parents=True, exist_ok=True)
    AUDIT_DIR.mkdir(parents=True, exist_ok=True)

    seifa, candidate_audit = build_seifa_source()
    processing_audit = build_processing_audit(seifa, candidate_audit)

    seifa.to_csv(OUTPUT_CSV, index=False, encoding="utf-8-sig")

    parquet_status = "written"
    try:
        seifa.to_parquet(OUTPUT_PARQUET, index=False)
    except Exception as exc:
        parquet_status = f"not written: {exc}"

    candidate_audit.to_csv(SOURCE_CANDIDATE_AUDIT, index=False, encoding="utf-8-sig")
    processing_audit.to_csv(AUDIT_OUTPUT, index=False, encoding="utf-8-sig")

    column_diag = pd.DataFrame(
        {
            "column_name": list(seifa.columns),
            "dtype": [str(seifa[col].dtype) for col in seifa.columns],
            "missing_count": [int(seifa[col].isna().sum()) for col in seifa.columns],
        }
    )
    column_diag.to_csv(COLUMN_DIAGNOSTIC_OUTPUT, index=False, encoding="utf-8-sig")

    print("Created SEIFA source table:")
    print(f"  {OUTPUT_CSV}")
    print(f"  {OUTPUT_PARQUET} ({parquet_status})")

    print("\nCreated audits:")
    print(f"  {AUDIT_OUTPUT}")
    print(f"  {SOURCE_CANDIDATE_AUDIT}")
    print(f"  {COLUMN_DIAGNOSTIC_OUTPUT}")

    print("\nProcessing audit:")
    print(processing_audit.to_string(index=False))

    failed = processing_audit.query("status == 'fail'")
    if not failed.empty:
        raise RuntimeError(f"SEIFA validation failed. Review {AUDIT_OUTPUT}")

    print("\nSA2 SEIFA 2021 source table created.")


if __name__ == "__main__":
    main()
