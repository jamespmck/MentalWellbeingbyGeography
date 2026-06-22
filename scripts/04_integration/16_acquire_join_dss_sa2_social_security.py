#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
16_acquire_join_dss_sa2_social_security.py

Acquire and join DSS payment-recipient data at SA2 level for the
MentalWellbeingByGeography project.

Why this uses the 2016-SA2 DSS file
-----------------------------------
The DSS 2021-SA2 machine-readable file begins in June 2023. For the
2021/2022-aligned master, this script instead uses the DSS historical
2016-SA2 machine-readable file and allocates it to ASGS 2021 SA2 using the
existing ABS SA2 2016->2021 correspondence built earlier in this project.

Run
---
cd "D:\\Good Measure\\MentalWellbeingbyGeography"
python "scripts\\04_integration\\16_acquire_join_dss_sa2_social_security.py"

Optional:
python "scripts\\04_integration\\16_acquire_join_dss_sa2_social_security.py" --debug
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

SCRIPT_NAME = Path(__file__).name
SCRIPT_VERSION = "v06-wide-fix-v3"

DSS_SA2_2016_HISTORIC_URL = (
    "https://data.gov.au/data/dataset/7a6cd81c-e834-4a0c-8d41-4aec150f958b/"
    "resource/5be6e4ee-f0b5-422f-bb8f-9ee50950c276/download/"
    "dss-payments-2016-sa2-jun-2019-to-mar-2023_map-historic.csv"
)

DSS_SA2_2021_CURRENT_URL = (
    "https://data.gov.au/data/dataset/7a6cd81c-e834-4a0c-8d41-4aec150f958b/"
    "resource/381e8508-26fe-4656-a88f-69ae15ee69a7/download/"
    "dss-benefit-and-payment-recipient-demographics-2021-sa2-december-2025.csv"
)

DEFAULT_TARGET_DATE = "2021-12-31"
DEFAULT_WINDOW_START = "2021-01-01"
DEFAULT_WINDOW_END = "2022-06-30"

PAYMENT_PATTERNS = {
    "age_pension": [r"\bage pension\b"],
    "jobseeker": [r"\bjobseeker\b", r"\bnewstart\b"],
    "youth_allowance": [r"\byouth allowance\b"],
    "disability_support_pension": [r"\bdisability support pension\b", r"\bdsp\b"],
    "carer_payment": [r"\bcarer payment\b"],
    "carer_allowance": [r"\bcarer allowance\b"],
    "parenting_payment": [r"\bparenting payment\b"],
    "family_tax_benefit": [r"\bfamily tax benefit\b", r"\bftb\b"],
    "commonwealth_rent_assistance": [r"\bcommonwealth rent assistance\b", r"\brent assistance\b", r"\bcra\b"],
    "health_care_card": [r"\bhealth care card\b"],
    "low_income_card": [r"\blow income\b"],
    "pensioner_concession_card": [r"\bpension.*concession\b", r"\bpcc\b"],
    "commonwealth_seniors_health_card": [r"\bcommonwealth seniors health\b", r"\bcshc\b"],
    "austudy": [r"\baustudy\b"],
    "abstudy": [r"\babstudy\b"],
    "special_benefit": [r"\bspecial benefit\b"],
}


def notify_script_completion(success: bool, script_name: str, started_at: datetime | None = None, detail: str = "") -> None:
    status = "completed" if success else "failed"
    title = f"{script_name} {status}"
    elapsed = ""
    if started_at is not None:
        seconds = int((datetime.now() - started_at).total_seconds())
        minutes, rem = divmod(seconds, 60)
        elapsed = f"\nElapsed: {minutes}m {rem}s"
    message = f"{script_name} has {status}.{elapsed}"
    if detail:
        message += f"\n\n{str(detail).replace(chr(39), '’').replace(chr(34), '”')[:700]}"

    try:
        if sys.platform.startswith("win"):
            import winsound
            winsound.MessageBeep(winsound.MB_ICONASTERISK if success else winsound.MB_ICONHAND)
        else:
            print("\a", end="", flush=True)
    except Exception:
        pass

    if sys.platform.startswith("win"):
        try:
            icon = 64 if success else 16
            timeout = 12 if success else 30
            ps = "$wshell = New-Object -ComObject WScript.Shell; " + f"$null = $wshell.Popup('{message}', {timeout}, '{title}', {icon})"
            subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=timeout + 5, check=False)
        except Exception:
            pass


@dataclass
class Logger:
    log_path: Path
    debug: bool = False

    def __post_init__(self) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.log_path.write_text("", encoding="utf-8")

    def log(self, level: str, msg: str) -> None:
        line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [{level}] {msg}"
        print(line, flush=True)
        with self.log_path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")

    def info(self, msg: str) -> None:
        self.log("INFO", msg)

    def warn(self, msg: str) -> None:
        self.log("WARN", msg)

    def dbg(self, msg: str) -> None:
        if self.debug:
            self.log("DEBUG", msg)


def norm(value: object) -> str:
    s = str(value).strip().lower().replace("\ufeff", "")
    s = re.sub(r"[^a-z0-9]+", "_", s)
    return re.sub(r"_+", "_", s).strip("_")


def ensure_code(series: pd.Series) -> pd.Series:
    out = series.astype("string").str.strip()
    out = out.str.replace(r"\.0$", "", regex=True).str.replace(r"[^0-9]", "", regex=True)
    return out.where(out.str.len() > 0)


