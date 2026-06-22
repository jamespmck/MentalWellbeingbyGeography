#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
20_validate_phidu_join_candidates.py

Purpose
-------
Validate PHIDU Social Health Atlas candidate workbooks/sheets before joining them
into the MentalWellbeingByGeography SA2 master.

This script does NOT join PHIDU data. It creates validation and shortlist audits so
that PHIDU can be added safely in a later, source-specific integration script.

Design principles
-----------------
- Treat v08 as the current working master.
- Validate geography keys before any join.
- Prefer direct SA2 evidence if PHIDU sheets genuinely carry SA2 2021 codes.
- Treat PHN and LGA PHIDU sheets as context candidates only until boundary year,
  code format and match coverage are acceptable.
- Avoid broad PHIDU joins. The PHIDU workbooks are very wide and contain many
  overlapping demographic, health and service-system indicators.

Expected inputs
---------------
Project root defaults to the current repository layout:
  D:\Good Measure\MentalWellbeingbyGeography

Required prior outputs:
  data/processed/integrated/sa2_predictor_universe_v08_with_clean_housing_context.parquet
  outputs/audits/phidu_join_readiness_audit_v09.csv

Outputs
-------
  outputs/audits/phidu_join_candidate_validation_v10.csv
  outputs/audits/phidu_geography_key_validation_v10.csv
  outputs/audits/phidu_indicator_priority_shortlist_v10.csv
  outputs/audits/phidu_validation_run_audit_v10.csv
  docs/source_registers/phidu_join_candidate_validation_register_v10.csv
  docs/methodology/phidu_join_candidate_validation_note_v10.md

Run
---
  python scripts/03_acquisition/20_validate_phidu_join_candidates.py --debug
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional

import pandas as pd


DEFAULT_PROJECT_ROOT = Path(r"D:\Good Measure\MentalWellbeingbyGeography")
DEFAULT_MASTER = Path(r"data\processed\integrated\sa2_predictor_universe_v08_with_clean_housing_context.parquet")
DEFAULT_READINESS = Path(r"outputs\audits\phidu_join_readiness_audit_v09.csv")

# Keep the validation bounded. PHIDU files can be large and numerous.
DEFAULT_MAX_SHEETS_PER_CATEGORY = 30
DEFAULT_SAMPLE_ROWS = 5000

# These are broad terms for later indicator prioritisation only.
PRIORITY_TERMS = {
    "mental_health": ["mental", "psychological", "distress", "suicide", "self_harm", "depress", "anxiety"],
    "disability_carers": ["disability", "carer", "need_for_assistance", "ndis"],
    "health_service_use": ["cmhcs", "ed_", "hospital", "admission", "medicare", "gp", "pharmaceutical", "pbs"],
    "social_determinants": ["unemploy", "income", "education", "housing", "rent", "mortgage", "homeless", "welfare", "pension"],
    "first_nations": ["aboriginal", "torres", "indigenous", "atsi"],
    "risk_factors": ["smoking", "alcohol", "obesity", "physical_activity", "risk", "diabetes"],
}


@dataclass
class Logger:
    path: Path
    debug_enabled: bool = False

    def _write(self, level: str, msg: str) -> None:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = f"[{ts}] [{level}] {msg}"
        print(line)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")

    def info(self, msg: str) -> None:
        self._write("INFO", msg)

    def warning(self, msg: str) -> None:
        self._write("WARNING", msg)

    def debug(self, msg: str) -> None:
        if self.debug_enabled:
            self._write("DEBUG", msg)


class ScriptError(RuntimeError):
    pass


