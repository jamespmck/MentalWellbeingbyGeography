from __future__ import annotations

from pathlib import Path
import argparse
import csv
import json
import re
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import pandas as pd

DEFAULT_PROJECT_ROOT = Path(r"D:\Good Measure\MentalWellbeingbyGeography")

SELECTED_DIR_REL = Path("data/raw/ndia/public_poc_selected")
SOURCE_REGISTER_REL = Path("docs/source_registers/ndia_public_poc_source_selection_register.csv")
AUDIT_DIR_REL = Path("outputs/audits")
DATA_DICT_DIR_REL = Path("docs/data_dictionaries")

SCHEMA_AUDIT_NAME = "ndia_public_poc_selected_file_schema_audit.csv"
PERIOD_AUDIT_NAME = "ndia_public_poc_selected_file_period_audit.csv"
GEOGRAPHY_AUDIT_NAME = "ndia_public_poc_joinable_geography_audit.csv"
FIELD_DICTIONARY_NAME = "ndia_public_poc_selected_file_field_dictionary.csv"
PROCESSING_RECOMMENDATIONS_NAME = "ndia_public_poc_processing_recommendations.csv"

SOURCE_FAMILY_ORDER = [
    "participants_by_sa2",
    "participants_by_sa3",
    "participant_numbers_plan_budgets",
    "payments",
    "active_providers",
    "market_concentration",
    "market_insights",
    "plan_management",
    "sda_participants",
    "sil_participants",
    "sda_dwellings_demand",
    "diagnosis",
    "first_nations_participants",
    "cald_participants",
    "baseline_outcomes",
    "other_ndia_public",
]

DATE_LIKE_COLUMN_TERMS = [
    "date",
    "period",
    "quarter",
    "qtr",
    "report",
    "as at",
    "as_at",
    "month",
    "year",
    "financial",
    "fy",
]

GEOGRAPHY_TERMS = {
    "sa2": ["sa2", "statistical area 2", "statistical area level 2"],
    "sa3": ["sa3", "statistical area 3", "statistical area level 3"],
    "sa4": ["sa4", "statistical area 4", "statistical area level 4"],
    "lga": ["lga", "local government area"],
    "service_district": ["service district", "service_district", "service district name"],
    "phn": ["phn", "primary health network"],
    "state": ["state", "territory", "state/territory", "jurisdiction"],
    "post_code": ["postcode", "post code", "postal"],
    "suburb": ["suburb"],
}

MEASURE_TERMS = {
    "participant": ["participant", "participants", "active participant", "participant count"],
    "plan_budget": ["budget", "committed", "committed supports", "plan budget"],
    "payment": ["payment", "payments", "paid", "claim", "claims"],
    "utilisation": ["utilisation", "utilization", "utilised", "utilized"],
    "provider": ["provider", "providers", "active provider"],
    "market_concentration": ["market concentration", "concentration", "top 10", "market share"],
    "psychosocial": ["psychosocial", "psycho-social", "mental health"],
    "disability": ["disability", "primary disability", "diagnosis"],
    "support_class": ["support class", "support_class"],
    "support_category": ["support category", "support_category"],
    "age": ["age", "age group"],
    "first_nations": ["first nations", "aboriginal", "torres strait", "indigenous"],
    "cald": ["cald", "culturally", "linguistically", "language", "country of birth"],
}

NUMERIC_MEASURE_TERMS = [
    "count",
    "participants",
    "number",
    "budget",
    "committed",
    "payment",
    "payments",
    "utilisation",
    "utilization",
    "rate",
    "percentage",
    "percent",
    "%",
    "providers",
    "amount",
    "total",
    "average",
    "mean",
]

SPECIAL_NULLS = {"", "nan", "none", "null", "n/a", "na", "n.p.", "np", "-", "--"}


@dataclass
class TableResult:
    file_path: Path
    source_family: str
    file_name: str
    extension: str
    sheet_name: str
    table_label: str
    read_status: str
    read_error: str
    row_count: int | None
    column_count: int | None
    columns: list[str]
    sample: pd.DataFrame | None
    full_df: pd.DataFrame | None


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    text = re.sub(r"\s+", " ", text)
    return text


def norm(value: Any) -> str:
    return clean_text(value).lower().replace("–", "-").replace("—", "-")


def normalise_column_name(col: Any) -> str:
    text = clean_text(col)
    text = re.sub(r"\s+", " ", text)
    return text


def source_family_from_path(path: Path) -> str:
    name = path.name
    prefix = name.split("__", 1)[0]
    if prefix in SOURCE_FAMILY_ORDER:
        return prefix

    lower = name.lower()
    if "participants_by_sa2" in lower or "participants by sa2" in lower:
        return "participants_by_sa2"
    if "participants_by_sa3" in lower or "participants by sa3" in lower:
        return "participants_by_sa3"
    if "plan_budget" in lower or "plan_budgets" in lower or "committed" in lower:
        return "participant_numbers_plan_budgets"
    if "utilisation" in lower or "utilization" in lower:
        return "utilisation"
    if "payment" in lower:
        return "payments"
    if "active_provider" in lower or "providers" in lower:
        return "active_providers"
    if "market_concentration" in lower:
        return "market_concentration"
    if "market_insights" in lower or "market_insights_dashboard" in lower:
        return "market_insights"
    if "diagnosis" in lower:
        return "diagnosis"

    return "other_ndia_public"