def derive_sa2_5digit_from_maincode_2016(series: pd.Series) -> pd.Series:
    """Derive ABS SA2_5DIGITCODE_2016 from SA2_MAINCODE_2016.

    ABS SA2_MAINCODE_2016 is a 9-digit hierarchical code. The DSS historic
    file uses ``sa2_5digitcode_2016``. That code is not the last five digits
    of the full maincode. It is the state/territory digit plus the final four
    SA2 digits.

    Example structure: ``101021007`` -> ``11007``.
    """
    s = ensure_code(series)
    first = s.str.slice(0, 1)
    last4 = s.str.slice(-4)
    out = first + last4
    return out.where(s.str.len() >= 5)


def find_project_root(start: Path | None = None) -> Path:
    candidates = []
    if start:
        candidates.append(start.resolve())
    candidates.append(Path.cwd().resolve())
    try:
        candidates.append(Path(__file__).resolve().parents[2])
    except Exception:
        pass
    for cand in candidates:
        for p in [cand] + list(cand.parents):
            if (p / "data" / "processed" / "integrated").exists():
                return p
    return Path.cwd().resolve()


def make_dirs(root: Path) -> dict[str, Path]:
    paths = {
        "raw_dss": root / "data" / "raw" / "dss",
        "processed_sources": root / "data" / "processed" / "sources",
        "processed_geography": root / "data" / "processed" / "geography",
        "integrated": root / "data" / "processed" / "integrated",
        "audits": root / "outputs" / "audits",
        "logs": root / "outputs" / "logs",
        "dicts": root / "docs" / "data_dictionaries",
        "methodology": root / "docs" / "methodology",
    }
    for p in paths.values():
        p.mkdir(parents=True, exist_ok=True)
    return paths


def read_table(path: Path) -> pd.DataFrame:
    if path.suffix.lower() == ".parquet":
        return pd.read_parquet(path)
    if path.suffix.lower() in {".csv", ".txt"}:
        try:
            return pd.read_csv(path, low_memory=False)
        except UnicodeDecodeError:
            return pd.read_csv(path, low_memory=False, encoding="latin1")
    if path.suffix.lower() in {".xlsx", ".xls"}:
        return pd.read_excel(path)
    raise ValueError(f"Unsupported table format: {path}")


def write_pair(df: pd.DataFrame, csv_path: Path, parquet_path: Path, logger: Logger) -> None:
    logger.info(f"Writing CSV: {csv_path}")
    df.to_csv(csv_path, index=False)
    logger.info(f"Writing parquet: {parquet_path}")
    df.to_parquet(parquet_path, index=False)


def download(url: str, dest: Path, logger: Logger, force: bool = False) -> Path:
    if dest.exists() and dest.stat().st_size > 0 and not force:
        logger.info(f"Using cached file: {dest}")
        return dest
    logger.info(f"Downloading: {url}")
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    request = urllib.request.Request(url, headers={"User-Agent": "MentalWellbeingByGeography data acquisition"})
    with urllib.request.urlopen(request, timeout=180) as response, tmp.open("wb") as out:
        shutil.copyfileobj(response, out)
    tmp.replace(dest)
    logger.info(f"Downloaded {dest.stat().st_size:,} bytes to {dest}")
    return dest


def parse_period_value(value: object) -> pd.Timestamp | pd.NaT:
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return pd.NaT
    s = str(value).strip()
    if not s or s.lower() in {"nan", "none", "null"}:
        return pd.NaT
    direct = pd.to_datetime(s, errors="coerce", dayfirst=True)
    if not pd.isna(direct):
        return pd.Timestamp(direct).normalize()
    compact = re.sub(r"[^0-9A-Za-z]", "", s).upper()
    if re.fullmatch(r"\d{8}", compact):
        out = pd.to_datetime(compact, format="%Y%m%d", errors="coerce")
        return pd.Timestamp(out).normalize() if not pd.isna(out) else pd.NaT
    if re.fullmatch(r"\d{6}", compact):
        out = pd.to_datetime(compact, format="%Y%m", errors="coerce")
        return pd.Timestamp(out).to_period("M").to_timestamp("M").normalize() if not pd.isna(out) else pd.NaT
    q_match = re.search(r"(20\d{2}).*Q([1-4])|Q([1-4]).*(20\d{2})", s.upper())
    if q_match:
        year = int(q_match.group(1) or q_match.group(4))
        q = int(q_match.group(2) or q_match.group(3))
        return pd.Timestamp(year=year, month=q * 3, day=1).to_period("M").to_timestamp("M").normalize()
    m = re.search(r"(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|SEPT|OCT|NOV|DEC)[A-Z]*\s*(20\d{2})", s.upper())
    if m:
        month_map = {"JAN":1,"FEB":2,"MAR":3,"APR":4,"MAY":5,"JUN":6,"JUL":7,"AUG":8,"SEP":9,"SEPT":9,"OCT":10,"NOV":11,"DEC":12}
        return pd.Timestamp(year=int(m.group(2)), month=month_map[m.group(1)], day=1).to_period("M").to_timestamp("M").normalize()
    try:
        f = float(s)
        if 20000 <= f <= 60000:
            out = pd.to_datetime(f, unit="D", origin="1899-12-30", errors="coerce")
            return pd.Timestamp(out).normalize() if not pd.isna(out) else pd.NaT
    except Exception:
        pass
    return pd.NaT


def parse_period_series(series: pd.Series) -> pd.Series:
    return series.map(parse_period_value)


