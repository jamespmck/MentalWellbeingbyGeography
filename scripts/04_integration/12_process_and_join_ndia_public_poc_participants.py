from __future__ import annotations

from pathlib import Path
from datetime import datetime, timezone
import argparse
import json
import re
import shutil
import warnings
from typing import Any

import pandas as pd

PROJECT_ROOT = Path(r"D:\Good Measure\MentalWellbeingbyGeography")

MASTER_IN_PARQUET = PROJECT_ROOT / "data" / "processed" / "integrated" / "sa2_predictor_universe_v02_with_aihw_sa3.parquet"
MASTER_IN_CSV = PROJECT_ROOT / "data" / "processed" / "integrated" / "sa2_predictor_universe_v02_with_aihw_sa3.csv"

SELECTED_DIR = PROJECT_ROOT / "data" / "raw" / "ndia" / "public_poc_selected"

SA2_BRIDGE_CSV = PROJECT_ROOT / "data" / "processed" / "geography" / "bridge_sa2_2016_to_2021.csv"
SA3_BRIDGE_CSV = PROJECT_ROOT / "data" / "processed" / "geography" / "bridge_sa3_2016_to_2021.csv"

PROCESSED_DIR = PROJECT_ROOT / "data" / "processed" / "sources"
INTEGRATED_DIR = PROJECT_ROOT / "data" / "processed" / "integrated"
AUDIT_DIR = PROJECT_ROOT / "outputs" / "audits"
DICT_DIR = PROJECT_ROOT / "docs" / "data_dictionaries"
METHOD_DIR = PROJECT_ROOT / "docs" / "methodology"

SA2_SOURCE_OUT_CSV = PROCESSED_DIR / "ndia_public_poc_participants_sa2_2021_allocated.csv"
SA2_SOURCE_OUT_PARQUET = PROCESSED_DIR / "ndia_public_poc_participants_sa2_2021_allocated.parquet"
SA3_SOURCE_OUT_CSV = PROCESSED_DIR / "ndia_public_poc_participants_sa3_2021_allocated.csv"
SA3_SOURCE_OUT_PARQUET = PROCESSED_DIR / "ndia_public_poc_participants_sa3_2021_allocated.parquet"

MASTER_OUT_CSV = INTEGRATED_DIR / "sa2_predictor_universe_v03_with_ndia_public_poc_context.csv"
MASTER_OUT_PARQUET = INTEGRATED_DIR / "sa2_predictor_universe_v03_with_ndia_public_poc_context.parquet"

PROCESSING_AUDIT_CSV = AUDIT_DIR / "ndia_public_poc_participant_processing_audit.csv"
PERIOD_AUDIT_CSV = AUDIT_DIR / "ndia_public_poc_participant_period_selection_audit.csv"
ALLOCATION_AUDIT_CSV = AUDIT_DIR / "ndia_public_poc_participant_allocation_audit.csv"
JOIN_AUDIT_CSV = AUDIT_DIR / "sa2_predictor_universe_v03_ndia_public_poc_join_audit.csv"
UNMATCHED_2016_CSV = AUDIT_DIR / "ndia_public_poc_participant_unmatched_2016_codes.csv"
DUPLICATE_CODE_ROWS_CSV = AUDIT_DIR / "ndia_public_poc_participant_duplicate_code_rows_after_period_filter.csv"
FIELD_DICTIONARY_CSV = DICT_DIR / "ndia_public_poc_participant_context_field_dictionary.csv"
METHOD_NOTE_MD = METHOD_DIR / "ndia_public_poc_participant_context_layer_note.md"

CONFIG = {
    "SA2": {
        "source_pattern": "participants_by_sa2__*.csv",
        "code_2016_candidates": ["SA2Cd2016", "SA2_CODE_2016", "SA2 Code 2016", "SA2_MAINCODE_2016", "SA2 code"],
        "name_2016_candidates": ["SA2Nm2016", "SA2_NAME_2016", "SA2 Name 2016", "SA2 name"],
        "bridge_path": SA2_BRIDGE_CSV,
        "bridge_from_col": "sa2_code_2016",
        "bridge_to_col": "sa2_code_2021",
        "bridge_to_name_col": "sa2_name_2021",
        "master_join_key": "sa2_code_2021",
        "source_out_csv": SA2_SOURCE_OUT_CSV,
        "source_out_parquet": SA2_SOURCE_OUT_PARQUET,
        "prefix": "ndia_poc_sa2_participants",
        "source_family": "participants_by_sa2",
        "native_geography": "SA2_2016",
        "target_geography": "SA2_2021",
    },
    "SA3": {
        "source_pattern": "participants_by_sa3__*.csv",
        "code_2016_candidates": ["SA3Cd2016", "SA3_CODE_2016", "SA3 Code 2016", "SA3 code"],
        "name_2016_candidates": ["SA3Nm2016", "SA3_NAME_2016", "SA3 Name 2016", "SA3 name"],
        "bridge_path": SA3_BRIDGE_CSV,
        "bridge_from_col": "sa3_code_2016",
        "bridge_to_col": "sa3_code_2021",
        "bridge_to_name_col": "sa3_name_2021",
        "master_join_key": "sa3_code_2021",
        "source_out_csv": SA3_SOURCE_OUT_CSV,
        "source_out_parquet": SA3_SOURCE_OUT_PARQUET,
        "prefix": "ndia_poc_sa3_participants",
        "source_family": "participants_by_sa3",
        "native_geography": "SA3_2016",
        "target_geography": "SA3_2021",
    },
}

SUPPRESSED_VALUES = {
    "n.p.", "np", "n.p", "not published", "not available", "na", "n/a", "nan", "none", "null", "-", "", "suppressed"
}


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_dirs() -> None:
    for path in [PROCESSED_DIR, INTEGRATED_DIR, AUDIT_DIR, DICT_DIR, METHOD_DIR]:
        path.mkdir(parents=True, exist_ok=True)


def normalise_code(value: Any) -> pd.NA | str:
    if pd.isna(value):
        return pd.NA
    text = str(value).strip().replace("\ufeff", "")
    if text == "" or text.lower() in {"nan", "none", "null"}:
        return pd.NA
    text = re.sub(r"\.0$", "", text)
    text = re.sub(r"\s+", "", text)
    return text


def normalise_col(value: Any) -> str:
    text = str(value).strip().replace("\ufeff", "")
    text = re.sub(r"\s+", " ", text)
    return text


def col_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value).lower())