def safe_int(value: Any) -> int | None:
    try:
        if pd.isna(value):
            return None
        return int(value)
    except Exception:
        return None


def read_source_register(project_root: Path) -> pd.DataFrame:
    path = project_root / SOURCE_REGISTER_REL
    if path.exists():
        try:
            return pd.read_csv(path, dtype=str, low_memory=False)
        except Exception:
            return pd.DataFrame()
    return pd.DataFrame()


def selected_files(project_root: Path, source_register: pd.DataFrame) -> list[Path]:
    selected_dir = project_root / SELECTED_DIR_REL
    files: list[Path] = []

    if "staged_file_path" in source_register.columns:
        for value in source_register["staged_file_path"].dropna().astype(str):
            path = Path(value)
            if path.exists() and path.is_file():
                files.append(path)

    if not files and selected_dir.exists():
        for path in selected_dir.rglob("*"):
            if path.is_file() and not path.name.startswith("~$"):
                files.append(path)

    unique = []
    seen = set()
    for path in files:
        key = str(path.resolve()).lower()
        if key not in seen:
            seen.add(key)
            unique.append(path)

    return sorted(unique, key=lambda p: (source_family_from_path(p), p.name.lower()))


def sniff_csv_delimiter(path: Path) -> str:
    try:
        with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as f:
            sample = f.read(20000)
        dialect = csv.Sniffer().sniff(sample, delimiters=[",", "\t", ";", "|"])
        return dialect.delimiter
    except Exception:
        return ","


def read_csv_robust(path: Path, full: bool = True, sample_rows: int = 5000) -> tuple[pd.DataFrame | None, pd.DataFrame | None, str, str]:
    delimiter = sniff_csv_delimiter(path)
    encodings = ["utf-8-sig", "utf-8", "cp1252", "latin-1"]
    last_error = ""

    for enc in encodings:
        try:
            if full:
                df = pd.read_csv(path, dtype=str, low_memory=False, sep=delimiter, encoding=enc)
                sample = df.head(sample_rows).copy()
            else:
                sample = pd.read_csv(path, dtype=str, low_memory=False, sep=delimiter, encoding=enc, nrows=sample_rows)
                df = None
            return df, sample, "read_ok", ""
        except Exception as exc:
            last_error = str(exc)

    return None, None, "read_failed", last_error


def read_excel_sheets(path: Path, sample_rows: int = 5000) -> list[TableResult]:
    results: list[TableResult] = []
    source_family = source_family_from_path(path)

    try:
        xls = pd.ExcelFile(path)
    except Exception as exc:
        return [
            TableResult(
                file_path=path,
                source_family=source_family,
                file_name=path.name,
                extension=path.suffix.lower(),
                sheet_name="",
                table_label=path.name,
                read_status="read_failed",
                read_error=str(exc),
                row_count=None,
                column_count=None,
                columns=[],
                sample=None,
                full_df=None,
            )
        ]

    for sheet in xls.sheet_names:
        try:
            df = pd.read_excel(path, sheet_name=sheet, dtype=str)
            df.columns = [normalise_column_name(c) for c in df.columns]
            results.append(
                TableResult(
                    file_path=path,
                    source_family=source_family,
                    file_name=path.name,
                    extension=path.suffix.lower(),
                    sheet_name=sheet,
                    table_label=f"{path.name}::{sheet}",
                    read_status="read_ok",
                    read_error="",
                    row_count=len(df),
                    column_count=len(df.columns),
                    columns=list(df.columns),
                    sample=df.head(sample_rows).copy(),
                    full_df=df,
                )
            )
        except Exception as exc:
            results.append(
                TableResult(
                    file_path=path,
                    source_family=source_family,
                    file_name=path.name,
                    extension=path.suffix.lower(),
                    sheet_name=sheet,
                    table_label=f"{path.name}::{sheet}",
                    read_status="read_failed",
                    read_error=str(exc),
                    row_count=None,
                    column_count=None,
                    columns=[],
                    sample=None,
                    full_df=None,
                )
            )

    return results


def extract_zip_members(path: Path, project_root: Path) -> list[Path]:
    out_dir = project_root / "data" / "raw" / "ndia" / "public_poc_selected_extracted" / path.stem
    out_dir.mkdir(parents=True, exist_ok=True)
    extracted: list[Path] = []

    try:
        with zipfile.ZipFile(path) as zf:
            for member in zf.infolist():
                if member.is_dir():
                    continue
                member_name = Path(member.filename).name
                if not member_name or member_name.startswith("~$"):
                    continue
                if not member_name.lower().endswith((".csv", ".xlsx", ".xls")):
                    continue
                target = out_dir / member_name
                with zf.open(member) as src, target.open("wb") as dst:
                    dst.write(src.read())
                extracted.append(target)
    except Exception:
        pass

    return extracted