def normalise_name(value: object) -> str:
    s = "" if value is None or (isinstance(value, float) and pd.isna(value)) else str(value)
    s = s.strip().lower()
    s = re.sub(r"[^a-z0-9]+", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return s


def normalise_code(value: object) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    s = str(value).strip()
    # Excel sometimes reads integer-looking codes as floats.
    if re.fullmatch(r"\d+\.0", s):
        s = s[:-2]
    s = re.sub(r"[^0-9A-Za-z]", "", s)
    return s.upper()


def normalise_text_key(value: object) -> str:
    s = "" if value is None or (isinstance(value, float) and pd.isna(value)) else str(value)
    s = s.lower().strip()
    s = re.sub(r"&", " and ", s)
    s = re.sub(r"[^a-z0-9]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def make_unique_columns(cols: Iterable[object]) -> list[str]:
    seen: dict[str, int] = {}
    out: list[str] = []
    for i, col in enumerate(cols):
        base = normalise_name(col)
        if not base:
            base = f"unnamed_{i}"
        count = seen.get(base, 0)
        seen[base] = count + 1
        if count:
            out.append(f"{base}_{count+1}")
        else:
            out.append(base)
    return out


def read_master(path: Path, logger: Logger) -> pd.DataFrame:
    if not path.exists():
        raise ScriptError(f"Base master not found: {path}")
    logger.info(f"Reading base master: {path}")
    if path.suffix.lower() == ".parquet":
        df = pd.read_parquet(path)
    else:
        df = pd.read_csv(path, dtype=str, low_memory=False)
    logger.info(f"Base master rows: {len(df):,}; columns: {len(df.columns):,}")
    return df


def build_master_key_sets(master: pd.DataFrame) -> dict[str, set[str]]:
    keys: dict[str, set[str]] = {}
    if "sa2_code_2021" in master.columns:
        keys["sa2_code_2021"] = set(master["sa2_code_2021"].map(normalise_code)) - {""}
    if "phn_2017_code" in master.columns:
        keys["phn_2017_code"] = set(master["phn_2017_code"].map(normalise_code)) - {""}
    if "phn_2017_name" in master.columns:
        keys["phn_2017_name_key"] = set(master["phn_2017_name"].map(normalise_text_key)) - {""}
    if "dominant_lga_code_2021" in master.columns:
        keys["dominant_lga_code_2021"] = set(master["dominant_lga_code_2021"].map(normalise_code)) - {""}
    if "dominant_lga_name_2021" in master.columns:
        keys["dominant_lga_name_2021_key"] = set(master["dominant_lga_name_2021"].map(normalise_text_key)) - {""}
    return keys


def load_readiness(path: Path, logger: Logger) -> pd.DataFrame:
    if not path.exists():
        raise ScriptError(f"PHIDU readiness audit not found: {path}")
    df = pd.read_csv(path, dtype=str, low_memory=False)
    df.columns = make_unique_columns(df.columns)
    logger.info(f"Read PHIDU readiness audit: {len(df):,} rows; {len(df.columns):,} columns")
    return df


def pick_col(df: pd.DataFrame, candidates: list[str]) -> Optional[str]:
    norm_to_actual = {normalise_name(c): c for c in df.columns}
    for c in candidates:
        key = normalise_name(c)
        if key in norm_to_actual:
            return norm_to_actual[key]
    # loose contains match
    for c in candidates:
        key = normalise_name(c)
        for actual in df.columns:
            if key and key in normalise_name(actual):
                return actual
    return None


def classify_candidate_rows(readiness: pd.DataFrame) -> pd.DataFrame:
    readiness = readiness.copy()
    status_col = pick_col(readiness, ["join_readiness", "readiness", "join_status", "candidate_join_status"])
    geog_col = pick_col(readiness, ["detected_geography", "geography", "geography_class", "geo_class"])
    sheet_col = pick_col(readiness, ["sheet_name", "worksheet", "sheet"])
    path_col = pick_col(readiness, ["local_path", "workbook_path", "download_path", "file_path", "path"])
    workbook_col = pick_col(readiness, ["workbook_name", "file_name", "downloaded_file_name"])

    if not sheet_col or not path_col:
        raise ScriptError(
            "Could not identify workbook path and sheet columns in PHIDU readiness audit. "
            f"Columns available: {list(readiness.columns)}"
        )

    def infer_category(row: pd.Series) -> str:
        status = normalise_name(row.get(status_col, "")) if status_col else ""
        geog = normalise_name(row.get(geog_col, "")) if geog_col else ""
        text = f"{status} {geog} {normalise_name(row.get(sheet_col, ''))}"
        if "direct_sa2" in text or re.search(r"\bsa2\b", text):
            return "sa2_candidate"
        if "phn" in text:
            return "phn_candidate"
        if "lga" in text:
            return "lga_candidate"
        if "pha" in text:
            return "pha_hold_context_only"
        return "other_review"

    readiness["validation_category"] = readiness.apply(infer_category, axis=1)
    readiness["_path_col"] = path_col
    readiness["_sheet_col"] = sheet_col
    readiness["_workbook_col"] = workbook_col if workbook_col else ""
    readiness["_status_col"] = status_col if status_col else ""
    readiness["_geog_col"] = geog_col if geog_col else ""
    return readiness


def select_validation_targets(candidates: pd.DataFrame, max_per_category: int) -> pd.DataFrame:
    keep_categories = ["sa2_candidate", "phn_candidate", "lga_candidate"]
    selected_parts = []
    for category in keep_categories:
        part = candidates[candidates["validation_category"] == category].copy()
        if part.empty:
            continue
        # Prefer non-summary sheets and likely mental/disability/service content.
        sheet_col = part["_sheet_col"].iloc[0]
        part["_priority_score"] = part[sheet_col].map(lambda x: score_indicator_text(str(x)))
        part["_is_summary"] = part[sheet_col].astype(str).str.contains("workbook_summary|contents", case=False, na=False)
        part = part.sort_values(["_is_summary", "_priority_score"], ascending=[True, False]).head(max_per_category)
        selected_parts.append(part)
    if not selected_parts:
        return candidates.head(0).copy()
    return pd.concat(selected_parts, ignore_index=True)


def score_indicator_text(text: str) -> int:
    norm = normalise_name(text)
    score = 0
    for _, terms in PRIORITY_TERMS.items():
        for term in terms:
            if term in norm:
                score += 2
    if any(x in norm for x in ["total", "persons", "population"]):
        score += 1
    if any(x in norm for x in ["contents", "workbook_summary"]):
        score -= 10
    return score


def read_excel_sheet_with_header_guess(path: Path, sheet_name: str, sample_rows: int, logger: Logger) -> tuple[pd.DataFrame, int, str]:
    errors = []
    best: Optional[tuple[int, pd.DataFrame, int]] = None
    for header in range(0, 12):
        try:
            df = pd.read_excel(path, sheet_name=sheet_name, header=header, nrows=sample_rows, dtype=object)
            df.columns = make_unique_columns(df.columns)
            # Drop wholly empty rows/columns.
            df = df.dropna(axis=0, how="all").dropna(axis=1, how="all")
            score = header_score(df)
            if best is None or score > best[0]:
                best = (score, df, header)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"header={header}: {type(exc).__name__}: {exc}")
            continue
    if best is None:
        raise ScriptError(f"Could not read sheet {sheet_name} in {path}. Errors: {' | '.join(errors[:3])}")
    score, df, header = best
    logger.debug(f"Header guess for {path.name}::{sheet_name}: header={header}; score={score}; rows={len(df)}; cols={len(df.columns)}")
    return df, header, "ok"


def header_score(df: pd.DataFrame) -> int:
    cols = [normalise_name(c) for c in df.columns]
    score = 0
    for c in cols:
        if any(token in c for token in ["sa2", "lga", "phn", "pha", "code", "name"]):
            score += 3
        if any(token in c for token in ["number", "rate", "ratio", "percent", "per_", "sr", "value", "persons"]):
            score += 1
    score += min(len(df.columns), 50) // 5
    return score


def detect_geography_columns(df: pd.DataFrame) -> dict[str, list[str]]:
    cols = list(df.columns)
    norm = {c: normalise_name(c) for c in cols}
    out: dict[str, list[str]] = {"sa2_code": [], "sa2_name": [], "phn_code": [], "phn_name": [], "lga_code": [], "lga_name": [], "pha_code": [], "pha_name": []}
    for c, n in norm.items():
        if "sa2" in n and "code" in n:
            out["sa2_code"].append(c)
        if "sa2" in n and "name" in n:
            out["sa2_name"].append(c)
        if "phn" in n and "code" in n:
            out["phn_code"].append(c)
        if "phn" in n and "name" in n:
            out["phn_name"].append(c)
        if "lga" in n and "code" in n:
            out["lga_code"].append(c)
        if "lga" in n and "name" in n:
            out["lga_name"].append(c)
        if "pha" in n and "code" in n:
            out["pha_code"].append(c)
        if "pha" in n and "name" in n:
            out["pha_name"].append(c)
        # PHIDU often uses hybrid names like code_phn_pha and code_phn_lga.
        if n.startswith("code_phn_pha") or n == "code_phn_pha":
            out["phn_code"].append(c)
        if n.startswith("name_of_phn_pha"):
            out["phn_name"].append(c)
        if n.startswith("code_phn_lga") or n == "code_phn_lga":
            out["lga_code"].append(c)
        if n.startswith("name_of_phn_lga"):
            out["lga_name"].append(c)
    # Deduplicate while preserving order.
    for k, v in out.items():
        seen = set()
        out[k] = [x for x in v if not (x in seen or seen.add(x))]
    return out


def choose_first(candidates: list[str]) -> Optional[str]:
    return candidates[0] if candidates else None


def validate_code_match(series: pd.Series, target_keys: set[str]) -> tuple[int, int, float, list[str]]:
    codes = series.map(normalise_code)
    values = sorted(set(codes) - {""})
    if not values:
        return 0, 0, 0.0, []
    matched = [v for v in values if v in target_keys]
    rate = len(matched) / len(values) if values else 0.0
    unmatched_sample = [v for v in values if v not in target_keys][:20]
    return len(values), len(matched), rate, unmatched_sample


def validate_name_match(series: pd.Series, target_keys: set[str]) -> tuple[int, int, float, list[str]]:
    names = series.map(normalise_text_key)
    values = sorted(set(names) - {""})
    if not values:
        return 0, 0, 0.0, []
    matched = [v for v in values if v in target_keys]
    rate = len(matched) / len(values) if values else 0.0
    unmatched_sample = [v for v in values if v not in target_keys][:20]
    return len(values), len(matched), rate, unmatched_sample


def numeric_column_profile(df: pd.DataFrame, exclude_cols: set[str]) -> pd.DataFrame:
    rows = []
    for col in df.columns:
        if col in exclude_cols:
            continue
        ser = df[col]
        if isinstance(ser, pd.DataFrame):
            continue
        numeric = pd.to_numeric(ser, errors="coerce")
        non_missing = int(numeric.notna().sum())
        parse_rate = float(numeric.notna().mean()) if len(numeric) else 0.0
        if non_missing == 0 or parse_rate < 0.40:
            continue
        n_norm = normalise_name(col)
        if any(token in n_norm for token in ["code", "name", "year", "date"]):
            continue
        rows.append({
            "source_column": col,
            "normalised_column": n_norm,
            "non_missing_count_sample": non_missing,
            "numeric_parse_rate_sample": parse_rate,
            "min_numeric_sample": float(numeric.min()) if non_missing else None,
            "max_numeric_sample": float(numeric.max()) if non_missing else None,
            "priority_score": score_indicator_text(n_norm),
            "priority_domain_hint": priority_domain_hint(n_norm),
        })
    return pd.DataFrame(rows).sort_values(["priority_score", "non_missing_count_sample"], ascending=[False, False]) if rows else pd.DataFrame()


def priority_domain_hint(text: str) -> str:
    norm = normalise_name(text)
    hits = []
    for domain, terms in PRIORITY_TERMS.items():
        if any(term in norm for term in terms):
            hits.append(domain)
    return ";".join(hits) if hits else "review"


def validate_target(row: pd.Series, master_keys: dict[str, set[str]], sample_rows: int, logger: Logger) -> tuple[list[dict], list[dict], list[dict]]:
    path_col = row["_path_col"]
    sheet_col = row["_sheet_col"]
    workbook_col = row["_workbook_col"]
    status_col = row["_status_col"]
    geog_col = row["_geog_col"]

    workbook_path = Path(str(row[path_col]))
    sheet_name = str(row[sheet_col])
    validation_category = str(row["validation_category"])
    workbook_name = str(row.get(workbook_col, workbook_path.name)) if workbook_col else workbook_path.name
    readiness_status = str(row.get(status_col, "")) if status_col else ""
    detected_geography = str(row.get(geog_col, "")) if geog_col else ""

    validation_rows = []
    key_rows = []
    indicator_rows = []

    if not workbook_path.exists():
        validation_rows.append({
            "workbook_path": str(workbook_path),
            "workbook_name": workbook_name,
            "sheet_name": sheet_name,
            "validation_category": validation_category,
            "read_status": "failed_missing_workbook",
            "recommended_action": "hold_context_only",
            "notes": "Workbook path from PHIDU readiness audit was not found locally.",
        })
        return validation_rows, key_rows, indicator_rows

    try:
        df, header_row, read_status = read_excel_sheet_with_header_guess(workbook_path, sheet_name, sample_rows, logger)
    except Exception as exc:  # noqa: BLE001
        validation_rows.append({
            "workbook_path": str(workbook_path),
            "workbook_name": workbook_name,
            "sheet_name": sheet_name,
            "validation_category": validation_category,
            "read_status": f"failed: {type(exc).__name__}: {exc}",
            "recommended_action": "hold_context_only",
            "notes": "Could not read sheet safely.",
        })
        return validation_rows, key_rows, indicator_rows

    geog_cols = detect_geography_columns(df)
    exclude = set(sum(geog_cols.values(), []))

    checks = []
    if validation_category == "sa2_candidate":
        col = choose_first(geog_cols["sa2_code"])
        if col:
            unique, matched, rate, sample = validate_code_match(df[col], master_keys.get("sa2_code_2021", set()))
            checks.append(("sa2_code_2021", col, unique, matched, rate, sample))
    if validation_category == "phn_candidate":
        col = choose_first(geog_cols["phn_code"])
        if col:
            unique, matched, rate, sample = validate_code_match(df[col], master_keys.get("phn_2017_code", set()))
            checks.append(("phn_2017_code", col, unique, matched, rate, sample))
        name_col = choose_first(geog_cols["phn_name"])
        if name_col:
            unique, matched, rate, sample = validate_name_match(df[name_col], master_keys.get("phn_2017_name_key", set()))
            checks.append(("phn_2017_name", name_col, unique, matched, rate, sample))
    if validation_category == "lga_candidate":
        col = choose_first(geog_cols["lga_code"])
        if col:
            unique, matched, rate, sample = validate_code_match(df[col], master_keys.get("dominant_lga_code_2021", set()))
            checks.append(("dominant_lga_code_2021", col, unique, matched, rate, sample))
        name_col = choose_first(geog_cols["lga_name"])
        if name_col:
            unique, matched, rate, sample = validate_name_match(df[name_col], master_keys.get("dominant_lga_name_2021_key", set()))
            checks.append(("dominant_lga_name_2021", name_col, unique, matched, rate, sample))

    best_rate = 0.0
    best_key = ""
    best_col = ""
    for target_key, source_col, unique, matched, rate, sample in checks:
        best_rate = max(best_rate, rate)
        if rate == best_rate:
            best_key = target_key
            best_col = source_col
        key_rows.append({
            "workbook_path": str(workbook_path),
            "workbook_name": workbook_name,
            "sheet_name": sheet_name,
            "validation_category": validation_category,
            "source_key_column": source_col,
            "target_master_key": target_key,
            "unique_source_keys_sample": unique,
            "matched_target_keys_sample": matched,
            "match_rate_sample": rate,
            "unmatched_source_key_sample": " | ".join(sample),
        })

    numeric_profile = numeric_column_profile(df, exclude)
    for _, ind in numeric_profile.head(30).iterrows():
        indicator_rows.append({
            "workbook_path": str(workbook_path),
            "workbook_name": workbook_name,
            "sheet_name": sheet_name,
            "validation_category": validation_category,
            "source_indicator_column": ind["source_column"],
            "normalised_indicator_column": ind["normalised_column"],
            "priority_domain_hint": ind["priority_domain_hint"],
            "priority_score": ind["priority_score"],
            "non_missing_count_sample": ind["non_missing_count_sample"],
            "numeric_parse_rate_sample": ind["numeric_parse_rate_sample"],
            "min_numeric_sample": ind["min_numeric_sample"],
            "max_numeric_sample": ind["max_numeric_sample"],
            "source_key_column_best_match": best_col,
            "target_master_key_best_match": best_key,
            "geography_match_rate_sample": best_rate,
        })

    if best_rate >= 0.95 and validation_category == "sa2_candidate":
        action = "safe_candidate_direct_sa2_join_after_indicator_selection"
    elif best_rate >= 0.95 and validation_category == "phn_candidate":
        action = "candidate_phn_context_join_after_boundary_year_confirmation"
    elif best_rate >= 0.95 and validation_category == "lga_candidate":
        action = "candidate_lga_context_join_with_area_share_caveat"
    elif best_rate > 0:
        action = "review_key_match_before_join"
    else:
        action = "hold_context_only_no_validated_key_match"

    validation_rows.append({
        "workbook_path": str(workbook_path),
        "workbook_name": workbook_name,
        "sheet_name": sheet_name,
        "validation_category": validation_category,
        "readiness_status_from_v09": readiness_status,
        "detected_geography_from_v09": detected_geography,
        "read_status": read_status,
        "header_row_used": header_row,
        "sample_rows_read": len(df),
        "sample_columns_read": len(df.columns),
        "sa2_code_columns_detected": " | ".join(geog_cols["sa2_code"]),
        "phn_code_columns_detected": " | ".join(geog_cols["phn_code"]),
        "lga_code_columns_detected": " | ".join(geog_cols["lga_code"]),
        "best_target_master_key": best_key,
        "best_source_key_column": best_col,
        "best_geography_match_rate_sample": best_rate,
        "candidate_numeric_indicator_columns_sample": len(numeric_profile),
        "top_priority_indicator_terms": ";".join(sorted(set(numeric_profile.head(10).get("priority_domain_hint", pd.Series(dtype=str)).astype(str)))) if not numeric_profile.empty else "",
        "recommended_action": action,
        "notes": recommended_notes(validation_category, action),
    })

    return validation_rows, key_rows, indicator_rows


def recommended_notes(category: str, action: str) -> str:
    if "direct_sa2" in action:
        return "Direct SA2 candidate. Validate indicator definitions and ASGS year before integration."
    if "phn" in action:
        return "PHN context candidate. Confirm PHIDU PHN boundary year aligns with PHN 2017 or add PHN 2023 context before joining."
    if "lga" in action:
        return "LGA context candidate. Join to dominant LGA only with area-share caveat; avoid treating as exact SA2 exposure."
    if "review" in action:
        return "Some key matching found, but match rate is below the safe threshold. Inspect key audit before using."
    return "No safe key match. Hold as context only."


def write_csv(df: pd.DataFrame, path: Path, logger: Logger) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    logger.info(f"Writing CSV: {path}")
    df.to_csv(path, index=False, encoding="utf-8-sig", quoting=csv.QUOTE_MINIMAL)


def write_methodology(path: Path, validation: pd.DataFrame, logger: Logger) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# PHIDU Social Health Atlas join candidate validation v10",
        "",
        f"Generated: {datetime.now().isoformat(timespec='seconds')}",
        "",
        "This validation step follows the PHIDU v09 inventory. It does not join PHIDU data into the SA2 master.",
        "",
        "## Method",
        "",
        "The script reads the PHIDU join-readiness audit, samples candidate SA2, PHN and LGA sheets, detects geography key columns, compares those keys with the v08 master, and inventories numeric indicator columns for prioritisation.",
        "",
        "## Decision rule",
        "",
        "A candidate is treated as join-ready only if geography key coverage is high and the join geography is methodologically acceptable. PHN and LGA candidates remain context candidates until boundary year and area-share issues are resolved.",
        "",
        "## Summary",
        "",
    ]
    if not validation.empty:
        summary = validation["recommended_action"].value_counts(dropna=False).reset_index()
        summary.columns = ["recommended_action", "sheet_count"]
        lines.append(summary.to_markdown(index=False))
    else:
        lines.append("No candidate sheets were validated.")
    lines.append("")
    lines.append("## Key caution")
    lines.append("")
    lines.append("Do not add broad PHIDU workbooks to the modelling table without indicator selection. PHIDU contains overlapping demographic, health-status, mortality, service-use and health-system indicators at mixed geographies.")
    path.write_text("\n".join(lines), encoding="utf-8")
    logger.info(f"Writing methodology note: {path}")