def read_csv_loose(path: Path) -> pd.DataFrame:
    # NDIA files are usually UTF-8 with BOM, but this keeps the script tolerant.
    for enc in ["utf-8-sig", "utf-8", "cp1252", "latin-1"]:
        try:
            df = pd.read_csv(path, dtype=str, low_memory=False, encoding=enc)
            df.columns = [normalise_col(c) for c in df.columns]
            return df
        except UnicodeDecodeError:
            continue
    df = pd.read_csv(path, dtype=str, low_memory=False)
    df.columns = [normalise_col(c) for c in df.columns]
    return df


def read_table(parquet_path: Path, csv_path: Path, label: str) -> pd.DataFrame:
    if parquet_path.exists():
        print(f"Reading {label}: {parquet_path}")
        return pd.read_parquet(parquet_path)
    if csv_path.exists():
        print(f"Reading {label}: {csv_path}")
        return pd.read_csv(csv_path, dtype=str, low_memory=False)
    raise FileNotFoundError(f"Could not find {label}: {parquet_path} or {csv_path}")


def exact_or_fuzzy_col(df: pd.DataFrame, candidates: list[str], required: bool = True) -> str | None:
    existing = {col_key(c): c for c in df.columns}
    for cand in candidates:
        key = col_key(cand)
        if key in existing:
            return existing[key]

    # Fuzzy contains fallback.
    cand_keys = [col_key(c) for c in candidates]
    for col in df.columns:
        key = col_key(col)
        if any(cand in key or key in cand for cand in cand_keys):
            return col

    if required:
        raise ValueError(f"Could not find required column. Candidates: {candidates}. Found: {list(df.columns)}")
    return None


def parse_numeric_series(series: pd.Series) -> pd.Series:
    s = series.astype("string").str.strip()
    lower = s.str.lower()
    s = s.mask(lower.isin(SUPPRESSED_VALUES), pd.NA)
    s = s.str.replace(",", "", regex=False)
    s = s.str.replace("$", "", regex=False)
    s = s.str.replace("%", "", regex=False)
    # Do not silently convert '<5' to 5. Treat small-cell/suppressed indicators as missing.
    s = s.mask(s.str.contains(r"^\s*[<>]", na=False), pd.NA)
    return pd.to_numeric(s, errors="coerce")


def numeric_quality(series: pd.Series) -> float:
    if series.empty:
        return 0.0
    parsed = parse_numeric_series(series)
    return float(parsed.notna().mean())


def detect_count_column(df: pd.DataFrame, code_col: str, name_col: str | None) -> tuple[str, pd.DataFrame]:
    rows = []
    excluded = {code_col}
    if name_col:
        excluded.add(name_col)

    for col in df.columns:
        key = col_key(col)
        if col in excluded:
            continue
        if any(token in key for token in ["code", "cd2016", "cd2021", "name", "state", "territory", "sa2", "sa3"]):
            # A column can contain state names or geography labels, not counts.
            if not any(token in key for token in ["participant", "count", "number", "total"]):
                continue

        quality = numeric_quality(df[col])
        score = quality * 10

        if "participant" in key or "participants" in key:
            score += 20
        if "count" in key or "number" in key or "num" in key:
            score += 15
        if key in {"participants", "participantcount", "count", "numberofparticipants", "totalparticipants"}:
            score += 15
        if any(token in key for token in ["rate", "percent", "percentage", "pct", "proportion"]):
            score -= 20
        if any(token in key for token in ["date", "period", "quarter", "month", "year", "asat"]):
            score -= 25

        rows.append(
            {
                "column_name": col,
                "column_key": key,
                "numeric_quality": quality,
                "score": score,
                "sample_values": " | ".join(df[col].dropna().astype(str).head(6).tolist()),
            }
        )

    audit = pd.DataFrame(rows).sort_values("score", ascending=False) if rows else pd.DataFrame(rows)

    if audit.empty or float(audit.iloc[0]["score"]) <= 0:
        raise ValueError("Could not identify participant count column. Review field dictionary/audit.")

    return str(audit.iloc[0]["column_name"]), audit