def read_tables_from_file(path: Path, project_root: Path) -> list[TableResult]:
    suffix = path.suffix.lower()
    source_family = source_family_from_path(path)

    if suffix == ".csv":
        df, sample, status, error = read_csv_robust(path, full=True)
        if df is not None:
            df.columns = [normalise_column_name(c) for c in df.columns]
            sample = df.head(5000).copy()
            return [
                TableResult(
                    file_path=path,
                    source_family=source_family,
                    file_name=path.name,
                    extension=suffix,
                    sheet_name="",
                    table_label=path.name,
                    read_status=status,
                    read_error=error,
                    row_count=len(df),
                    column_count=len(df.columns),
                    columns=list(df.columns),
                    sample=sample,
                    full_df=df,
                )
            ]

        return [
            TableResult(
                file_path=path,
                source_family=source_family,
                file_name=path.name,
                extension=suffix,
                sheet_name="",
                table_label=path.name,
                read_status=status,
                read_error=error,
                row_count=None,
                column_count=None,
                columns=[],
                sample=None,
                full_df=None,
            )
        ]

    if suffix in {".xlsx", ".xls"}:
        return read_excel_sheets(path)

    if suffix == ".zip":
        results: list[TableResult] = []
        for extracted in extract_zip_members(path, project_root):
            for table in read_tables_from_file(extracted, project_root):
                table.source_family = source_family
                table.file_name = path.name
                table.table_label = f"{path.name}::{extracted.name}" + (f"::{table.sheet_name}" if table.sheet_name else "")
                results.append(table)
        if results:
            return results
        return [
            TableResult(
                file_path=path,
                source_family=source_family,
                file_name=path.name,
                extension=suffix,
                sheet_name="",
                table_label=path.name,
                read_status="zip_no_supported_members",
                read_error="No CSV/XLSX/XLS members found or extract failed.",
                row_count=None,
                column_count=None,
                columns=[],
                sample=None,
                full_df=None,
            )
        ]

    return [
        TableResult(
            file_path=path,
            source_family=source_family,
            file_name=path.name,
            extension=suffix,
            sheet_name="",
            table_label=path.name,
            read_status="unsupported_extension",
            read_error=f"Unsupported extension: {suffix}",
            row_count=None,
            column_count=None,
            columns=[],
            sample=None,
            full_df=None,
        )
    ]


def infer_geography_from_columns(columns: list[str]) -> dict[str, Any]:
    text = " | ".join(columns)
    lower = norm(text)
    result: dict[str, Any] = {}

    for geo, terms in GEOGRAPHY_TERMS.items():
        hits = [term for term in terms if term in lower]
        result[f"has_{geo}_column_signal"] = bool(hits)
        result[f"{geo}_column_terms"] = " | ".join(hits)

    return result


def looks_like_sa2_code(value: Any) -> bool:
    text = clean_text(value)
    text = re.sub(r"\.0$", "", text)
    return bool(re.fullmatch(r"\d{9}", text))


def looks_like_sa3_code(value: Any) -> bool:
    text = clean_text(value)
    text = re.sub(r"\.0$", "", text)
    return bool(re.fullmatch(r"\d{5}", text))


def infer_code_columns(df: pd.DataFrame | None, columns: list[str]) -> dict[str, Any]:
    result = {
        "candidate_sa2_code_columns": "",
        "candidate_sa3_code_columns": "",
        "candidate_sa2_name_columns": "",
        "candidate_sa3_name_columns": "",
    }

    if df is None or df.empty:
        return result

    sa2_code_cols = []
    sa3_code_cols = []
    sa2_name_cols = []
    sa3_name_cols = []

    for col in columns:
        lower = norm(col)
        sample_values = df[col].dropna().astype(str).head(1000).tolist() if col in df.columns else []

        if "sa2" in lower and ("code" in lower or "id" in lower):
            sa2_code_cols.append(col)
        elif sample_values:
            matches = sum(looks_like_sa2_code(v) for v in sample_values)
            if matches >= max(3, int(len(sample_values) * 0.5)):
                sa2_code_cols.append(col)

        if "sa3" in lower and ("code" in lower or "id" in lower):
            sa3_code_cols.append(col)
        elif sample_values:
            matches = sum(looks_like_sa3_code(v) for v in sample_values)
            if matches >= max(3, int(len(sample_values) * 0.5)):
                sa3_code_cols.append(col)

        if "sa2" in lower and "name" in lower:
            sa2_name_cols.append(col)
        if "sa3" in lower and "name" in lower:
            sa3_name_cols.append(col)

    result["candidate_sa2_code_columns"] = " | ".join(sorted(set(sa2_code_cols)))
    result["candidate_sa3_code_columns"] = " | ".join(sorted(set(sa3_code_cols)))
    result["candidate_sa2_name_columns"] = " | ".join(sorted(set(sa2_name_cols)))
    result["candidate_sa3_name_columns"] = " | ".join(sorted(set(sa3_name_cols)))

    return result


