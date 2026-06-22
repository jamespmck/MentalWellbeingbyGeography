"""
Build a source-selection register for the public NDIA proof-of-concept layer.

Purpose
-------
This script stages the earliest publicly available NDIA files already downloaded
into this project. It does not join NDIA data to the SA2 master. It creates a
register that makes the time-alignment limitation explicit.

Project rule
------------
NDIA public files are a proof-of-concept service-system context layer.
They are excluded from the primary 2021-aligned model unless explicitly used in
separate sensitivity or demonstration analysis.

Inputs scanned
--------------
1. data/raw/ndia/public_data_downloads/downloads
2. data/raw/ndia/explore_data_tool_capture/**/downloads
3. data/raw/ndia/explore_data_tool_historical_probe/**
4. existing NDIA audit files under outputs/audits, when present

Outputs
-------
1. data/raw/ndia/public_poc_selected/*
2. outputs/audits/ndia_public_poc_source_inventory.csv
3. outputs/audits/ndia_public_poc_selected_sources.csv
4. outputs/audits/ndia_public_poc_excluded_sources.csv
5. docs/source_registers/ndia_public_poc_source_selection_register.csv
6. docs/methodology/ndia_public_poc_context_layer_note.md
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

PROJECT_ROOT = Path(r"D:\Good Measure\MentalWellbeingbyGeography")

RAW_NDIA = PROJECT_ROOT / "data" / "raw" / "ndia"
PUBLIC_DOWNLOADS = RAW_NDIA / "public_data_downloads" / "downloads"
EXPLORE_CAPTURE_ROOT = RAW_NDIA / "explore_data_tool_capture"
HISTORICAL_PROBE_ROOT = RAW_NDIA / "explore_data_tool_historical_probe"
SELECTED_DIR = RAW_NDIA / "public_poc_selected"

AUDIT_DIR = PROJECT_ROOT / "outputs" / "audits"
SOURCE_REGISTER_DIR = PROJECT_ROOT / "docs" / "source_registers"
METHODOLOGY_DIR = PROJECT_ROOT / "docs" / "methodology"

INVENTORY_OUT = AUDIT_DIR / "ndia_public_poc_source_inventory.csv"
SELECTED_OUT = AUDIT_DIR / "ndia_public_poc_selected_sources.csv"
EXCLUDED_OUT = AUDIT_DIR / "ndia_public_poc_excluded_sources.csv"
REGISTER_OUT = SOURCE_REGISTER_DIR / "ndia_public_poc_source_selection_register.csv"
NOTE_OUT = METHODOLOGY_DIR / "ndia_public_poc_context_layer_note.md"

MANIFEST_PATH = AUDIT_DIR / "ndia_public_data_download_manifest.csv"
FILE_INVENTORY_PATH = AUDIT_DIR / "ndia_public_data_file_inventory.csv"
SHEET_INVENTORY_PATH = AUDIT_DIR / "ndia_workbook_sheet_inventory.csv"
ACTIVE_CANDIDATES_PATH = AUDIT_DIR / "ndia_sa2_sa3_active_candidate_sources.csv"
HELD_ASIDE_PATH = AUDIT_DIR / "ndia_lga_service_district_phn_held_aside_sources.csv"
REJECTED_PATH = AUDIT_DIR / "ndia_rejected_non_granular_sources.csv"
EXPLORE_CANDIDATE_SUMMARY_PATH = AUDIT_DIR / "ndia_explore_latest_capture_candidate_summary.csv"

SUPPORTED_EXTENSIONS = {".csv", ".xlsx", ".xls", ".zip", ".json"}

MONTHS = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}

QUARTER_END_MONTH = {
    "q1": 9,   # NDIA financial year quarter 1: September
    "q2": 12,  # December
    "q3": 3,   # March
    "q4": 6,   # June
}

FAMILY_ORDER = [
    "participants_by_sa2",
    "participants_by_sa3",
    "diagnosis",
    "participant_numbers_plan_budgets",
    "utilisation",
    "payments",
    "active_providers",
    "market_insights",
    "market_concentration",
    "sda_participants",
    "sil_participants",
    "sda_dwellings_demand",
    "plan_management",
    "first_nations_participants",
    "cald_participants",
    "participant_goals",
    "baseline_outcomes",
    "other_ndia_public",
]

POC_FAMILY_GROUPS = {
    "participants_by_sa2": "participant_context",
    "participants_by_sa3": "participant_context",
    "diagnosis": "participant_disability_context",
    "participant_numbers_plan_budgets": "plan_budget_context",
    "utilisation": "utilisation_context",
    "payments": "payment_context",
    "active_providers": "provider_context",
    "market_insights": "market_context",
    "market_concentration": "market_context",
    "sda_participants": "housing_disability_context",
    "sil_participants": "supported_living_context",
    "sda_dwellings_demand": "housing_disability_context",
    "plan_management": "plan_management_context",
    "first_nations_participants": "equity_context",
    "cald_participants": "equity_context",
    "participant_goals": "participant_context",
    "baseline_outcomes": "participant_context",
    "other_ndia_public": "other_context",
}


@dataclass
class FileMeta:
    path: Path
    source_origin: str
    source_run: str


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and pd.isna(value):
        return ""
    text = str(value).strip()
    text = text.replace("–", "-").replace("—", "-")
    text = re.sub(r"\s+", " ", text)
    return text


def norm_text(value: Any) -> str:
    text = clean_text(value).lower()
    text = text.replace("_", " ")
    text = text.replace("-", " ")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def slug(value: str, max_len: int = 120) -> str:
    text = clean_text(value).lower()
    text = text.replace("&", " and ")
    text = re.sub(r"[^a-z0-9._-]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    return (text or "blank")[:max_len].strip("_")


def sha256_file(path: Path, block_size: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(block_size), b""):
            h.update(block)
    return h.hexdigest()


def safe_read_csv(path: Path, nrows: int | None = None) -> pd.DataFrame | None:
    encodings = ["utf-8-sig", "utf-8", "cp1252", "latin-1"]
    for enc in encodings:
        try:
            return pd.read_csv(path, dtype=str, nrows=nrows, low_memory=False, encoding=enc)
        except Exception:
            continue
    return None


def safe_read_excel_sheets(path: Path, nrows: int = 500) -> list[tuple[str, pd.DataFrame]]:
    out: list[tuple[str, pd.DataFrame]] = []
    try:
        xls = pd.ExcelFile(path)
    except Exception:
        return out

    for sheet in xls.sheet_names:
        try:
            df = pd.read_excel(path, sheet_name=sheet, dtype=str, nrows=nrows)
            out.append((sheet, df))
        except Exception:
            continue
    return out


def parse_date(value: Any) -> pd.Timestamp | None:
    text = clean_text(value)
    if not text:
        return None

    # Remove common prefixes from file/link text.
    text = re.sub(r"(?i)\b(as at|data|to|at|quarter ending|for period ending)\b", " ", text)
    text = re.sub(r"\s+", " ", text).strip()

    # YYYYMM at start or in filename, for example 202112_01.
    m = re.search(r"(?<!\d)(20\d{2})(0[1-9]|1[0-2])(?!\d)", text)
    if m:
        year = int(m.group(1))
        month = int(m.group(2))
        return pd.Timestamp(year=year, month=month, day=1) + pd.offsets.MonthEnd(0)

    # 30 June 2024 / 31 March 2026.
    m = re.search(
        r"(?i)(?<!\d)([0-3]?\d)\s+"
        r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
        r"jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?|tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
        r"\s+(20\d{2})(?!\d)",
        text,
    )
    if m:
        day = int(m.group(1))
        month = MONTHS[m.group(2).lower()]
        year = int(m.group(3))
        try:
            return pd.Timestamp(year=year, month=month, day=day)
        except Exception:
            return pd.Timestamp(year=year, month=month, day=1) + pd.offsets.MonthEnd(0)

    # June 2024 / September 2025.
    m = re.search(
        r"(?i)\b"
        r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
        r"jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?|tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
        r"\s+(20\d{2})\b",
        text,
    )
    if m:
        month = MONTHS[m.group(1).lower()]
        year = int(m.group(2))
        return pd.Timestamp(year=year, month=month, day=1) + pd.offsets.MonthEnd(0)

    # FY21/22 Q4, 2122_q4, 2021-22 q4, q4 fy21/22.
    lower = text.lower().replace("\\", "/").replace("_", "-")
    m = re.search(r"(?i)(?:fy\s*)?(20)?(\d{2})\s*[-/]\s*(\d{2}).*?\b(q[1-4])\b", lower)
    if not m:
        m = re.search(r"(?i)\b(q[1-4])\b.*?(?:fy\s*)?(20)?(\d{2})\s*[-/]\s*(\d{2})", lower)
        if m:
            q = m.group(1).lower()
            start_yy = int(m.group(3))
            end_yy = int(m.group(4))
        else:
            q = ""
            start_yy = end_yy = -1
    else:
        start_yy = int(m.group(2))
        end_yy = int(m.group(3))
        q = m.group(4).lower()

    if q:
        end_year = 2000 + end_yy
        month = QUARTER_END_MONTH[q]
        # Q1 and Q2 of an Australian financial year end in the start calendar year.
        if q in {"q1", "q2"}:
            year = 2000 + start_yy
        else:
            year = end_year
        return pd.Timestamp(year=year, month=month, day=1) + pd.offsets.MonthEnd(0)

    # 2122_q4 / 2526_q3.
    m = re.search(r"(?<!\d)(\d{2})(\d{2})[-_ ]?(q[1-4])(?!\d)", lower)
    if m:
        start_yy = int(m.group(1))
        end_yy = int(m.group(2))
        q = m.group(3).lower()
        month = QUARTER_END_MONTH[q]
        year = 2000 + (start_yy if q in {"q1", "q2"} else end_yy)
        return pd.Timestamp(year=year, month=month, day=1) + pd.offsets.MonthEnd(0)

    # Fall back to pandas for date-like cells.
    try:
        ts = pd.to_datetime(text, errors="coerce", dayfirst=True)
        if pd.notna(ts):
            return pd.Timestamp(ts)
    except Exception:
        pass

    return None


def infer_period_from_texts(texts: list[str]) -> tuple[str, str, str]:
    candidates = []
    for text in texts:
        dt = parse_date(text)
        if dt is not None and pd.notna(dt):
            candidates.append(pd.Timestamp(dt).normalize())

    if not candidates:
        return "", "", "not_detected"

    earliest = min(candidates)
    latest = max(candidates)

    if earliest == latest:
        return earliest.date().isoformat(), latest.date().isoformat(), "detected_from_filename_or_metadata"

    return earliest.date().isoformat(), latest.date().isoformat(), "detected_multiple_dates_from_filename_or_metadata"


def infer_period_from_dataframe(df: pd.DataFrame) -> tuple[str, str, str, str]:
    if df is None or df.empty:
        return "", "", "", "no_sample_rows"

    date_cols = []
    for col in df.columns:
        ncol = norm_text(col)
        if any(token in ncol for token in ["rprtdt", "report date", "date", "as at", "period", "quarter"]):
            date_cols.append(col)

    values = []
    used_cols = []
    for col in date_cols[:10]:
        ser = df[col].dropna().astype(str).head(2000)
        parsed = []
        for value in ser.unique()[:300]:
            dt = parse_date(value)
            if dt is not None and pd.notna(dt):
                parsed.append(pd.Timestamp(dt).normalize())
        if parsed:
            used_cols.append(col)
            values.extend(parsed)

    if not values:
        return "", "", "", "no_parseable_date_columns"

    return (
        min(values).date().isoformat(),
        max(values).date().isoformat(),
        " | ".join(used_cols),
        "detected_from_content_date_columns",
    )


def infer_source_origin(path: Path) -> tuple[str, str]:
    text = str(path)
    lower = text.lower()

    if "explore_data_tool_capture" in lower:
        parts = path.parts
        run = ""
        try:
            idx = [p.lower() for p in parts].index("explore_data_tool_capture")
            run = parts[idx + 1] if len(parts) > idx + 1 else ""
        except Exception:
            run = ""
        return "explore_data_tool_capture", run

    if "explore_data_tool_historical_probe" in lower:
        return "explore_data_tool_historical_probe", ""

    if "public_data_downloads" in lower:
        return "public_data_downloads", ""

    return "unknown", ""


def infer_family(path: Path, metadata_text: str = "") -> str:
    text = norm_text(f"{path.name} {metadata_text}")

    if "participants by sa2" in text or "participant by sa2" in text:
        return "participants_by_sa2"
    if "participants by sa3" in text or "participant by sa3" in text:
        return "participants_by_sa3"
    if "diagnosis" in text or "disability" in text and "count by diagnosis" in text:
        return "diagnosis"
    if "participant numbers" in text or "plan budgets" in text or "committed supports" in text:
        return "participant_numbers_plan_budgets"
    if "utilisation" in text or "utilization" in text or "utlstn" in text:
        return "utilisation"
    if "payments" in text or "payment" in text:
        return "payments"
    if "active providers" in text or "provider" in text and "active" in text:
        return "active_providers"
    if "market insights" in text or "market insight" in text or "market dashboard" in text:
        return "market_insights"
    if "market concentration" in text or "concentration" in text:
        return "market_concentration"
    if "sda participants" in text:
        return "sda_participants"
    if "sil participants" in text:
        return "sil_participants"
    if "sda enrolled dwellings" in text or "sda dwellings" in text or "enrolled dwellings" in text:
        return "sda_dwellings_demand"
    if "plan management" in text:
        return "plan_management"
    if "first nations" in text or "first_nations" in text or "aboriginal" in text or "torres strait" in text:
        return "first_nations_participants"
    if "culturally and linguistically diverse" in text or "cald" in text:
        return "cald_participants"
    if "participant goals" in text or "goals" in text:
        return "participant_goals"
    if "baseline outcomes" in text or "outcomes" in text:
        return "baseline_outcomes"

    return "other_ndia_public"


def infer_geography_and_signals(text: str, columns: list[str]) -> dict[str, bool]:
    combined = norm_text(" ".join([text] + columns))

    return {
        "has_sa2_signal": bool(re.search(r"\bsa2\b|statistical area 2", combined)),
        "has_sa3_signal": bool(re.search(r"\bsa3\b|statistical area 3", combined)),
        "has_sa4_signal": bool(re.search(r"\bsa4\b|statistical area 4", combined)),
        "has_lga_signal": bool(re.search(r"\blga\b|local government area", combined)),
        "has_service_district_signal": "service district" in combined or "srvcdstrct" in combined or "srvc dstrct" in combined,
        "has_phn_signal": bool(re.search(r"\bphn\b|primary health network", combined)),
        "has_state_signal": bool(re.search(r"\bstate\b|statecd", combined)),
        "has_postcode_signal": "postcode" in combined,
        "has_psychosocial_signal": "psychosocial" in combined or "psycho social" in combined,
        "has_disability_signal": "disability" in combined or "dsblty" in combined or "diagnosis" in combined,
        "has_support_category_signal": "support category" in combined or "suppcat" in combined,
        "has_support_class_signal": "support class" in combined or "suppclass" in combined,
        "has_participant_signal": "participant" in combined or "participants" in combined,
        "has_payment_signal": "payment" in combined or "payments" in combined,
        "has_provider_signal": "provider" in combined or "prvdr" in combined,
        "has_market_signal": "market" in combined or "concentration" in combined,
        "has_plan_budget_signal": "plan budget" in combined or "committed support" in combined,
        "has_utilisation_signal": "utilisation" in combined or "utilization" in combined or "utlstn" in combined,
    }


def inspect_file(path: Path) -> dict[str, Any]:
    suffix = path.suffix.lower()
    columns: list[str] = []
    sheet_names: list[str] = []
    sample_row_count = ""
    column_count = ""
    content_period_min = ""
    content_period_max = ""
    content_period_columns = ""
    content_period_source = ""
    inspect_status = "not_inspected"
    inspect_error = ""

    try:
        if suffix == ".csv":
            df = safe_read_csv(path, nrows=5000)
            if df is not None:
                columns = [clean_text(c) for c in df.columns]
                sample_row_count = len(df)
                column_count = len(df.columns)
                pmin, pmax, pcols, psrc = infer_period_from_dataframe(df)
                content_period_min, content_period_max = pmin, pmax
                content_period_columns, content_period_source = pcols, psrc
                inspect_status = "pass"
            else:
                inspect_status = "fail"
                inspect_error = "Could not read CSV with supported encodings."

        elif suffix in {".xlsx", ".xls"}:
            sheets = safe_read_excel_sheets(path, nrows=500)
            if sheets:
                all_cols = []
                pmins = []
                pmaxs = []
                pcols_all = []
                for sheet, df in sheets:
                    sheet_names.append(sheet)
                    all_cols.extend([f"{sheet}::{clean_text(c)}" for c in df.columns])
                    pmin, pmax, pcols, psrc = infer_period_from_dataframe(df)
                    if pmin:
                        pmins.append(pmin)
                    if pmax:
                        pmaxs.append(pmax)
                    if pcols:
                        pcols_all.append(f"{sheet}::{pcols}")
                columns = all_cols[:500]
                sample_row_count = sum(len(df) for _, df in sheets)
                column_count = len(all_cols)
                content_period_min = min(pmins) if pmins else ""
                content_period_max = max(pmaxs) if pmaxs else ""
                content_period_columns = " | ".join(pcols_all[:20])
                content_period_source = "detected_from_excel_sheets" if pmins else "no_parseable_date_columns"
                inspect_status = "pass"
            else:
                inspect_status = "fail"
                inspect_error = "Could not read workbook or no sheets read."

        elif suffix == ".zip":
            with zipfile.ZipFile(path) as zf:
                names = zf.namelist()
            columns = names[:300]
            sheet_names = []
            sample_row_count = ""
            column_count = len(names)
            inspect_status = "pass_zip_inventory_only"

        elif suffix == ".json":
            try:
                text = path.read_text(encoding="utf-8-sig", errors="replace")[:200000]
                data = json.loads(text)
                if isinstance(data, list) and data and isinstance(data[0], dict):
                    columns = list(data[0].keys())
                    sample_row_count = min(len(data), 5000)
                    column_count = len(columns)
                    df = pd.DataFrame(data[:5000])
                    pmin, pmax, pcols, psrc = infer_period_from_dataframe(df)
                    content_period_min, content_period_max = pmin, pmax
                    content_period_columns, content_period_source = pcols, psrc
                elif isinstance(data, dict):
                    columns = list(data.keys())[:300]
                    sample_row_count = 1
                    column_count = len(columns)
                inspect_status = "pass"
            except Exception as exc:
                inspect_status = "fail"
                inspect_error = f"Could not parse JSON: {exc}"

        else:
            inspect_status = "skipped_extension"

    except Exception as exc:
        inspect_status = "fail"
        inspect_error = str(exc)

    return {
        "columns_detected": " | ".join(columns[:300]),
        "sheet_names": " | ".join(sheet_names),
        "sample_row_count": sample_row_count,
        "column_count": column_count,
        "content_period_min": content_period_min,
        "content_period_max": content_period_max,
        "content_period_columns": content_period_columns,
        "content_period_source": content_period_source,
        "inspect_status": inspect_status,
        "inspect_error": inspect_error,
    }


def load_optional_csv(path: Path) -> pd.DataFrame:
    if path.exists():
        try:
            return pd.read_csv(path, dtype=str, low_memory=False)
        except Exception:
            return pd.DataFrame()
    return pd.DataFrame()


def build_manifest_lookup() -> dict[str, dict[str, Any]]:
    manifest = load_optional_csv(MANIFEST_PATH)
    lookup: dict[str, dict[str, Any]] = {}

    if manifest.empty:
        return lookup

    for _, row in manifest.iterrows():
        local_path = clean_text(row.get("local_path", ""))
        file_name = Path(local_path).name if local_path else ""
        keys = {local_path.lower(), file_name.lower()}
        for key in keys:
            if key:
                lookup[key] = row.to_dict()

    return lookup


def discover_files() -> list[FileMeta]:
    files: list[FileMeta] = []

    roots = [
        PUBLIC_DOWNLOADS,
        EXPLORE_CAPTURE_ROOT,
        HISTORICAL_PROBE_ROOT,
    ]

    seen: set[str] = set()

    for root in roots:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
                continue
            key = str(path.resolve()).lower()
            if key in seen:
                continue
            seen.add(key)
            origin, run = infer_source_origin(path)
            files.append(FileMeta(path=path, source_origin=origin, source_run=run))

    return files


def determine_join_readiness(signals: dict[str, bool], family: str) -> tuple[str, str]:
    if signals["has_sa2_signal"]:
        return "joinable_now_sa2", "Contains SA2 signal. Can be processed for proof-of-concept SA2 context if columns validate."

    if signals["has_sa3_signal"]:
        return "joinable_now_sa3", "Contains SA3 signal. Can be joined through sa3_code_2021 after processing."

    if signals["has_lga_signal"] or signals["has_service_district_signal"] or signals["has_phn_signal"]:
        return "context_only_unbridged", "Contains LGA/service district/PHN signal. Hold aside until bridge is validated."

    if signals["has_state_signal"]:
        return "context_only_state", "State-level only. Do not join to SA2 master."

    if family in {"market_insights", "market_concentration"}:
        return "context_only_review", "Market source. Review geography and aggregation before use."

    return "manual_review", "No clear SA2/SA3/LGA/service district/PHN geography signal detected."


def determine_poc_use(join_readiness: str) -> tuple[str, str]:
    primary = "exclude_from_primary_2021_aligned_model"

    if join_readiness in {"joinable_now_sa2", "joinable_now_sa3"}:
        return primary, "eligible_for_ndia_public_poc_sensitivity_or_demonstration_layer"

    if join_readiness in {"context_only_unbridged", "context_only_state", "context_only_review"}:
        return primary, "hold_for_descriptive_context_or_bridge_validation"

    return primary, "exclude_until_manual_review"


def select_period(row: dict[str, Any]) -> tuple[str, str, str, str]:
    text_period_min = clean_text(row.get("text_period_min", ""))
    text_period_max = clean_text(row.get("text_period_max", ""))
    text_period_source = clean_text(row.get("text_period_source", ""))

    content_period_min = clean_text(row.get("content_period_min", ""))
    content_period_max = clean_text(row.get("content_period_max", ""))
    content_period_source = clean_text(row.get("content_period_source", ""))

    if content_period_min or content_period_max:
        return (
            content_period_min or content_period_max,
            content_period_max or content_period_min,
            "content",
            content_period_source,
        )

    if text_period_min or text_period_max:
        return (
            text_period_min or text_period_max,
            text_period_max or text_period_min,
            "filename_or_link_text",
            text_period_source,
        )

    return "", "", "not_detected", "not_detected"


def build_inventory() -> pd.DataFrame:
    manifest_lookup = build_manifest_lookup()
    files = discover_files()

    rows = []

    for item in files:
        path = item.path
        stat = path.stat()
        manifest_row = manifest_lookup.get(str(path).lower()) or manifest_lookup.get(path.name.lower()) or {}

        link_text = clean_text(manifest_row.get("link_text", ""))
        source_page_url = clean_text(manifest_row.get("source_page_url", ""))
        download_url = clean_text(manifest_row.get("download_url", "")) or clean_text(manifest_row.get("final_url", ""))
        manifest_family = clean_text(manifest_row.get("source_family", ""))

        metadata_text = " ".join([path.name, link_text, manifest_family, source_page_url, download_url])
        family = infer_family(path, metadata_text)

        inspection = inspect_file(path)
        columns_text = clean_text(inspection.get("columns_detected", ""))
        sheets_text = clean_text(inspection.get("sheet_names", ""))

        signals = infer_geography_and_signals(metadata_text + " " + sheets_text, columns_text.split(" | "))

        text_min, text_max, text_source = infer_period_from_texts([path.name, link_text, download_url, source_page_url])

        base_row: dict[str, Any] = {
            "inventory_timestamp_utc": now_utc(),
            "source_origin": item.source_origin,
            "source_run": item.source_run,
            "source_family": family,
            "poc_family_group": POC_FAMILY_GROUPS.get(family, "other_context"),
            "manifest_source_family": manifest_family,
            "file_path": str(path),
            "file_name": path.name,
            "file_suffix": path.suffix.lower(),
            "file_size_bytes": stat.st_size,
            "file_modified_utc": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
            "sha256": sha256_file(path),
            "link_text": link_text,
            "source_page_url": source_page_url,
            "download_url": download_url,
            "text_period_min": text_min,
            "text_period_max": text_max,
            "text_period_source": text_source,
        }

        base_row.update(inspection)
        base_row.update(signals)

        period_min, period_max, period_basis, period_source = select_period(base_row)
        base_row["period_min"] = period_min
        base_row["period_max"] = period_max
        base_row["period_basis"] = period_basis
        base_row["period_source"] = period_source
        base_row["period_sort_date"] = period_max or period_min

        join_readiness, readiness_reason = determine_join_readiness(signals, family)
        primary_model_use, sensitivity_use = determine_poc_use(join_readiness)

        base_row["join_readiness"] = join_readiness
        base_row["join_readiness_reason"] = readiness_reason
        base_row["primary_model_use"] = primary_model_use
        base_row["poc_sensitivity_use"] = sensitivity_use
        base_row["time_alignment_status"] = "time_misaligned_public_ndia_poc_layer"
        base_row["time_alignment_note"] = (
            "NDIA public source period does not define the 2021-aligned primary model. "
            "Use only as proof-of-concept service-system context unless separately justified."
        )

        rows.append(base_row)

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)

    # Convert to sortable datetime, but keep original text columns.
    df["period_sort_dt"] = pd.to_datetime(df["period_sort_date"], errors="coerce")
    df["family_rank"] = df["source_family"].map({name: i for i, name in enumerate(FAMILY_ORDER)}).fillna(999).astype(int)

    df = df.sort_values(
        ["family_rank", "source_family", "period_sort_dt", "file_name", "file_size_bytes"],
        ascending=[True, True, True, True, True],
        na_position="last",
    ).reset_index(drop=True)

    return df


def select_sources(inventory: pd.DataFrame, include_context_only: bool) -> tuple[pd.DataFrame, pd.DataFrame]:
    if inventory.empty:
        return pd.DataFrame(), pd.DataFrame()

    eligible_statuses = {"joinable_now_sa2", "joinable_now_sa3"}
    if include_context_only:
        eligible_statuses.update({"context_only_unbridged", "context_only_state", "context_only_review"})

    eligible = inventory[inventory["join_readiness"].isin(eligible_statuses)].copy()

    # Do not select opaque app assets as source data. Keep downloaded JS/JSON in inventory only.
    eligible = eligible[~eligible["file_name"].str.lower().str.endswith((".js", ".css"), na=False)]

    # Prefer actual downloaded tabular files over app probe JSON unless JSON is the only family candidate.
    eligible["source_origin_rank"] = eligible["source_origin"].map(
        {
            "public_data_downloads": 0,
            "explore_data_tool_capture": 1,
            "explore_data_tool_historical_probe": 2,
            "unknown": 9,
        }
    ).fillna(9)

    eligible["join_rank"] = eligible["join_readiness"].map(
        {
            "joinable_now_sa2": 0,
            "joinable_now_sa3": 1,
            "context_only_unbridged": 2,
            "context_only_review": 3,
            "context_only_state": 4,
            "manual_review": 9,
        }
    ).fillna(9)

    eligible["period_sort_dt"] = pd.to_datetime(eligible["period_sort_date"], errors="coerce")

    # Earliest public source per family. Where dates tie, prefer SA2/SA3, then public data downloads.
    selected_rows = []
    for family, group in eligible.groupby("source_family", dropna=False):
        g = group.sort_values(
            ["period_sort_dt", "join_rank", "source_origin_rank", "file_name", "file_size_bytes"],
            ascending=[True, True, True, True, True],
            na_position="last",
        )
        selected_rows.append(g.iloc[0].to_dict())

    selected = pd.DataFrame(selected_rows)

    if selected.empty:
        excluded = inventory.copy()
        excluded["selection_status"] = "not_selected_no_eligible_sources"
        return selected, excluded

    selected_hashes = set(selected["sha256"].astype(str))

    selected = selected.copy()
    selected["selection_status"] = "selected_earliest_public_available_for_family"
    selected["selection_rule"] = (
        "Earliest detected public source period per NDIA source family; "
        "SA2/SA3 joinable sources preferred where available; all selected sources remain excluded from primary aligned model."
    )

    excluded = inventory[~inventory["sha256"].astype(str).isin(selected_hashes)].copy()
    excluded["selection_status"] = "not_selected"
    excluded["selection_rule"] = "Another source in the same family was earlier or more suitable for the proof-of-concept selection."

    return selected, excluded


def copy_selected_files(selected: pd.DataFrame) -> pd.DataFrame:
    if selected.empty:
        return selected

    SELECTED_DIR.mkdir(parents=True, exist_ok=True)

    copied_paths = []
    copied_names = []

    for _, row in selected.iterrows():
        src = Path(clean_text(row["file_path"]))
        family = clean_text(row["source_family"])
        period = clean_text(row.get("period_max", "")) or clean_text(row.get("period_min", "")) or "unknown_period"
        period_slug = slug(period)
        base_name = src.name
        dest_name = f"{slug(family, 60)}__{period_slug}__{base_name}"
        dest = SELECTED_DIR / dest_name

        counter = 2
        while dest.exists() and sha256_file(dest) != clean_text(row["sha256"]):
            dest_name = f"{slug(family, 60)}__{period_slug}__v{counter}__{base_name}"
            dest = SELECTED_DIR / dest_name
            counter += 1

        if not dest.exists():
            shutil.copy2(src, dest)

        copied_paths.append(str(dest))
        copied_names.append(dest.name)

    selected = selected.copy()
    selected["staged_file_path"] = copied_paths
    selected["staged_file_name"] = copied_names
    return selected


def write_methodology_note(selected: pd.DataFrame) -> None:
    METHODOLOGY_DIR.mkdir(parents=True, exist_ok=True)

    lines = [
        "# NDIA public proof-of-concept context layer",
        "",
        f"Generated: {now_utc()}",
        "",
        "## Status",
        "",
        "This project uses the earliest publicly available NDIA files located in the project downloads as a proof-of-concept context layer. These sources are not treated as time-aligned 2021 predictors in the primary model.",
        "",
        "## Core time-aligned analytical layer",
        "",
        "- ABS Census 2021 and QuickStats SA2 variables",
        "- ABS SEIFA 2021",
        "- ABS remoteness 2021",
        "- AIHW Regional Profiles 2021-22 at SA3",
        "- ABS NSMHW modelled SA2 estimates 2020-22",
        "",
        "## NDIA public proof-of-concept rule",
        "",
        "NDIA public files are used to demonstrate the Good Measure source discovery, staging and later integration method. They must be clearly labelled as a time-misaligned service-system context layer unless a future tailored NDIA request supplies 2021-22 SA2/SA3 data.",
        "",
        "## Modelling rule",
        "",
        "- Exclude NDIA public POC variables from the primary 2021-aligned model.",
        "- Use NDIA public POC variables only in a separate proof-of-concept sensitivity or demonstration model.",
        "- Preserve source period fields in all processed outputs.",
        "- Prefer SA2 and SA3 public sources. Hold LGA, service district, PHN and state-only sources aside unless a validated bridge is used.",
        "",
        "## Selected source families",
        "",
    ]

    if selected.empty:
        lines.append("No NDIA public POC sources were selected.")
    else:
        show_cols = ["source_family", "period_min", "period_max", "join_readiness", "file_name"]
        for _, row in selected[show_cols].sort_values("source_family").iterrows():
            lines.append(
                f"- {row['source_family']}: {row['file_name']} "
                f"({row.get('period_min', '')} to {row.get('period_max', '')}; {row.get('join_readiness', '')})"
            )

    lines.append("")
    NOTE_OUT.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Stage earliest publicly available NDIA proof-of-concept source files and create selection registers."
    )
    parser.add_argument(
        "--include-context-only",
        action="store_true",
        help=(
            "Also select earliest LGA/service district/PHN/state/context-only families. "
            "Default selects SA2/SA3 joinable families where available."
        ),
    )
    parser.add_argument(
        "--project-root",
        default=str(PROJECT_ROOT),
        help="Project root. Defaults to D:\\Good Measure\\MentalWellbeingbyGeography.",
    )
    args = parser.parse_args()

    # Allow project root override while retaining global default paths in ordinary use.
    if Path(args.project_root) != PROJECT_ROOT:
        raise SystemExit(
            "This script currently uses project-level constants. Edit PROJECT_ROOT at the top of the script "
            "if you need to run it from a different root."
        )

    AUDIT_DIR.mkdir(parents=True, exist_ok=True)
    SOURCE_REGISTER_DIR.mkdir(parents=True, exist_ok=True)
    METHODOLOGY_DIR.mkdir(parents=True, exist_ok=True)
    SELECTED_DIR.mkdir(parents=True, exist_ok=True)

    inventory = build_inventory()

    if inventory.empty:
        print("No NDIA files found. Check these folders:")
        print(f"  {PUBLIC_DOWNLOADS}")
        print(f"  {EXPLORE_CAPTURE_ROOT}")
        print(f"  {HISTORICAL_PROBE_ROOT}")
        inventory.to_csv(INVENTORY_OUT, index=False, encoding="utf-8-sig")
        return

    selected, excluded = select_sources(inventory, include_context_only=args.include_context_only)
    selected = copy_selected_files(selected)

    inventory_out = inventory.drop(columns=["period_sort_dt", "family_rank"], errors="ignore")
    selected_out = selected.drop(columns=["period_sort_dt", "family_rank", "source_origin_rank", "join_rank"], errors="ignore")
    excluded_out = excluded.drop(columns=["period_sort_dt", "family_rank", "source_origin_rank", "join_rank"], errors="ignore")

    inventory_out.to_csv(INVENTORY_OUT, index=False, encoding="utf-8-sig")
    selected_out.to_csv(SELECTED_OUT, index=False, encoding="utf-8-sig")
    excluded_out.to_csv(EXCLUDED_OUT, index=False, encoding="utf-8-sig")
    selected_out.to_csv(REGISTER_OUT, index=False, encoding="utf-8-sig")
    write_methodology_note(selected_out)

    print("\nCreated NDIA public proof-of-concept source selection outputs:")
    print(f"  Inventory:         {INVENTORY_OUT}")
    print(f"  Selected sources:  {SELECTED_OUT}")
    print(f"  Excluded sources:  {EXCLUDED_OUT}")
    print(f"  Source register:   {REGISTER_OUT}")
    print(f"  Methodology note:  {NOTE_OUT}")
    print(f"  Staged files:      {SELECTED_DIR}")

    print("\nSelection summary:")
    summary = (
        selected_out.groupby(["source_family", "join_readiness", "period_min", "period_max"], dropna=False)
        .size()
        .reset_index(name="selected_file_count")
        .sort_values(["source_family", "period_max"])
    )
    if summary.empty:
        print("  No sources selected.")
    else:
        print(summary.to_string(index=False))

    print("\nSelected files:")
    if selected_out.empty:
        print("  None")
    else:
        cols = [
            "source_family",
            "period_min",
            "period_max",
            "join_readiness",
            "file_name",
            "staged_file_path",
        ]
        print(selected_out[cols].sort_values(["source_family", "period_max"]).to_string(index=False))

    print("\nImportant modelling rule:")
    print("  NDIA public POC sources are excluded from the primary 2021-aligned model.")
    print("  Use them only in a separate proof-of-concept sensitivity/demonstration layer.")


if __name__ == "__main__":
    main()