def parse_dates_from_series(series: pd.Series) -> pd.Series:
    """Parse explicit NDIA reporting-period/date values to ISO date strings.

    v6 is deliberately defensive: it is called only on explicit reporting-period
    columns such as RprtDt. It handles compact dates, month/year labels,
    financial-quarter labels and common day-month-year strings. It never passes
    bare geography-like numeric codes to pandas/dateutil.
    """
    raw = series.astype("string").str.strip()
    raw = raw.mask(raw.str.lower().isin(SUPPRESSED_VALUES), pd.NA)

    quarter_map = {
        "q1": "09-30",
        "q2": "12-31",
        "q3": "03-31",
        "q4": "06-30",
    }

    month_end_map = {
        "jan": "01-31", "january": "01-31",
        "feb": "02-28", "february": "02-28",
        "mar": "03-31", "march": "03-31",
        "apr": "04-30", "april": "04-30",
        "may": "05-31",
        "jun": "06-30", "june": "06-30",
        "jul": "07-31", "july": "07-31",
        "aug": "08-31", "august": "08-31",
        "sep": "09-30", "sept": "09-30", "september": "09-30",
        "oct": "10-31", "october": "10-31",
        "nov": "11-30", "november": "11-30",
        "dec": "12-31", "december": "12-31",
    }

    month_num_map = {
        "jan": 1, "january": 1,
        "feb": 2, "february": 2,
        "mar": 3, "march": 3,
        "apr": 4, "april": 4,
        "may": 5,
        "jun": 6, "june": 6,
        "jul": 7, "july": 7,
        "aug": 8, "august": 8,
        "sep": 9, "sept": 9, "september": 9,
        "oct": 10, "october": 10,
        "nov": 11, "november": 11,
        "dec": 12, "december": 12,
    }

    month_pattern = (
        r"jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
        r"jul(?:y)?|aug(?:ust)?|sep(?:t|tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?"
    )

    def clean(value: Any) -> str | None:
        if pd.isna(value):
            return None
        t = str(value).strip()
        if not t or t.lower() in SUPPRESSED_VALUES:
            return None
        t = t.replace("–", "-").replace("—", "-")
        t = t.replace("_", " ")
        t = re.sub(r"(?i)^data\s+as\s+at\s+", "", t)
        t = re.sub(r"(?i)^as\s+at\s+", "", t)
        t = re.sub(r"(?i)^quarter\s+ending\s+", "", t)
        t = re.sub(r"(?i)^period\s+ending\s+", "", t)
        t = re.sub(r"(?i)^report(?:ing)?\s+period\s*:?\s*", "", t)
        t = re.sub(r"\s+", " ", t).strip()
        return t

    def valid_year(year: int) -> bool:
        return 2000 <= year <= 2035

    def iso_from_parts(year: int, month: int, day: int) -> str | pd.NA:
        if not valid_year(year) or not (1 <= month <= 12) or not (1 <= day <= 31):
            return pd.NA
        try:
            parsed = pd.Timestamp(year=year, month=month, day=day)
        except Exception:
            return pd.NA
        return parsed.date().isoformat()

    def month_end(year: int, month: int) -> str | pd.NA:
        if not valid_year(year) or not (1 <= month <= 12):
            return pd.NA
        try:
            return (pd.Timestamp(year=year, month=month, day=1) + pd.offsets.MonthEnd(0)).date().isoformat()
        except Exception:
            return pd.NA

    def parse_one(value: Any) -> Any:
        t = clean(value)
        if t is None:
            return pd.NA

        low = t.lower().strip()
        compact = re.sub(r"\s+", "", low)

        # Compact numeric reporting dates, e.g. 20211231 or 202112.
        if re.fullmatch(r"20\d{6}", compact):
            return iso_from_parts(int(compact[0:4]), int(compact[4:6]), int(compact[6:8]))

        if re.fullmatch(r"20\d{4}", compact):
            return month_end(int(compact[0:4]), int(compact[4:6]))

        # Excel serial dates. Restrict to plausible modern administrative dates.
        if re.fullmatch(r"\d{5}", compact):
            serial = int(compact)
            if 36526 <= serial <= 50000:
                parsed = pd.to_datetime(serial, unit="D", origin="1899-12-30", errors="coerce")
                if pd.notna(parsed):
                    ts = pd.Timestamp(parsed)
                    if valid_year(int(ts.year)):
                        return ts.date().isoformat()
            return pd.NA

        # Calendar quarter labels: 2021 Q4, Q4 2021, 2021-Q4, Q4-2021.
        m = re.fullmatch(r"(20\d{2})\s*[- ]?\s*q([1-4])", low)
        if m:
            year = int(m.group(1))
            q = f"q{m.group(2)}"
            # Calendar quarter end, not financial-year quarter.
            cal_q_end = {"q1": "03-31", "q2": "06-30", "q3": "09-30", "q4": "12-31"}
            if valid_year(year):
                return f"{year}-{cal_q_end[q]}"
            return pd.NA

        m = re.fullmatch(r"q([1-4])\s*[- ]?\s*(20\d{2})", low)
        if m:
            q = f"q{m.group(1)}"
            year = int(m.group(2))
            cal_q_end = {"q1": "03-31", "q2": "06-30", "q3": "09-30", "q4": "12-31"}
            if valid_year(year):
                return f"{year}-{cal_q_end[q]}"
            return pd.NA

        # Financial-year quarter labels: Q4 FY21/22, FY2021-22 Q4, etc.
        m = re.search(r"q([1-4])\s*(?:fy)?\s*(\d{2,4})\s*[/\-]\s*(\d{2,4})", low)
        if not m:
            m2 = re.search(r"(?:fy)?\s*(\d{2,4})\s*[/\-]\s*(\d{2,4})\s*q([1-4])", low)
            if m2:
                start_yy = int(m2.group(1)[-2:])
                end_yy = int(m2.group(2)[-2:])
                q = f"q{m2.group(3)}"
            else:
                start_yy = end_yy = None
                q = None
        else:
            q = f"q{m.group(1)}"
            start_yy = int(m.group(2)[-2:])
            end_yy = int(m.group(3)[-2:])

        if q and start_yy is not None and end_yy is not None:
            year = 2000 + (start_yy if q in {"q1", "q2"} else end_yy)
            if valid_year(year):
                return f"{year}-{quarter_map[q]}"
            return pd.NA

        # Month-year labels such as Jun-2021, June 2022, Mar 26, 03/2022.
        m = re.fullmatch(rf"({month_pattern})[\s\-/]*(\d{{2,4}})", low)
        if m:
            month_word = m.group(1)
            yy = int(m.group(2))
            year = 2000 + yy if yy < 100 else yy
            return f"{year}-{month_end_map[month_word]}" if valid_year(year) else pd.NA

        m = re.fullmatch(r"(\d{1,2})[\-/](20\d{2}|\d{2})", low)
        if m:
            month = int(m.group(1))
            yy = int(m.group(2))
            year = 2000 + yy if yy < 100 else yy
            return month_end(year, month)

        # Date with month name: 30 June 2021, 30-Jun-2021, 30 Jun 21.
        m = re.fullmatch(rf"(\d{{1,2}})[\s\-/]+({month_pattern})[\s\-/]+(\d{{2,4}})", low)
        if m:
            day = int(m.group(1))
            month = month_num_map[m.group(2)]
            yy = int(m.group(3))
            year = 2000 + yy if yy < 100 else yy
            return iso_from_parts(year, month, day)

        # Numeric date-shaped strings only. Never parse bare numeric codes.
        m = re.fullmatch(r"(20\d{2})[-/](\d{1,2})[-/](\d{1,2})", low)
        if m:
            return iso_from_parts(int(m.group(1)), int(m.group(2)), int(m.group(3)))

        m = re.fullmatch(r"(\d{1,2})[-/](\d{1,2})[-/](\d{2,4})", low)
        if m:
            day = int(m.group(1))
            month = int(m.group(2))
            yy = int(m.group(3))
            year = 2000 + yy if yy < 100 else yy
            return iso_from_parts(year, month, day)

        # Last safe fallback for explicit period columns: values containing a month name.
        if re.search(month_pattern, low):
            try:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    parsed = pd.to_datetime(t, errors="coerce", dayfirst=True)
                if pd.notna(parsed):
                    ts = pd.Timestamp(parsed)
                    if valid_year(int(ts.year)):
                        return ts.date().isoformat()
            except Exception:
                return pd.NA

        return pd.NA

    return pd.Series([parse_one(v) for v in raw], index=series.index, dtype="string")