def notify(success: bool, title: str, detail: str = "") -> None:
    try:
        if sys.platform.startswith("win"):
            import winsound
            winsound.MessageBeep(winsound.MB_ICONASTERISK if success else winsound.MB_ICONHAND)
            msg = f"{title}\n{detail}"[:900].replace("'", "’")
            icon = 64 if success else 16
            ps = "$wshell = New-Object -ComObject WScript.Shell; " + f"$null = $wshell.Popup('{msg}', 12, '{title}', {icon})"
            subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=17, check=False)
        else:
            print("\a", end="", flush=True)
    except Exception:
        pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate PHIDU join candidates before SA2 master integration.")
    parser.add_argument("--project-root", default=str(DEFAULT_PROJECT_ROOT))
    parser.add_argument("--base-master", default=str(DEFAULT_MASTER))
    parser.add_argument("--readiness-audit", default=str(DEFAULT_READINESS))
    parser.add_argument("--max-sheets-per-category", type=int, default=DEFAULT_MAX_SHEETS_PER_CATEGORY)
    parser.add_argument("--sample-rows", type=int, default=DEFAULT_SAMPLE_ROWS)
    parser.add_argument("--debug", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    root = Path(args.project_root)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    logger = Logger(root / "outputs" / "logs" / f"20_validate_phidu_join_candidates_{ts}.log", debug_enabled=args.debug)

    logger.info("PHIDU join-candidate validation v10")
    logger.info(f"Project root: {root}")
    logger.info(f"Log path: {logger.path}")

    master_path = Path(args.base_master)
    if not master_path.is_absolute():
        master_path = root / master_path
    readiness_path = Path(args.readiness_audit)
    if not readiness_path.is_absolute():
        readiness_path = root / readiness_path

    master = read_master(master_path, logger)
    master_keys = build_master_key_sets(master)
    logger.info("Master key availability: " + ", ".join(f"{k}={len(v)}" for k, v in master_keys.items()))

    readiness = load_readiness(readiness_path, logger)
    candidates = classify_candidate_rows(readiness)
    targets = select_validation_targets(candidates, args.max_sheets_per_category)
    logger.info(f"Validation targets selected: {len(targets):,}")
    if not targets.empty:
        logger.info("Target categories:\n" + targets["validation_category"].value_counts().to_string())

    validation_rows: list[dict] = []
    key_rows: list[dict] = []
    indicator_rows: list[dict] = []

    for i, row in targets.iterrows():
        logger.info(f"Validating target {i+1}/{len(targets)}: {row.get(row['_workbook_col'], '')} :: {row.get(row['_sheet_col'], '')} [{row['validation_category']}]")
        v, k, ind = validate_target(row, master_keys, args.sample_rows, logger)
        validation_rows.extend(v)
        key_rows.extend(k)
        indicator_rows.extend(ind)

    validation_df = pd.DataFrame(validation_rows)
    key_df = pd.DataFrame(key_rows)
    indicator_df = pd.DataFrame(indicator_rows)

    if not indicator_df.empty:
        indicator_df = indicator_df.sort_values(["priority_score", "geography_match_rate_sample", "non_missing_count_sample"], ascending=[False, False, False])

    run_audit = pd.DataFrame([
        {"check_name": "base_master_file", "value": str(master_path), "status": "info", "notes": "v08 master used for key validation."},
        {"check_name": "base_master_rows", "value": len(master), "status": "pass" if len(master) == 2472 else "review", "notes": "Expected SA2 row count is 2472."},
        {"check_name": "readiness_audit_file", "value": str(readiness_path), "status": "info", "notes": "PHIDU v09 join-readiness audit."},
        {"check_name": "readiness_rows", "value": len(readiness), "status": "info", "notes": "Rows in v09 readiness audit."},
        {"check_name": "validation_targets", "value": len(targets), "status": "pass" if len(targets) > 0 else "review", "notes": "Candidate sheets sampled for key validation."},
        {"check_name": "validated_sheets", "value": len(validation_df), "status": "info", "notes": "Validated sheet records written."},
        {"check_name": "indicator_shortlist_rows", "value": len(indicator_df), "status": "info", "notes": "Candidate numeric indicator rows found in sampled sheets."},
    ])

    out_audit = root / "outputs" / "audits"
    out_docs = root / "docs" / "source_registers"
    out_methods = root / "docs" / "methodology"

    write_csv(validation_df, out_audit / "phidu_join_candidate_validation_v10.csv", logger)
    write_csv(key_df, out_audit / "phidu_geography_key_validation_v10.csv", logger)
    write_csv(indicator_df, out_audit / "phidu_indicator_priority_shortlist_v10.csv", logger)
    write_csv(run_audit, out_audit / "phidu_validation_run_audit_v10.csv", logger)
    write_csv(validation_df, out_docs / "phidu_join_candidate_validation_register_v10.csv", logger)
    write_methodology(out_methods / "phidu_join_candidate_validation_note_v10.md", validation_df, logger)

    logger.info("PHIDU validation complete.")
    if not validation_df.empty:
        logger.info("Recommended actions:\n" + validation_df["recommended_action"].value_counts(dropna=False).to_string())
    logger.info("Next action: review phidu_join_candidate_validation_v10.csv and phidu_indicator_priority_shortlist_v10.csv before requesting any PHIDU join script.")
    notify(True, "PHIDU validation completed", f"Targets: {len(targets)}; indicators: {len(indicator_df)}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001
        notify(False, "PHIDU validation failed", f"{type(exc).__name__}: {exc}")
        raise
