from __future__ import annotations

from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from datetime import datetime, timezone
import argparse
import re
import shutil
import sys
import time

import pandas as pd

PROJECT_ROOT = Path(r"D:\Good Measure\MentalWellbeingbyGeography")

RAW_DIR = PROJECT_ROOT / "data" / "raw" / "abs" / "correspondences" / "asgs_2016_to_2021_exact"
PROCESSED_DIR = PROJECT_ROOT / "data" / "processed" / "geography"
AUDIT_DIR = PROJECT_ROOT / "outputs" / "audits"
METHOD_DIR = PROJECT_ROOT / "docs" / "methodology"
NDIA_SELECTED_DIR = PROJECT_ROOT / "data" / "raw" / "ndia" / "public_poc_selected"

SA2_URL = "https://www.abs.gov.au/statistics/standards/australian-statistical-geography-standard-asgs/edition-3-july-2021-june-2026/access-and-downloads/correspondences/CG_SA2_2016_SA2_2021.csv"
SA3_URL = "https://www.abs.gov.au/statistics/standards/australian-statistical-geography-standard-asgs/edition-3-july-2021-june-2026/access-and-downloads/correspondences/CG_SA3_2016_SA3_2021.csv"

SA2_RAW = RAW_DIR / "CG_SA2_2016_SA2_2021.csv"
SA3_RAW = RAW_DIR / "CG_SA3_2016_SA3_2021.csv"

SA2_BRIDGE_CSV = PROCESSED_DIR / "bridge_sa2_2016_to_2021.csv"
SA2_BRIDGE_PARQUET = PROCESSED_DIR / "bridge_sa2_2016_to_2021.parquet"
SA3_BRIDGE_CSV = PROCESSED_DIR / "bridge_sa3_2016_to_2021.csv"
SA3_BRIDGE_PARQUET = PROCESSED_DIR / "bridge_sa3_2016_to_2021.parquet"

SOURCE_AUDIT_CSV = AUDIT_DIR / "abs_asgs_2016_2021_bridge_source_audit.csv"
BRIDGE_AUDIT_CSV = AUDIT_DIR / "abs_asgs_2016_2021_bridge_audit.csv"
NDIA_READINESS_CSV = AUDIT_DIR / "ndia_2016_code_bridge_readiness_audit.csv"
NDIA_UNMATCHED_SA2_CSV = AUDIT_DIR / "ndia_2016_code_unmatched_sa2.csv"
NDIA_UNMATCHED_SA3_CSV = AUDIT_DIR / "ndia_2016_code_unmatched_sa3.csv"
METHOD_NOTE_MD = METHOD_DIR / "ndia_public_poc_2016_to_2021_geography_bridge_note.md"

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) ASGS-exact-bridge/2.1"

EXPECTED = {
    "SA2": {
        "url": SA2_URL,
        "raw": SA2_RAW,
        "required_columns": [
            "SA2_MAINCODE_2016",
            "SA2_NAME_2016",
            "SA2_CODE_2021",
            "SA2_NAME_2021",
            "RATIO_FROM_TO",
            "INDIV_TO_REGION_QLTY_INDICATOR",
            "OVERALL_QUALITY_INDICATOR",
            "BMOS_NULL_FLAG",
        ],
        "rename": {
            "SA2_MAINCODE_2016": "sa2_code_2016",
            "SA2_NAME_2016": "sa2_name_2016",
            "SA2_CODE_2021": "sa2_code_2021",
            "SA2_NAME_2021": "sa2_name_2021",
            "RATIO_FROM_TO": "ratio_from_to",
            "INDIV_TO_REGION_QLTY_INDICATOR": "individual_to_region_quality_indicator",
            "OVERALL_QUALITY_INDICATOR": "overall_quality_indicator",
            "BMOS_NULL_FLAG": "bmos_null_flag",
        },
        "out_csv": SA2_BRIDGE_CSV,
        "out_parquet": SA2_BRIDGE_PARQUET,
        "from_col": "sa2_code_2016",
        "to_col": "sa2_code_2021",
        "ndia_pattern": "participants_by_sa2__*.csv",
        "ndia_code_column_candidates": ["SA2Cd2016", "SA2_CODE_2016", "SA2 Code 2016", "SA2_MAINCODE_2016"],
        "min_unique_from": 1800,
        "max_unique_from": 3500,
        "min_unique_to": 1800,
        "max_unique_to": 3500,
        "max_reasonable_rows": 20000,
    },
    "SA3": {
        "url": SA3_URL,
        "raw": SA3_RAW,
        "required_columns": [
            "SA3_CODE_2016",
            "SA3_NAME_2016",
            "SA3_CODE_2021",
            "SA3_NAME_2021",
            "RATIO_FROM_TO",
            "INDIV_TO_REGION_QLTY_INDICATOR",
            "OVERALL_QUALITY_INDICATOR",
            "BMOS_NULL_FLAG",
        ],
        "rename": {
            "SA3_CODE_2016": "sa3_code_2016",
            "SA3_NAME_2016": "sa3_name_2016",
            "SA3_CODE_2021": "sa3_code_2021",
            "SA3_NAME_2021": "sa3_name_2021",
            "RATIO_FROM_TO": "ratio_from_to",
            "INDIV_TO_REGION_QLTY_INDICATOR": "individual_to_region_quality_indicator",
            "OVERALL_QUALITY_INDICATOR": "overall_quality_indicator",
            "BMOS_NULL_FLAG": "bmos_null_flag",
        },
        "out_csv": SA3_BRIDGE_CSV,
        "out_parquet": SA3_BRIDGE_PARQUET,
        "from_col": "sa3_code_2016",
        "to_col": "sa3_code_2021",
        "ndia_pattern": "participants_by_sa3__*.csv",
        "ndia_code_column_candidates": ["SA3Cd2016", "SA3_CODE_2016", "SA3 Code 2016"],
        "min_unique_from": 250,
        "max_unique_from": 500,
        "min_unique_to": 250,
        "max_unique_to": 500,
        "max_reasonable_rows": 5000,
    },
}


