#!/usr/bin/env python3
"""
17_acquire_join_housing_affordability_context.py

MentalWellbeingByGeography
Good Measure

Purpose
-------
Acquire and join a housing affordability context layer after v06 DSS integration.

The primary target is the Regional Data Hub / ABS-derived Census Mortgage and Rent
Affordability Indicators for LGAs and SA2s. The preferred resource is the 2011-2021
MAID/RAID long-format table. The script is defensive because public catalogue pages
and resource URLs can change.

The script also inventories housing-related Census QuickStats fields that already
exist in the current master, so that the project can distinguish genuinely new
housing data from housing variables already present through Census QuickStats.

Outputs
-------
- data/processed/integrated/sa2_predictor_universe_v07_with_housing_affordability_context.csv
- data/processed/integrated/sa2_predictor_universe_v07_with_housing_affordability_context.parquet
- data/processed/sources/housing_affordability_2021_sa2_features.csv
- outputs/audits/housing_existing_master_column_inventory_v07.csv
- outputs/audits/housing_affordability_source_selection_audit_v07.csv
- outputs/audits/housing_affordability_schema_audit_v07.csv
- outputs/audits/housing_affordability_join_audit_v07.csv
- outputs/audits/housing_affordability_unmatched_audit_v07.csv
- docs/data_dictionaries/housing_affordability_context_field_dictionary_v07.csv
- docs/methodology/housing_affordability_context_layer_note_v07.md

Important modelling caveat
--------------------------
Housing affordability fields from MAID/RAID are household counts unless explicitly
identified as rates or proportions. Treat them as context features that need
population/household denominators or derived percentages before substantive modelling.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

import pandas as pd

SCRIPT_NAME = Path(__file__).name

DEFAULT_PROJECT_ROOT = Path(r"D:\Good Measure\MentalWellbeingbyGeography")
DEFAULT_BASE_MASTER = Path(
    r"D:\Good Measure\MentalWellbeingbyGeography\data\processed\integrated\sa2_predictor_universe_v06_with_dss_sa2_context.parquet"
)

DATASET_PAGE = "https://catalogue.data.infrastructure.gov.au/dataset/rdh-census-housing-affordability-data-for-lgas-and-sa2s-mortgage-and-rent-affordability-indicators"
RESOURCE_TIME_SERIES_LONG = "a29d2ba0-ea5d-4495-a637-6b5521e7501e"
RESOURCE_ALL_TABLES_XLSX = "b8cd5524-dd8d-4306-b388-c7ce264b8944"
RESOURCE_PAGE_TIME_SERIES_LONG = f"{DATASET_PAGE}/resource/{RESOURCE_TIME_SERIES_LONG}"
RESOURCE_PAGE_ALL_TABLES_XLSX = f"{DATASET_PAGE}/resource/{RESOURCE_ALL_TABLES_XLSX}"
DATASET_SLUG = "rdh-census-housing-affordability-data-for-lgas-and-sa2s-mortgage-and-rent-affordability-indicators"

TARGET_YEAR = 2021

HOUSING_EXISTING_PATTERNS = [
    r"housing",
    r"dwelling",
    r"rent",
    r"rental",
    r"mortgage",
    r"tenure",
    r"owned",
    r"owner",
    r"landlord",
    r"bedroom",
    r"occupancy",
    r"overcrowd",
    r"homeless",
    r"unoccupied",
    r"caravan",
    r"boarding",
]

KEYWORD_DROP_FOR_EXISTING = [
    r"source_.*present_flag",
]

NOT_MEASURE_COL_PATTERNS = [
    r"^sa2.*code",
    r"^sa2.*name",
    r"^lga.*code",
    r"^lga.*name",
    r"^area.*code",
    r"^area.*name",
    r"^geography.*code",
    r"^geography.*name",
    r"^region.*code",
    r"^region.*name",
    r"^year$",
    r"^census.*year$",
    r"^time$",
]


class Logger:
    def __init__(self, log_path: Path, debug: bool = False) -> None:
        self.log_path = log_path
        self.debug_enabled = debug
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("w", encoding="utf-8") as f:
            f.write(f"{SCRIPT_NAME} log started {datetime.now().isoformat(timespec='seconds')}\n")

    def _write(self, level: str, message: str) -> None:
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        text = f"[{stamp}] [{level}] {message}"
        print(text, flush=True)
        with self.log_path.open("a", encoding="utf-8") as f:
            f.write(text + "\n")

    def info(self, message: str) -> None:
        self._write("INFO", message)

    def debug(self, message: str) -> None:
        if self.debug_enabled:
            self._write("DEBUG", message)

    def warning(self, message: str) -> None:
        self._write("WARNING", message)

    def error(self, message: str) -> None:
        self._write("ERROR", message)


def notify_script_completion(success: bool, script_name: str, started_at: datetime | None = None, detail: str = "") -> None:
    status = "completed" if success else "failed"
    title = f"{script_name} {status}"
    elapsed = ""
    if started_at is not None:
        try:
            seconds = int((datetime.now() - started_at).total_seconds())
            minutes, rem = divmod(seconds, 60)
            elapsed = f"\nElapsed: {minutes}m {rem}s"
        except Exception:
            elapsed = ""
    message = f"{script_name} has {status}.{elapsed}"
    if detail:
        clean_detail = str(detail).replace("'", "’").replace('"', "”")
        message += f"\n\n{clean_detail[:500]}"

    try:
        if sys.platform.startswith("win"):
            import winsound
            winsound.MessageBeep(winsound.MB_ICONASTERISK if success else winsound.MB_ICONHAND)
        else:
            print("\a", end="", flush=True)
    except Exception:
        try:
            print("\a", end="", flush=True)
        except Exception:
            pass

    if sys.platform.startswith("win"):
        try:
            icon = 64 if success else 16
            timeout_seconds = 12 if success else 30
            ps = (
                "$wshell = New-Object -ComObject WScript.Shell; "
                f"$null = $wshell.Popup('{message}', {timeout_seconds}, '{title}', {icon})"
            )
            subprocess.run(
                ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=timeout_seconds + 5,
                check=False,
            )
        except Exception:
            pass


def slugify(value: Any, max_len: int = 90) -> str:
    text = "" if value is None else str(value)
    text = text.strip().lower()
    text = re.sub(r"[^a-z0-9]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    if not text:
        text = "unknown"
    return text[:max_len].strip("_")


def norm_col(value: Any) -> str:
    return slugify(value, max_len=120)


def ensure_dirs(root: Path) -> dict[str, Path]:
    paths = {
        "raw": root / "data" / "raw" / "housing",
        "processed_sources": root / "data" / "processed" / "sources",
        "processed_integrated": root / "data" / "processed" / "integrated",
        "audits": root / "outputs" / "audits",
        "logs": root / "outputs" / "logs",
        "dicts": root / "docs" / "data_dictionaries",
        "methodology": root / "docs" / "methodology",
    }
    for p in paths.values():
        p.mkdir(parents=True, exist_ok=True)
    return paths


def write_csv(df: pd.DataFrame, path: Path, logger: Logger) -> None:
    logger.info(f"Writing CSV: {path}")
    df.to_csv(path, index=False, encoding="utf-8-sig")


def write_parquet(df: pd.DataFrame, path: Path, logger: Logger) -> None:
    try:
        logger.info(f"Writing parquet: {path}")
        df.to_parquet(path, index=False)
    except Exception as exc:
        logger.warning(f"Could not write parquet {path}: {exc}")


def read_master(path: Path, logger: Logger) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"Base master not found: {path}")
    logger.info(f"Reading base master: {path}")
    if path.suffix.lower() == ".parquet":
        df = pd.read_parquet(path)
    else:
        df = pd.read_csv(path, dtype=str, low_memory=False)
    logger.info(f"Base master rows: {len(df):,}; columns: {len(df.columns):,}")
    if "sa2_code_2021" not in df.columns:
        raise ValueError("Base master must contain sa2_code_2021")
    df["sa2_code_2021"] = df["sa2_code_2021"].astype(str).str.replace(r"\.0$", "", regex=True).str.zfill(9)
    return df


def inventory_existing_housing_columns(master: pd.DataFrame) -> pd.DataFrame:
    rows = []
    compiled = [re.compile(p, re.I) for p in HOUSING_EXISTING_PATTERNS]
    drop_compiled = [re.compile(p, re.I) for p in KEYWORD_DROP_FOR_EXISTING]
    for col in master.columns:
        n = norm_col(col)
        if any(p.search(n) for p in drop_compiled):
            continue
        matched = [p.pattern for p in compiled if p.search(n)]
        if not matched:
            continue
        s = master[col]
        numeric = pd.to_numeric(s, errors="coerce")
        rows.append({
            "column_name": col,
            "normalised_column_name": n,
            "matched_patterns": "; ".join(matched),
            "non_missing_count": int(s.notna().sum()),
            "numeric_parse_rate": float(numeric.notna().mean()) if len(s) else 0.0,
            "min_numeric": float(numeric.min()) if numeric.notna().any() else None,
            "max_numeric": float(numeric.max()) if numeric.notna().any() else None,
            "source_hint": "existing_master_quickstats_or_prior_layer" if col.startswith("census_qs_") else "existing_master_other",
        })
    return pd.DataFrame(rows).sort_values(["source_hint", "normalised_column_name"]) if rows else pd.DataFrame(columns=[
        "column_name", "normalised_column_name", "matched_patterns", "non_missing_count", "numeric_parse_rate", "min_numeric", "max_numeric", "source_hint"
    ])


def request_json(url: str, timeout: int = 60) -> dict[str, Any]:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 MentalWellbeingByGeography data acquisition",
            "Accept": "application/json,text/plain,*/*",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def request_bytes(url: str, timeout: int = 180) -> bytes:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 MentalWellbeingByGeography data acquisition",
            "Accept": "text/csv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,application/octet-stream,*/*",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def ckan_resource_urls(resource_id: str, logger: Logger) -> list[dict[str, Any]]:
    endpoints = [
        f"https://catalogue.data.infrastructure.gov.au/api/3/action/resource_show?id={resource_id}",
        f"https://catalogue.data.infrastructure.gov.au/data/api/3/action/resource_show?id={resource_id}",
        f"https://data.gov.au/data/api/3/action/resource_show?id={resource_id}",
    ]
    out: list[dict[str, Any]] = []
    for endpoint in endpoints:
        try:
            logger.debug(f"Trying CKAN resource_show endpoint: {endpoint}")
            js = request_json(endpoint, timeout=60)
            result = js.get("result", {}) if isinstance(js, dict) else {}
            url = result.get("url") or result.get("download_url")
            if url:
                out.append({
                    "resource_id": resource_id,
                    "endpoint": endpoint,
                    "resource_url": url,
                    "name": result.get("name"),
                    "format": result.get("format"),
                    "mimetype": result.get("mimetype"),
                    "last_modified": result.get("last_modified"),
                    "status": "resource_show_success",
                })
        except Exception as exc:
            out.append({
                "resource_id": resource_id,
                "endpoint": endpoint,
                "resource_url": None,
                "name": None,
                "format": None,
                "mimetype": None,
                "last_modified": None,
                "status": f"resource_show_failed: {type(exc).__name__}: {exc}",
            })
    return out


def scrape_links_from_page(page_url: str, logger: Logger, page_kind: str) -> list[dict[str, Any]]:
    links: list[dict[str, Any]] = []
    try:
        logger.debug(f"Scraping page: {page_url}")
        html = request_bytes(page_url, timeout=120).decode("utf-8", errors="replace")
    except Exception as exc:
        return [{
            "resource_id": None,
            "endpoint": page_url,
            "resource_url": None,
            "name": None,
            "format": None,
            "mimetype": None,
            "last_modified": None,
            "status": f"{page_kind}_scrape_failed: {type(exc).__name__}: {exc}",
        }]

    # Extract href links and keep a broad audit trail of plausible download links.
    for m in re.finditer(r'href=["\']([^"\']+)["\']', html, flags=re.I):
        href = m.group(1)
        abs_url = urllib.parse.urljoin(page_url, href)
        text_context = html[max(0, m.start() - 350): m.end() + 350]
        clean_context = re.sub(r"<[^>]+>", " ", text_context)
        combined = (abs_url + " " + clean_context).lower()
        looks_downloadish = any(k in combined for k in [
            "download", ".csv", ".xlsx", ".xls", ".zip", "datastore", "resource", "maid", "raid", "rent", "mortgage", "afford"
        ])
        if looks_downloadish:
            links.append({
                "resource_id": None,
                "endpoint": page_url,
                "resource_url": abs_url,
                "name": clean_context[:500],
                "format": None,
                "mimetype": None,
                "last_modified": None,
                "status": f"{page_kind}_candidate_link",
            })

    # CKAN resource pages often expose relative /download/ links in scripts or metadata.
    for m in re.finditer(r'(https?://[^\s"\'<>]+)', html, flags=re.I):
        url = m.group(1).rstrip("),.;]")
        combined = url.lower()
        if any(k in combined for k in ["download", ".csv", ".xlsx", ".xls", ".zip", "maid", "raid"]):
            links.append({
                "resource_id": None,
                "endpoint": page_url,
                "resource_url": url,
                "name": "absolute_url_in_page_html",
                "format": None,
                "mimetype": None,
                "last_modified": None,
                "status": f"{page_kind}_absolute_url_candidate",
            })

    return links


def scrape_resource_links_from_dataset_page(logger: Logger) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    rows.extend(scrape_links_from_page(DATASET_PAGE, logger, "dataset_page"))
    rows.extend(scrape_links_from_page(RESOURCE_PAGE_TIME_SERIES_LONG, logger, "resource_page_time_series_long"))
    rows.extend(scrape_links_from_page(RESOURCE_PAGE_ALL_TABLES_XLSX, logger, "resource_page_all_tables_xlsx"))
    return rows


def package_show_resources(logger: Logger) -> list[dict[str, Any]]:
    endpoints = [
        f"https://catalogue.data.infrastructure.gov.au/api/3/action/package_show?id={DATASET_SLUG}",
        f"https://catalogue.data.infrastructure.gov.au/data/api/3/action/package_show?id={DATASET_SLUG}",
        f"https://data.gov.au/data/api/3/action/package_show?id={DATASET_SLUG}",
    ]
    rows: list[dict[str, Any]] = []
    for endpoint in endpoints:
        try:
            logger.debug(f"Trying CKAN package_show endpoint: {endpoint}")
            js = request_json(endpoint, timeout=90)
            result = js.get("result", {}) if isinstance(js, dict) else {}
            for res in result.get("resources", []) or []:
                url = res.get("url") or res.get("download_url")
                rows.append({
                    "resource_id": res.get("id"),
                    "endpoint": endpoint,
                    "resource_url": url,
                    "name": res.get("name"),
                    "format": res.get("format"),
                    "mimetype": res.get("mimetype"),
                    "last_modified": res.get("last_modified"),
                    "status": "package_show_resource",
                })
        except Exception as exc:
            rows.append({
                "resource_id": None,
                "endpoint": endpoint,
                "resource_url": None,
                "name": None,
                "format": None,
                "mimetype": None,
                "last_modified": None,
                "status": f"package_show_failed: {type(exc).__name__}: {exc}",
            })
    return rows

def candidate_resources(logger: Logger) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    rows.extend(ckan_resource_urls(RESOURCE_TIME_SERIES_LONG, logger))
    rows.extend(ckan_resource_urls(RESOURCE_ALL_TABLES_XLSX, logger))
    rows.extend(package_show_resources(logger))
    rows.extend(scrape_resource_links_from_dataset_page(logger))

    # Deterministic resource-page and download endpoint candidates. These are safe because they still go through
    # normal HTTP download and content parsing checks.
    for resource_id, name_hint, fmt in [
        (RESOURCE_TIME_SERIES_LONG, "2011-2021 Time Series: MAID & RAID (long format)", "CSV"),
        (RESOURCE_ALL_TABLES_XLSX, "All Tables - Excel Workbook", "XLSX"),
    ]:
        for base in [
            "https://catalogue.data.infrastructure.gov.au",
            "https://catalogue.data.infrastructure.gov.au/data",
            "https://data.gov.au/data",
        ]:
            rows.append({
                "resource_id": resource_id,
                "endpoint": "deterministic_resource_download_candidate",
                "resource_url": f"{base}/dataset/{DATASET_SLUG}/resource/{resource_id}/download",
                "name": name_hint,
                "format": fmt,
                "mimetype": None,
                "last_modified": None,
                "status": "deterministic_download_candidate",
            })

    df = pd.DataFrame(rows)
    if df.empty:
        return pd.DataFrame(columns=["resource_id", "endpoint", "resource_url", "name", "format", "mimetype", "last_modified", "status"])
    return df.drop_duplicates(subset=["resource_url", "endpoint", "resource_id"], keep="first")

def infer_filename(url: str, fallback: str) -> str:
    parsed = urllib.parse.urlparse(url)
    name = Path(urllib.parse.unquote(parsed.path)).name
    if not name or "." not in name:
        name = fallback
    name = re.sub(r"[^A-Za-z0-9_.-]+", "_", name)
    return name


def download_first_viable_resource(candidates: pd.DataFrame, raw_dir: Path, logger: Logger, force: bool = False) -> tuple[Path | None, pd.DataFrame]:
    audit_rows = []
    viable = candidates[candidates["resource_url"].notna()].copy() if "resource_url" in candidates.columns else pd.DataFrame()
    if viable.empty:
        return None, pd.DataFrame([{"status": "no_viable_resource_url", "resource_url": None, "local_path": None}])

    def score(row: pd.Series) -> int:
        text = " ".join(str(row.get(k, "")) for k in ["resource_url", "name", "format", "mimetype"]).lower()
        score_val = 0
        if str(row.get("resource_id", "")) == RESOURCE_TIME_SERIES_LONG:
            score_val += 100
        if "time" in text and "series" in text:
            score_val += 40
        if "long" in text:
            score_val += 30
        if "csv" in text:
            score_val += 20
        if "xlsx" in text or "xls" in text:
            score_val += 10
        if "maid" in text or "raid" in text:
            score_val += 20
        return -score_val

    viable["_sort_score"] = viable.apply(score, axis=1)
    viable = viable.sort_values("_sort_score")

    for i, row in viable.iterrows():
        url = str(row["resource_url"])
        if not url.lower().startswith(("http://", "https://")):
            continue
        filename = infer_filename(url, f"housing_affordability_candidate_{i}.dat")
        if "." not in filename.lower():
            # Use format hint.
            fmt = str(row.get("format") or "").lower()
            if "csv" in fmt:
                filename += ".csv"
            elif "xls" in fmt:
                filename += ".xlsx"
        local_path = raw_dir / filename
        try:
            if local_path.exists() and not force and local_path.stat().st_size > 0:
                logger.info(f"Using cached housing affordability resource: {local_path}")
                audit_rows.append({**row.to_dict(), "download_status": "cached", "local_path": str(local_path), "bytes": local_path.stat().st_size})
                return local_path, pd.DataFrame(audit_rows)
            logger.info(f"Downloading housing affordability resource: {url}")
            data = request_bytes(url, timeout=240)
            local_path.write_bytes(data)
            audit_rows.append({**row.to_dict(), "download_status": "downloaded", "local_path": str(local_path), "bytes": len(data)})
            logger.info(f"Downloaded {len(data):,} bytes to {local_path}")
            return local_path, pd.DataFrame(audit_rows)
        except Exception as exc:
            logger.warning(f"Candidate failed: {url} :: {type(exc).__name__}: {exc}")
            audit_rows.append({**row.to_dict(), "download_status": f"failed: {type(exc).__name__}: {exc}", "local_path": str(local_path), "bytes": None})
    return None, pd.DataFrame(audit_rows)


def read_source_any(path: Path, logger: Logger) -> pd.DataFrame:
    suffix = path.suffix.lower()
    logger.info(f"Reading housing affordability source: {path}")
    if suffix in [".csv", ".txt"]:
        return pd.read_csv(path, dtype=str, low_memory=False)
    if suffix in [".tsv"]:
        return pd.read_csv(path, dtype=str, sep="\t", low_memory=False)
    if suffix in [".xlsx", ".xls"]:
        sheets = pd.read_excel(path, sheet_name=None, dtype=str)
        # Prefer sheets that look like long SA2 2021 MAID/RAID data.
        scored = []
        for name, df in sheets.items():
            text = " ".join([str(name)] + [str(c) for c in df.columns]).lower()
            score = 0
            for key in ["sa2", "2021", "maid", "raid", "long", "rent", "mortgage"]:
                if key in text:
                    score += 1
            scored.append((score, name, df))
        scored.sort(key=lambda x: x[0], reverse=True)
        if not scored:
            raise ValueError(f"Workbook has no sheets: {path}")
        logger.info(f"Selected workbook sheet: {scored[0][1]} (score {scored[0][0]})")
        return scored[0][2]
    raise ValueError(f"Unsupported source file type: {path.suffix}")


def find_col_by_patterns(columns: Iterable[str], patterns: list[str], must_not: list[str] | None = None) -> str | None:
    must_not = must_not or []
    for col in columns:
        n = norm_col(col)
        if any(re.search(p, n, re.I) for p in must_not):
            continue
        if all(re.search(p, n, re.I) for p in patterns):
            return col
    return None


def detect_year_col(df: pd.DataFrame) -> str | None:
    candidates = []
    for col in df.columns:
        n = norm_col(col)
        if n in ["year", "census_year", "censusyear"] or ("year" in n and "financial" not in n):
            vals = pd.to_numeric(df[col].astype(str).str.extract(r"(20\d{2}|19\d{2})", expand=False), errors="coerce")
            rate = vals.notna().mean()
            candidates.append((rate, col))
    candidates.sort(reverse=True)
    return candidates[0][1] if candidates and candidates[0][0] > 0.2 else None


def detect_sa2_code_col(df: pd.DataFrame) -> str | None:
    cols = list(df.columns)
    # Strong name-based guesses first.
    for patterns in [[r"sa2", r"code"], [r"sa2", r"main"], [r"area", r"code"], [r"geography", r"code"]]:
        col = find_col_by_patterns(cols, patterns)
        if col:
            vals = df[col].astype(str).str.extract(r"(\d{9})", expand=False)
            if vals.notna().mean() > 0.2:
                return col
    # Data-based fallback.
    scored = []
    for col in cols:
        vals = df[col].astype(str).str.extract(r"(\d{9})", expand=False)
        rate = vals.notna().mean()
        if rate > 0.2:
            scored.append((rate, col))
    scored.sort(reverse=True)
    return scored[0][1] if scored else None


def filter_to_sa2_2021(df: pd.DataFrame, logger: Logger) -> tuple[pd.DataFrame, dict[str, Any]]:
    out = df.copy()
    schema: dict[str, Any] = {
        "raw_rows": len(df),
        "raw_columns": len(df.columns),
        "year_col": None,
        "sa2_code_col": None,
        "geography_filter_col": None,
        "rows_after_year_filter": None,
        "rows_after_geography_filter": None,
    }

    # Clean empty rows/columns.
    out = out.dropna(how="all")
    out = out.loc[:, ~out.columns.astype(str).str.match(r"^Unnamed", na=False)]

    year_col = detect_year_col(out)
    schema["year_col"] = year_col
    if year_col:
        year_vals = pd.to_numeric(out[year_col].astype(str).str.extract(r"(20\d{2}|19\d{2})", expand=False), errors="coerce")
        if (year_vals == TARGET_YEAR).any():
            out = out.loc[year_vals == TARGET_YEAR].copy()
    schema["rows_after_year_filter"] = len(out)

    # Geography filter if available.
    geo_cols = []
    for col in out.columns:
        n = norm_col(col)
        if any(k in n for k in ["geography", "geographic", "geo", "area_type", "level", "region_type"]):
            geo_cols.append(col)
    chosen_geo = None
    for col in geo_cols:
        vals = out[col].astype(str).str.lower()
        if vals.str.contains(r"\bsa2\b|statistical area level 2|statistical area 2", regex=True, na=False).any():
            chosen_geo = col
            out = out.loc[vals.str.contains(r"\bsa2\b|statistical area level 2|statistical area 2", regex=True, na=False)].copy()
            break
    schema["geography_filter_col"] = chosen_geo
    schema["rows_after_geography_filter"] = len(out)

    sa2_col = detect_sa2_code_col(out)
    schema["sa2_code_col"] = sa2_col
    if not sa2_col:
        raise ValueError("Could not detect a 9-digit SA2 code column in the housing affordability source.")
    out["sa2_code_2021"] = out[sa2_col].astype(str).str.extract(r"(\d{9})", expand=False).str.zfill(9)
    out = out[out["sa2_code_2021"].notna()].copy()
    logger.info(f"Housing affordability rows after SA2/year filtering: {len(out):,}")
    return out, schema


def numeric_parse(series: pd.Series) -> pd.Series:
    return pd.to_numeric(
        series.astype(str)
        .str.replace(",", "", regex=False)
        .str.replace("%", "", regex=False)
        .str.replace("$", "", regex=False)
        .str.replace("..", "", regex=False)
        .str.strip(),
        errors="coerce",
    )


def build_housing_features(sa2_df: pd.DataFrame, logger: Logger) -> tuple[pd.DataFrame, pd.DataFrame]:
    df = sa2_df.copy()
    code_col = "sa2_code_2021"

    col_audit_rows = []
    numeric_cols = []
    for col in df.columns:
        n = norm_col(col)
        if col == code_col:
            continue
        if any(re.search(p, n) for p in NOT_MEASURE_COL_PATTERNS):
            continue
        values = numeric_parse(df[col])
        parse_rate = float(values.notna().mean()) if len(values) else 0.0
        non_missing = int(values.notna().sum())
        unique_non_null = int(values.dropna().nunique()) if non_missing else 0
        is_numeric_measure = parse_rate > 0.75 and non_missing > 0 and unique_non_null > 1
        col_audit_rows.append({
            "source_column_name": col,
            "normalised_column_name": n,
            "numeric_parse_rate": parse_rate,
            "non_missing_numeric_count": non_missing,
            "unique_numeric_count": unique_non_null,
            "selected_as_measure": int(is_numeric_measure),
            "sample_values": " | ".join(df[col].astype(str).dropna().head(8).tolist()),
        })
        if is_numeric_measure:
            numeric_cols.append(col)

    logger.info(f"Housing affordability numeric measure candidates: {len(numeric_cols):,}")

    if not numeric_cols:
        # Try long-format pivot. Use object descriptor columns and first numeric value-like column.
        raise ValueError("No numeric housing affordability measure columns were detected after SA2/year filtering.")

    # If there are a reasonable number of numeric columns, treat source as wide.
    if len(numeric_cols) <= 80:
        features = df[[code_col] + numeric_cols].copy()
        rename = {col: f"housing_affordability_2021_{slugify(col, 70)}" for col in numeric_cols}
        for col in numeric_cols:
            features[col] = numeric_parse(features[col])
        features = features.groupby(code_col, as_index=False)[numeric_cols].sum(min_count=1)
        features = features.rename(columns=rename)
        features["source_housing_affordability_2021_present_flag"] = 1
        return features, pd.DataFrame(col_audit_rows)

    # If very many numeric columns exist, use a long-format strategy.
    # Descriptor columns: object columns that are not code/name/year/geo filters and have limited categories.
    descriptor_cols = []
    for col in df.columns:
        if col == code_col or col in numeric_cols:
            continue
        n = norm_col(col)
        if any(re.search(p, n) for p in NOT_MEASURE_COL_PATTERNS):
            continue
        nunique = df[col].dropna().astype(str).nunique()
        if 1 < nunique <= 80:
            descriptor_cols.append(col)
    descriptor_cols = descriptor_cols[:5]
    value_cols = numeric_cols[:5]
    logger.info(f"Using long-format pivot with descriptors {descriptor_cols} and value columns {value_cols}")
    long_rows = []
    for val_col in value_cols:
        temp = df[[code_col] + descriptor_cols + [val_col]].copy()
        temp["_value"] = numeric_parse(temp[val_col])
        temp = temp[temp["_value"].notna()].copy()
        if descriptor_cols:
            temp["_feature"] = temp[descriptor_cols].astype(str).agg("__".join, axis=1)
            temp["_feature"] = temp["_feature"].map(lambda x: f"{val_col}__{x}")
        else:
            temp["_feature"] = val_col
        temp["_feature"] = temp["_feature"].map(lambda x: f"housing_affordability_2021_{slugify(x, 90)}")
        long_rows.append(temp[[code_col, "_feature", "_value"]])
    long = pd.concat(long_rows, ignore_index=True)
    features = long.pivot_table(index=code_col, columns="_feature", values="_value", aggfunc="sum", fill_value=0).reset_index()
    features.columns.name = None
    features["source_housing_affordability_2021_present_flag"] = 1
    return features, pd.DataFrame(col_audit_rows)


def join_features(master: pd.DataFrame, features: pd.DataFrame, logger: Logger) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    base_rows = len(master)
    base_cols = len(master.columns)
    feature_cols = [c for c in features.columns if c != "sa2_code_2021"]
    if features["sa2_code_2021"].duplicated().any():
        dup = features.loc[features["sa2_code_2021"].duplicated(), "sa2_code_2021"].head(10).tolist()
        raise ValueError(f"Housing features contain duplicate SA2 rows. Sample: {dup}")
    out = master.merge(features, on="sa2_code_2021", how="left", validate="one_to_one")
    if "source_housing_affordability_2021_present_flag" in out.columns:
        out["source_housing_affordability_2021_present_flag"] = out["source_housing_affordability_2021_present_flag"].fillna(0).astype(int)
    matched = int(out[feature_cols].notna().any(axis=1).sum()) if feature_cols else 0
    unmatched = master.loc[~master["sa2_code_2021"].isin(features["sa2_code_2021"]), [
        c for c in ["sa2_code_2021", "sa2_name_2021", "sa3_code_2021", "sa3_name_2021", "state_name_2021"] if c in master.columns
    ]].copy()
    audit = pd.DataFrame([
        {"check_name": "base_master_rows", "value": base_rows, "status": "pass" if base_rows == 2472 else "review", "notes": "Expected SA2 row count for current project spine."},
        {"check_name": "base_master_columns", "value": base_cols, "status": "info", "notes": ""},
        {"check_name": "housing_feature_rows", "value": len(features), "status": "info", "notes": "SA2 rows in housing affordability feature table."},
        {"check_name": "housing_feature_columns", "value": len(feature_cols), "status": "info", "notes": "Housing affordability columns added or considered."},
        {"check_name": "master_rows_after_join", "value": len(out), "status": "pass" if len(out) == base_rows else "fail", "notes": "Join must not change SA2 row count."},
        {"check_name": "master_columns_after_join", "value": len(out.columns), "status": "info", "notes": ""},
        {"check_name": "duplicate_sa2_rows_after_join", "value": int(out["sa2_code_2021"].duplicated().sum()), "status": "pass" if int(out["sa2_code_2021"].duplicated().sum()) == 0 else "fail", "notes": ""},
        {"check_name": "sa2_rows_with_housing_affordability_context", "value": matched, "status": "info", "notes": "Rows with at least one joined housing affordability field."},
    ])
    return out, audit, unmatched


def build_dictionary(feature_cols: list[str]) -> pd.DataFrame:
    rows = []
    for col in feature_cols:
        rows.append({
            "column_name": col,
            "source_family": "housing affordability / Census MAID RAID",
            "native_geography": "SA2 2021 where available",
            "field_role": "context_predictor_candidate",
            "primary_model_use": "candidate_after_denominator_or_rate_review",
            "notes": "Household count or affordability category field unless audit confirms otherwise. Use with household denominators or convert to proportions before interpretation.",
        })
    return pd.DataFrame(rows)


def methodology_note() -> str:
    return f"""# Housing affordability context layer v07