def infer_measure_signals(columns: list[str], df: pd.DataFrame | None) -> dict[str, Any]:
    col_text = " | ".join(columns)
    lower = norm(col_text)

    if df is not None and not df.empty:
        sample_text = " | ".join(
            clean_text(v)
            for v in df.head(200).astype(str).fillna("").to_numpy().ravel().tolist()[:5000]
        )
        lower = lower + " | " + norm(sample_text)

    result = {}
    for name, terms in MEASURE_TERMS.items():
        hits = [term for term in terms if term in lower]
        result[f"has_{name}_signal"] = bool(hits)
        result[f"{name}_signal_terms"] = " | ".join(hits)

    return result


def parse_period_from_text(value: Any) -> list[pd.Timestamp]:
    text = clean_text(value)
    if not text:
        return []

    text_norm = text.lower().replace("–", "-").replace("—", "-")
    dates: list[pd.Timestamp] = []

    # Explicit quarter tokens: 2526_q3, 2122_q4, Q4 FY21/22, FY2021-22 Q4.
    patterns = [
        r"(?P<fy1>\d{2})(?P<fy2>\d{2})[_\-\s]*q(?P<q>[1-4])",
        r"q(?P<q>[1-4])\s*fy\s*(?P<fy1>\d{2})\s*/\s*(?P<fy2>\d{2})",
        r"q(?P<q>[1-4])\s*fy\s*(?P<fy1>\d{4})\s*[-/]\s*(?P<fy2>\d{2,4})",
        r"fy\s*(?P<fy1>\d{2})\s*/\s*(?P<fy2>\d{2})\s*q(?P<q>[1-4])",
        r"fy\s*(?P<fy1>\d{4})\s*[-/]\s*(?P<fy2>\d{2,4})\s*q(?P<q>[1-4])",
    ]

    quarter_end_month_day = {
        1: (9, 30),
        2: (12, 31),
        3: (3, 31),
        4: (6, 30),
    }

    for pattern in patterns:
        for match in re.finditer(pattern, text_norm):
            try:
                q = int(match.group("q"))
                fy1_raw = match.group("fy1")
                fy2_raw = match.group("fy2")

                fy1 = int(fy1_raw)
                fy2 = int(fy2_raw)

                if fy1 < 100:
                    fy1 += 2000
                if fy2 < 100:
                    fy2 += 2000

                if q in {1, 2}:
                    year = fy1
                else:
                    year = fy2

                month, day = quarter_end_month_day[q]
                dates.append(pd.Timestamp(year=year, month=month, day=day))
            except Exception:
                continue

    # yyyy-mm-dd and dd/mm/yyyy style dates.
    explicit_date_patterns = [
        r"\b\d{4}-\d{1,2}-\d{1,2}\b",
        r"\b\d{1,2}/\d{1,2}/\d{4}\b",
        r"\b\d{1,2}-\d{1,2}-\d{4}\b",
    ]

    for pattern in explicit_date_patterns:
        for m in re.findall(pattern, text_norm):
            try:
                if re.fullmatch(r"\d{4}-\d{1,2}-\d{1,2}", m):
                    ts = pd.to_datetime(m, errors="coerce", dayfirst=False)
                else:
                    ts = pd.to_datetime(m, errors="coerce", dayfirst=True)
                if pd.notna(ts):
                    dates.append(pd.Timestamp(ts).normalize())
            except Exception:
                continue

    # Month year phrases.
    for pattern in [
        r"(?:as at|at|to|june|march|september|december)?\s*(?P<day>30|31)?\s*(?P<month>january|february|march|april|may|june|july|august|september|october|november|december)\s+(?P<year>20\d{2})",
        r"(?P<month>january|february|march|april|may|june|july|august|september|october|november|december)[_\-\s]+(?P<year>20\d{2})",
    ]:
        for match in re.finditer(pattern, text_norm):
            try:
                month_name = match.group("month")
                year = int(match.group("year"))
                month = pd.Timestamp(f"1 {month_name} {year}").month
                # Use common quarter-end day where applicable, otherwise month-end.
                day = {3: 31, 6: 30, 9: 30, 12: 31}.get(month)
                if day is None:
                    day = pd.Timestamp(year=year, month=month, day=1).days_in_month
                dates.append(pd.Timestamp(year=year, month=month, day=day))
            except Exception:
                continue

    # Filename shorthand such as 202112.
    for match in re.finditer(r"\b(20\d{2})(0[1-9]|1[0-2])\b", text_norm):
        try:
            year = int(match.group(1))
            month = int(match.group(2))
            day = {3: 31, 6: 30, 9: 30, 12: 31}.get(month)
            if day is None:
                day = pd.Timestamp(year=year, month=month, day=1).days_in_month
            dates.append(pd.Timestamp(year=year, month=month, day=day))
        except Exception:
            continue

    return dates