def detect_period_columns(df: pd.DataFrame) -> pd.DataFrame:
    rows = []

    explicit_period_keys = {
        "rprtdt",
        "rptdt",
        "rptdate",
        "reportdt",
        "reportdate",
        "reportingdt",
        "reportingdate",
        "rprtdat",
        "asat",
        "asatdate",
        "extractdate",
        "referencedate",
        "referenceperiod",
        "reportingperiod",
    }

    # First pass: exact explicit reporting-date columns only. This avoids the
    # false-negative path where RprtDt is present but the generic detector skips it.
    for col in df.columns:
        key = col_key(col)
        if key not in explicit_period_keys:
            continue
        parsed = parse_dates_from_series(df[col])
        parse_rate = float(parsed.notna().mean()) if len(df) else 0.0
        non_null_dates = sorted(parsed.dropna().unique().tolist())
        rows.append(
            {
                "column_name": col,
                "column_key": key,
                "parse_rate": parse_rate,
                "score": 100 + parse_rate * 10,
                "period_count": len(non_null_dates),
                "period_min": non_null_dates[0] if non_null_dates else "",
                "period_max": non_null_dates[-1] if non_null_dates else "",
                "sample_periods": " | ".join(non_null_dates[:12]),
                "sample_values": " | ".join(df[col].dropna().astype(str).head(12).tolist()),
            }
        )

    # Second pass: date-like headers only, excluding labels/codes/counts.
    period_name_tokens = [
        "date", "period", "quarter", "month", "year", "asat", "as_at", "report", "reference", "extract"
    ]
    hard_exclude_tokens = [
        "code", "maincode", "cd2016", "cd2021", "sa2cd", "sa3cd", "sa4cd", "postcode",
        "name", "participantcount", "participants", "count", "number", "total", "amount", "value"
    ]

    already = {row["column_name"] for row in rows}
    for col in df.columns:
        if col in already:
            continue
        key = col_key(col)
        col_text = str(col).strip().lower()
        name_is_period_like = any(token in key for token in period_name_tokens)
        column_header_is_date = bool(
            re.fullmatch(r"\d{4}-\d{1,2}-\d{1,2}", col_text)
            or re.fullmatch(r"\d{1,2}/\d{1,2}/\d{2,4}", col_text)
            or re.fullmatch(r"(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*[\s\-/]*\d{2,4}", col_text)
        )
        if not name_is_period_like and not column_header_is_date:
            continue
        if any(token in key for token in hard_exclude_tokens):
            continue

        parsed = parse_dates_from_series(df[col])
        parse_rate = float(parsed.notna().mean()) if len(df) else 0.0
        if parse_rate <= 0:
            continue
        non_null_dates = sorted(parsed.dropna().unique().tolist())
        rows.append(
            {
                "column_name": col,
                "column_key": key,
                "parse_rate": parse_rate,
                "score": 10 + parse_rate * 10,
                "period_count": len(non_null_dates),
                "period_min": non_null_dates[0] if non_null_dates else "",
                "period_max": non_null_dates[-1] if non_null_dates else "",
                "sample_periods": " | ".join(non_null_dates[:12]),
                "sample_values": " | ".join(df[col].dropna().astype(str).head(12).tolist()),
            }
        )

    if not rows:
        return pd.DataFrame(rows)
    return pd.DataFrame(rows).sort_values("score", ascending=False)

def period_from_filename(path: Path) -> str | None:
    text = path.name
    m = re.search(r"__(\d{4}-\d{2}-\d{2})__", text)
    if m:
        return m.group(1)
    m = re.search(r"(\d{4})[-_ ]?(\d{2})[-_ ]?(\d{2})", text)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    return None


def iso_to_timestamp(value: str | None) -> pd.Timestamp | None:
    if not value or str(value).lower() in {"unknown", "nan", "none", "null", ""}:
        return None
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        return None
    return pd.Timestamp(parsed)


def choose_period(
    available: list[str],
    policy: str,
    target_date: str,
    window_start: str,
    window_end: str,
    tie_break: str,
) -> tuple[str, dict]:
    if not available:
        return "unknown", {
            "selected_period_alignment_basis": "no_available_periods",
            "selected_period_in_target_window": False,
            "selected_period_days_from_target": "",
        }

    target = iso_to_timestamp(target_date)
    start = iso_to_timestamp(window_start)
    end = iso_to_timestamp(window_end)

    available_ts = [(p, iso_to_timestamp(p)) for p in sorted(set(available))]
    available_ts = [(p, ts) for p, ts in available_ts if ts is not None]

    if not available_ts:
        return available[0], {
            "selected_period_alignment_basis": "unparsed_available_periods",
            "selected_period_in_target_window": False,
            "selected_period_days_from_target": "",
        }

    if policy == "earliest":
        selected, selected_ts = available_ts[0]
        basis = "earliest_available"
    elif policy == "latest":
        selected, selected_ts = available_ts[-1]
        basis = "latest_available"
    else:
        in_window = [
            (p, ts) for p, ts in available_ts
            if (start is None or ts >= start) and (end is None or ts <= end)
        ]

        if policy == "latest_within_window":
            if in_window:
                selected, selected_ts = max(in_window, key=lambda item: item[1])
                basis = "latest_available_inside_target_window"
            else:
                selected, selected_ts = min(
                    available_ts,
                    key=lambda item: abs((item[1] - target).days) if target is not None else 10**9,
                )
                basis = "no_period_inside_window_closest_available_to_target"
        else:
            # Default: closest to target inside window; if no in-window periods, closest to target anywhere.
            candidates = in_window if in_window else available_ts
            basis = "closest_to_target_inside_window" if in_window else "no_period_inside_window_closest_available_to_target"

            def sort_key(item: tuple[str, pd.Timestamp]) -> tuple[int, int]:
                p, ts = item
                distance = abs((ts - target).days) if target is not None else 10**9
                # When two periods are equally close, prefer the later period by default.
                tie = -int(ts.timestamp()) if tie_break == "later" else int(ts.timestamp())
                return (distance, tie)

            selected, selected_ts = min(candidates, key=sort_key)

    in_target_window = bool(
        (start is None or selected_ts >= start)
        and (end is None or selected_ts <= end)
    )
    days_from_target = int((selected_ts - target).days) if target is not None else ""

    return selected, {
        "selected_period_alignment_basis": basis,
        "selected_period_in_target_window": in_target_window,
        "selected_period_days_from_target": days_from_target,
    }


