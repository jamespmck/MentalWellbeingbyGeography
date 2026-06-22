#!/usr/bin/env python3
"""
35_process_native_geography_all_sources.py

Purpose
-------
Create first-pass native-geography processed tables for all acquired source families in the
MentalWellbeingByGeography project, without forcing higher-level geographies into the SA2 master.

The script is intentionally conservative:
- cleanly copies/standardises already-processed native source tables where they exist;
- extracts workbook/CSV/ZIP tabular sources into native-geography staging tables;
- holds PDF/report/page-only or ambiguous sources as context rather than inventing a key;
- writes audits showing what was processed, held, failed, or needs manual review.

It does NOT build scoped masters and does NOT join higher-level values to SA2.

Usage
-----
cd "D:\\Good Measure\\MentalWellbeingbyGeography"
python "D:\\Good Measure\\MentalWellbeingbyGeography\\scripts\\04_integration\\35_process_native_geography_all_sources.py" --debug

Optional:
python "...\\35_process_native_geography_all_sources.py" --debug --max-rows-per-table 0

Set --max-rows-per-table to a positive integer for test runs. Default 0 means no row cap.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import shutil
import sys
import zipfile
from collections import Counter, defaultdict
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional, Any

import pandas as pd

VERSION = "v20"
SCRIPT_NAME = "35_process_native_geography_all_sources"

# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------


def now_stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def find_project_root(start: Optional[Path] = None) -> Path:
    if start is None:
        start = Path.cwd()
    start = start.resolve()
    candidates = [start] + list(start.parents)
    for p in candidates:
        if (p / "data").exists() and (p / "scripts").exists():
            return p
    # Fallback for script saved under scripts/04_integration
    try:
        here = Path(__file__).resolve()
        for p in [here.parent] + list(here.parents):
            if (p / "data").exists() and (p / "scripts").exists():
                return p
    except Exception:
        pass
    return start


def mkdir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def rel(path: Optional[Path], root: Path) -> str:
    if path is None:
        return ""
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except Exception:
        return str(path)


def sha256_file(path: Path, block_size: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(block_size), b""):
            h.update(block)
    return h.hexdigest()


def safe_name(value: str, max_len: int = 140) -> str:
    value = str(value or "").strip().lower()
    value = value.replace("&", " and ")
    value = re.sub(r"[^a-z0-9]+", "_", value)
    value = re.sub(r"_+", "_", value).strip("_")
    if not value:
        value = "unnamed"
    return value[:max_len].strip("_")


def uniquify_columns(cols: Iterable[Any]) -> list[str]:
    seen: dict[str, int] = {}
    out: list[str] = []
    for c in cols:
        name = safe_name(str(c))
        if name in ("nan", "none", "unnamed") or name.startswith("unnamed_"):
            name = "col"
        count = seen.get(name, 0)
        if count:
            out.append(f"{name}_{count+1}")
        else:
            out.append(name)
        seen[name] = count + 1
    return out


def write_table(df: pd.DataFrame, out_base: Path, write_parquet: bool = True) -> tuple[Path, Optional[Path], str]:
    mkdir(out_base.parent)
    csv_path = out_base.with_suffix(".csv")
    df.to_csv(csv_path, index=False)
    pq_path: Optional[Path] = None
    pq_status = "not_written"
    if write_parquet:
        try:
            pq_path = out_base.with_suffix(".parquet")
            df.to_parquet(pq_path, index=False)
            pq_status = "written"
        except Exception as e:
            pq_path = None
            pq_status = f"parquet_failed: {type(e).__name__}: {e}"
    return csv_path, pq_path, pq_status


def read_csv_robust(path: Path, nrows: Optional[int] = None) -> pd.DataFrame:
    encodings = ["utf-8-sig", "utf-8", "cp1252", "latin1"]
    last_error = None
    for enc in encodings:
        try:
            return pd.read_csv(path, nrows=nrows, encoding=enc, low_memory=False)
        except Exception as e:
            last_error = e
    raise last_error if last_error else RuntimeError(f"Could not read {path}")


def read_any_processed_table(path: Path) -> pd.DataFrame:
    if path.suffix.lower() == ".parquet":
        return pd.read_parquet(path)
    if path.suffix.lower() == ".csv":
        return read_csv_robust(path)
    if path.suffix.lower() in [".xlsx", ".xls"]:
        return pd.read_excel(path)
    raise ValueError(f"Unsupported processed table extension: {path.suffix}")


# ---------------------------------------------------------------------------
# Geography detection
# ---------------------------------------------------------------------------

GEOG_ORDER = ["sa2", "sa3", "sa4", "lga", "phn", "state", "ndis_service_area", "unknown"]


def detect_geography_from_columns(columns: Iterable[str]) -> tuple[str, list[str], str]:
    cols = [safe_name(c) for c in columns]
    joined = " ".join(cols)
    key_cols: list[str] = []

    patterns = {
        "sa2": [r"\bsa2\b", r"sa2_code", r"sa2_maincode", r"sa2_2021", r"sa2_2016"],
        "sa3": [r"\bsa3\b", r"sa3_code", r"sa3_2021", r"sa3_2016"],
        "sa4": [r"\bsa4\b", r"sa4_code", r"sa4_2021", r"sa4_2016"],
        "lga": [r"\blga\b", r"local_government", r"lga_code", r"lga_name"],
        "phn": [r"\bphn\b", r"primary_health_network", r"phn_code", r"phn_name"],
        "state": [r"\bstate\b", r"territory", r"jurisdiction", r"ste_code", r"ste_name", r"aust"],
        "ndis_service_area": [r"service_district", r"service_area", r"ndis_region", r"participant_service"],
    }

    scores: dict[str, int] = {}
    for geog, pats in patterns.items():
        score = 0
        for pat in pats:
            if re.search(pat, joined):
                score += 1
        for c in cols:
            if any(re.search(pat, c) for pat in pats):
                key_cols.append(c)
        scores[geog] = score

    # Prefer the most granular recognised geography where tied enough.
    for geog in ["sa2", "sa3", "sa4", "lga", "phn", "ndis_service_area", "state"]:
        if scores.get(geog, 0) > 0:
            return geog, sorted(set(key_cols)), f"column_match:{scores}"
    return "unknown", [], f"no_column_match:{scores}"


def detect_geography_from_values(df: pd.DataFrame, max_rows: int = 200) -> tuple[str, list[str], str]:
    sample = df.head(max_rows).copy()
    cols = [safe_name(c) for c in sample.columns]
    value_scores: Counter[str] = Counter()
    key_cols: list[str] = []

    # Code patterns are not perfect, but useful for staging.
    code_patterns = {
        "sa2": re.compile(r"^\d{9}$"),
        "sa3": re.compile(r"^\d{5}$"),
        "sa4": re.compile(r"^\d{3}$"),
        "lga": re.compile(r"^\d{5}$"),
        "state": re.compile(r"^[1-9]$"),
    }

    for raw_col, clean_col in zip(sample.columns, cols):
        vals = sample[raw_col].dropna().astype(str).str.strip()
        if vals.empty:
            continue
        # Need enough code-like values to matter.
        for geog, pat in code_patterns.items():
            n = vals.map(lambda x: bool(pat.match(x))).sum()
            if n >= max(3, min(10, len(vals) // 4)):
                value_scores[geog] += int(n)
                key_cols.append(clean_col)
        low_vals = " ".join(vals.head(50).str.lower().tolist())
        if "primary health network" in low_vals or re.search(r"\bphn\b", low_vals):
            value_scores["phn"] += 5
            key_cols.append(clean_col)
        if "local government" in low_vals or re.search(r"\blga\b", low_vals):
            value_scores["lga"] += 5
            key_cols.append(clean_col)
        if "state" in low_vals or "territory" in low_vals or "australia" in low_vals:
            value_scores["state"] += 3
            key_cols.append(clean_col)

    if value_scores:
        # Prefer column detection over value detection where possible outside this function.
        for geog in ["sa2", "sa3", "sa4", "lga", "phn", "ndis_service_area", "state"]:
            if value_scores.get(geog, 0) > 0:
                return geog, sorted(set(key_cols)), f"value_match:{dict(value_scores)}"
    return "unknown", [], "no_value_match"


def detect_geography(df: pd.DataFrame) -> tuple[str, list[str], str]:
    c_geog, c_keys, c_reason = detect_geography_from_columns(df.columns)
    if c_geog != "unknown":
        return c_geog, c_keys, c_reason
    v_geog, v_keys, v_reason = detect_geography_from_values(df)
    return v_geog, v_keys, v_reason


def geography_output_dir(root: Path, geog: str) -> Path:
    mapping = {
        "sa2": root / "data" / "processed" / "native" / "sa2",
        "sa3": root / "data" / "processed" / "native" / "sa3",
        "sa4": root / "data" / "processed" / "native" / "sa4",
        "lga": root / "data" / "processed" / "native" / "lga",
        "phn": root / "data" / "processed" / "native" / "phn",
        "state": root / "data" / "processed" / "native" / "state",
        "ndis_service_area": root / "data" / "processed" / "native" / "ndis_service_area",
        "unknown": root / "data" / "processed" / "native" / "unknown_review",
    }
    return mapping.get(geog, mapping["unknown"])


def add_source_metadata(df: pd.DataFrame, source_family: str, source_path: Path, root: Path, source_table: str, reference_period: str = "") -> pd.DataFrame:
    out = df.copy()
    out.insert(0, "source_reference_period", reference_period)
    out.insert(0, "source_table", source_table)
    out.insert(0, "source_path", rel(source_path, root))
    out.insert(0, "source_family", source_family)
    return out


# ---------------------------------------------------------------------------
# Source discovery
# ---------------------------------------------------------------------------

@dataclass
class SourceRecord:
    source_family: str
    path: Path
    source_name: str = ""
    source_url: str = ""
    record_origin: str = ""
    recommended_action: str = ""


def infer_source_family(path: Path, root: Path) -> str:
    s = str(path).replace("\\", "/").lower()
    name = path.name.lower()
    if "/data/raw/abs/census_2021_gcp_sa2/" in s:
        return "abs_census_2021_sa2"
    if "/data/raw/abs/geography/" in s:
        return "abs_geography_2021"
    if "/data/raw/abs/nsmhw/" in s:
        return "abs_nsmhw_sa2_modelled_estimates"
    if "/data/raw/abs/seifa/" in s:
        return "abs_seifa_2021"
    if "abs_homelessness" in s:
        return "abs_homelessness"
    if "/data/raw/aedc" in s:
        return "aedc_child_development"
    if "aihw" in s and "regional_profiles" in s:
        return "aihw_regional_profiles_sa3"
    if "aihw" in s and "regional_activity" in s:
        return "aihw_mental_health_regional_activity"
    if "aihw" in s and "mbs_primary" in s:
        return "aihw_mbs_primary_care_geography"
    if "aihw" in s and "specialist_homelessness" in s:
        return "aihw_specialist_homelessness_services"
    if "aihw" in s and "mental_health_data_tables" in s:
        return "aihw_mental_health_data_tables"
    if "phidu" in s:
        return "phidu_raw"
    if "ndis" in s or "ndia" in s:
        return "ndis_service_area_candidate"
    if "dss" in s or "social_security" in s:
        return "dss"
    if "state_health" in s or "lhd" in s or "hhs" in s:
        return "state_health_geography_inventory"
    if "bridge" in s or "concordance" in s:
        return "geography_bridges"
    return "unknown_source_family"


def load_freeze_manifest(root: Path) -> list[SourceRecord]:
    manifest = root / "outputs" / "audits" / "raw_acquisition_freeze_manifest_v15.csv"
    records: list[SourceRecord] = []
    if manifest.exists() and manifest.stat().st_size > 0:
        try:
            df = pd.read_csv(manifest)
            # Flexible column handling.
            path_cols = [c for c in ["path", "raw_file_path", "active_raw_file_path", "file_path", "absolute_path"] if c in df.columns]
            rel_cols = [c for c in ["relative_path", "active_relative_path"] if c in df.columns]
            fam_col = "source_family" if "source_family" in df.columns else None
            name_col = "source_name" if "source_name" in df.columns else None
            url_col = "source_url" if "source_url" in df.columns else ("url" if "url" in df.columns else None)
            origin = "raw_acquisition_freeze_manifest_v15"
            for _, row in df.iterrows():
                p: Optional[Path] = None
                for c in path_cols:
                    val = str(row.get(c, "") or "").strip()
                    if val and val.lower() != "nan":
                        p = Path(val)
                        break
                if p is None:
                    for c in rel_cols:
                        val = str(row.get(c, "") or "").strip()
                        if val and val.lower() != "nan":
                            p = root / val
                            break
                if p is None:
                    continue
                if not p.exists() or not p.is_file():
                    continue
                fam = str(row.get(fam_col, "") if fam_col else "").strip() or infer_source_family(p, root)
                records.append(SourceRecord(
                    source_family=fam,
                    path=p,
                    source_name=str(row.get(name_col, "") if name_col else ""),
                    source_url=str(row.get(url_col, "") if url_col else ""),
                    record_origin=origin,
                ))
        except Exception as e:
            print(f"[WARN] Could not read freeze manifest {manifest}: {type(e).__name__}: {e}")
    return records


def scan_active_raw(root: Path) -> list[SourceRecord]:
    raw = root / "data" / "raw"
    if not raw.exists():
        return []
    out: list[SourceRecord] = []
    allowed_ext = {".csv", ".xlsx", ".xls", ".zip", ".html", ".pdf", ".txt", ".download"}
    for p in raw.rglob("*"):
        if not p.is_file():
            continue
        parts_lower = [part.lower() for part in p.parts]
        if "_archive" in parts_lower or "archive" in parts_lower:
            continue
        if p.suffix.lower() not in allowed_ext and p.name.lower() != ".extracted":
            continue
        out.append(SourceRecord(
            source_family=infer_source_family(p, root),
            path=p,
            source_name=p.name,
            record_origin="active_raw_scan",
        ))
    return out


def dedupe_records(records: list[SourceRecord]) -> list[SourceRecord]:
    seen: set[str] = set()
    out: list[SourceRecord] = []
    for r in records:
        key = str(r.path.resolve()).lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


# ---------------------------------------------------------------------------
# Processors
# ---------------------------------------------------------------------------

@dataclass
class ProcessAudit:
    source_family: str
    source_path: str
    source_table: str
    native_geography: str
    status: str
    rows: int = 0
    columns: int = 0
    key_columns_detected: str = ""
    output_csv: str = ""
    output_parquet: str = ""
    parquet_status: str = ""
    reason: str = ""
    caveat: str = ""


def find_header_row(raw: pd.DataFrame) -> int:
    best_row = 0
    best_score = -1
    keywords = ["sa2", "sa3", "sa4", "lga", "phn", "state", "territory", "area", "code", "name", "year", "number", "count", "percent", "%", "rate"]
    for i in range(min(len(raw), 30)):
        vals = raw.iloc[i].dropna().astype(str).str.strip().tolist()
        if not vals:
            continue
        joined = " ".join(vals).lower()
        non_empty = len(vals)
        key_score = sum(1 for k in keywords if k in joined)
        score = key_score * 5 + non_empty
        if score > best_score:
            best_score = score
            best_row = i
    return best_row


def clean_dataframe(df: pd.DataFrame, max_rows: int = 0) -> pd.DataFrame:
    df = df.copy()
    df.columns = uniquify_columns(df.columns)
    # Drop completely empty rows/columns.
    df = df.dropna(axis=1, how="all")
    df = df.dropna(axis=0, how="all")
    # Strip strings lightly.
    for c in df.columns:
        if pd.api.types.is_object_dtype(df[c]):
            try:
                df[c] = df[c].astype(str).str.strip().replace({"nan": pd.NA, "None": pd.NA, "": pd.NA})
            except Exception:
                pass
    if max_rows and max_rows > 0 and len(df) > max_rows:
        df = df.head(max_rows).copy()
    return df


def process_dataframe(df: pd.DataFrame, record: SourceRecord, root: Path, source_table: str, out_stem: str, max_rows: int = 0, force_geog: Optional[str] = None) -> ProcessAudit:
    try:
        df = clean_dataframe(df, max_rows=max_rows)
        if df.empty:
            return ProcessAudit(record.source_family, rel(record.path, root), source_table, "unknown", "held_empty_table", reason="table empty after cleaning")
        geog, key_cols, reason = detect_geography(df)
        if force_geog:
            geog = force_geog
            reason = f"forced:{force_geog}; {reason}"
        out_df = add_source_metadata(df, record.source_family, record.path, root, source_table)
        out_dir = geography_output_dir(root, geog)
        out_base = out_dir / out_stem
        csv_path, pq_path, pq_status = write_table(out_df, out_base)
        status = "processed_native_table" if geog != "unknown" else "processed_unknown_geography_review"
        caveat = "Review key columns and suppression before modelling." if geog == "unknown" else "Native geography staged; validate key uniqueness and definitions before scoped master build."
        return ProcessAudit(
            source_family=record.source_family,
            source_path=rel(record.path, root),
            source_table=source_table,
            native_geography=geog,
            status=status,
            rows=len(out_df),
            columns=len(out_df.columns),
            key_columns_detected=";".join(key_cols),
            output_csv=rel(csv_path, root),
            output_parquet=rel(pq_path, root) if pq_path else "",
            parquet_status=pq_status,
            reason=reason,
            caveat=caveat,
        )
    except Exception as e:
        return ProcessAudit(record.source_family, rel(record.path, root), source_table, "unknown", "failed", reason=f"{type(e).__name__}: {e}")


def process_csv_file(record: SourceRecord, root: Path, max_rows: int = 0) -> list[ProcessAudit]:
    try:
        df = read_csv_robust(record.path, nrows=(max_rows if max_rows > 0 else None))
        stem = f"{safe_name(record.source_family)}__{safe_name(record.path.stem)}__{VERSION}"
        return [process_dataframe(df, record, root, record.path.name, stem, max_rows=0)]
    except Exception as e:
        return [ProcessAudit(record.source_family, rel(record.path, root), record.path.name, "unknown", "failed", reason=f"{type(e).__name__}: {e}")]


def process_excel_file(record: SourceRecord, root: Path, max_rows: int = 0, max_sheets: int = 80) -> list[ProcessAudit]:
    audits: list[ProcessAudit] = []
    try:
        xls = pd.ExcelFile(record.path)
    except Exception as e:
        return [ProcessAudit(record.source_family, rel(record.path, root), "(workbook_open)", "unknown", "failed", reason=f"{type(e).__name__}: {e}")]

    sheet_names = xls.sheet_names[:max_sheets]
    for sheet in sheet_names:
        if safe_name(sheet) in {"contents", "readme", "metadata", "user_guide", "notes"}:
            # Still register, but do not emit a data table.
            audits.append(ProcessAudit(record.source_family, rel(record.path, root), sheet, "unknown", "held_metadata_sheet", reason="metadata/contents/readme sheet"))
            continue
        try:
            raw = pd.read_excel(record.path, sheet_name=sheet, header=None, nrows=80)
            header_row = find_header_row(raw)
            df = pd.read_excel(record.path, sheet_name=sheet, header=header_row)
            stem = f"{safe_name(record.source_family)}__{safe_name(record.path.stem)}__{safe_name(sheet)}__{VERSION}"
            audits.append(process_dataframe(df, record, root, sheet, stem, max_rows=max_rows))
        except Exception as e:
            audits.append(ProcessAudit(record.source_family, rel(record.path, root), sheet, "unknown", "failed", reason=f"{type(e).__name__}: {e}"))
    return audits


def process_zip_file(record: SourceRecord, root: Path, max_rows: int = 0) -> list[ProcessAudit]:
    audits: list[ProcessAudit] = []
    extract_root = root / "data" / "interim" / "native_processing_extracts" / safe_name(record.path.stem)
    mkdir(extract_root)
    try:
        with zipfile.ZipFile(record.path) as z:
            members = [m for m in z.namelist() if not m.endswith("/")]
            for m in members:
                ext = Path(m).suffix.lower()
                if ext not in [".csv", ".xlsx", ".xls"]:
                    audits.append(ProcessAudit(record.source_family, rel(record.path, root), m, "unknown", "held_zip_non_table_member", reason=f"extension {ext} not processed"))
                    continue
                target = extract_root / Path(m).name
                if not target.exists():
                    with z.open(m) as src, target.open("wb") as dst:
                        shutil.copyfileobj(src, dst)
                member_record = SourceRecord(record.source_family, target, source_name=m, source_url=record.source_url, record_origin=f"zip_member:{rel(record.path, root)}")
                if ext == ".csv":
                    audits.extend(process_csv_file(member_record, root, max_rows=max_rows))
                else:
                    audits.extend(process_excel_file(member_record, root, max_rows=max_rows))
    except Exception as e:
        audits.append(ProcessAudit(record.source_family, rel(record.path, root), "(zip_open)", "unknown", "failed", reason=f"{type(e).__name__}: {e}"))
    return audits


def process_hold_file(record: SourceRecord, root: Path, status: str, reason: str, caveat: str) -> ProcessAudit:
    return ProcessAudit(
        source_family=record.source_family,
        source_path=rel(record.path, root),
        source_table=record.path.name,
        native_geography="hold",
        status=status,
        rows=0,
        columns=0,
        reason=reason,
        caveat=caveat,
    )


# ---------------------------------------------------------------------------
# Already-processed native tables
# ---------------------------------------------------------------------------

PROCESSED_NATIVE_CANDIDATES = [
    # SA2 processed source tables
    ("abs_sa2_spine_2021", "sa2", ["data/processed/spines/sa2_2021_spine.parquet", "data/processed/spines/sa2_2021_spine.csv"]),
    ("abs_seifa_sa2_2021", "sa2", ["data/processed/sources/sa2_seifa_2021.parquet", "data/processed/sources/sa2_seifa_2021.csv"]),
    ("abs_remoteness_sa2_2021", "sa2", ["data/processed/sources/sa2_remoteness_2021.parquet", "data/processed/sources/sa2_remoteness_2021.csv"]),
    ("abs_nsmhw_sa2_modelled_2020_22", "sa2", ["data/processed/sources/sa2_nsmhw_modelled_estimates_2020_22_wide.parquet", "data/processed/sources/sa2_nsmhw_modelled_estimates_2020_22_wide.csv"]),
    ("abs_census_quickstats_sa2_2021", "sa2", ["data/processed/sources/sa2_census_2021_quickstats_variables.parquet", "data/processed/sources/sa2_census_2021_quickstats_variables.csv"]),
    ("dss_social_security_sa2_2021", "sa2", ["data/processed/sources/dss_sa2_social_security_2021_12_wide.parquet", "data/processed/sources/dss_sa2_social_security_2021_12_wide.csv"]),
    ("housing_quickstats_sa2_derived", "sa2", ["data/processed/sources/sa2_housing_quickstats_clean_context_v08.parquet", "data/processed/sources/sa2_housing_quickstats_clean_context_v08.csv"]),
    # SA3 tables
    ("aihw_regional_profiles_sa3_2021_22", "sa3", ["data/processed/sources/sa3_aihw_regional_profiles_selected_measures_2021_22.parquet", "data/processed/sources/sa3_aihw_regional_profiles_selected_measures_2021_22.csv"]),
    ("aihw_regional_profiles_sa3_long_2021_22", "sa3", ["data/processed/sources/sa3_aihw_regional_profiles_long_2021_22.parquet", "data/processed/sources/sa3_aihw_regional_profiles_long_2021_22.csv"]),
    # LGA/PHN PHIDU tables
    ("phidu_lga_context_selected_v12", "lga", ["data/processed/sources/phidu_lga_context_selected_wide_v12.parquet", "data/processed/sources/phidu_lga_context_selected_wide_v12.csv"]),
    ("phidu_phn_context_selected_v12", "phn", ["data/processed/sources/phidu_phn_context_selected_wide_v12.parquet", "data/processed/sources/phidu_phn_context_selected_wide_v12.csv"]),
    # NDIA hold-aside if present
    ("ndia_public_poc_sa2_context_holdaside", "sa2", ["data/processed/sources/ndia_public_poc_sa2_context_wide.parquet", "data/processed/sources/ndia_public_poc_sa2_context_wide.csv"]),
]


def copy_processed_native_tables(root: Path, max_rows: int = 0) -> list[ProcessAudit]:
    audits: list[ProcessAudit] = []
    for label, geog, candidate_rel_paths in PROCESSED_NATIVE_CANDIDATES:
        source_path = None
        for relp in candidate_rel_paths:
            p = root / relp
            if p.exists() and p.stat().st_size > 0:
                source_path = p
                break
        if source_path is None:
            audits.append(ProcessAudit(label, "", label, geog, "not_found_optional_processed_source", reason="candidate processed source file not found"))
            continue
        try:
            df = read_any_processed_table(source_path)
            if max_rows and max_rows > 0 and len(df) > max_rows:
                df = df.head(max_rows).copy()
            df = clean_dataframe(df, max_rows=0)
            # Preserve table mostly as-is but add metadata.
            rec = SourceRecord(label, source_path, record_origin="processed_source_copy")
            out_df = add_source_metadata(df, label, source_path, root, label)
            out_base = geography_output_dir(root, geog) / f"{safe_name(label)}__native_copy__{VERSION}"
            csv_path, pq_path, pq_status = write_table(out_df, out_base)
            audits.append(ProcessAudit(
                source_family=label,
                source_path=rel(source_path, root),
                source_table=label,
                native_geography=geog,
                status="copied_existing_processed_native_table",
                rows=len(out_df),
                columns=len(out_df.columns),
                key_columns_detected=";".join(detect_geography(df)[1]),
                output_csv=rel(csv_path, root),
                output_parquet=rel(pq_path, root) if pq_path else "",
                parquet_status=pq_status,
                reason="existing processed source table copied into native geography layer",
                caveat="Review lineage and key uniqueness before scoped master build.",
            ))
        except Exception as e:
            audits.append(ProcessAudit(label, rel(source_path, root), label, geog, "failed", reason=f"{type(e).__name__}: {e}"))
    return audits


# ---------------------------------------------------------------------------
# Main orchestration
# ---------------------------------------------------------------------------


def should_process_record(record: SourceRecord) -> tuple[bool, str, str]:
    p = record.path
    ext = p.suffix.lower()
    name = p.name.lower()
    fam = record.source_family

    if name == ".extracted":
        return False, "held_marker_file", "extraction marker; not data"
    if ext in [".pdf"]:
        return False, "held_report_pdf_context", "PDF/report context; not automatically parsed into model-ready table"
    if ext in [".html", ".download"]:
        return False, "held_source_page_snapshot", "HTML/download endpoint snapshot; provenance only unless separately parsed"
    if ext in [".txt"] and "readme" in name:
        return False, "held_readme_metadata", "README/metadata; not source table"
    if ext not in [".csv", ".xlsx", ".xls", ".zip"]:
        return False, "held_unsupported_extension", f"unsupported extension {ext}"
    # Avoid re-processing huge 340 individual AIHW regional profile raw extracts if already processed source exists.
    if fam == "aihw_regional_profiles_sa3" and "individual" in str(p).lower():
        return False, "held_already_processed_regional_profile_extract", "individual Tableau extract; use processed SA3 AIHW source table"
    # ABS geography raw files are reference sources; copy processed spine/bridges separately, don't dump all raw workbooks into native table layer.
    if fam in ["abs_geography_2021", "abs_seifa_2021", "abs_nsmhw_sa2_modelled_estimates", "abs_census_2021_sa2"]:
        return False, "held_raw_foundation_source", "foundation raw source already processed by dedicated scripts or retained as raw provenance"
    return True, "", ""


def process_records(records: list[SourceRecord], root: Path, max_rows: int = 0, debug: bool = False) -> list[ProcessAudit]:
    audits: list[ProcessAudit] = []
    total = len(records)
    for i, rec in enumerate(records, start=1):
        p = rec.path
        if debug:
            print(f"[{i}/{total}] {rec.source_family}: {rel(p, root)}")
        ok, hold_status, hold_reason = should_process_record(rec)
        if not ok:
            audits.append(process_hold_file(
                rec,
                root,
                hold_status,
                hold_reason,
                caveat="Held during automated native processing; review manually if needed.",
            ))
            continue
        ext = p.suffix.lower()
        if ext == ".csv":
            audits.extend(process_csv_file(rec, root, max_rows=max_rows))
        elif ext in [".xlsx", ".xls"]:
            audits.extend(process_excel_file(rec, root, max_rows=max_rows))
        elif ext == ".zip":
            audits.extend(process_zip_file(rec, root, max_rows=max_rows))
        else:
            audits.append(process_hold_file(rec, root, "held_unhandled", f"unhandled extension {ext}", "Review manually."))
    return audits


def summarise_audits(audits: list[ProcessAudit]) -> pd.DataFrame:
    df = pd.DataFrame([asdict(a) for a in audits])
    if df.empty:
        return pd.DataFrame()
    summary = (
        df.groupby(["source_family", "native_geography", "status"], dropna=False)
        .agg(tables=("source_table", "count"), rows=("rows", "sum"), columns_max=("columns", "max"))
        .reset_index()
        .sort_values(["source_family", "native_geography", "status"])
    )
    return summary


def build_geography_table_index(root: Path) -> pd.DataFrame:
    native = root / "data" / "processed" / "native"
    rows = []
    if native.exists():
        for p in native.rglob("*.csv"):
            if p.name.endswith("_register.csv"):
                continue
            try:
                nrows = sum(1 for _ in p.open("r", encoding="utf-8", errors="ignore")) - 1
            except Exception:
                nrows = None
            rows.append({
                "native_geography": p.parent.name,
                "relative_path": rel(p, root),
                "file_size_mb": round(p.stat().st_size / (1024 * 1024), 6),
                "estimated_rows": nrows,
            })
    return pd.DataFrame(rows).sort_values(["native_geography", "relative_path"]) if rows else pd.DataFrame(columns=["native_geography", "relative_path", "file_size_mb", "estimated_rows"])


def write_method_note(root: Path, stamp: str, audits_df: pd.DataFrame, summary_df: pd.DataFrame) -> Path:
    note_path = root / "docs" / "methodology" / f"native_geography_processing_note_{VERSION}_{stamp}.md"
    mkdir(note_path.parent)
    lines = [
        "# Native geography processing note",
        "",
        f"Run timestamp: {stamp}",
        f"Version: {VERSION}",
        "",
        "This run processed acquired raw and existing processed source files into native-geography staging tables.",
        "It did not build scoped masters and did not join higher-level values to SA2.",
        "",
        "## Interpretation rules",
        "",
        "- Tables in `data/processed/native/sa2` can be candidates for the SA2 master after leakage and outcome-source review.",
        "- Tables in `sa3`, `lga`, `phn`, `sa4` and `state` remain native higher-level context until joined through the foreign-key master.",
        "- Tables in `unknown_review` require manual key review before any scoped master inclusion.",
        "- Held PDFs, HTML pages, README files and raw foundation workbooks remain provenance/context, not model inputs.",
        "",
        "## Run counts",
        "",
        f"Audit rows: {len(audits_df)}",
    ]
    if not summary_df.empty:
        lines += ["", "## Summary by source/geography/status", "", summary_df.to_markdown(index=False)]
    note_path.write_text("\n".join(lines), encoding="utf-8")
    return note_path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", default="", help="Project root. Defaults to current directory / detected root.")
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--max-rows-per-table", type=int, default=0, help="Optional cap for test runs. 0 means no cap.")
    args = parser.parse_args()

    root = Path(args.project_root).resolve() if args.project_root else find_project_root()
    stamp = now_stamp()

    # Directories.
    for sub in [
        "data/processed/native/sa2", "data/processed/native/sa3", "data/processed/native/sa4",
        "data/processed/native/lga", "data/processed/native/phn", "data/processed/native/state",
        "data/processed/native/ndis_service_area", "data/processed/native/unknown_review",
        "data/interim/native_processing_extracts", "outputs/audits", "outputs/logs",
        "docs/source_registers", "docs/methodology", "docs/data_dictionaries",
    ]:
        mkdir(root / sub)

    log_path = root / "outputs" / "logs" / f"{SCRIPT_NAME}_{stamp}.log"

    def log(msg: str) -> None:
        print(msg)
        with log_path.open("a", encoding="utf-8") as f:
            f.write(msg + "\n")

    log(f"Native geography processing {VERSION}")
    log(f"Project root: {root}")
    log(f"Log path: {log_path}")
    log(f"Max rows per table: {args.max_rows_per_table if args.max_rows_per_table else 'none'}")

    records = dedupe_records(load_freeze_manifest(root) + scan_active_raw(root))
    log(f"Source records found: {len(records)}")

    processed_copy_audits = copy_processed_native_tables(root, max_rows=args.max_rows_per_table)
    log(f"Existing processed native tables checked: {len(processed_copy_audits)}")

    raw_audits = process_records(records, root, max_rows=args.max_rows_per_table, debug=args.debug)
    log(f"Raw/native extraction audit rows: {len(raw_audits)}")

    all_audits = processed_copy_audits + raw_audits
    audits_df = pd.DataFrame([asdict(a) for a in all_audits])
    summary_df = summarise_audits(all_audits)
    index_df = build_geography_table_index(root)

    # Write outputs.
    audit_path = root / "outputs" / "audits" / f"native_geography_processing_audit_{VERSION}_{stamp}.csv"
    latest_audit_path = root / "outputs" / "audits" / f"native_geography_processing_audit_{VERSION}.csv"
    summary_path = root / "outputs" / "audits" / f"native_geography_processing_summary_{VERSION}_{stamp}.csv"
    latest_summary_path = root / "outputs" / "audits" / f"native_geography_processing_summary_{VERSION}.csv"
    table_index_path = root / "outputs" / "audits" / f"native_geography_table_index_{VERSION}_{stamp}.csv"
    latest_table_index_path = root / "outputs" / "audits" / f"native_geography_table_index_{VERSION}.csv"
    register_path = root / "docs" / "source_registers" / f"native_geography_processed_table_register_{VERSION}.csv"

    audits_df.to_csv(audit_path, index=False)
    audits_df.to_csv(latest_audit_path, index=False)
    summary_df.to_csv(summary_path, index=False)
    summary_df.to_csv(latest_summary_path, index=False)
    index_df.to_csv(table_index_path, index=False)
    index_df.to_csv(latest_table_index_path, index=False)
    index_df.to_csv(register_path, index=False)
    note_path = write_method_note(root, stamp, audits_df, summary_df)

    # Run metrics.
    run_audit = pd.DataFrame([
        {"metric": "source_records_found", "value": len(records)},
        {"metric": "processed_copy_audit_rows", "value": len(processed_copy_audits)},
        {"metric": "raw_extraction_audit_rows", "value": len(raw_audits)},
        {"metric": "total_audit_rows", "value": len(all_audits)},
        {"metric": "processed_native_table_rows", "value": int((audits_df["status"].astype(str).str.contains("processed|copied", case=False, na=False)).sum()) if not audits_df.empty else 0},
        {"metric": "failed_rows", "value": int((audits_df["status"] == "failed").sum()) if not audits_df.empty else 0},
        {"metric": "unknown_review_tables", "value": int((audits_df["native_geography"] == "unknown").sum()) if not audits_df.empty else 0},
        {"metric": "native_table_index_rows", "value": len(index_df)},
    ])
    run_audit_path = root / "outputs" / "audits" / f"native_geography_processing_run_audit_{VERSION}_{stamp}.csv"
    latest_run_audit_path = root / "outputs" / "audits" / f"native_geography_processing_run_audit_{VERSION}.csv"
    run_audit.to_csv(run_audit_path, index=False)
    run_audit.to_csv(latest_run_audit_path, index=False)

    log("Native geography processing complete.")
    log(f"Audit: {audit_path}")
    log(f"Summary: {summary_path}")
    log(f"Table index: {table_index_path}")
    log(f"Register: {register_path}")
    log(f"Note: {note_path}")
    log("Run audit:")
    log(run_audit.to_string(index=False))

    if not summary_df.empty:
        log("Summary preview:")
        log(summary_df.head(80).to_string(index=False))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