def infer_periods(table: TableResult) -> dict[str, Any]:
    dates: list[pd.Timestamp] = []
    sources: list[str] = []

    # File and sheet names often contain period.
    for source_text in [str(table.file_path), table.file_name, table.sheet_name, table.table_label]:
        found = parse_period_from_text(source_text)
        if found:
            dates.extend(found)
            sources.append("file_or_sheet_name")

    df = table.full_df
    if df is not None and not df.empty:
        candidate_cols = [
            col for col in df.columns
            if any(term in norm(col) for term in DATE_LIKE_COLUMN_TERMS)
        ]

        # Include first 20 columns as some NDIS files use terse labels.
        candidate_cols = list(dict.fromkeys(candidate_cols + list(df.columns[:20])))

        for col in candidate_cols:
            try:
                values = df[col].dropna().astype(str).head(2000).tolist()
            except Exception:
                continue
            for value in values:
                found = parse_period_from_text(value)
                if found:
                    dates.extend(found)
                    sources.append(f"column:{col}")

    unique_dates = sorted(set(pd.Timestamp(d).normalize() for d in dates if pd.notna(d)))

    return {
        "period_dates_detected": " | ".join(d.strftime("%Y-%m-%d") for d in unique_dates),
        "period_min": unique_dates[0].strftime("%Y-%m-%d") if unique_dates else "",
        "period_max": unique_dates[-1].strftime("%Y-%m-%d") if unique_dates else "",
        "period_detection_sources": " | ".join(sorted(set(sources))),
        "period_count_detected": len(unique_dates),
    }


def infer_value_profile(df: pd.DataFrame | None, columns: list[str]) -> dict[str, Any]:
    if df is None or df.empty:
        return {
            "numeric_like_column_count": 0,
            "candidate_measure_columns": "",
            "all_missing_columns": "",
            "duplicate_rows": "",
        }

    numeric_like_cols = []
    candidate_measure_cols = []
    all_missing_cols = []

    for col in columns:
        s = df[col]
        non_missing = s.dropna().astype(str).map(clean_text)
        non_missing = non_missing[~non_missing.str.lower().isin(SPECIAL_NULLS)]

        if len(non_missing) == 0:
            all_missing_cols.append(col)
            continue

        cleaned = (
            non_missing
            .str.replace(",", "", regex=False)
            .str.replace("$", "", regex=False)
            .str.replace("%", "", regex=False)
            .str.strip()
        )
        numeric = pd.to_numeric(cleaned, errors="coerce")
        numeric_share = numeric.notna().mean() if len(numeric) else 0

        if numeric_share >= 0.8:
            numeric_like_cols.append(col)

        lower_col = norm(col)
        if any(term in lower_col for term in NUMERIC_MEASURE_TERMS) or numeric_share >= 0.8:
            candidate_measure_cols.append(col)

    duplicate_rows = int(df.duplicated().sum()) if len(df) else 0

    return {
        "numeric_like_column_count": len(numeric_like_cols),
        "candidate_numeric_like_columns": " | ".join(numeric_like_cols),
        "candidate_measure_columns": " | ".join(candidate_measure_cols),
        "all_missing_columns": " | ".join(all_missing_cols),
        "duplicate_rows": duplicate_rows,
    }


def lookup_register_metadata(table: TableResult, source_register: pd.DataFrame) -> dict[str, Any]:
    meta = {
        "register_join_readiness": "",
        "register_period_min": "",
        "register_period_max": "",
        "register_selected_status": "",
        "register_notes": "",
    }

    if source_register.empty:
        return meta

    candidates = source_register.copy()
    path_str = str(table.file_path)
    file_name = table.file_name

    mask = pd.Series(False, index=candidates.index)

    for col in ["staged_file_path", "source_path", "file_path", "downloaded_file_path"]:
        if col in candidates.columns:
            mask = mask | candidates[col].fillna("").astype(str).eq(path_str)
            mask = mask | candidates[col].fillna("").astype(str).str.endswith(file_name, na=False)

    if "file_name" in candidates.columns:
        mask = mask | candidates["file_name"].fillna("").astype(str).eq(file_name)

    if "source_family" in candidates.columns:
        mask = mask | candidates["source_family"].fillna("").astype(str).eq(table.source_family)

    matched = candidates.loc[mask]
    if matched.empty:
        return meta

    row = matched.iloc[0]
    for out_key, possible_cols in {
        "register_join_readiness": ["join_readiness"],
        "register_period_min": ["period_min"],
        "register_period_max": ["period_max"],
        "register_selected_status": ["selection_status", "selected_status", "selected"],
        "register_notes": ["notes", "methodology_note"],
    }.items():
        for col in possible_cols:
            if col in row.index and pd.notna(row[col]):
                meta[out_key] = clean_text(row[col])
                break

    return meta