def ensure_dirs() -> None:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    AUDIT_DIR.mkdir(parents=True, exist_ok=True)
    METHOD_DIR.mkdir(parents=True, exist_ok=True)


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def norm_code(value) -> object:
    if value is None or pd.isna(value):
        return pd.NA
    text = str(value).strip().replace("\ufeff", "")
    text = re.sub(r"\.0$", "", text)
    if text == "" or text.lower() in {"nan", "none", "null"}:
        return pd.NA
    return text


def download_file(url: str, dest: Path, force: bool = False, timeout: int = 90) -> dict:
    if dest.exists() and dest.stat().st_size > 0 and not force:
        return {
            "timestamp_utc": now_utc(),
            "url": url,
            "local_path": str(dest),
            "status": "already_available",
            "size_bytes": dest.stat().st_size,
            "error": "",
        }

    req = Request(url, headers={"User-Agent": USER_AGENT})

    try:
        with urlopen(req, timeout=timeout) as response:
            body = response.read()
        dest.write_bytes(body)
        return {
            "timestamp_utc": now_utc(),
            "url": url,
            "local_path": str(dest),
            "status": "downloaded",
            "size_bytes": len(body),
            "error": "",
        }
    except Exception as exc:
        return {
            "timestamp_utc": now_utc(),
            "url": url,
            "local_path": str(dest),
            "status": "download_failed",
            "size_bytes": 0,
            "error": str(exc),
        }


def find_local_exact(filename: str) -> Path | None:
    search_roots = [
        RAW_DIR,
        PROJECT_ROOT / "data" / "raw" / "abs",
        PROJECT_ROOT / "data" / "raw",
        PROJECT_ROOT / "data" / "external",
    ]
    for root in search_roots:
        if not root.exists():
            continue
        for path in root.rglob(filename):
            if path.exists() and path.stat().st_size > 0:
                return path
    return None


def read_csv_strict(path: Path) -> pd.DataFrame:
    # ABS correspondence CSVs are normally UTF-8-compatible.
    # dtype=str prevents geographic codes from being coerced to numbers.
    return pd.read_csv(path, dtype=str, low_memory=False)