def select_reference_period(
    df: pd.DataFrame,
    source_path: Path,
    policy: str,
    target_date: str = "2021-12-31",
    window_start: str = "2021-01-01",
    window_end: str = "2022-06-30",
    tie_break: str = "later",
) -> tuple[pd.DataFrame, dict, str | None]:
    period_audit = detect_period_columns(df)

    if not period_audit.empty:
        print("  Period column candidates:")
        print(period_audit[["column_name", "parse_rate", "period_count", "period_min", "period_max", "sample_values"]].head(5).to_string(index=False))

    if not period_audit.empty and float(period_audit.iloc[0]["parse_rate"]) > 0:
        col = str(period_audit.iloc[0]["column_name"])
        parsed = parse_dates_from_series(df[col])
        available = sorted(parsed.dropna().unique().tolist())
        if available:
            selected, alignment = choose_period(
                available=available,
                policy=policy,
                target_date=target_date,
                window_start=window_start,
                window_end=window_end,
                tie_break=tie_break,
            )
            filtered = df.loc[parsed == selected].copy()
            return filtered, {
                "period_detection_method": "column_values",
                "period_column": col,
                "period_policy": policy,
                "target_date": target_date,
                "target_window_start": window_start,
                "target_window_end": window_end,
                "target_tie_break": tie_break,
                "available_period_count": len(available),
                "available_periods": " | ".join(available),
                "period_min": available[0],
                "period_max": available[-1],
                "selected_reference_period": selected,
                "rows_before_period_filter": len(df),
                "rows_after_period_filter": len(filtered),
                "period_column_parse_rate": float(period_audit.iloc[0]["parse_rate"]),
                **alignment,
            }, selected

    fallback = period_from_filename(source_path)

    # For target-window selection, a filename date such as the public download
    # date is not acceptable when the file itself contains row-level reporting
    # periods. If no explicit period column is found, fail fast rather than
    # silently selecting a current 2026 file date.
    if policy in {"target_window", "latest_within_window"}:
        raise ValueError(
            "No explicit reporting-period column was detected. Expected a column such as RprtDt. "
            f"Available columns: {list(df.columns)}"
        )

    return df.copy(), {
        "period_detection_method": "filename_or_single_file",
        "period_column": "",
        "period_policy": policy,
        "target_date": target_date,
        "target_window_start": window_start,
        "target_window_end": window_end,
        "target_tie_break": tie_break,
        "available_period_count": 1 if fallback else 0,
        "available_periods": fallback or "",
        "period_min": fallback or "",
        "period_max": fallback or "",
        "selected_reference_period": fallback or "unknown",
        "rows_before_period_filter": len(df),
        "rows_after_period_filter": len(df),
        "period_column_parse_rate": 0,
        "selected_period_alignment_basis": "filename_or_single_file",
        "selected_period_in_target_window": bool(
            fallback
            and iso_to_timestamp(fallback) is not None
            and iso_to_timestamp(window_start) <= iso_to_timestamp(fallback) <= iso_to_timestamp(window_end)
        ),
        "selected_period_days_from_target": (
            int((iso_to_timestamp(fallback) - iso_to_timestamp(target_date)).days)
            if fallback and iso_to_timestamp(fallback) is not None and iso_to_timestamp(target_date) is not None
            else ""
        ),
    }, fallback or "unknown"


def locate_source_file(cfg: dict) -> Path:
    matches = sorted(SELECTED_DIR.glob(cfg["source_pattern"]))
    if not matches:
        raise FileNotFoundError(f"No staged NDIA POC file matching {cfg['source_pattern']} in {SELECTED_DIR}")
    # The source register should have staged one file. If more than one, use newest modified and audit warning later.
    return max(matches, key=lambda p: p.stat().st_mtime)


def find_name_col(df: pd.DataFrame, cfg: dict) -> str | None:
    try:
        return exact_or_fuzzy_col(df, cfg["name_2016_candidates"], required=False)
    except Exception:
        return None


def build_2016_code_level(
    df: pd.DataFrame,
    cfg: dict,
    source_path: Path,
    policy: str,
    target_date: str,
    window_start: str,
    window_end: str,
    tie_break: str,
) -> tuple[pd.DataFrame, dict, pd.DataFrame, pd.DataFrame]:
    code_col = exact_or_fuzzy_col(df, cfg["code_2016_candidates"], required=True)
    name_col = find_name_col(df, cfg)
    count_col, count_col_audit = detect_count_column(df, code_col, name_col)

    filtered, period_info, selected_period = select_reference_period(
        df=df,
        source_path=source_path,
        policy=policy,
        target_date=target_date,
        window_start=window_start,
        window_end=window_end,
        tie_break=tie_break,
    )

    working = filtered.copy()
    working["_code_2016"] = working[code_col].map(normalise_code).astype("string")
    working["_participant_count"] = parse_numeric_series(working[count_col])

    if name_col:
        working["_name_2016"] = working[name_col].astype("string").str.strip()
    else:
        working["_name_2016"] = pd.NA

    duplicate_rows = (
        working.groupby("_code_2016", dropna=False)
        .size()
        .reset_index(name="rows_after_period_filter")
        .query("rows_after_period_filter > 1")
        .copy()
    )
    duplicate_rows["source_family"] = cfg["source_family"]
    duplicate_rows["source_file"] = source_path.name
    duplicate_rows["selected_reference_period"] = selected_period

    code_level = (
        working.dropna(subset=["_code_2016"])
        .groupby("_code_2016", dropna=False)
        .agg(
            name_2016=("_name_2016", lambda x: next((str(v) for v in x if pd.notna(v) and str(v).strip()), "")),
            participant_count_2016=("_participant_count", "sum"),
            source_row_count=("_participant_count", "size"),
            non_missing_count_rows=("_participant_count", lambda x: int(pd.Series(x).notna().sum())),
            suppressed_or_missing_count_rows=("_participant_count", lambda x: int(pd.Series(x).isna().sum())),
        )
        .reset_index()
        .rename(columns={"_code_2016": cfg["bridge_from_col"]})
    )

    # pandas sum returns 0 for all-missing groups by default. Correct that.
    all_missing_mask = code_level["non_missing_count_rows"] == 0
    code_level.loc[all_missing_mask, "participant_count_2016"] = pd.NA

    source_info = {
        "source_family": cfg["source_family"],
        "source_file": source_path.name,
        "source_path": str(source_path),
        "native_geography": cfg["native_geography"],
        "target_geography": cfg["target_geography"],
        "raw_row_count": len(df),
        "raw_column_count": len(df.columns),
        "code_column": code_col,
        "name_column": name_col or "",
        "count_column": count_col,
        "unique_2016_codes_after_period_filter": int(code_level[cfg["bridge_from_col"]].nunique(dropna=True)),
        "rows_after_period_filter": len(filtered),
        "duplicate_2016_codes_after_period_filter": int((duplicate_rows["rows_after_period_filter"] > 1).sum()) if not duplicate_rows.empty else 0,
        "participant_count_total_2016_before_allocation": float(pd.to_numeric(code_level["participant_count_2016"], errors="coerce").sum()),
        **period_info,
    }

    count_col_audit["source_family"] = cfg["source_family"]
    count_col_audit["source_file"] = source_path.name
    count_col_audit["selected_count_column"] = count_col_audit["column_name"] == count_col

    return code_level, source_info, count_col_audit, duplicate_rows