def assess_joinability(table: TableResult, geo: dict[str, Any], codes: dict[str, Any], register_join_readiness: str) -> dict[str, Any]:
    source_family = table.source_family
    has_sa2 = bool(codes.get("candidate_sa2_code_columns")) or bool(geo.get("has_sa2_column_signal"))
    has_sa3 = bool(codes.get("candidate_sa3_code_columns")) or bool(geo.get("has_sa3_column_signal"))
    has_lga = bool(geo.get("has_lga_column_signal"))
    has_service_district = bool(geo.get("has_service_district_column_signal"))
    has_state = bool(geo.get("has_state_column_signal"))

    if source_family == "participants_by_sa2" and has_sa2:
        recommendation = "process_for_poc_sa2_join"
        join_key = codes.get("candidate_sa2_code_columns") or "review_sa2_column"
        reason = "Participants by SA2 has SA2 geography and can be joined directly as a time-misaligned POC context layer."
    elif source_family == "participants_by_sa3" and has_sa3:
        recommendation = "process_for_poc_sa3_join"
        join_key = codes.get("candidate_sa3_code_columns") or "review_sa3_column"
        reason = "Participants by SA3 has SA3 geography and can be joined directly to SA2 rows through sa3_code_2021 as a time-misaligned POC context layer."
    elif has_sa2 and "joinable" in norm(register_join_readiness):
        recommendation = "review_for_possible_sa2_join"
        join_key = codes.get("candidate_sa2_code_columns") or "review_sa2_column"
        reason = "SA2 signal detected. Review columns and period before joining."
    elif has_sa3 and "joinable" in norm(register_join_readiness):
        recommendation = "review_for_possible_sa3_join"
        join_key = codes.get("candidate_sa3_code_columns") or "review_sa3_column"
        reason = "SA3 signal detected. Review columns and period before joining."
    elif has_lga or has_service_district:
        recommendation = "hold_as_context_only_until_bridge_validated"
        join_key = ""
        reason = "LGA or service district signal detected. Do not join until geography bridge is validated."
    elif has_state:
        recommendation = "context_only_state_level"
        join_key = ""
        reason = "State-level context only. Do not join to SA2 modelling master."
    else:
        recommendation = "manual_review_no_clear_join_geography"
        join_key = ""
        reason = "No clear SA2 or SA3 join geography detected."

    return {
        "processing_recommendation": recommendation,
        "recommended_join_key": join_key,
        "processing_reason": reason,
    }