def build_bridge(level: str, force_download: bool = False, timeout: int = 90) -> tuple[pd.DataFrame, list[dict]]:
    cfg = EXPECTED[level]
    source_audit = []

    row = download_file(cfg["url"], cfg["raw"], force=force_download, timeout=timeout)
    row["geography"] = level
    row["source_role"] = "official_abs_exact_correspondence"
    source_audit.append(row)

    raw_path = cfg["raw"]

    if not raw_path.exists() or raw_path.stat().st_size == 0:
        local = find_local_exact(raw_path.name)
        if local is None:
            raise FileNotFoundError(
                f"Could not download or find required official ABS file: {raw_path.name}. "
                f"Expected URL: {cfg['url']}"
            )
        shutil.copy2(local, raw_path)
        source_audit.append(
            {
                "timestamp_utc": now_utc(),
                "url": "",
                "local_path": str(local),
                "status": "copied_from_local_exact_match",
                "size_bytes": local.stat().st_size,
                "error": "",
                "geography": level,
                "source_role": "local_exact_filename_fallback",
            }
        )

    df = read_csv_strict(raw_path)
    df.columns = [str(c).strip().replace("\ufeff", "") for c in df.columns]

    missing_cols = [col for col in cfg["required_columns"] if col not in df.columns]
    if missing_cols:
        raise ValueError(
            f"{level} correspondence file is not the official expected {level} 2016→2021 file. "
            f"Missing columns: {missing_cols}. Found columns: {list(df.columns)}"
        )

    bridge = df[cfg["required_columns"]].rename(columns=cfg["rename"]).copy()

    from_col = cfg["from_col"]
    to_col = cfg["to_col"]

    bridge[from_col] = bridge[from_col].map(norm_code).astype("string")
    bridge[to_col] = bridge[to_col].map(norm_code).astype("string")
    bridge["ratio_from_to"] = pd.to_numeric(bridge["ratio_from_to"], errors="coerce")
    bridge["bmos_null_flag"] = bridge["bmos_null_flag"].map(norm_code).astype("string")

    bridge["source_file"] = raw_path.name
    bridge["source_url"] = cfg["url"]
    bridge["native_bridge_geography"] = level
    bridge["bridge_method"] = "official_abs_grid_based_correspondence"
    bridge["allocation_required_for_counts"] = bridge.groupby(from_col)[to_col].transform("nunique") > 1

    # Do not silently output a Mesh Block bridge under an SA2/SA3 name again.
    row_count = len(bridge)
    unique_from = int(bridge[from_col].nunique(dropna=True))
    unique_to = int(bridge[to_col].nunique(dropna=True))

    if row_count > cfg["max_reasonable_rows"]:
        raise ValueError(
            f"{level} bridge has {row_count:,} rows, which is far too many for an official {level} correspondence. "
            "This looks like a Mesh Block correspondence or another misclassified source. Refusing to write bridge."
        )

    if not (cfg["min_unique_from"] <= unique_from <= cfg["max_unique_from"]):
        raise ValueError(
            f"{level} bridge has {unique_from:,} unique 2016 codes, outside expected range "
            f"{cfg['min_unique_from']}–{cfg['max_unique_from']}. Refusing to write bridge."
        )

    if not (cfg["min_unique_to"] <= unique_to <= cfg["max_unique_to"]):
        raise ValueError(
            f"{level} bridge has {unique_to:,} unique 2021 codes, outside expected range "
            f"{cfg['min_unique_to']}–{cfg['max_unique_to']}. Refusing to write bridge."
        )

    bridge.to_csv(cfg["out_csv"], index=False, encoding="utf-8-sig")
    bridge.to_parquet(cfg["out_parquet"], index=False)

    return bridge, source_audit