This layer attempts to add Census mortgage and rent affordability indicators to the SA2 master.

Source target:
- Dataset page: {DATASET_PAGE}
- Preferred resource: 2011-2021 Time Series MAID & RAID long format, resource id `{RESOURCE_TIME_SERIES_LONG}`
- Fallback resource: All Tables Excel workbook, resource id `{RESOURCE_ALL_TABLES_XLSX}`

Method:
1. Read the v06 master with DSS social security context.
2. Inventory housing-related columns already present in the master, mainly from Census QuickStats.
3. Attempt to acquire the Regional Data Hub/ABS-derived MAID and RAID affordability data.
4. Filter to 2021 and SA2-level records where detected.
5. Build numeric SA2 features and join by `sa2_code_2021`.
6. Preserve the SA2 spine; the join must not change row count.

Interpretation caveat:
Housing affordability indicators are usually household counts by affordability category. They should not be interpreted as rates until divided by a relevant denominator, such as renting households, mortgaged households or occupied private dwellings.

Modelling rule:
Treat these as context predictor candidates. Before primary modelling, derive proportions or include appropriate household/population denominators.
"""




def build_existing_only_dictionary(existing_inventory: pd.DataFrame) -> pd.DataFrame:
    rows = []
    if existing_inventory is None or existing_inventory.empty:
        return pd.DataFrame(columns=["column_name", "source_family", "native_geography", "field_role", "primary_model_use", "notes"])
    for col in existing_inventory["column_name"].astype(str).tolist():
        rows.append({
            "column_name": col,
            "source_family": "existing Census QuickStats / prior master housing variables",
            "native_geography": "SA2 2021 master column",
            "field_role": "existing_context_predictor_candidate",
            "primary_model_use": "candidate_after_feature_scoping_and_denominator_review",
            "notes": "Existing housing-related field already present in the v06 master. This v07 fallback did not add external MAID/RAID columns because no downloadable external resource was available during acquisition.",
        })
    return pd.DataFrame(rows)


def fallback_existing_only_v07(
    master: pd.DataFrame,
    existing_inventory: pd.DataFrame,
    source_selection_audit: pd.DataFrame,
    paths: dict[str, Path],
    logger: Logger,
) -> None:
    """Create a documented v07 checkpoint when the external MAID/RAID resource cannot be downloaded."""
    logger.warning("No external housing affordability resource was downloaded. Creating v07 as an existing-housing-inventory checkpoint.")
    out = master.copy()
    out["source_housing_affordability_external_2021_present_flag"] = 0
    out["housing_affordability_external_acquisition_status"] = "external_resource_not_downloaded_existing_master_inventory_only"

    join_audit = pd.DataFrame([
        {"check_name": "base_master_rows", "value": len(master), "status": "pass" if len(master) == 2472 else "review", "notes": "Expected SA2 row count for current project spine."},
        {"check_name": "base_master_columns", "value": len(master.columns), "status": "info", "notes": "v06 source master columns."},
        {"check_name": "existing_housing_related_columns_in_master", "value": len(existing_inventory), "status": "info", "notes": "Housing-related columns already present, mostly from Census QuickStats."},
        {"check_name": "external_housing_resource_downloaded", "value": 0, "status": "review", "notes": "External MAID/RAID source was not downloaded. Review source candidate and selection audits."},
        {"check_name": "master_rows_after_join", "value": len(out), "status": "pass" if len(out) == len(master) else "fail", "notes": "Fallback must not change SA2 row count."},
        {"check_name": "master_columns_after_join", "value": len(out.columns), "status": "info", "notes": "Only fallback status fields added."},
        {"check_name": "duplicate_sa2_rows_after_join", "value": int(out["sa2_code_2021"].duplicated().sum()), "status": "pass" if int(out["sa2_code_2021"].duplicated().sum()) == 0 else "fail", "notes": ""},
        {"check_name": "sa2_rows_with_housing_affordability_context", "value": int(len(out)), "status": "info", "notes": "Rows retain existing master housing context where present; no external MAID/RAID columns joined."},
    ])
    write_csv(join_audit, paths["audits"] / "housing_affordability_join_audit_v07.csv", logger)
    write_csv(pd.DataFrame(columns=["sa2_code_2021", "sa2_name_2021", "reason"]), paths["audits"] / "housing_affordability_unmatched_audit_v07.csv", logger)
    write_csv(build_existing_only_dictionary(existing_inventory), paths["dicts"] / "housing_affordability_context_field_dictionary_v07.csv", logger)

    note = methodology_note() + """

