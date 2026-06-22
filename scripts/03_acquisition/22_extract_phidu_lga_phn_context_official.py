#!/usr/bin/env python3
"""
22_extract_phidu_lga_phn_context_official.py

Purpose
-------
Target PHIDU Social Health Atlas workbooks that are explicitly organised at
Local Government Area (LGA) and Primary Health Network (PHN) level.

This script does NOT join PHIDU into the SA2 master. It:
  1. Downloads official PHIDU LGA/PHN Australia workbooks.
  2. Validates whether workbook rows can be keyed to the existing v08 master
     LGA and PHN context fields.
  3. Extracts selected high-value LGA and PHN contextual sheets into wide and
     long source tables.
  4. Writes join-readiness audits so a later script can make an explicit,
     documented higher-level context join.

Rationale
---------
PHIDU publishes Social Health Atlas data by LGA and PHN. These are not SA2
variables. If later joined to the SA2 master, values will repeat across all
SA2s assigned to an LGA/PHN. Use as higher-level context only, with grouped CV
and clear caveats.

Run
---
cd "D:\\Good Measure\\MentalWellbeingbyGeography"
python "D:\\Good Measure\\MentalWellbeingbyGeography\\scripts\\03_acquisition\\22_extract_phidu_lga_phn_context_official.py" --debug
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import re
import sys
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import pandas as pd

try:
    import winsound  # type: ignore
except Exception:  # pragma: no cover
    winsound = None


SCRIPT_NAME = "22_extract_phidu_lga_phn_context_official"
VERSION = "v12"

PHIDU_OFFICIAL_SOURCES = [
    {
        "source_id": "phidu_lga_australia",
        "geography": "LGA",
        "description": "PHIDU Social Health Atlas of Australia: Local Government Areas, Australia workbook",
        "url": "https://phidu.torrens.edu.au/current/data/sha-aust/lga/phidu_data_lga_aust.xlsx",
        "filename": "phidu_official_lga_australia.xlsx",
    },
    {
        "source_id": "phidu_phn_with_component_phas",
        "geography": "PHN",
        "description": "PHIDU Social Health Atlas of Australia: Primary Health Networks with component PHAs",
        "url": "https://phidu.torrens.edu.au/current/data/sha-aust/phn_pha_parts/phidu_data_phn_pha_parts_aust.xlsx",
        "filename": "phidu_official_phn_with_component_phas.xlsx",
    },
    {
        "source_id": "phidu_phn_with_component_lgas",
        "geography": "PHN",
        "description": "PHIDU Social Health Atlas of Australia: Primary Health Networks with component LGAs",
        "url": "https://phidu.torrens.edu.au/current/data/sha-aust/phn_lga_parts/phidu_data_phn_lga_aust.xlsx",
        "filename": "phidu_official_phn_with_component_lgas.xlsx",
    },
]

# Keep first pass narrow. These domains are most relevant to MentalWellbeingByGeography.
PRIORITY_SHEET_PATTERNS = [
    "Estimates_mental_health",
    "ED_mental",
    "CMHCS",
    "NDIS_disability",
    "Census_disability",
    "Homelessness",
    "Income_support",
    "Housing_Transport",
    "Estimates_risk_factors_adults",
    "Estimates_self_assessed_health",
    "Admissions_prevent_diag_total",
    "Education",
    "IRSD",
    "Aboriginal_persons",
]

NON_INDICATOR_TERMS = [
    "code", "name", "area", "phn", "lga", "pha", "state", "gccsa", "rest of", "australia",
]


@dataclass
class KeyMatch:
    key_type: str
    target_key_name: str
    source_column: str | None
    source_column_index: int | None
    non_empty_values: int
    source_unique_values: int
    matched_values: int
    matched_unique_values: int
    target_unique_values: int
    target_unique_matched: int
    source_value_match_rate: float
    source_unique_match_rate: float
    target_unique_coverage_rate: float
    sample_values: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Extract official PHIDU LGA/PHN context workbooks")
    parser.add_argument("--project-root", default=r"D:\Good Measure\MentalWellbeingbyGeography")
    parser.add_argument("--base-master", default=r"data\processed\integrated\sa2_predictor_universe_v08_with_clean_housing_context.parquet")
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--force-download", action="store_true")
    parser.add_argument("--max-sheets", type=int, default=0, help="Debug limit. 0 means all sheets.")
    parser.add_argument("--priority-only", action="store_true", default=True, help="Extract only priority sheets. Default true.")
    parser.add_argument("--all-sheets", action="store_true", help="Override --priority-only and process all sheets.")
    parser.add_argument("--min-lga-target-coverage", type=float, default=0.65)
    parser.add_argument("--min-phn-target-coverage", type=float, default=0.85)
    return parser.parse_args()


def setup_logger(project_root: Path, debug: bool) -> logging.Logger:
    log_dir = project_root / "outputs" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    timestamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = log_dir / f"{SCRIPT_NAME}_{timestamp}.log"

    logger = logging.getLogger(SCRIPT_NAME)
    logger.handlers.clear()
    logger.setLevel(logging.DEBUG if debug else logging.INFO)

    fmt = logging.Formatter("[%(asctime)s] [%(levelname)s] %(message)s", "%Y-%m-%d %H:%M:%S")
    sh = logging.StreamHandler(sys.stdout)
    sh.setLevel(logging.DEBUG if debug else logging.INFO)
    sh.setFormatter(fmt)
    fh = logging.FileHandler(log_path, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)
    logger.addHandler(sh)
    logger.addHandler(fh)

    logger.info("Log path: %s", log_path)
    return logger


def notify(title: str, message: str) -> None:
    try:
        if winsound:
            winsound.MessageBeep(winsound.MB_ICONASTERISK)
    except Exception:
        pass
    try:
        import subprocess
        safe_title = title.replace("'", "")
        safe_message = message.replace("'", "")
        subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                f"$wshell = New-Object -ComObject WScript.Shell; $wshell.Popup('{safe_message}', 8, '{safe_title}', 64) | Out-Null",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
            check=False,
        )
    except Exception:
        pass


def norm_text(value: object) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    s = str(value).strip().lower()
    s = re.sub(r"\s+", " ", s)
    return s


def slugify(value: object, max_len: int = 90) -> str:
    s = "" if value is None else str(value).strip().lower()
    s = s.replace("%", " pct ")
    s = s.replace("$", " dollar ")
    s = s.replace("&", " and ")
    s = re.sub(r"[^a-z0-9]+", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return s[:max_len].strip("_") or "unnamed"


def make_unique_columns(columns: Iterable[object]) -> list[str]:
    seen: dict[str, int] = {}
    out: list[str] = []
    for i, c in enumerate(columns):
        base = slugify(c) if str(c).strip() and not str(c).lower().startswith("unnamed") else f"unnamed_{i}"
        n = seen.get(base, 0)
        seen[base] = n + 1
        out.append(base if n == 0 else f"{base}_{n+1}")
    return out


def write_csv(df: pd.DataFrame, path: Path, logger: logging.Logger) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    logger.info("Writing CSV: %s", path)
    df.to_csv(path, index=False, encoding="utf-8-sig")


def write_parquet(df: pd.DataFrame, path: Path, logger: logging.Logger) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    logger.info("Writing parquet: %s", path)
    df.to_parquet(path, index=False)


def download_file(url: str, path: Path, force: bool, logger: logging.Logger) -> tuple[bool, str]:
    if path.exists() and path.stat().st_size > 0 and not force:
        return True, "cached"

    path.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,application/octet-stream,*/*",
        },
    )
    logger.info("Downloading: %s", url)
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            data = r.read()
        if len(data) < 1024:
            raise RuntimeError(f"Downloaded file is unexpectedly small: {len(data)} bytes")
        path.write_bytes(data)
        return True, f"downloaded_bytes={len(data)}"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def load_master_keys(master: pd.DataFrame) -> dict[str, set[str]]:
    keys: dict[str, set[str]] = {}
    if "dominant_lga_code_2021" in master.columns:
        keys["LGA_CODE"] = set(master["dominant_lga_code_2021"].dropna().astype(str).str.replace(r"\.0$", "", regex=True).str.zfill(5))
    else:
        keys["LGA_CODE"] = set()
    if "dominant_lga_name_2021" in master.columns:
        keys["LGA_NAME"] = set(master["dominant_lga_name_2021"].dropna().map(norm_text))
    else:
        keys["LGA_NAME"] = set()
    if "phn_2017_code" in master.columns:
        keys["PHN_CODE"] = set(master["phn_2017_code"].dropna().astype(str).str.replace(r"\.0$", "", regex=True).str.zfill(3))
    else:
        keys["PHN_CODE"] = set()
    if "phn_2017_name" in master.columns:
        keys["PHN_NAME"] = set(master["phn_2017_name"].dropna().map(norm_text))
    else:
        keys["PHN_NAME"] = set()
    return keys


def normalise_code_series(series: pd.Series, key_type: str) -> pd.Series:
    s = series.astype(str).str.strip()
    s = s.str.replace(r"\.0$", "", regex=True)
    # Extract simple numeric codes from mixed cells, but keep original if no clear numeric token.
    extracted = s.str.extract(r"(\d{3,9})", expand=False)
    s = extracted.fillna(s)
    if key_type == "LGA_CODE":
        s = s.where(~s.str.fullmatch(r"\d+", na=False), s.str.zfill(5))
    elif key_type == "PHN_CODE":
        s = s.where(~s.str.fullmatch(r"\d+", na=False), s.str.zfill(3))
    return s.replace({"nan": "", "None": ""})


def find_best_key_match(df: pd.DataFrame, target_values: set[str], key_type: str, target_key_name: str) -> KeyMatch:
    best: KeyMatch | None = None
    for idx, col in enumerate(df.columns):
        raw = df[col]
        if isinstance(raw, pd.DataFrame):
            continue
        if key_type.endswith("CODE"):
            vals = normalise_code_series(raw, key_type)
        else:
            vals = raw.map(norm_text)
        vals = vals[vals.ne("") & vals.notna()]
        non_empty = int(len(vals))
        if non_empty == 0:
            km = KeyMatch(key_type, target_key_name, col, idx, 0, 0, 0, 0, len(target_values), 0, 0.0, 0.0, 0.0, "")
        else:
            unique_vals = set(vals.astype(str))
            matched_mask = vals.astype(str).isin(target_values)
            matched_values = int(matched_mask.sum())
            matched_unique = len(unique_vals.intersection(target_values))
            target_matched = matched_unique
            sample = " | ".join(list(vals.astype(str).head(8)))
            km = KeyMatch(
                key_type=key_type,
                target_key_name=target_key_name,
                source_column=col,
                source_column_index=idx,
                non_empty_values=non_empty,
                source_unique_values=len(unique_vals),
                matched_values=matched_values,
                matched_unique_values=matched_unique,
                target_unique_values=len(target_values),
                target_unique_matched=target_matched,
                source_value_match_rate=matched_values / non_empty if non_empty else 0.0,
                source_unique_match_rate=matched_unique / len(unique_vals) if unique_vals else 0.0,
                target_unique_coverage_rate=target_matched / len(target_values) if target_values else 0.0,
                sample_values=sample,
            )
        if best is None:
            best = km
        else:
            # Prefer target coverage, then source purity, then matched rows.
            score = (km.target_unique_coverage_rate, km.source_unique_match_rate, km.source_value_match_rate, km.matched_values)
            best_score = (best.target_unique_coverage_rate, best.source_unique_match_rate, best.source_value_match_rate, best.matched_values)
            if score > best_score:
                best = km
    assert best is not None
    return best


def read_sheet_with_best_header(path: Path, sheet: str, logger: logging.Logger) -> tuple[pd.DataFrame | None, int | None, str]:
    errors = []
    best_df: pd.DataFrame | None = None
    best_header: int | None = None
    best_score = -1
    for header in range(0, 8):
        try:
            df = pd.read_excel(path, sheet_name=sheet, header=header, dtype=object, engine="openpyxl")
            df = df.dropna(how="all")
            if df.empty:
                continue
            df.columns = make_unique_columns(df.columns)
            # Score likely data sheets: at least one numeric-ish column and one non-empty key-ish column.
            non_empty_cols = sum(int(df[c].notna().sum() > 0) for c in df.columns)
            first_col_nonempty = int(df.iloc[:, 0].notna().sum()) if df.shape[1] else 0
            score = non_empty_cols + min(first_col_nonempty, 1000) // 10
            if score > best_score:
                best_score = score
                best_df = df
                best_header = header
        except Exception as e:
            errors.append(f"header={header}: {type(e).__name__}: {e}")
    if best_df is None:
        return None, None, " | ".join(errors[:5])
    return best_df, best_header, "ok"


def is_priority_sheet(sheet: str) -> bool:
    sl = sheet.lower()
    return any(p.lower() in sl for p in PRIORITY_SHEET_PATTERNS)


def find_name_column(df: pd.DataFrame, key_col: str | None, geography: str) -> str | None:
    if key_col is None:
        return None
    cols = list(df.columns)
    key_idx = cols.index(key_col) if key_col in cols else -1
    # Prefer column immediately after key if it has mostly text.
    if 0 <= key_idx + 1 < len(cols):
        cand = cols[key_idx + 1]
        text_rate = df[cand].dropna().astype(str).str.contains(r"[A-Za-z]", regex=True).mean()
        if pd.notna(text_rate) and text_rate > 0.5:
            return cand
    geo = geography.lower()
    for c in cols:
        cn = c.lower()
        if "name" in cn and (geo in cn or "area" in cn or "residence" in cn):
            return c
    for c in cols:
        if "name" in c.lower():
            return c
    return None


def numeric_indicator_columns(df: pd.DataFrame, key_col: str | None, name_col: str | None) -> list[str]:
    out = []
    for c in df.columns:
        if c in {key_col, name_col}:
            continue
        cn = c.lower()
        if any(term in cn for term in ["code", "name", "area", "phn", "lga", "pha"]):
            # Do not discard if it is clearly a measure with area in title? Keep conservative.
            continue
        ser = pd.to_numeric(df[c], errors="coerce")
        nonmiss = int(ser.notna().sum())
        if nonmiss == 0:
            continue
        parse_rate = nonmiss / max(int(df[c].notna().sum()), 1)
        if parse_rate >= 0.5:
            out.append(c)
    return out


def filter_matched_rows(df: pd.DataFrame, key_col: str, key_type: str, target_values: set[str]) -> pd.DataFrame:
    out = df.copy()
    if key_type.endswith("CODE"):
        out["__join_key"] = normalise_code_series(out[key_col], key_type)
    else:
        out["__join_key"] = out[key_col].map(norm_text)
    out = out[out["__join_key"].isin(target_values)].copy()
    return out


def inspect_workbook(
    path: Path,
    source_id: str,
    geography: str,
    keys: dict[str, set[str]],
    args: argparse.Namespace,
    logger: logging.Logger,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    import openpyxl

    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    sheets = [s for s in wb.sheetnames if not s.lower().startswith("contents") and "note" not in s.lower()]
    if args.priority_only and not args.all_sheets:
        priority_sheets = [s for s in sheets if is_priority_sheet(s)]
        # Always include a few key structural sheets if present.
        priority_sheets += [s for s in sheets if s.lower() in {"phas", "lga", "lgas", "phn", "phns"}]
        sheets = list(dict.fromkeys(priority_sheets))
    if args.max_sheets and args.max_sheets > 0:
        sheets = sheets[: args.max_sheets]
    logger.info("Inspecting %s; sheets selected: %s", path.name, len(sheets))

    audit_rows: list[dict] = []
    indicator_rows: list[dict] = []
    wide_tables: list[pd.DataFrame] = []
    long_rows: list[dict] = []

    if geography == "LGA":
        key_candidates = [("LGA_CODE", "dominant_lga_code_2021", keys["LGA_CODE"]), ("LGA_NAME", "dominant_lga_name_2021", keys["LGA_NAME"])]
        threshold = args.min_lga_target_coverage
    else:
        key_candidates = [("PHN_CODE", "phn_2017_code", keys["PHN_CODE"]), ("PHN_NAME", "phn_2017_name", keys["PHN_NAME"])]
        threshold = args.min_phn_target_coverage

    for sheet in sheets:
        df, header_row, status = read_sheet_with_best_header(path, sheet, logger)
        if df is None:
            audit_rows.append(
                {
                    "source_id": source_id,
                    "workbook_name": path.name,
                    "sheet_name": sheet,
                    "geography": geography,
                    "read_status": status,
                    "recommended_action": "hold_context_only_read_failed",
                }
            )
            continue

        best_matches = [find_best_key_match(df, target, kt, tkn) for kt, tkn, target in key_candidates if target]
        best = sorted(
            best_matches,
            key=lambda km: (km.target_unique_coverage_rate, km.source_unique_match_rate, km.source_value_match_rate, km.matched_values),
            reverse=True,
        )[0]
        name_col = find_name_column(df, best.source_column, geography)
        num_cols = numeric_indicator_columns(df, best.source_column, name_col)
        join_ready = best.target_unique_coverage_rate >= threshold
        action = "extract_context_candidate" if join_ready else "hold_context_only_key_coverage_below_threshold"

        audit_rows.append(
            {
                "source_id": source_id,
                "workbook_name": path.name,
                "sheet_name": sheet,
                "geography": geography,
                "read_status": status,
                "header_row_used_zero_based": header_row,
                "rows_read": len(df),
                "columns_read": len(df.columns),
                "best_key_type": best.key_type,
                "best_target_key_name": best.target_key_name,
                "best_source_key_column": best.source_column,
                "best_source_key_column_index": best.source_column_index,
                "source_name_column": name_col,
                "non_empty_key_values": best.non_empty_values,
                "source_unique_key_values": best.source_unique_values,
                "matched_key_values": best.matched_values,
                "matched_unique_key_values": best.matched_unique_values,
                "target_unique_values": best.target_unique_values,
                "target_unique_matched": best.target_unique_matched,
                "source_value_match_rate": round(best.source_value_match_rate, 6),
                "source_unique_match_rate": round(best.source_unique_match_rate, 6),
                "target_unique_coverage_rate": round(best.target_unique_coverage_rate, 6),
                "numeric_indicator_columns": len(num_cols),
                "priority_sheet": int(is_priority_sheet(sheet)),
                "recommended_action": action,
                "sample_key_values": best.sample_values,
            }
        )

        for c in num_cols:
            ser = pd.to_numeric(df[c], errors="coerce")
            indicator_rows.append(
                {
                    "source_id": source_id,
                    "workbook_name": path.name,
                    "sheet_name": sheet,
                    "geography": geography,
                    "indicator_column": c,
                    "indicator_slug": slugify(c, 120),
                    "non_missing_numeric_count": int(ser.notna().sum()),
                    "min_numeric": float(ser.min()) if ser.notna().any() else None,
                    "max_numeric": float(ser.max()) if ser.notna().any() else None,
                    "recommended_action": "candidate_context_indicator" if join_ready else "hold_context_only_key_coverage_below_threshold",
                }
            )

        if join_ready and best.source_column and num_cols:
            matched = filter_matched_rows(df, best.source_column, best.key_type, key_candidates[0][2] if best.key_type == key_candidates[0][0] else key_candidates[1][2])
            if not matched.empty:
                # Wide table for this source/sheet.
                wide = pd.DataFrame()
                wide[f"phidu_{geography.lower()}_join_key"] = matched["__join_key"].astype(str)
                if name_col:
                    wide[f"phidu_{geography.lower()}_source_name"] = matched[name_col].astype(str)
                for c in num_cols:
                    out_col = f"phidu_{geography.lower()}_{slugify(sheet, 45)}__{slugify(c, 65)}"
                    wide[out_col] = pd.to_numeric(matched[c], errors="coerce")
                    # Long rows for indicator inventory and possible later reshaping.
                    for key, name, val in zip(
                        wide[f"phidu_{geography.lower()}_join_key"],
                        wide.get(f"phidu_{geography.lower()}_source_name", pd.Series([None] * len(wide))),
                        wide[out_col],
                    ):
                        if pd.notna(val):
                            long_rows.append(
                                {
                                    "source_id": source_id,
                                    "geography": geography,
                                    "join_key": key,
                                    "source_name": name,
                                    "sheet_name": sheet,
                                    "source_indicator_column": c,
                                    "output_indicator_column": out_col,
                                    "value": val,
                                }
                            )
                # One row per key if sheet has repeated matched rows. Use first non-null per key.
                value_cols = [c for c in wide.columns if c.startswith(f"phidu_{geography.lower()}_") and c not in {f"phidu_{geography.lower()}_join_key", f"phidu_{geography.lower()}_source_name"}]
                group_cols = [f"phidu_{geography.lower()}_join_key"]
                if f"phidu_{geography.lower()}_source_name" in wide.columns:
                    # Use mode/first source name separately.
                    name_map = wide.groupby(f"phidu_{geography.lower()}_join_key")[f"phidu_{geography.lower()}_source_name"].agg(lambda x: x.dropna().astype(str).iloc[0] if len(x.dropna()) else None).reset_index()
                else:
                    name_map = None
                wide_values = wide[group_cols + value_cols].groupby(group_cols, as_index=False).first()
                if name_map is not None:
                    wide_values = wide_values.merge(name_map, on=f"phidu_{geography.lower()}_join_key", how="left")
                    # Put name after key.
                    ordered = [f"phidu_{geography.lower()}_join_key", f"phidu_{geography.lower()}_source_name"] + value_cols
                    wide_values = wide_values[[c for c in ordered if c in wide_values.columns]]
                wide_tables.append(wide_values)

    audit_df = pd.DataFrame(audit_rows)
    indicator_df = pd.DataFrame(indicator_rows)
    if wide_tables:
        # Merge sheet-level wide tables by join key. Avoid duplicate source name columns.
        merged = wide_tables[0]
        key_col = f"phidu_{geography.lower()}_join_key"
        for wt in wide_tables[1:]:
            drop_cols = [c for c in wt.columns if c.endswith("_source_name") and c in merged.columns]
            wt2 = wt.drop(columns=drop_cols, errors="ignore")
            merged = merged.merge(wt2, on=key_col, how="outer")
        wide_df = merged
    else:
        wide_df = pd.DataFrame()
    long_df = pd.DataFrame(long_rows)
    return audit_df, indicator_df, wide_df, long_df


def main() -> None:
    args = parse_args()
    if args.all_sheets:
        args.priority_only = False

    project_root = Path(args.project_root)
    logger = setup_logger(project_root, args.debug)
    logger.info("PHIDU official LGA/PHN context extraction %s", VERSION)
    logger.info("Project root: %s", project_root)

    raw_dir = project_root / "data" / "raw" / "phidu"
    src_dir = project_root / "data" / "processed" / "sources"
    audit_dir = project_root / "outputs" / "audits"
    register_dir = project_root / "docs" / "source_registers"
    method_dir = project_root / "docs" / "methodology"

    master_path = project_root / args.base_master
    logger.info("Reading base master: %s", master_path)
    master = pd.read_parquet(master_path)
    logger.info("Base master rows: %s; columns: %s", len(master), len(master.columns))
    keys = load_master_keys(master)
    logger.info(
        "Master key counts: LGA codes=%s; LGA names=%s; PHN codes=%s; PHN names=%s",
        len(keys["LGA_CODE"]), len(keys["LGA_NAME"]), len(keys["PHN_CODE"]), len(keys["PHN_NAME"]),
    )

    download_rows = []
    downloaded_sources = []
    for src in PHIDU_OFFICIAL_SOURCES:
        path = raw_dir / src["filename"]
        ok, message = download_file(src["url"], path, args.force_download, logger)
        download_rows.append({**src, "local_path": str(path), "download_ok": int(ok), "download_status": message, "file_size_bytes": path.stat().st_size if path.exists() else 0})
        if ok:
            downloaded_sources.append((src, path))
        else:
            logger.warning("Download failed for %s: %s", src["source_id"], message)
    download_df = pd.DataFrame(download_rows)
    write_csv(download_df, audit_dir / f"phidu_official_lga_phn_download_audit_{VERSION}.csv", logger)

    audit_frames = []
    indicator_frames = []
    wide_lga_frames = []
    wide_phn_frames = []
    long_frames = []

    for src, path in downloaded_sources:
        audit_df, indicator_df, wide_df, long_df = inspect_workbook(path, src["source_id"], src["geography"], keys, args, logger)
        audit_frames.append(audit_df)
        indicator_frames.append(indicator_df)
        if not wide_df.empty:
            if src["geography"] == "LGA":
                wide_lga_frames.append(wide_df)
            elif src["geography"] == "PHN":
                wide_phn_frames.append(wide_df)
        if not long_df.empty:
            long_frames.append(long_df)

    validation_df = pd.concat(audit_frames, ignore_index=True) if audit_frames else pd.DataFrame()
    indicator_df = pd.concat(indicator_frames, ignore_index=True) if indicator_frames else pd.DataFrame()
    long_df = pd.concat(long_frames, ignore_index=True) if long_frames else pd.DataFrame()

    def merge_wide(frames: list[pd.DataFrame], geography: str) -> pd.DataFrame:
        if not frames:
            return pd.DataFrame()
        key_col = f"phidu_{geography.lower()}_join_key"
        merged = frames[0]
        for frame in frames[1:]:
            # Avoid identical non-value source name columns and duplicate indicator columns.
            duplicates = [c for c in frame.columns if c in merged.columns and c != key_col]
            frame2 = frame.drop(columns=duplicates, errors="ignore")
            merged = merged.merge(frame2, on=key_col, how="outer")
        return merged

    wide_lga = merge_wide(wide_lga_frames, "LGA")
    wide_phn = merge_wide(wide_phn_frames, "PHN")

    write_csv(validation_df, audit_dir / f"phidu_official_lga_phn_key_validation_{VERSION}.csv", logger)
    write_csv(indicator_df, audit_dir / f"phidu_official_lga_phn_indicator_inventory_{VERSION}.csv", logger)
    write_csv(validation_df, register_dir / f"phidu_official_lga_phn_context_register_{VERSION}.csv", logger)

    if not wide_lga.empty:
        write_csv(wide_lga, src_dir / f"phidu_lga_context_selected_wide_{VERSION}.csv", logger)
        write_parquet(wide_lga, src_dir / f"phidu_lga_context_selected_wide_{VERSION}.parquet", logger)
    else:
        write_csv(pd.DataFrame(), src_dir / f"phidu_lga_context_selected_wide_{VERSION}.csv", logger)

    if not wide_phn.empty:
        write_csv(wide_phn, src_dir / f"phidu_phn_context_selected_wide_{VERSION}.csv", logger)
        write_parquet(wide_phn, src_dir / f"phidu_phn_context_selected_wide_{VERSION}.parquet", logger)
    else:
        write_csv(pd.DataFrame(), src_dir / f"phidu_phn_context_selected_wide_{VERSION}.csv", logger)

    if not long_df.empty:
        write_csv(long_df, src_dir / f"phidu_lga_phn_context_selected_long_{VERSION}.csv", logger)
        write_parquet(long_df, src_dir / f"phidu_lga_phn_context_selected_long_{VERSION}.parquet", logger)
    else:
        write_csv(pd.DataFrame(), src_dir / f"phidu_lga_phn_context_selected_long_{VERSION}.csv", logger)

    readiness_rows = []
    for geography, wide_df, min_cov in [("LGA", wide_lga, args.min_lga_target_coverage), ("PHN", wide_phn, args.min_phn_target_coverage)]:
        key_col = f"phidu_{geography.lower()}_join_key"
        validation_geo = validation_df[validation_df["geography"].eq(geography)] if not validation_df.empty else pd.DataFrame()
        extractable_sheets = int(validation_geo[validation_geo["recommended_action"].eq("extract_context_candidate")]["sheet_name"].nunique()) if not validation_geo.empty else 0
        readiness_rows.append(
            {
                "geography": geography,
                "context_table_created": int(not wide_df.empty),
                "context_rows": int(len(wide_df)) if not wide_df.empty else 0,
                "context_columns": int(len(wide_df.columns)) if not wide_df.empty else 0,
                "unique_context_keys": int(wide_df[key_col].nunique()) if not wide_df.empty and key_col in wide_df.columns else 0,
                "extractable_priority_sheets": extractable_sheets,
                "minimum_target_coverage_threshold": min_cov,
                "recommended_action": "candidate_higher_level_context_join_after_manual_review" if not wide_df.empty else "hold_context_only_no_validated_context_table",
                "notes": "Higher-level context only. Joining to SA2 will repeat values across SA2s assigned to the same LGA/PHN. Use grouped CV and do not interpret as direct SA2 measurement.",
            }
        )
    readiness_df = pd.DataFrame(readiness_rows)
    write_csv(readiness_df, audit_dir / f"phidu_official_lga_phn_join_readiness_{VERSION}.csv", logger)

    note = f"""# PHIDU official LGA/PHN context extraction {VERSION}\n\nThis run targeted the PHIDU Social Health Atlas workbooks explicitly published for Local Government Areas and Primary Health Networks.\n\nOutputs are source context tables, not an SA2 master join. Any later SA2 join must be treated as a higher-level contextual join, because LGA/PHN values will repeat across SA2s.\n\nBase master: `{args.base_master}`\n\nOfficial source URLs:\n\n- LGA Australia: `{PHIDU_OFFICIAL_SOURCES[0]['url']}`\n- PHN with component PHAs: `{PHIDU_OFFICIAL_SOURCES[1]['url']}`\n- PHN with component LGAs: `{PHIDU_OFFICIAL_SOURCES[2]['url']}`\n\nKey caveats:\n\n- LGA context uses `dominant_lga_code_2021` from the SA2 master if later joined. This is a dominant-area assignment, not a population-weighted LGA allocation.\n- PHN context uses `phn_2017_code` from the SA2 master if later joined. Boundary year should be checked against PHIDU metadata.\n- These fields should be excluded from primary SA2-only feature sets unless model validation groups by LGA/PHN or explicitly treats them as higher-level contextual predictors.\n"""
    method_dir.mkdir(parents=True, exist_ok=True)
    note_path = method_dir / f"phidu_official_lga_phn_context_extraction_note_{VERSION}.md"
    logger.info("Writing methodology note: %s", note_path)
    note_path.write_text(note, encoding="utf-8")

    logger.info("PHIDU official LGA/PHN context extraction complete.")
    logger.info("Readiness summary:\n%s", readiness_df.to_string(index=False))
    notify("PHIDU extraction complete", "Official LGA/PHN PHIDU context extraction finished. Review join readiness audit.")


if __name__ == "__main__":
    start = time.time()
    try:
        main()
    except Exception as e:
        notify("PHIDU extraction failed", f"{type(e).__name__}: {e}")
        raise
    finally:
        _ = time.time() - start