def detect_period_column(df: pd.DataFrame, logger: Logger) -> tuple[str, pd.DataFrame]:
    keys = ["rprtdt", "report", "reference", "ref_date", "quarter", "period", "date", "as_at"]
    candidates = [c for c in df.columns if any(k in norm(c) for k in keys)]
    for c in df.columns:
        if norm(c) == "rprtdt" and c not in candidates:
            candidates.insert(0, c)
    rows = []
    for c in candidates:
        parsed = parse_period_series(df[c].head(min(len(df), 25000)))
        rows.append({
            "column_name": c,
            "normalised_column_name": norm(c),
            "parse_rate_sample": float(parsed.notna().mean()),
            "period_count_sample": int(parsed.nunique(dropna=True)),
            "period_min_sample": parsed.min(),
            "period_max_sample": parsed.max(),
            "sample_values": " | ".join(df[c].dropna().astype(str).head(12).tolist()),
        })
    audit = pd.DataFrame(rows).sort_values(["parse_rate_sample", "period_count_sample"], ascending=[False, False]) if rows else pd.DataFrame()
    if not audit.empty:
        logger.info("Period column candidates:\n" + audit.head(10).to_string(index=False))
    good = audit[(audit["parse_rate_sample"] >= 0.8) & (audit["period_count_sample"] >= 2)] if not audit.empty else pd.DataFrame()
    if good.empty:
        raise ValueError(f"No usable period column detected. Available columns: {list(df.columns)}")
    return str(good.iloc[0]["column_name"]), audit


def detect_sa2_2016_column(df: pd.DataFrame) -> str:
    for c in df.columns:
        n = norm(c)
        if n in {"sa2cd2016", "sa2_code_2016", "sa2_maincode_2016", "sa2code2016"}:
            return c
    scored = []
    for c in df.columns:
        n = norm(c)
        score = 0
        if "sa2" in n: score += 3
        if "2016" in n: score += 3
        if "code" in n or n.endswith("cd") or "_cd" in n: score += 2
        if "name" in n or "nm" in n: score -= 2
        if score > 0: scored.append((score, c))
    if not scored:
        raise ValueError(f"No SA2 2016 code column detected. Columns: {list(df.columns)}")
    return sorted(scored, reverse=True)[0][1]


def detect_payment_column(df: pd.DataFrame, exclude: set[str]) -> str:
    scored = []
    for c in df.columns:
        if c in exclude: continue
        n = norm(c)
        if any(k in n for k in ["payment", "benefit", "program", "measure", "category", "type"]):
            nunique = int(df[c].astype("string").nunique(dropna=True))
            score = 0
            if "payment" in n: score += 5
            if "benefit" in n: score += 4
            if "program" in n: score += 3
            if 2 <= nunique <= 500: score += 3
            if nunique > 2000: score -= 5
            scored.append((score, -nunique, c))
    if scored:
        return sorted(scored, reverse=True)[0][2]
    fallback = []
    for c in df.columns:
        if c in exclude: continue
        sample = df[c].dropna().head(500)
        if sample.empty: continue
        if pd.to_numeric(sample, errors="coerce").notna().mean() < 0.2:
            nunique = int(df[c].astype("string").nunique(dropna=True))
            if 2 <= nunique <= 500:
                fallback.append((nunique, c))
    if fallback:
        return sorted(fallback)[0][1]
    raise ValueError("Could not detect payment label column.")


def detect_value_column(df: pd.DataFrame, exclude: set[str]) -> str:
    scored = []
    for c in df.columns:
        if c in exclude: continue
        n = norm(c)
        numeric = pd.to_numeric(df[c], errors="coerce")
        rate = float(numeric.notna().mean())
        if rate < 0.6: continue
        score = rate
        for k in ["count", "cnt", "number", "recipient", "persons", "value", "total"]:
            if k in n: score += 3
        if any(k in n for k in ["rate", "percent", "pct"]): score -= 3
        scored.append((score, c))
    if not scored:
        raise ValueError("Could not detect recipient count/value column.")
    return sorted(scored, reverse=True)[0][1]


def classify_payment(label: object) -> str | None:
    s = re.sub(r"[^a-z0-9]+", " ", str(label).strip().lower())
    s = re.sub(r"\s+", " ", s).strip()
    for slug, patterns in PAYMENT_PATTERNS.items():
        if any(re.search(p, s) for p in patterns):
            return slug
    return None