## v07 acquisition status

The external MAID/RAID housing affordability resource was not downloaded during this run. v07 was still created as a documented checkpoint because the v06 master already contains housing-related Census QuickStats variables. The relevant existing columns are listed in `outputs/audits/housing_existing_master_column_inventory_v07.csv`.

No external MAID/RAID fields were added. The two fallback metadata fields are:
- `source_housing_affordability_external_2021_present_flag`
- `housing_affordability_external_acquisition_status`

Before modelling, use the existing housing inventory to select rent, mortgage, tenure, dwelling and occupancy variables already available in the master.
"""
    note_path = paths["methodology"] / "housing_affordability_context_layer_note_v07.md"
    logger.info(f"Writing methodology note: {note_path}")
    note_path.write_text(note, encoding="utf-8")

    out_csv = paths["processed_integrated"] / "sa2_predictor_universe_v07_with_housing_affordability_context.csv"
    out_parquet = paths["processed_integrated"] / "sa2_predictor_universe_v07_with_housing_affordability_context.parquet"
    write_csv(out, out_csv, logger)
    write_parquet(out, out_parquet, logger)
    logger.info("Created v07 fallback housing context master:")
    logger.info(f"  {out_parquet}")
    logger.info(f"  {out_csv}")

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Acquire and join housing affordability context to SA2 master.")
    p.add_argument("--project-root", default=str(DEFAULT_PROJECT_ROOT))
    p.add_argument("--base-master", default=str(DEFAULT_BASE_MASTER))
    p.add_argument("--debug", action="store_true")
    p.add_argument("--force-download", action="store_true")
    p.add_argument("--audit-only", action="store_true", help="Only inventory existing master housing columns and source candidates; do not join external data.")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    root = Path(args.project_root)
    paths = ensure_dirs(root)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    logger = Logger(paths["logs"] / f"17_acquire_join_housing_affordability_context_{stamp}.log", debug=args.debug)

    logger.info("Housing affordability context acquisition and join")
    logger.info(f"Project root: {root}")
    logger.info(f"Log path: {logger.log_path}")
    logger.info(f"Base master: {args.base_master}")

    master = read_master(Path(args.base_master), logger)

    existing_inventory = inventory_existing_housing_columns(master)
    write_csv(existing_inventory, paths["audits"] / "housing_existing_master_column_inventory_v07.csv", logger)
    logger.info(f"Existing housing-related master columns found: {len(existing_inventory):,}")

    candidates = candidate_resources(logger)
    write_csv(candidates, paths["audits"] / "housing_affordability_source_candidate_audit_v07.csv", logger)

    if args.audit_only:
        logger.info("Audit-only mode selected. Stopping before external acquisition/join.")
        return

    source_path, source_selection_audit = download_first_viable_resource(candidates, paths["raw"], logger, force=args.force_download)
    write_csv(source_selection_audit, paths["audits"] / "housing_affordability_source_selection_audit_v07.csv", logger)
    if source_path is None:
        fallback_existing_only_v07(master, existing_inventory, source_selection_audit, paths, logger)
        return

    raw = read_source_any(source_path, logger)
    logger.info(f"Housing source raw rows: {len(raw):,}; columns: {len(raw.columns):,}")

    filtered, schema_info = filter_to_sa2_2021(raw, logger)
    schema_audit = pd.DataFrame([schema_info])

    features, column_audit = build_housing_features(filtered, logger)
    feature_cols = [c for c in features.columns if c != "sa2_code_2021"]
    logger.info(f"Housing feature table rows: {len(features):,}; columns: {len(features.columns):,}")

    write_csv(schema_audit, paths["audits"] / "housing_affordability_schema_audit_v07.csv", logger)
    write_csv(column_audit, paths["audits"] / "housing_affordability_measure_column_audit_v07.csv", logger)
    write_csv(features, paths["processed_sources"] / "housing_affordability_2021_sa2_features.csv", logger)
    write_parquet(features, paths["processed_sources"] / "housing_affordability_2021_sa2_features.parquet", logger)

    joined, join_audit, unmatched = join_features(master, features, logger)
    write_csv(join_audit, paths["audits"] / "housing_affordability_join_audit_v07.csv", logger)
    write_csv(unmatched, paths["audits"] / "housing_affordability_unmatched_audit_v07.csv", logger)

    dict_df = build_dictionary(feature_cols)
    write_csv(dict_df, paths["dicts"] / "housing_affordability_context_field_dictionary_v07.csv", logger)
    note_path = paths["methodology"] / "housing_affordability_context_layer_note_v07.md"
    logger.info(f"Writing methodology note: {note_path}")
    note_path.write_text(methodology_note(), encoding="utf-8")

    out_csv = paths["processed_integrated"] / "sa2_predictor_universe_v07_with_housing_affordability_context.csv"
    out_parquet = paths["processed_integrated"] / "sa2_predictor_universe_v07_with_housing_affordability_context.parquet"
    write_csv(joined, out_csv, logger)
    write_parquet(joined, out_parquet, logger)

    logger.info("Created v07 housing affordability master:")
    logger.info(f"  {out_parquet}")
    logger.info(f"  {out_csv}")
    logger.info("Next action: review housing_affordability_join_audit_v07.csv and housing_existing_master_column_inventory_v07.csv")


if __name__ == "__main__":
    _started = datetime.now()
    try:
        main()
    except Exception as exc:
        tb = traceback.format_exc()
        print(tb, file=sys.stderr)
        notify_script_completion(False, SCRIPT_NAME, _started, detail=str(exc))
        raise
    else:
        notify_script_completion(True, SCRIPT_NAME, _started)