def audit_bridge(level: str, bridge: pd.DataFrame) -> list[dict]:
    cfg = EXPECTED[level]
    from_col = cfg["from_col"]
    to_col = cfg["to_col"]

    rows = []

    def add(check_name: str, value, status: str, notes: str = ""):
        rows.append(
            {
                "geography": level,
                "check_name": check_name,
                "value": value,
                "status": status,
                "notes": notes,
            }
        )

    row_count = len(bridge)
    unique_from = int(bridge[from_col].nunique(dropna=True))
    unique_to = int(bridge[to_col].nunique(dropna=True))
    missing_from = int(bridge[from_col].isna().sum())
    missing_to = int(bridge[to_col].isna().sum())
    rows_with_ratio = int(bridge["ratio_from_to"].notna().sum())
    ratio_min = bridge["ratio_from_to"].min()
    ratio_max = bridge["ratio_from_to"].max()

    from_to_counts = bridge.groupby(from_col, dropna=True)[to_col].nunique(dropna=True)
    to_from_counts = bridge.groupby(to_col, dropna=True)[from_col].nunique(dropna=True)

    source_many_to_one = int((to_from_counts > 1).sum())
    source_one_to_many = int((from_to_counts > 1).sum())

    ratio_sums = (
        bridge.dropna(subset=[from_col])
        .groupby(from_col, dropna=True)["ratio_from_to"]
        .sum(min_count=1)
        .reset_index(name="ratio_sum_from_code")
    )
    ratio_sum_low = int((ratio_sums["ratio_sum_from_code"] < 0.98).sum())
    ratio_sum_high = int((ratio_sums["ratio_sum_from_code"] > 1.02).sum())

    add("bridge_available", 1, "pass")
    add("source_file", bridge["source_file"].iloc[0], "info")
    add("bridge_row_count", row_count, "pass")
    add("unique_2016_codes", unique_from, "pass")
    add("unique_2021_codes", unique_to, "pass")
    add("missing_2016_codes", missing_from, "pass" if missing_from == 0 else "review")
    add("missing_2021_codes", missing_to, "pass" if missing_to == 0 else "review")
    add("rows_with_ratio", rows_with_ratio, "pass" if rows_with_ratio == row_count else "review")
    add("ratio_min", ratio_min, "pass" if pd.notna(ratio_min) and ratio_min >= 0 else "review")
    add("ratio_max", ratio_max, "pass" if pd.notna(ratio_max) and ratio_max <= 1 else "review")
    add(
        "source_2016_codes_requiring_allocation",
        source_one_to_many,
        "review" if source_one_to_many else "pass",
        "Counts from 2016 geographies mapping to multiple 2021 geographies must be allocated using ratio_from_to.",
    )
    add(
        "target_2021_codes_receiving_multiple_2016_sources",
        source_many_to_one,
        "info",
        "Expected in correspondence files; target 2021 regions can receive population from multiple 2016 regions.",
    )
    add(
        "source_2016_ratio_sum_below_0_98",
        ratio_sum_low,
        "review" if ratio_sum_low else "pass",
        "Review if source ratios do not sum close to 1. Null/BMOS flags may explain small differences.",
    )
    add(
        "source_2016_ratio_sum_above_1_02",
        ratio_sum_high,
        "review" if ratio_sum_high else "pass",
        "Review if source ratios sum materially above 1.",
    )

    return rows


def find_ndia_file(level: str) -> Path | None:
    cfg = EXPECTED[level]
    if not NDIA_SELECTED_DIR.exists():
        return None
    files = sorted(NDIA_SELECTED_DIR.glob(cfg["ndia_pattern"]))
    if files:
        return files[0]
    # fallback: looser contains search
    token = "participants_by_sa2" if level == "SA2" else "participants_by_sa3"
    files = sorted([p for p in NDIA_SELECTED_DIR.glob("*.csv") if token in p.name.lower()])
    return files[0] if files else None


def find_column_case_insensitive(df: pd.DataFrame, candidates: list[str]) -> str | None:
    lower_map = {str(c).strip().lower(): c for c in df.columns}
    for cand in candidates:
        if cand.lower() in lower_map:
            return lower_map[cand.lower()]
    # looser fallback
    for col in df.columns:
        c = str(col).lower().replace("_", "")
        for cand in candidates:
            cc = cand.lower().replace("_", "")
            if cc == c:
                return col
    return None