def read_bridge(cfg: dict) -> pd.DataFrame:
    path = cfg["bridge_path"]
    if not path.exists():
        raise FileNotFoundError(f"Missing bridge file: {path}")
    bridge = pd.read_csv(path, dtype=str, low_memory=False)
    bridge.columns = [normalise_col(c) for c in bridge.columns]

    required = [cfg["bridge_from_col"], cfg["bridge_to_col"], cfg["bridge_to_name_col"], "ratio_from_to"]
    missing = [c for c in required if c not in bridge.columns]
    if missing:
        raise ValueError(f"Bridge {path} is missing required columns: {missing}. Found: {list(bridge.columns)}")

    bridge[cfg["bridge_from_col"]] = bridge[cfg["bridge_from_col"]].map(normalise_code).astype("string")
    bridge[cfg["bridge_to_col"]] = bridge[cfg["bridge_to_col"]].map(normalise_code).astype("string")
    bridge["ratio_from_to"] = pd.to_numeric(bridge["ratio_from_to"], errors="coerce")
    bridge = bridge.dropna(subset=[cfg["bridge_from_col"]]).copy()
    return bridge


def allocate_to_2021(code_level: pd.DataFrame, cfg: dict, selected_period: str, source_info: dict) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    bridge = read_bridge(cfg)
    from_col = cfg["bridge_from_col"]
    to_col = cfg["bridge_to_col"]
    to_name_col = cfg["bridge_to_name_col"]
    prefix = cfg["prefix"]

    merged = code_level.merge(
        bridge,
        on=from_col,
        how="left",
        validate="many_to_many",
        indicator=True,
    )

    unmatched = merged.loc[merged["_merge"] == "left_only"].copy()
    unmatched["source_family"] = cfg["source_family"]
    unmatched["source_file"] = source_info["source_file"]
    unmatched["selected_reference_period"] = selected_period
    unmatched["reason"] = "2016 geography code not found in official ABS 2016→2021 bridge"

    allocatable = merged.loc[
        (merged["_merge"] == "both")
        & merged[to_col].notna()
        & merged["ratio_from_to"].notna()
        & (merged["ratio_from_to"] > 0)
    ].copy()

    allocatable["participant_count_allocated"] = (
        pd.to_numeric(allocatable["participant_count_2016"], errors="coerce") * allocatable["ratio_from_to"]
    )

    allocatable["allocation_required_for_counts"] = (
        allocatable.groupby(from_col)[to_col].transform("nunique") > 1
    )

    grouped = (
        allocatable.groupby([to_col, to_name_col], dropna=False)
        .agg(
            participant_count_allocated=("participant_count_allocated", "sum"),
            contributing_2016_code_count=(from_col, "nunique"),
            contributing_bridge_row_count=(from_col, "size"),
            allocation_required_2016_code_count=(
                "allocation_required_for_counts",
                lambda x: int(pd.Series(x).fillna(False).astype(bool).sum()),
            ),
            min_ratio_from_to=("ratio_from_to", "min"),
            max_ratio_from_to=("ratio_from_to", "max"),
        )
        .reset_index()
    )

    source_col = f"source_{prefix}_public_poc_present_flag"

    out = pd.DataFrame()
    out[cfg["master_join_key"]] = grouped[to_col].map(normalise_code).astype("string")
    out[f"{prefix}_count"] = grouped["participant_count_allocated"]
    out[f"{prefix}_reference_period"] = selected_period
    out[f"{prefix}_native_geography"] = cfg["native_geography"]
    out[f"{prefix}_target_geography"] = cfg["target_geography"]
    out[f"{prefix}_allocation_method"] = "ABS 2016→2021 RATIO_FROM_TO allocation"
    out[f"{prefix}_contributing_2016_code_count"] = grouped["contributing_2016_code_count"]
    out[f"{prefix}_contributing_bridge_row_count"] = grouped["contributing_bridge_row_count"]
    out[f"{prefix}_allocation_required_bridge_row_count"] = grouped["allocation_required_2016_code_count"]
    out[f"{prefix}_min_ratio_from_to"] = grouped["min_ratio_from_to"]
    out[f"{prefix}_max_ratio_from_to"] = grouped["max_ratio_from_to"]
    out[source_col] = True

    original_total = pd.to_numeric(code_level["participant_count_2016"], errors="coerce").sum()
    allocated_total = pd.to_numeric(out[f"{prefix}_count"], errors="coerce").sum()
    unmatched_total = pd.to_numeric(unmatched.get("participant_count_2016", pd.Series(dtype="float")), errors="coerce").sum()

    audit = {
        "source_family": cfg["source_family"],
        "native_geography": cfg["native_geography"],
        "target_geography": cfg["target_geography"],
        "selected_reference_period": selected_period,
        "source_2016_code_count": int(code_level[from_col].nunique(dropna=True)),
        "target_2021_code_count": int(out[cfg["master_join_key"]].nunique(dropna=True)),
        "bridge_row_count_used": len(allocatable),
        "unmatched_2016_code_count": int(unmatched[from_col].nunique(dropna=True)) if not unmatched.empty else 0,
        "source_participant_total_2016": float(original_total) if pd.notna(original_total) else None,
        "allocated_participant_total_2021": float(allocated_total) if pd.notna(allocated_total) else None,
        "unmatched_participant_total_2016": float(unmatched_total) if pd.notna(unmatched_total) else None,
        "allocation_total_difference": float(original_total - allocated_total) if pd.notna(original_total) and pd.notna(allocated_total) else None,
        "allocation_total_retained_pct": round(float(allocated_total / original_total * 100), 4) if original_total else None,
        "allocation_required_2016_codes": int(
            allocatable.loc[allocatable["allocation_required_for_counts"], from_col].nunique(dropna=True)
        ),
        "status": "pass" if original_total and allocated_total / original_total >= 0.98 else "review",
        "notes": "Participant counts are allocated from ASGS 2016 to ASGS 2021 using official ABS RATIO_FROM_TO. NDIA POC layer uses the best available 2021/2022-aligned public reference period where available and remains excluded from the primary model by default.",
    }

    return out, unmatched, audit


def write_table(df: pd.DataFrame, csv_path: Path, parquet_path: Path) -> None:
    df.to_csv(csv_path, index=False, encoding="utf-8-sig")
    try:
        df.to_parquet(parquet_path, index=False)
    except Exception as exc:
        print(f"Warning: could not write parquet {parquet_path}: {exc}")