def build_field_dictionary_row(table: TableResult, col: str, df: pd.DataFrame | None) -> dict[str, Any]:
    lower = norm(col)

    field_role = "unknown"
    if any(term in lower for term in ["sa2", "sa3", "lga", "state", "service district", "region", "geography"]):
        field_role = "geography"
    elif any(term in lower for term in DATE_LIKE_COLUMN_TERMS):
        field_role = "period"
    elif any(term in lower for term in NUMERIC_MEASURE_TERMS):
        field_role = "measure_or_count"
    elif any(term in lower for term in ["disability", "diagnosis", "psychosocial", "support class", "support category", "age", "gender", "sex"]):
        field_role = "category_or_stratifier"

    non_missing = ""
    missing = ""
    unique_count = ""
    sample_values = ""
    numeric_like = ""

    if df is not None and col in df.columns:
        s = df[col]
        non_missing_int = int(s.notna().sum())
        missing_int = int(s.isna().sum())
        unique_int = int(s.nunique(dropna=True))
        values = [clean_text(v) for v in s.dropna().astype(str).head(8).tolist()]

        non_missing = non_missing_int
        missing = missing_int
        unique_count = unique_int
        sample_values = " | ".join(values)

        cleaned = (
            s.dropna()
            .astype(str)
            .map(clean_text)
            .str.replace(",", "", regex=False)
            .str.replace("$", "", regex=False)
            .str.replace("%", "", regex=False)
        )
        cleaned = cleaned[~cleaned.str.lower().isin(SPECIAL_NULLS)]
        if len(cleaned):
            numeric_like = bool(pd.to_numeric(cleaned, errors="coerce").notna().mean() >= 0.8)
        else:
            numeric_like = False

    return {
        "source_family": table.source_family,
        "file_name": table.file_name,
        "sheet_name": table.sheet_name,
        "table_label": table.table_label,
        "column_name": col,
        "field_role_inferred": field_role,
        "non_missing_count": non_missing,
        "missing_count": missing,
        "unique_count": unique_count,
        "numeric_like": numeric_like,
        "sample_values": sample_values,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Audit staged NDIA public proof-of-concept source files before any SA2/SA3 join."
    )
    parser.add_argument(
        "--project-root",
        default=str(DEFAULT_PROJECT_ROOT),
        help="Project root folder. Defaults to the MentalWellbeingByGeography project path.",
    )
    parser.add_argument(
        "--sample-rows",
        type=int,
        default=5000,
        help="Rows to retain as sample for profiling. Full CSV/XLSX is still read for row counts where practical.",
    )

    args = parser.parse_args()
    project_root = Path(args.project_root)
    audit_dir = project_root / AUDIT_DIR_REL
    dict_dir = project_root / DATA_DICT_DIR_REL
    audit_dir.mkdir(parents=True, exist_ok=True)
    dict_dir.mkdir(parents=True, exist_ok=True)

    source_register = read_source_register(project_root)
    files = selected_files(project_root, source_register)

    if not files:
        raise FileNotFoundError(
            f"No staged NDIA POC selected files found. Checked source register and {project_root / SELECTED_DIR_REL}"
        )

    schema_rows: list[dict[str, Any]] = []
    period_rows: list[dict[str, Any]] = []
    geography_rows: list[dict[str, Any]] = []
    field_rows: list[dict[str, Any]] = []
    recommendation_rows: list[dict[str, Any]] = []

    print("NDIA public POC selected-file schema audit")
    print(f"Project root: {project_root}")
    print(f"Selected files found: {len(files)}")

    table_count = 0

    for file_path in files:
        print(f"\nReading: {file_path.name}")
        tables = read_tables_from_file(file_path, project_root)

        for table in tables:
            table_count += 1
            columns = table.columns
            df = table.full_df
            sample = table.sample

            register_meta = lookup_register_metadata(table, source_register)
            geo = infer_geography_from_columns(columns)
            codes = infer_code_columns(sample, columns)
            measure_signals = infer_measure_signals(columns, sample)
            periods = infer_periods(table)
            value_profile = infer_value_profile(df, columns)
            joinability = assess_joinability(
                table,
                geo,
                codes,
                register_meta.get("register_join_readiness", ""),
            )

            schema_row = {
                "run_timestamp_utc": now_utc(),
                "source_family": table.source_family,
                "file_name": table.file_name,
                "file_path": str(table.file_path),
                "extension": table.extension,
                "sheet_name": table.sheet_name,
                "table_label": table.table_label,
                "read_status": table.read_status,
                "read_error": table.read_error,
                "row_count": table.row_count,
                "column_count": table.column_count,
                "columns_json": json.dumps(columns, ensure_ascii=False),
                **register_meta,
                **geo,
                **codes,
                **measure_signals,
                **value_profile,
                **periods,
                **joinability,
                "primary_modelling_rule": "exclude_from_primary_2021_aligned_model",
                "poc_context_rule": "use_only_in_time_misaligned_ndia_public_poc_context_layer",
            }
            schema_rows.append(schema_row)

            period_rows.append(
                {
                    "source_family": table.source_family,
                    "file_name": table.file_name,
                    "sheet_name": table.sheet_name,
                    "table_label": table.table_label,
                    "read_status": table.read_status,
                    "period_min": periods["period_min"],
                    "period_max": periods["period_max"],
                    "period_count_detected": periods["period_count_detected"],
                    "period_dates_detected": periods["period_dates_detected"],
                    "period_detection_sources": periods["period_detection_sources"],
                    "register_period_min": register_meta.get("register_period_min", ""),
                    "register_period_max": register_meta.get("register_period_max", ""),
                    "period_alignment_assessment": (
                        "time_misaligned_context_layer"
                        if periods["period_min"] and periods["period_min"] > "2022-06-30"
                        else "near_or_within_2021_22_review_required"
                    ),
                }
            )

            geography_rows.append(
                {
                    "source_family": table.source_family,
                    "file_name": table.file_name,
                    "sheet_name": table.sheet_name,
                    "table_label": table.table_label,
                    "read_status": table.read_status,
                    "row_count": table.row_count,
                    "column_count": table.column_count,
                    "candidate_sa2_code_columns": codes.get("candidate_sa2_code_columns", ""),
                    "candidate_sa3_code_columns": codes.get("candidate_sa3_code_columns", ""),
                    "candidate_sa2_name_columns": codes.get("candidate_sa2_name_columns", ""),
                    "candidate_sa3_name_columns": codes.get("candidate_sa3_name_columns", ""),
                    "has_sa2_column_signal": geo.get("has_sa2_column_signal", False),
                    "has_sa3_column_signal": geo.get("has_sa3_column_signal", False),
                    "has_lga_column_signal": geo.get("has_lga_column_signal", False),
                    "has_service_district_column_signal": geo.get("has_service_district_column_signal", False),
                    "has_phn_column_signal": geo.get("has_phn_column_signal", False),
                    "has_state_column_signal": geo.get("has_state_column_signal", False),
                    "register_join_readiness": register_meta.get("register_join_readiness", ""),
                    "processing_recommendation": joinability["processing_recommendation"],
                    "recommended_join_key": joinability["recommended_join_key"],
                    "processing_reason": joinability["processing_reason"],
                }
            )

            recommendation_rows.append(
                {
                    "source_family": table.source_family,
                    "file_name": table.file_name,
                    "sheet_name": table.sheet_name,
                    "table_label": table.table_label,
                    "period_min": periods["period_min"],
                    "period_max": periods["period_max"],
                    "row_count": table.row_count,
                    "column_count": table.column_count,
                    "register_join_readiness": register_meta.get("register_join_readiness", ""),
                    "processing_recommendation": joinability["processing_recommendation"],
                    "recommended_join_key": joinability["recommended_join_key"],
                    "processing_reason": joinability["processing_reason"],
                    "next_script_stage": (
                        "build_sa2_participant_count_poc_source"
                        if joinability["processing_recommendation"] == "process_for_poc_sa2_join"
                        else "build_sa3_participant_count_poc_source"
                        if joinability["processing_recommendation"] == "process_for_poc_sa3_join"
                        else "hold_in_source_register_only"
                    ),
                }
            )

            for col in columns:
                field_rows.append(build_field_dictionary_row(table, col, df))

            print(
                f"  {table.table_label}: {table.read_status}; "
                f"rows={table.row_count}; cols={table.column_count}; "
                f"recommendation={joinability['processing_recommendation']}"
            )

    schema_df = pd.DataFrame(schema_rows)
    period_df = pd.DataFrame(period_rows)
    geography_df = pd.DataFrame(geography_rows)
    field_df = pd.DataFrame(field_rows)
    recommendations_df = pd.DataFrame(recommendation_rows)

    source_family_rank = {name: i for i, name in enumerate(SOURCE_FAMILY_ORDER)}
    if not schema_df.empty:
        schema_df["source_family_rank"] = schema_df["source_family"].map(source_family_rank).fillna(999).astype(int)
        schema_df = schema_df.sort_values(["source_family_rank", "file_name", "sheet_name"]).drop(columns=["source_family_rank"])

    if not period_df.empty:
        period_df["source_family_rank"] = period_df["source_family"].map(source_family_rank).fillna(999).astype(int)
        period_df = period_df.sort_values(["source_family_rank", "file_name", "sheet_name"]).drop(columns=["source_family_rank"])

    if not geography_df.empty:
        geography_df["source_family_rank"] = geography_df["source_family"].map(source_family_rank).fillna(999).astype(int)
        geography_df = geography_df.sort_values(["source_family_rank", "file_name", "sheet_name"]).drop(columns=["source_family_rank"])

    if not recommendations_df.empty:
        recommendations_df["source_family_rank"] = recommendations_df["source_family"].map(source_family_rank).fillna(999).astype(int)
        recommendations_df = recommendations_df.sort_values(
            ["next_script_stage", "source_family_rank", "file_name", "sheet_name"]
        ).drop(columns=["source_family_rank"])

    schema_path = audit_dir / SCHEMA_AUDIT_NAME
    period_path = audit_dir / PERIOD_AUDIT_NAME
    geography_path = audit_dir / GEOGRAPHY_AUDIT_NAME
    field_path = dict_dir / FIELD_DICTIONARY_NAME
    recommendations_path = audit_dir / PROCESSING_RECOMMENDATIONS_NAME

    schema_df.to_csv(schema_path, index=False, encoding="utf-8-sig")
    period_df.to_csv(period_path, index=False, encoding="utf-8-sig")
    geography_df.to_csv(geography_path, index=False, encoding="utf-8-sig")
    field_df.to_csv(field_path, index=False, encoding="utf-8-sig")
    recommendations_df.to_csv(recommendations_path, index=False, encoding="utf-8-sig")

    print("\nCreated NDIA public POC selected-file audit outputs:")
    print(f"  Schema audit:        {schema_path}")
    print(f"  Period audit:        {period_path}")
    print(f"  Geography audit:     {geography_path}")
    print(f"  Field dictionary:    {field_path}")
    print(f"  Recommendations:     {recommendations_path}")

    print("\nProcessing recommendations summary:")
    if recommendations_df.empty:
        print("  No recommendations created.")
    else:
        summary = (
            recommendations_df.groupby(["processing_recommendation", "next_script_stage"], dropna=False)
            .size()
            .reset_index(name="table_count")
            .sort_values(["next_script_stage", "processing_recommendation"])
        )
        print(summary.to_string(index=False))

    print("\nJoinable candidates:")
    joinable = recommendations_df[
        recommendations_df["next_script_stage"].isin(
            ["build_sa2_participant_count_poc_source", "build_sa3_participant_count_poc_source"]
        )
    ]
    if joinable.empty:
        print("  None detected. Review geography audit manually.")
    else:
        cols = [
            "source_family",
            "file_name",
            "sheet_name",
            "period_min",
            "period_max",
            "row_count",
            "recommended_join_key",
            "next_script_stage",
        ]
        print(joinable[cols].to_string(index=False))

    print("\nImportant modelling rule:")
    print("  NDIA public POC files remain excluded from the primary 2021-aligned model.")
    print("  Only directly joinable SA2/SA3 participant-count files should proceed to a separate POC context join.")


if __name__ == "__main__":
    main()