def audit_ndia_readiness(level: str, bridge: pd.DataFrame) -> tuple[dict, pd.DataFrame]:
    cfg = EXPECTED[level]
    from_col = cfg["from_col"]
    to_col = cfg["to_col"]

    ndia_file = find_ndia_file(level)
    if ndia_file is None:
        return (
            {
                "geography": level,
                "ndia_file_found": False,
                "ndia_file_path": "",
                "ndia_row_count": 0,
                "ndia_column_count": 0,
                "ndia_code_column": "",
                "ndia_unique_2016_codes": 0,
                "bridge_found": True,
                "bridge_row_count": len(bridge),
                "bridge_unique_2016_codes": int(bridge[from_col].nunique(dropna=True)),
                "bridge_unique_2021_codes": int(bridge[to_col].nunique(dropna=True)),
                "matched_unique_2016_codes": 0,
                "unmatched_unique_2016_codes": 0,
                "coverage_pct": pd.NA,
                "ndia_2016_codes_requiring_allocation_to_multiple_2021_codes": 0,
                "status": "review",
                "notes": f"No staged NDIA {level} participant file found.",
            },
            pd.DataFrame(),
        )

    ndia = pd.read_csv(ndia_file, dtype=str, low_memory=False)
    code_col = find_column_case_insensitive(ndia, cfg["ndia_code_column_candidates"])

    if code_col is None:
        return (
            {
                "geography": level,
                "ndia_file_found": True,
                "ndia_file_path": str(ndia_file),
                "ndia_row_count": len(ndia),
                "ndia_column_count": len(ndia.columns),
                "ndia_code_column": "",
                "ndia_unique_2016_codes": 0,
                "bridge_found": True,
                "bridge_row_count": len(bridge),
                "bridge_unique_2016_codes": int(bridge[from_col].nunique(dropna=True)),
                "bridge_unique_2021_codes": int(bridge[to_col].nunique(dropna=True)),
                "matched_unique_2016_codes": 0,
                "unmatched_unique_2016_codes": 0,
                "coverage_pct": 0,
                "ndia_2016_codes_requiring_allocation_to_multiple_2021_codes": 0,
                "status": "fail",
                "notes": f"Could not find expected NDIA {level} 2016 code column. Columns: {list(ndia.columns)}",
            },
            pd.DataFrame(),
        )

    ndia_codes = (
        ndia[code_col]
        .map(norm_code)
        .dropna()
        .astype("string")
        .drop_duplicates()
        .sort_values()
        .reset_index(drop=True)
    )
    ndia_codes_df = pd.DataFrame({f"{level.lower()}_code_2016": ndia_codes})

    bridge_codes = bridge[[from_col]].dropna().drop_duplicates().copy()
    bridge_codes[from_col] = bridge_codes[from_col].astype("string")

    merged = ndia_codes_df.merge(
        bridge_codes,
        left_on=f"{level.lower()}_code_2016",
        right_on=from_col,
        how="left",
        indicator=True,
    )

    matched = int((merged["_merge"] == "both").sum())
    total = int(len(merged))
    unmatched = int(total - matched)
    coverage_pct = round((matched / total) * 100, 3) if total else pd.NA

    unmatched_df = merged.loc[merged["_merge"] != "both", [f"{level.lower()}_code_2016"]].copy()
    unmatched_df["ndia_source_file"] = ndia_file.name
    unmatched_df["reason"] = "NDIA 2016 geography code not found in official ABS 2016→2021 correspondence."

    from_to_counts = bridge.groupby(from_col, dropna=True)[to_col].nunique(dropna=True).reset_index(name="n_2021_targets")
    alloc_check = ndia_codes_df.merge(
        from_to_counts,
        left_on=f"{level.lower()}_code_2016",
        right_on=from_col,
        how="left",
    )
    allocation_required = int((alloc_check["n_2021_targets"].fillna(0) > 1).sum())

    if coverage_pct == 100:
        status = "pass"
        notes = "All staged NDIA 2016 geography codes are covered by the official ABS correspondence."
    elif coverage_pct >= 95:
        status = "review"
        notes = "High but incomplete coverage. Review unmatched codes before processing."
    else:
        status = "fail"
        notes = "Coverage is too low. Do not process NDIA POC participant counts until bridge issue is resolved."

    return (
        {
            "geography": level,
            "ndia_file_found": True,
            "ndia_file_path": str(ndia_file),
            "ndia_row_count": len(ndia),
            "ndia_column_count": len(ndia.columns),
            "ndia_code_column": code_col,
            "ndia_unique_2016_codes": total,
            "bridge_found": True,
            "bridge_row_count": len(bridge),
            "bridge_unique_2016_codes": int(bridge[from_col].nunique(dropna=True)),
            "bridge_unique_2021_codes": int(bridge[to_col].nunique(dropna=True)),
            "matched_unique_2016_codes": matched,
            "unmatched_unique_2016_codes": unmatched,
            "coverage_pct": coverage_pct,
            "ndia_2016_codes_requiring_allocation_to_multiple_2021_codes": allocation_required,
            "status": status,
            "notes": notes,
        },
        unmatched_df,
    )