def build_field_dictionary(sa2_out: pd.DataFrame, sa3_out: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for df, geography in [(sa2_out, "SA2"), (sa3_out, "SA3")]:
        for col in df.columns:
            if col in {"sa2_code_2021", "sa3_code_2021"}:
                role = "join_key"
            elif col.endswith("_count"):
                role = "poc_context_measure"
            elif col.startswith("source_"):
                role = "source_presence_flag"
            else:
                role = "metadata"
            rows.append(
                {
                    "column_name": col,
                    "field_role": role,
                    "source_family": "ndia_public_poc_participant_counts",
                    "native_geography": f"{geography}_2016",
                    "analysis_geography": f"{geography}_2021" if geography == "SA2" else "SA3_2021 repeated across SA2 rows after join",
                    "primary_model_use": "exclude",
                    "poc_model_use": "candidate_context_variable" if role == "poc_context_measure" else "metadata_or_join_field",
                    "notes": "Time-misaligned NDIA public POC context layer. Counts allocated from ASGS 2016 to ASGS 2021 using ABS RATIO_FROM_TO.",
                }
            )
    return pd.DataFrame(rows)


def join_to_master(sa2_out: pd.DataFrame, sa3_out: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    master = read_table(MASTER_IN_PARQUET, MASTER_IN_CSV, "SA2 master v02 with AIHW SA3")
    before_rows = len(master)
    before_cols = len(master.columns)

    master = master.copy()
    master["sa2_code_2021"] = master["sa2_code_2021"].map(normalise_code).astype("string")
    master["sa3_code_2021"] = master["sa3_code_2021"].map(normalise_code).astype("string")

    sa2 = sa2_out.copy()
    sa3 = sa3_out.copy()
    sa2["sa2_code_2021"] = sa2["sa2_code_2021"].map(normalise_code).astype("string")
    sa3["sa3_code_2021"] = sa3["sa3_code_2021"].map(normalise_code).astype("string")

    if sa2.duplicated("sa2_code_2021").sum() > 0:
        raise ValueError("SA2 NDIA output has duplicate sa2_code_2021 values.")
    if sa3.duplicated("sa3_code_2021").sum() > 0:
        raise ValueError("SA3 NDIA output has duplicate sa3_code_2021 values.")

    joined = master.merge(sa2, on="sa2_code_2021", how="left", validate="one_to_one")
    joined = joined.merge(sa3, on="sa3_code_2021", how="left", validate="many_to_one")

    for col in [c for c in joined.columns if c.startswith("source_ndia_poc_") and c.endswith("_present_flag")]:
        joined[col] = joined[col].fillna(False).astype(bool)

    after_rows = len(joined)
    after_cols = len(joined.columns)

    sa2_flag = "source_ndia_poc_sa2_participants_public_poc_present_flag"
    sa3_flag = "source_ndia_poc_sa3_participants_public_poc_present_flag"

    audit_rows = [
        {
            "check_name": "master_rows_before_join",
            "value": before_rows,
            "status": "pass" if before_rows == 2472 else "review",
            "notes": "Expected SA2 row count for current master.",
        },
        {
            "check_name": "master_columns_before_join",
            "value": before_cols,
            "status": "info",
            "notes": "",
        },
        {
            "check_name": "ndia_sa2_source_rows",
            "value": len(sa2),
            "status": "info",
            "notes": "2021 SA2 rows with allocated NDIA public POC participant counts.",
        },
        {
            "check_name": "ndia_sa3_source_rows",
            "value": len(sa3),
            "status": "info",
            "notes": "2021 SA3 rows with allocated NDIA public POC participant counts.",
        },
        {
            "check_name": "master_rows_after_join",
            "value": after_rows,
            "status": "pass" if after_rows == before_rows else "fail",
            "notes": "Join must not change SA2 row count.",
        },
        {
            "check_name": "master_columns_after_join",
            "value": after_cols,
            "status": "info",
            "notes": "",
        },
        {
            "check_name": "duplicate_sa2_rows_after_join",
            "value": int(joined.duplicated("sa2_code_2021").sum()),
            "status": "pass" if int(joined.duplicated("sa2_code_2021").sum()) == 0 else "fail",
            "notes": "",
        },
        {
            "check_name": "sa2_rows_with_ndia_sa2_poc_count",
            "value": int(joined[sa2_flag].sum()) if sa2_flag in joined.columns else 0,
            "status": "info",
            "notes": "Rows with joined SA2-level NDIA public POC participant counts.",
        },
        {
            "check_name": "sa2_rows_with_ndia_sa3_poc_count",
            "value": int(joined[sa3_flag].sum()) if sa3_flag in joined.columns else 0,
            "status": "info",
            "notes": "Rows with joined SA3-level NDIA public POC participant counts repeated across SA2 rows.",
        },
    ]

    return joined, pd.DataFrame(audit_rows)


def write_method_note(sa2_info: dict, sa3_info: dict) -> None:
    text = f"""# NDIA public proof-of-concept participant context layer

Generated: {now_utc()}

This layer stages public NDIA participant-count files found in the project discovery workflow and selects the best available reference period for alignment with the 2021 Census, the 2021-22 AIHW service-system layer and the NSMHW 2020-22 outcome window.

## Method

- NDIA participant files use ASGS 2016 geography codes.
- The active MentalWellbeingByGeography master uses ASGS 2021 geography codes.
- Participant counts are allocated from 2016 geography to 2021 geography using the official ABS correspondence field `RATIO_FROM_TO`.
- SA2 participant counts are joined to the SA2 master by `sa2_code_2021`.
- SA3 participant counts are joined by `sa3_code_2021` and repeat across SA2s within the same SA3.

## Reference periods selected

- SA2 participant file: `{sa2_info.get('selected_reference_period', 'unknown')}`
- SA3 participant file: `{sa3_info.get('selected_reference_period', 'unknown')}`

## Modelling rule

This NDIA layer is a public proof-of-concept context layer. The period selection targets the 2021/2022 evidence window where available, but the public NDIA extracts remain structurally limited and must be excluded from the primary model unless the modelling plan explicitly treats them as a separate sensitivity/context layer.

It may be used only in a separate proof-of-concept, sensitivity or demonstration model with explicit caveats. It should not be interpreted as a complete measure of NDIS access, psychosocial support investment or service availability.

## Outputs

- `data/processed/sources/ndia_public_poc_participants_sa2_2021_allocated.csv`
- `data/processed/sources/ndia_public_poc_participants_sa3_2021_allocated.csv`
- `data/processed/integrated/sa2_predictor_universe_v03_with_ndia_public_poc_context.csv`
"""
    METHOD_NOTE_MD.write_text(text, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Process and join NDIA public POC participant counts using ABS ASGS 2016→2021 bridges.")
    parser.add_argument(
        "--period-policy",
        choices=["target_window", "latest_within_window", "closest_to_target_any", "earliest", "latest"],
        default="target_window",
        help="Reference-period policy. Default selects the available period closest to target date inside the target window, with fallback to closest available.",
    )
    parser.add_argument("--target-date", default="2021-12-31", help="Preferred NDIA reference date for alignment with the 2021/2022 evidence window.")
    parser.add_argument("--window-start", default="2021-01-01", help="Start of acceptable NDIA alignment window.")
    parser.add_argument("--window-end", default="2022-06-30", help="End of acceptable NDIA alignment window.")
    parser.add_argument("--tie-break", choices=["earlier", "later"], default="later", help="Tie-break when two periods are equally close to target date.")
    parser.add_argument("--min-allocation-retained-pct", type=float, default=98.0, help="Review/fail threshold for retained participant count after bridge allocation.")
    parser.add_argument("--fail-on-unmatched", action="store_true", help="Raise an error if any NDIA 2016 codes are unmatched to the ABS bridge.")
    args = parser.parse_args()

    ensure_dirs()

    print("NDIA public POC participant processor and context join - target 2021/2022 alignment v6")
    print(f"Project root: {PROJECT_ROOT}")
    print(f"Period policy: {args.period_policy}")
    print(f"Target date: {args.target_date}")
    print(f"Target window: {args.window_start} to {args.window_end}")
    print(f"Tie-break: {args.tie_break}")
    print("\nImportant: this creates a public NDIA POC context layer, not a primary model input by default.\n")

    processing_rows = []
    period_rows = []
    allocation_rows = []
    unmatched_all = []
    duplicate_all = []
    count_col_audits = []

    outputs = {}
    source_infos = {}

    for level, cfg in CONFIG.items():
        print(f"Processing {level} NDIA participant source...")
        source_path = locate_source_file(cfg)
        print(f"  Source: {source_path}")

        raw = read_csv_loose(source_path)
        code_level, source_info, count_col_audit, duplicate_rows = build_2016_code_level(
            raw,
            cfg,
            source_path,
            args.period_policy,
            args.target_date,
            args.window_start,
            args.window_end,
            args.tie_break,
        )
        selected_period = str(source_info.get("selected_reference_period", "unknown"))

        allocated, unmatched, allocation_audit = allocate_to_2021(code_level, cfg, selected_period, source_info)

        # Hard review/fail rules.
        retained = allocation_audit.get("allocation_total_retained_pct")
        unmatched_count = int(allocation_audit.get("unmatched_2016_code_count") or 0)
        if retained is not None and retained < args.min_allocation_retained_pct:
            allocation_audit["status"] = "review"
            allocation_audit["notes"] += f" Retained pct below threshold {args.min_allocation_retained_pct}."
        if args.fail_on_unmatched and unmatched_count > 0:
            raise ValueError(f"{level}: {unmatched_count} unmatched 2016 codes. See unmatched audit.")

        write_table(allocated, cfg["source_out_csv"], cfg["source_out_parquet"])
        print(f"  Created: {cfg['source_out_csv']}")
        print(f"  Rows: {len(allocated):,}; allocation retained: {allocation_audit.get('allocation_total_retained_pct')}%")

        processing_rows.append(source_info)
        period_rows.append({"source_family": cfg["source_family"], **{k: source_info.get(k) for k in [
            "period_detection_method", "period_column", "period_policy", "target_date", "target_window_start", "target_window_end", "target_tie_break", "available_period_count", "available_periods", "period_min", "period_max", "selected_reference_period", "selected_period_alignment_basis", "selected_period_in_target_window", "selected_period_days_from_target", "rows_before_period_filter", "rows_after_period_filter", "period_column_parse_rate"
        ]}})
        allocation_rows.append(allocation_audit)
        count_col_audits.append(count_col_audit)
        if not unmatched.empty:
            unmatched_all.append(unmatched)
        if not duplicate_rows.empty:
            duplicate_all.append(duplicate_rows)

        outputs[level] = allocated
        source_infos[level] = source_info

    joined, join_audit = join_to_master(outputs["SA2"], outputs["SA3"])
    joined.to_csv(MASTER_OUT_CSV, index=False, encoding="utf-8-sig")
    try:
        joined.to_parquet(MASTER_OUT_PARQUET, index=False)
    except Exception as exc:
        print(f"Warning: could not write master parquet: {exc}")

    pd.DataFrame(processing_rows).to_csv(PROCESSING_AUDIT_CSV, index=False, encoding="utf-8-sig")
    pd.DataFrame(period_rows).to_csv(PERIOD_AUDIT_CSV, index=False, encoding="utf-8-sig")
    pd.DataFrame(allocation_rows).to_csv(ALLOCATION_AUDIT_CSV, index=False, encoding="utf-8-sig")
    join_audit.to_csv(JOIN_AUDIT_CSV, index=False, encoding="utf-8-sig")

    if unmatched_all:
        pd.concat(unmatched_all, ignore_index=True, sort=False).to_csv(UNMATCHED_2016_CSV, index=False, encoding="utf-8-sig")
    else:
        pd.DataFrame().to_csv(UNMATCHED_2016_CSV, index=False, encoding="utf-8-sig")

    if duplicate_all:
        pd.concat(duplicate_all, ignore_index=True, sort=False).to_csv(DUPLICATE_CODE_ROWS_CSV, index=False, encoding="utf-8-sig")
    else:
        pd.DataFrame().to_csv(DUPLICATE_CODE_ROWS_CSV, index=False, encoding="utf-8-sig")

    if count_col_audits:
        pd.concat(count_col_audits, ignore_index=True, sort=False).to_csv(
            AUDIT_DIR / "ndia_public_poc_participant_count_column_detection_audit.csv",
            index=False,
            encoding="utf-8-sig",
        )

    field_dict = build_field_dictionary(outputs["SA2"], outputs["SA3"])
    field_dict.to_csv(FIELD_DICTIONARY_CSV, index=False, encoding="utf-8-sig")

    write_method_note(source_infos["SA2"], source_infos["SA3"])

    print("\nCreated NDIA public POC participant context outputs:")
    print(f"  SA2 allocated source: {SA2_SOURCE_OUT_CSV}")
    print(f"  SA3 allocated source: {SA3_SOURCE_OUT_CSV}")
    print(f"  v03 context master:   {MASTER_OUT_CSV}")
    print(f"  v03 context parquet:  {MASTER_OUT_PARQUET}")

    print("\nCreated audits:")
    for path in [
        PROCESSING_AUDIT_CSV,
        PERIOD_AUDIT_CSV,
        ALLOCATION_AUDIT_CSV,
        JOIN_AUDIT_CSV,
        UNMATCHED_2016_CSV,
        DUPLICATE_CODE_ROWS_CSV,
        FIELD_DICTIONARY_CSV,
        METHOD_NOTE_MD,
    ]:
        print(f"  {path}")

    print("\nAllocation audit summary:")
    print(pd.DataFrame(allocation_rows).to_string(index=False))

    print("\nJoin audit summary:")
    print(join_audit.to_string(index=False))

    print("\nModelling rule:")
    print("  Exclude all ndia_poc_* variables from the primary 2021-aligned model.")
    print("  Use them only in a separate proof-of-concept context/sensitivity model.")


if __name__ == "__main__":
    main()