def slugify_column(label: object) -> str:
    """Create a stable snake_case suffix from a DSS wide payment column."""
    s = str(label).strip().lower()
    s = s.replace("&", " and ")
    s = re.sub(r"[^a-z0-9]+", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return s or "unnamed_payment_field"


def detect_wide_payment_columns(df: pd.DataFrame, exclude: set[str], logger: Logger) -> tuple[list[dict], pd.DataFrame]:
    """
    DSS SA2 historic files are wide: one row per SA2/date and one column per payment.
    This function selects numeric payment-recipient columns by matching the column names,
    rather than looking for a payment-label column.
    """
    rows: list[dict] = []
    selected: list[dict] = []
    for c in df.columns:
        if c in exclude:
            continue
        n = norm(c)
        raw_label = str(c).strip()
        numeric = pd.to_numeric(df[c], errors="coerce")
        numeric_rate = float(numeric.notna().mean()) if len(df) else 0.0
        concept = classify_payment(raw_label)

        # Exclude obvious geography/text metadata even if a word accidentally matches.
        is_metadata = any(k in n for k in ["sa2", "state", "name", "label", "date", "code", "gccsa", "sa3", "sa4"])
        is_selected = bool(concept and numeric_rate >= 0.60 and not is_metadata)

        row = {
            "source_column_name": c,
            "normalised_column_name": n,
            "matched_concept": concept,
            "numeric_parse_rate": numeric_rate,
            "non_missing_count": int(numeric.notna().sum()),
            "numeric_total_selected_period": float(numeric.sum(skipna=True)) if numeric.notna().any() else np.nan,
            "selected_as_payment_count_column": int(is_selected),
            "output_slug": slugify_column(c) if is_selected else "",
            "sample_values": " | ".join(df[c].dropna().astype(str).head(8).tolist()),
        }
        rows.append(row)
        if is_selected:
            selected.append(row)

    audit = pd.DataFrame(rows).sort_values(
        ["selected_as_payment_count_column", "matched_concept", "numeric_total_selected_period"],
        ascending=[False, True, False],
        na_position="last",
    )

    logger.info("Detected DSS wide payment columns:\n" + audit.head(40).to_string(index=False))
    if not selected:
        raise ValueError(
            "No configured DSS wide payment columns matched. "
            "Review dss_sa2_payment_column_audit_v06.csv and expand PAYMENT_PATTERNS."
        )
    logger.info(f"Selected DSS wide payment columns: {len(selected):,}")
    return selected, audit


def build_wide_2016_from_wide(df: pd.DataFrame, sa2_col: str, selected_payment_columns: list[dict], selected_period: pd.Timestamp, logger: Logger):
    """Build an SA2-2016 wide table from a DSS wide source table."""
    d = df.copy()
    d["sa2_code_2016"] = ensure_code(d[sa2_col])
    d = d[d["sa2_code_2016"].notna()].copy()

    out = d[["sa2_code_2016"]].copy()
    output_cols: list[str] = []

    mapping_rows = []
    for item in selected_payment_columns:
        src = item["source_column_name"]
        suffix = item["output_slug"]
        out_col = f"dss_sa2_2016_{suffix}_recipients"
        # Avoid accidental duplicate output names.
        if out_col in output_cols:
            out_col = f"dss_sa2_2016_{suffix}_{len(output_cols) + 1}_recipients"
        out[out_col] = pd.to_numeric(d[src], errors="coerce")
        output_cols.append(out_col)
        mapping_rows.append({
            "source_column_name": src,
            "matched_concept": item.get("matched_concept"),
            "output_column_name": out_col,
            "selected_reference_period": selected_period.date().isoformat(),
            "recipient_total_selected_period": float(out[out_col].sum(skipna=True)),
            "sa2_2016_non_missing_count": int(out[out_col].notna().sum()),
        })

    # The DSS file should already be one row per SA2/date, but group defensively.
    grouped = out.groupby("sa2_code_2016", dropna=False)[output_cols].sum(min_count=1).reset_index()
    grouped["dss_selected_reference_period"] = selected_period.date().isoformat()
    grouped["source_dss_sa2_2016_present_flag"] = 1

    logger.info(f"Built DSS SA2 2016 wide table: {len(grouped):,} SA2 rows; {len(output_cols):,} payment columns")
    return grouped, pd.DataFrame(mapping_rows)


def select_period(df: pd.DataFrame, period_col: str, target: str, start: str, end: str, logger: Logger):
    target_ts = pd.Timestamp(target)
    start_ts = pd.Timestamp(start)
    end_ts = pd.Timestamp(end)
    temp = df.copy()
    temp["_dss_reference_period"] = parse_period_series(temp[period_col])
    periods = temp["_dss_reference_period"].dropna().value_counts().sort_index().rename_axis("reference_period").reset_index(name="row_count")
    periods["within_target_window"] = (periods["reference_period"] >= start_ts) & (periods["reference_period"] <= end_ts)
    periods["absolute_days_from_target"] = (periods["reference_period"] - target_ts).abs().dt.days
    logger.info("Available DSS periods:\n" + periods.to_string(index=False))
    eligible = periods[periods["within_target_window"]].sort_values(["absolute_days_from_target", "reference_period"], ascending=[True, False])
    if eligible.empty:
        raise ValueError(f"No DSS periods inside {start} to {end}.")
    selected = pd.Timestamp(eligible.iloc[0]["reference_period"])
    filtered = temp[temp["_dss_reference_period"] == selected].copy()
    logger.info(f"Selected DSS reference period: {selected.date()} with {len(filtered):,} rows")
    return filtered, selected, periods


def build_wide_2016(df: pd.DataFrame, sa2_col: str, payment_col: str, value_col: str, selected_period: pd.Timestamp, logger: Logger):
    d = df.copy()
    d["sa2_code_2016"] = ensure_code(d[sa2_col])
    d["payment_raw_label"] = d[payment_col].astype("string").str.strip()
    d["payment_slug"] = d["payment_raw_label"].map(classify_payment)
    d["recipient_count"] = pd.to_numeric(d[value_col], errors="coerce")
    label_audit = d.groupby(["payment_raw_label", "payment_slug"], dropna=False).agg(row_count=("recipient_count", "size"), recipient_total=("recipient_count", "sum"), sa2_2016_count=("sa2_code_2016", "nunique")).reset_index().sort_values(["payment_slug", "recipient_total"], ascending=[True, False])
    matched = d[d["payment_slug"].notna() & d["sa2_code_2016"].notna()].copy()
    if matched.empty:
        raise ValueError("No configured DSS payment labels matched. Review dss_sa2_payment_label_audit_v06.csv and expand PAYMENT_PATTERNS.")
    logger.info(f"Matched DSS rows: {len(matched):,}")
    logger.info(f"Matched payment concepts: {sorted(matched['payment_slug'].dropna().unique().tolist())}")
    grouped = matched.groupby(["sa2_code_2016", "payment_slug"], dropna=False)["recipient_count"].sum(min_count=1).reset_index()
    wide = grouped.pivot(index="sa2_code_2016", columns="payment_slug", values="recipient_count").reset_index()
    wide.columns.name = None
    wide = wide.rename(columns={c: f"dss_sa2_2016_{c}_recipients" for c in wide.columns if c != "sa2_code_2016"})
    wide["dss_selected_reference_period"] = selected_period.date().isoformat()
    wide["source_dss_sa2_2016_present_flag"] = 1
    return wide, label_audit


def find_bridge(root: Path) -> Path:
    for rel in [
        r"data\processed\geography\bridge_sa2_2016_to_2021.csv",
        r"data\processed\geography\bridge_sa2_2016_to_2021.parquet",
        r"data\processed\spines\bridge_sa2_2016_to_2021.csv",
        r"data\processed\sources\bridge_sa2_2016_to_2021.csv",
    ]:
        p = root / rel
        if p.exists():
            return p
    raise FileNotFoundError("Could not find bridge_sa2_2016_to_2021.csv/parquet. Run the ABS bridge script first.")


def detect_bridge_columns(bridge: pd.DataFrame):
    src = tgt = ratio = None
    for c in bridge.columns:
        n = norm(c)
        if "sa2" in n and "2016" in n and ("code" in n or "maincode" in n or n.endswith("cd")): src = src or c
        if "sa2" in n and "2021" in n and ("code" in n or "maincode" in n or n.endswith("cd")): tgt = tgt or c
        if n == "ratio_from_to" or ("ratio" in n and ("from" in n or "to" in n)): ratio = ratio or c
    for c in bridge.columns:
        n = norm(c)
        if n == "sa2_maincode_2016": src = c
        if n == "sa2_maincode_2021": tgt = c
        if n == "ratio_from_to": ratio = c
    if not all([src, tgt, ratio]):
        raise ValueError(f"Could not detect bridge columns. Columns: {list(bridge.columns)}")
    return src, tgt, ratio


def allocate_to_2021(wide: pd.DataFrame, bridge: pd.DataFrame, logger: Logger):
    """Allocate DSS SA2 2016 wide counts to SA2 2021.

    DSS historical SA2 files use ``sa2_5digitcode_2016`` rather than the full
    ASGS SA2_MAINCODE_2016 used by the ABS 2016->2021 correspondence. This
    function therefore tries a direct full-code join first, then falls back to
    matching the DSS 5-digit code against the ABS-style 2016 SA2 5-digit code
    derived from the full maincode.

    Important: SA2_5DIGITCODE_2016 is not the last five digits of
    SA2_MAINCODE_2016. It is the state/territory digit plus the final four
    SA2 digits. A previous version used the last-five-digits rule and correctly
    failed because that creates nationally duplicated keys.
    """
    src_col, tgt_col, ratio_col = detect_bridge_columns(bridge)
    b = bridge[[src_col, tgt_col, ratio_col]].copy()
    b["sa2_code_2016_full"] = ensure_code(b[src_col])
    b["sa2_code_2021"] = ensure_code(b[tgt_col])
    b["_ratio"] = pd.to_numeric(b[ratio_col], errors="coerce")
    b = b[b["sa2_code_2016_full"].notna() & b["sa2_code_2021"].notna() & b["_ratio"].notna()].copy()
    b["sa2_5digitcode_2016"] = derive_sa2_5digit_from_maincode_2016(b["sa2_code_2016_full"])

    w = wide.copy()
    w["sa2_code_2016"] = ensure_code(w["sa2_code_2016"])
    w["sa2_5digitcode_2016"] = w["sa2_code_2016"].astype("string").str[-5:]

    value_cols = [c for c in w.columns if c.startswith("dss_sa2_2016_") and c.endswith("_recipients")]
    if not value_cols:
        raise ValueError("No DSS recipient count columns found before allocation.")

    # Attempt 1: direct full-code match. This will usually fail for the DSS
    # historical SA2 file because it carries a 5-digit SA2 code.
    direct = w.merge(
        b[["sa2_code_2016_full", "sa2_code_2021", "_ratio"]],
        left_on="sa2_code_2016",
        right_on="sa2_code_2016_full",
        how="left",
        indicator=True,
    )
    direct_matched = int((direct["_merge"] == "both").sum())
    logger.info(f"DSS bridge direct full-code matched rows: {direct_matched:,} of {len(w):,}")

    if direct_matched > 0:
        merged = direct.rename(columns={"sa2_code_2016": "dss_sa2_2016_source_code"})
        join_method = "direct_full_sa2_2016_code"
    else:
        # Attempt 2: 5-digit fallback. Guard against ambiguous bridge keys.
        bridge_key_audit = (
            b.groupby("sa2_5digitcode_2016", dropna=False)
            .agg(
                full_2016_code_count=("sa2_code_2016_full", "nunique"),
                bridge_row_count=("sa2_code_2016_full", "size"),
                target_2021_code_count=("sa2_code_2021", "nunique"),
            )
            .reset_index()
        )
        ambiguous = bridge_key_audit[bridge_key_audit["full_2016_code_count"] > 1].copy()
        if not ambiguous.empty:
            # This should be rare or absent. If it occurs, refuse the fallback
            # rather than silently allocating to the wrong 2016 SA2.
            sample = ambiguous.head(20).to_string(index=False)
            raise ValueError(
                "Cannot safely allocate DSS 5-digit SA2 codes: the derived ABS-style "
                "SA2_5DIGITCODE_2016 keys are duplicated/ambiguous in the bridge. "
                "This usually means the bridge source is not an SA2_MAINCODE_2016 bridge "
                "or the 5-digit derivation rule needs review. Sample:\n" + sample
            )

        merged = w.merge(
            b[["sa2_5digitcode_2016", "sa2_code_2016_full", "sa2_code_2021", "_ratio"]],
            on="sa2_5digitcode_2016",
            how="left",
            indicator=True,
        )
        fallback_matched = int((merged["_merge"] == "both").sum())
        logger.info(f"DSS bridge 5-digit fallback matched rows: {fallback_matched:,} of {len(w):,}")
        join_method = "fallback_dss_sa2_5digitcode_2016_to_abs_state_digit_plus_last4"

    unmatched = merged[merged["_merge"] == "left_only"].copy()
    alloc = merged[merged["_merge"] == "both"].copy()
    if alloc.empty:
        sample_wide = w[["sa2_code_2016", "sa2_5digitcode_2016"]].head(20).to_string(index=False)
        sample_bridge = b[["sa2_code_2016_full", "sa2_5digitcode_2016", "sa2_code_2021", "_ratio"]].head(20).to_string(index=False)
        raise ValueError(
            "DSS allocation failed: no DSS SA2 2016 codes matched the ABS 2016->2021 bridge.\n\n"
            "Sample DSS keys:\n" + sample_wide + "\n\nSample bridge keys:\n" + sample_bridge
        )

    out_cols = []
    for c in value_cols:
        out_c = c.replace("dss_sa2_2016_", "dss_sa2_2021_allocated_")
        alloc[out_c] = pd.to_numeric(alloc[c], errors="coerce") * alloc["_ratio"]
        out_cols.append(out_c)

    out = alloc.groupby("sa2_code_2021", dropna=False)[out_cols].sum(min_count=1).reset_index()
    selected_reference_period = str(w["dss_selected_reference_period"].dropna().iloc[0]) if "dss_selected_reference_period" in w.columns and w["dss_selected_reference_period"].notna().any() else ""
    out["dss_selected_reference_period"] = selected_reference_period
    out["dss_sa2_2016_to_2021_allocation_method"] = join_method
    out["source_dss_sa2_2016_allocated_to_2021_present_flag"] = 1

    source_total = float(np.nansum(w[value_cols].to_numpy(dtype=float))) if value_cols else np.nan
    allocated_total = float(np.nansum(out[out_cols].to_numpy(dtype=float))) if out_cols else np.nan
    unmatched_total = float(np.nansum(unmatched[value_cols].to_numpy(dtype=float))) if value_cols and not unmatched.empty else 0.0
    retained_pct = allocated_total / source_total * 100 if source_total else np.nan

    audit = pd.DataFrame([{
        "source_family": "dss_social_security_sa2",
        "native_geography": "SA2_2016_DSS_5DIGIT",
        "target_geography": "SA2_2021",
        "selected_reference_period": selected_reference_period,
        "allocation_join_method": join_method,
        "source_2016_code_count": int(w["sa2_code_2016"].nunique()),
        "target_2021_code_count": int(out["sa2_code_2021"].nunique()),
        "bridge_row_count_used": int(len(b)),
        "unmatched_2016_code_count": int(unmatched["sa2_code_2016"].nunique()) if "sa2_code_2016" in unmatched.columns and not unmatched.empty else 0,
        "source_recipient_total_2016_across_selected_concepts": source_total,
        "allocated_recipient_total_2021_across_selected_concepts": allocated_total,
        "unmatched_recipient_total_2016_across_selected_concepts": unmatched_total,
        "allocation_total_difference": source_total - allocated_total,
        "allocation_total_retained_pct": retained_pct,
        "status": "pass" if source_total and retained_pct >= 0.98 else "review",
        "notes": "DSS counts allocated from DSS 2016 SA2 5-digit codes to ASGS 2021 SA2 using ABS 2016->2021 correspondence. Counts are not population-standardised rates.",
    }])
    logger.info("DSS allocation audit:\n" + audit.to_string(index=False))
    if not unmatched.empty:
        logger.info(f"Unmatched DSS 2016 SA2 source codes after allocation: {unmatched['sa2_code_2016'].nunique():,}")
    return out, audit, unmatched


def join_master(master: pd.DataFrame, dss: pd.DataFrame, base_path: Path, logger: Logger):
    master = master.copy()
    master["sa2_code_2021"] = ensure_code(master["sa2_code_2021"])
    dss = dss.copy()
    dss["sa2_code_2021"] = ensure_code(dss["sa2_code_2021"])
    before_rows = len(master)
    before_cols = len(master.columns)
    joined = master.merge(dss, on="sa2_code_2021", how="left", validate="one_to_one")
    present = "source_dss_sa2_2016_allocated_to_2021_present_flag"
    matched = int(joined[present].notna().sum()) if present in joined.columns else 0
    dupes = int(joined["sa2_code_2021"].duplicated().sum())
    audit = pd.DataFrame([
        {"check_name": "base_master_file", "value": str(base_path), "status": "info", "notes": ""},
        {"check_name": "master_rows_before_join", "value": before_rows, "status": "pass" if before_rows == 2472 else "review", "notes": "Expected SA2 row count."},
        {"check_name": "master_columns_before_join", "value": before_cols, "status": "info", "notes": ""},
        {"check_name": "dss_sa2_2021_source_rows", "value": len(dss), "status": "info", "notes": "Allocated DSS SA2 2021 rows."},
        {"check_name": "master_rows_after_join", "value": len(joined), "status": "pass" if len(joined) == before_rows else "fail", "notes": "Join must not change SA2 row count."},
        {"check_name": "master_columns_after_join", "value": len(joined.columns), "status": "info", "notes": ""},
        {"check_name": "duplicate_sa2_rows_after_join", "value": dupes, "status": "pass" if dupes == 0 else "fail", "notes": ""},
        {"check_name": "sa2_rows_with_dss_context", "value": matched, "status": "info", "notes": "Rows with joined allocated DSS payment context."},
    ])
    unmatched_cols = [c for c in ["sa2_code_2021", "sa2_name_2021", "sa3_code_2021", "sa3_name_2021", "state_name_2021"] if c in joined.columns]
    unmatched = joined[joined[present].isna()][unmatched_cols].copy() if present in joined.columns else pd.DataFrame()
    logger.info("DSS join audit:\n" + audit.to_string(index=False))
    return joined, audit, unmatched


def make_dictionary(joined: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for c in joined.columns:
        if c.startswith("dss_sa2_2021_allocated_") or c in {"dss_selected_reference_period", "source_dss_sa2_2016_allocated_to_2021_present_flag"}:
            rows.append({
                "column_name": c,
                "source_family": "DSS social security/payment recipient demographics",
                "native_geography": "SA2 2016 allocated to SA2 2021",
                "field_role": "context_predictor_candidate" if c.startswith("dss_sa2_2021_allocated_") else "source_metadata",
                "primary_model_use": "candidate_context_predictor_after_rate_or_population_control_review" if c.startswith("dss_sa2_2021_allocated_") else "audit/source availability only",
                "notes": "Recipient counts, not rates. Use with denominators or population controls." if c.startswith("dss_sa2_2021_allocated_") else "DSS source metadata.",
            })
    return pd.DataFrame(rows)


def write_note(path: Path, selected_period: pd.Timestamp, retained_pct: float | None) -> None:
    path.write_text(f"""# DSS SA2 social-security context layer {SCRIPT_VERSION}

Selected DSS reporting period: `{selected_period.date().isoformat()}`

The script uses the DSS historical SA2 2016 machine-readable file because it contains the 2021/2022 window. The DSS 2021-SA2 machine-readable file begins later, from June 2023, so it is not used for the primary aligned context layer.

The layer retains selected payment-recipient count concepts relevant to socioeconomic, disability, caring, family and housing context, then allocates SA2 2016 counts to SA2 2021 using the official ABS SA2 2016 to SA2 2021 correspondence already built in this project.

Allocation retained across selected concepts: `{retained_pct if retained_pct is not None else 'not calculated'}`

Limitations:

- DSS fields are counts, not rates.
- Counts partly reflect population size.
- Use denominators or population controls before substantive modelling interpretation.
- Allocation from SA2 2016 to SA2 2021 adds correspondence uncertainty.
- DSS confidentiality treatment affects small cells. Review the DSS source notes and the project audits.
""", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", default=None)
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--force-download", action="store_true")
    parser.add_argument("--target-date", default=DEFAULT_TARGET_DATE)
    parser.add_argument("--window-start", default=DEFAULT_WINDOW_START)
    parser.add_argument("--window-end", default=DEFAULT_WINDOW_END)
    parser.add_argument("--base-master", default=None)
    args = parser.parse_args()

    root = find_project_root(Path(args.project_root) if args.project_root else None)
    dirs = make_dirs(root)
    log_path = dirs["logs"] / f"{Path(__file__).stem}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    logger = Logger(log_path, debug=args.debug)

    logger.info("DSS SA2 social-security acquisition and join")
    logger.info(f"Project root: {root}")
    logger.info(f"Log path: {log_path}")

    base_master = Path(args.base_master) if args.base_master else dirs["integrated"] / "sa2_predictor_universe_v05_with_phn_lga_context.parquet"
    if not base_master.exists():
        alt = dirs["integrated"] / "sa2_predictor_universe_v05_with_phn_lga_context.csv"
        if alt.exists(): base_master = alt
        else: raise FileNotFoundError(f"Base master not found: {base_master}")
    logger.info(f"Base master: {base_master}")

    raw_path = dirs["raw_dss"] / "dss_payments_2016_sa2_jun_2019_to_mar_2023_map_historic.csv"
    download(DSS_SA2_2016_HISTORIC_URL, raw_path, logger, force=args.force_download)

    pd.DataFrame([
        {"source_family": "dss_social_security_sa2", "source_role": "primary_aligned_historical_2016_sa2", "url": DSS_SA2_2016_HISTORIC_URL, "local_path": str(raw_path), "used_in_join": 1, "notes": "Used because it contains 2021/2022 reporting periods. Allocated to SA2 2021."},
        {"source_family": "dss_social_security_sa2", "source_role": "current_2021_sa2_reference_only", "url": DSS_SA2_2021_CURRENT_URL, "local_path": "", "used_in_join": 0, "notes": "Begins June 2023, outside preferred 2021/2022 alignment window."},
    ]).to_csv(dirs["audits"] / "dss_sa2_source_selection_audit_v06.csv", index=False)

    logger.info("Reading DSS raw CSV")
    raw = read_table(raw_path)
    logger.info(f"Raw DSS rows: {len(raw):,}; columns: {len(raw.columns):,}")

    pd.DataFrame([{"column_name": c, "normalised_column_name": norm(c), "dtype": str(raw[c].dtype), "non_missing_count": int(raw[c].notna().sum()), "non_missing_pct": float(raw[c].notna().mean() * 100), "sample_values": " | ".join(raw[c].dropna().astype(str).head(8).tolist())} for c in raw.columns]).to_csv(dirs["audits"] / "dss_sa2_source_schema_audit_v06.csv", index=False)

    period_col, period_candidates = detect_period_column(raw, logger)
    sa2_col = detect_sa2_2016_column(raw)
    logger.info("Detected key columns:\n" + pd.DataFrame([
        {"detected_role": "period_col", "column_name": period_col},
        {"detected_role": "sa2_2016_code_col", "column_name": sa2_col},
    ]).to_string(index=False))
    period_candidates.to_csv(dirs["audits"] / "dss_sa2_period_column_candidates_audit_v06.csv", index=False)

    selected_df, selected_period, period_audit = select_period(raw, period_col, args.target_date, args.window_start, args.window_end, logger)
    period_audit.to_csv(dirs["audits"] / "dss_sa2_period_selection_audit_v06.csv", index=False)

    selected_payment_columns, payment_column_audit = detect_wide_payment_columns(
        selected_df,
        exclude={period_col, sa2_col},
        logger=logger,
    )
    payment_column_audit.to_csv(dirs["audits"] / "dss_sa2_payment_column_audit_v06.csv", index=False)

    wide_2016, label_audit = build_wide_2016_from_wide(selected_df, sa2_col, selected_payment_columns, selected_period, logger)
    label_audit.to_csv(dirs["audits"] / "dss_sa2_payment_label_audit_v06.csv", index=False)
    write_pair(wide_2016, dirs["processed_sources"] / "dss_sa2_2016_payment_counts_selected_2021_22_wide.csv", dirs["processed_sources"] / "dss_sa2_2016_payment_counts_selected_2021_22_wide.parquet", logger)

    bridge_path = find_bridge(root)
    logger.info(f"Using bridge: {bridge_path}")
    bridge = read_table(bridge_path)
    allocated, allocation_audit, unmatched_2016 = allocate_to_2021(wide_2016, bridge, logger)
    allocation_audit.to_csv(dirs["audits"] / "dss_sa2_allocation_audit_v06.csv", index=False)
    unmatched_2016.to_csv(dirs["audits"] / "dss_sa2_unmatched_2016_codes_v06.csv", index=False)
    write_pair(allocated, dirs["processed_sources"] / "dss_sa2_2021_payment_counts_allocated_2021_22.csv", dirs["processed_sources"] / "dss_sa2_2021_payment_counts_allocated_2021_22.parquet", logger)

    master = read_table(base_master)
    joined, join_audit, unmatched_join = join_master(master, allocated, base_master, logger)
    join_audit.to_csv(dirs["audits"] / "sa2_predictor_universe_v06_dss_sa2_join_audit.csv", index=False)
    unmatched_join.to_csv(dirs["audits"] / "sa2_predictor_universe_v06_dss_sa2_unmatched_audit.csv", index=False)

    out_csv = dirs["integrated"] / "sa2_predictor_universe_v06_with_dss_sa2_context.csv"
    out_parquet = dirs["integrated"] / "sa2_predictor_universe_v06_with_dss_sa2_context.parquet"
    write_pair(joined, out_csv, out_parquet, logger)

    make_dictionary(joined).to_csv(dirs["dicts"] / "dss_sa2_context_field_dictionary_v06.csv", index=False)
    retained_pct = float(allocation_audit["allocation_total_retained_pct"].iloc[0]) if not allocation_audit.empty else None
    write_note(dirs["methodology"] / "dss_sa2_social_security_context_layer_note_v06.md", selected_period, retained_pct)

    pd.DataFrame([{ "source_family": "dss_social_security_sa2", "status": "integrated_context_candidate", "master_file": str(out_parquet), "selected_reference_period": selected_period.date().isoformat(), "notes": "DSS selected payment-recipient counts integrated after allocation from SA2 2016 to SA2 2021. Counts require denominators or population controls." }]).to_csv(dirs["audits"] / "extended_acquisition_completion_status_v06_dss_update.csv", index=False)

    logger.info("Created v06 DSS master:")
    logger.info(f"  {out_parquet}")
    logger.info(f"  {out_csv}")
    logger.info("Next action: review dss_sa2_payment_label_audit_v06.csv and sa2_predictor_universe_v06_dss_sa2_join_audit.csv")


if __name__ == "__main__":
    started = datetime.now()
    try:
        main()
    except Exception as exc:
        notify_script_completion(False, SCRIPT_NAME, started, detail=str(exc))
        raise
    else:
        notify_script_completion(True, SCRIPT_NAME, started, detail="DSS SA2 context layer completed using wide payment columns. Review v06 audits.")