def write_method_note() -> None:
    METHOD_NOTE_MD.write_text(
        """# NDIA public POC 2016 to 2021 geography bridge note

This script builds the NDIA proof-of-concept geography bridge using the official ABS ASGS Edition 3 correspondence files:

- `CG_SA2_2016_SA2_2021.csv`
- `CG_SA3_2016_SA3_2021.csv`

The previous bridge attempt was rejected because it selected a Mesh Block-scale correspondence: the audit showed more than 350,000 unique 2016 codes, which is impossible for SA2 or SA3.

The corrected bridge requires the exact ABS correspondence columns for SA2 and SA3, validates reasonable geography counts, and refuses to write outputs if a Mesh Block or other misclassified source is detected.

NDIA public proof-of-concept participant files use 2016 ASGS codes (`SA2Cd2016`, `SA3Cd2016`). The active MentalWellbeingByGeography master uses 2021 ASGS codes. Counts should therefore be allocated from 2016 to 2021 using `ratio_from_to` where a 2016 geography maps to multiple 2021 geographies.

The NDIA public POC layer remains excluded from the primary 2021-aligned model and may only be used in a separate demonstration or sensitivity layer.
""",
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Build exact ABS SA2/SA3 2016→2021 bridges for NDIA POC participant files.")
    parser.add_argument("--force-download", action="store_true", help="Download official ABS files even if local files already exist.")
    parser.add_argument("--timeout", type=int, default=90, help="Download timeout in seconds.")
    args = parser.parse_args()

    ensure_dirs()

    print("ABS exact ASGS 2016→2021 bridge builder for NDIA public POC v2")
    print(f"Project root: {PROJECT_ROOT}")
    print("\nThis corrected script requires the official ABS SA2 and SA3 correspondence files.")
    print("It will refuse Mesh Block-scale files.\n")

    all_source_audit: list[dict] = []
    all_bridge_audit: list[dict] = []
    readiness_rows: list[dict] = []
    unmatched_outputs = {}

    bridges = {}

    for level in ["SA2", "SA3"]:
        print(f"Building {level} bridge...")
        bridge, source_audit = build_bridge(level, force_download=args.force_download, timeout=args.timeout)
        bridges[level] = bridge
        all_source_audit.extend(source_audit)
        all_bridge_audit.extend(audit_bridge(level, bridge))
        print(f"  Created: {EXPECTED[level]['out_csv']}")
        print(f"  Rows: {len(bridge):,}; unique 2016: {bridge[EXPECTED[level]['from_col']].nunique(dropna=True):,}; unique 2021: {bridge[EXPECTED[level]['to_col']].nunique(dropna=True):,}")

    for level, bridge in bridges.items():
        readiness, unmatched_df = audit_ndia_readiness(level, bridge)
        readiness_rows.append(readiness)
        unmatched_outputs[level] = unmatched_df

    pd.DataFrame(all_source_audit).to_csv(SOURCE_AUDIT_CSV, index=False, encoding="utf-8-sig")
    pd.DataFrame(all_bridge_audit).to_csv(BRIDGE_AUDIT_CSV, index=False, encoding="utf-8-sig")
    pd.DataFrame(readiness_rows).to_csv(NDIA_READINESS_CSV, index=False, encoding="utf-8-sig")

    unmatched_outputs["SA2"].to_csv(NDIA_UNMATCHED_SA2_CSV, index=False, encoding="utf-8-sig")
    unmatched_outputs["SA3"].to_csv(NDIA_UNMATCHED_SA3_CSV, index=False, encoding="utf-8-sig")

    write_method_note()

    print("\nCreated outputs:")
    print(f"  SA2 bridge:       {SA2_BRIDGE_CSV}")
    print(f"  SA3 bridge:       {SA3_BRIDGE_CSV}")
    print(f"  Source audit:     {SOURCE_AUDIT_CSV}")
    print(f"  Bridge audit:     {BRIDGE_AUDIT_CSV}")
    print(f"  NDIA readiness:   {NDIA_READINESS_CSV}")
    print(f"  SA2 unmatched:    {NDIA_UNMATCHED_SA2_CSV}")
    print(f"  SA3 unmatched:    {NDIA_UNMATCHED_SA3_CSV}")
    print(f"  Method note:      {METHOD_NOTE_MD}")

    bridge_audit = pd.DataFrame(all_bridge_audit)
    readiness = pd.DataFrame(readiness_rows)

    print("\nBridge audit summary:")
    print(bridge_audit.to_string(index=False))

    print("\nNDIA bridge readiness summary:")
    print(readiness.to_string(index=False))

    bad = readiness[~readiness["status"].isin(["pass", "review"])]
    if not bad.empty:
        print("\nAt least one readiness check failed. Do not process NDIA POC joins yet.")
        sys.exit(1)

    print("\nNext step:")
    print("  Use these bridges to allocate NDIA 2016 participant counts to 2021 SA2/SA3.")
    print("  Do not copy counts across directly when allocation_required_for_counts is true.")


if __name__ == "__main__":
    main()
