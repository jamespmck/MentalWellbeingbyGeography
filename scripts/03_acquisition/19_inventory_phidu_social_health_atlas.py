#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
19_inventory_phidu_social_health_atlas.py

Inventory PHIDU Social Health Atlas workbooks before any join to the
MentalWellbeingByGeography master.

Purpose
-------
This is an acquisition and schema-audit script, not a join script.

It:
  1. validates the current v08 master exists and has one SA2 row per area;
  2. discovers PHIDU Social Health Atlas workbook links from the PHIDU data page;
  3. downloads a capped set of relevant workbook candidates;
  4. inventories workbook sheets, likely geography columns and indicator columns;
  5. classifies join readiness by geography: SA2, LGA, PHN, PHA, IARE, remoteness, SEIFA;
  6. writes an acquisition register and methodology note.

It deliberately does not join PHIDU data to the master. PHIDU geographies
must be reviewed first. PHA and PHN/LGA component structures are not equivalent
to SA2 and should not be forced into the SA2 master without a validated method.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import html
import logging
import re
import shutil
import sys
import time
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen

import pandas as pd


SCRIPT_VERSION = "v09"
SCRIPT_NAME = Path(__file__).name

PHIDU_DATA_URL = "https://phidu.torrens.edu.au/social-health-atlases/data"
PHIDU_ARCHIVE_URL = "https://phidu.torrens.edu.au/social-health-atlases/data-archive/data-archive-social-health-atlases-of-australia"
PHIDU_CONTENTS_URL = "https://phidu.torrens.edu.au/social-health-atlases/indicators-and-notes-on-the-data/social-health-atlases-of-australia-contents"

DEFAULT_BASE_MASTER = r"data\processed\integrated\sa2_predictor_universe_v08_with_clean_housing_context.parquet"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0 Safari/537.36 MentalWellbeingByGeography/PHIDUInventory"
)

HIGH_VALUE_TERMS = [
    "health status",
    "disability",
    "carer",
    "carers",
    "mental",
    "welfare services",
    "health and welfare",
    "use and provision",
    "demographic and social",
    "lga",
    "local government",
    "primary health network",
    "phn",
    "population health area",
    "pha",
]

LOW_PRIORITY_TERMS = [
    "archive",
    "remoteness",
    "socioeconomic disadvantage",
    "inequality",
    "time series",
    "indigenous area",
    "aboriginal",
    "torres strait",
]


@dataclass
class Candidate:
    source_page: str
    candidate_url: str
    candidate_text: str
    candidate_kind: str
    geography_guess: str
    topic_guess: str
    priority_score: int
    selected_for_download: int
    notes: str = ""


@dataclass
class DownloadRecord:
    candidate_url: str
    candidate_text: str
    geography_guess: str
    topic_guess: str
    local_path: str
    status: str
    http_status_or_error: str
    bytes_downloaded: int
    sha256: str
    notes: str = ""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Inventory PHIDU Social Health Atlas workbooks.")
    parser.add_argument("--project-root", default=".", help="Project root. Defaults to current directory.")
    parser.add_argument("--base-master", default=DEFAULT_BASE_MASTER, help="Base master path relative to project root or absolute.")
    parser.add_argument("--debug", action="store_true", help="Verbose logging.")
    parser.add_argument("--force-download", action="store_true", help="Re-download PHIDU files even if cached.")
    parser.add_argument("--include-archive", action="store_true", help="Also scan PHIDU archive page. Default: latest data page only.")
    parser.add_argument("--max-downloads", type=int, default=10, help="Maximum workbook files to download. Default 10.")
    parser.add_argument(
        "--geographies",
        default="lga,phn,pha",
        help="Comma-separated geography families to prioritise. Default: lga,phn,pha.",
    )
    parser.add_argument(
        "--workbook-row-sample",
        type=int,
        default=250,
        help="Rows to sample per sheet when inspecting workbook schemas. Default 250.",
    )
    parser.add_argument(
        "--skip-download",
        action="store_true",
        help="Only discover candidate links and write register; do not download workbooks.",
    )
    return parser.parse_args()


def setup_logging(root: Path, debug: bool) -> tuple[logging.Logger, Path]:
    log_dir = root / "outputs" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = log_dir / f"19_inventory_phidu_social_health_atlas_{timestamp}.log"

    logger = logging.getLogger("phidu_inventory")
    logger.setLevel(logging.DEBUG if debug else logging.INFO)
    logger.handlers.clear()

    formatter = logging.Formatter("[%(asctime)s] [%(levelname)s] %(message)s", "%Y-%m-%d %H:%M:%S")

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setLevel(logging.DEBUG if debug else logging.INFO)
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    return logger, log_path


def notify_script_completion(success: bool, script_name: str, started_at: datetime | None = None, detail: str = "") -> None:
    """
    Windows/Positron-friendly completion notification.
    """
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
        safe_detail = str(detail).replace("'", "’").replace('"', "”")
        message += f"\n\n{safe_detail[:600]}"

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
            import subprocess

            icon = 64 if success else 16
            timeout_seconds = 10 if success else 25
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


def ensure_dirs(root: Path) -> dict[str, Path]:
    dirs = {
        "raw_phidu": root / "data" / "raw" / "phidu",
        "processed_sources": root / "data" / "processed" / "sources",
        "audits": root / "outputs" / "audits",
        "source_registers": root / "docs" / "source_registers",
        "methodology": root / "docs" / "methodology",
    }
    for path in dirs.values():
        path.mkdir(parents=True, exist_ok=True)
    return dirs


def write_csv(path: Path, rows: list[dict] | pd.DataFrame, logger: logging.Logger) -> None:
    logger.info("Writing CSV: %s", path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(rows, pd.DataFrame):
        rows.to_csv(path, index=False)
    else:
        pd.DataFrame(rows).to_csv(path, index=False)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def safe_request(url: str, timeout: int = 60) -> bytes:
    req = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "*/*"})
    with urlopen(req, timeout=timeout) as resp:
        return resp.read()


def fetch_text(url: str, logger: logging.Logger, timeout: int = 60) -> tuple[str, str]:
    logger.debug("Fetching HTML: %s", url)
    data = safe_request(url, timeout=timeout)
    text = data.decode("utf-8", errors="replace")
    return text, hashlib.sha256(data).hexdigest()


def strip_tags(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text, flags=re.S)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def extract_links(page_url: str, html_text: str) -> list[tuple[str, str]]:
    # Conservative anchor extraction. Good enough for PHIDU static pages.
    links: list[tuple[str, str]] = []
    for match in re.finditer(r"<a\b([^>]*)>(.*?)</a>", html_text, flags=re.I | re.S):
        attrs, inner = match.group(1), match.group(2)
        href_match = re.search(r"href\s*=\s*['\"]([^'\"]+)['\"]", attrs, flags=re.I)
        if not href_match:
            continue
        href = html.unescape(href_match.group(1)).strip()
        if not href or href.startswith("#") or href.lower().startswith(("javascript:", "mailto:")):
            continue
        url = urljoin(page_url, href)
        text = strip_tags(inner)
        links.append((url, text))
    return links


def normalise_text(x: str) -> str:
    x = html.unescape(str(x or "")).lower()
    x = re.sub(r"[^a-z0-9]+", " ", x)
    return re.sub(r"\s+", " ", x).strip()


def slugify(x: str, max_len: int = 160) -> str:
    x = normalise_text(x)
    x = re.sub(r"\s+", "_", x)
    x = x.strip("_")
    return (x[:max_len].strip("_") or "phidu_resource")


def classify_geography(url: str, text: str) -> str:
    n = normalise_text(f"{text} {url}")
    if "population health area" in n or re.search(r"\bpha\b", n):
        return "PHA"
    if "local government" in n or re.search(r"\blga\b", n):
        return "LGA"
    if "primary health network" in n or re.search(r"\bphn\b", n):
        if "component lga" in n or "component local government" in n:
            return "PHN_WITH_COMPONENT_LGAS"
        if "component pha" in n or "component population health" in n:
            return "PHN_WITH_COMPONENT_PHAS"
        return "PHN"
    if "indigenous area" in n or re.search(r"\biare\b", n):
        return "IARE"
    if "remoteness" in n:
        return "REMOTENESS"
    if "socioeconomic disadvantage" in n or "inequality" in n:
        return "SEIFA_QUINTILE"
    if "statistical area level 3" in n or re.search(r"\bsa3\b", n):
        return "SA3"
    if "statistical area level 4" in n or re.search(r"\bsa4\b", n):
        return "SA4"
    if "statistical area level 2" in n or re.search(r"\bsa2\b", n):
        return "SA2"
    return "UNKNOWN"


def classify_topic(url: str, text: str) -> str:
    n = normalise_text(f"{text} {url}")
    if "health status" in n or "disease prevention" in n or "disability" in n or "carer" in n or "deaths" in n:
        return "health_status_disability_carers_deaths"
    if "use and provision" in n or "health and welfare services" in n or "services" in n:
        return "health_welfare_services"
    if "demographic" in n or "social indicators" in n:
        return "demographic_social"
    if "all topics" in n or "all indicators" in n:
        return "all_topics"
    if "socioeconomic" in n or "inequality" in n:
        return "socioeconomic_disadvantage"
    if "remoteness" in n:
        return "remoteness"
    if "indigenous" in n or "aboriginal" in n or "torres strait" in n:
        return "first_nations"
    return "unknown"


def score_candidate(url: str, text: str, requested_geographies: set[str]) -> int:
    n = normalise_text(f"{text} {url}")
    geo = classify_geography(url, text).lower()
    score = 0
    if url.lower().endswith((".xlsx", ".xls", ".csv", ".zip")):
        score += 8
    if "xlsx" in n or "workbook" in n:
        score += 5
    for term in HIGH_VALUE_TERMS:
        if term in n:
            score += 3
    for g in requested_geographies:
        if g and g in geo:
            score += 8
        if g == "lga" and ("local government" in n or re.search(r"\blga\b", n)):
            score += 8
        if g == "phn" and ("primary health network" in n or re.search(r"\bphn\b", n)):
            score += 8
        if g == "pha" and ("population health area" in n or re.search(r"\bpha\b", n)):
            score += 8
    for term in LOW_PRIORITY_TERMS:
        if term in n:
            score -= 1
    if "pdf" in n or url.lower().endswith(".pdf"):
        score -= 6
    if "maps" in n or "graphs" in n:
        score -= 2
    return score


def candidate_kind(url: str, text: str) -> str:
    lower = url.lower()
    n = normalise_text(f"{text} {url}")
    if lower.endswith(".xlsx") or ".xlsx" in lower or "xlsx" in n:
        return "workbook_xlsx_candidate"
    if lower.endswith(".xls") or ".xls" in lower or "xls" in n:
        return "workbook_xls_candidate"
    if lower.endswith(".csv") or ".csv" in lower:
        return "csv_candidate"
    if lower.endswith(".zip") or ".zip" in lower:
        return "zip_candidate"
    return "html_or_resource_page_candidate"


def discover_candidates(
    source_pages: list[str],
    requested_geographies: set[str],
    logger: logging.Logger,
) -> list[Candidate]:
    candidates: list[Candidate] = []
    seen: set[str] = set()

    for page in source_pages:
        try:
            html_text, _ = fetch_text(page, logger)
        except Exception as exc:
            logger.warning("Could not fetch source page %s: %s", page, exc)
            candidates.append(
                Candidate(
                    source_page=page,
                    candidate_url=page,
                    candidate_text="source page fetch failed",
                    candidate_kind="source_page_fetch_failed",
                    geography_guess="UNKNOWN",
                    topic_guess="unknown",
                    priority_score=-999,
                    selected_for_download=0,
                    notes=str(exc),
                )
            )
            continue

        # include source page itself
        key = page
        if key not in seen:
            seen.add(key)
            candidates.append(
                Candidate(
                    source_page=page,
                    candidate_url=page,
                    candidate_text="source_page",
                    candidate_kind="source_page",
                    geography_guess=classify_geography(page, "source_page"),
                    topic_guess=classify_topic(page, "source_page"),
                    priority_score=0,
                    selected_for_download=0,
                    notes="text/html",
                )
            )

        links = extract_links(page, html_text)
        logger.info("Discovered %s links from %s", len(links), page)

        for url, text in links:
            url_clean = url.split("#")[0]
            if url_clean in seen:
                continue
            seen.add(url_clean)

            kind = candidate_kind(url_clean, text)
            score = score_candidate(url_clean, text, requested_geographies)
            # Keep broad list for audit, but only meaningful candidates get positive scores.
            if kind == "html_or_resource_page_candidate" and score < 3:
                # Still keep PHIDU-internal pages likely to contain xlsx resources.
                if "phidu.torrens.edu.au" not in url_clean:
                    continue

            candidates.append(
                Candidate(
                    source_page=page,
                    candidate_url=url_clean,
                    candidate_text=text,
                    candidate_kind=kind,
                    geography_guess=classify_geography(url_clean, text),
                    topic_guess=classify_topic(url_clean, text),
                    priority_score=score,
                    selected_for_download=0,
                    notes="discovered_anchor",
                )
            )

    # Crawl promising PHIDU internal resource pages one level for hidden/direct workbook links.
    extra: list[Candidate] = []
    for cand in list(candidates):
        if cand.candidate_kind != "html_or_resource_page_candidate":
            continue
        if cand.priority_score < 6:
            continue
        if "phidu.torrens.edu.au" not in cand.candidate_url:
            continue
        try:
            sub_html, _ = fetch_text(cand.candidate_url, logger)
            sub_links = extract_links(cand.candidate_url, sub_html)
        except Exception as exc:
            logger.debug("Could not crawl candidate page %s: %s", cand.candidate_url, exc)
            continue
        for url, text in sub_links:
            url_clean = url.split("#")[0]
            if url_clean in seen:
                continue
            kind = candidate_kind(url_clean, text)
            score = score_candidate(url_clean, f"{cand.candidate_text} {text}", requested_geographies)
            if kind in {"workbook_xlsx_candidate", "workbook_xls_candidate", "csv_candidate", "zip_candidate"} or score >= 8:
                seen.add(url_clean)
                extra.append(
                    Candidate(
                        source_page=cand.candidate_url,
                        candidate_url=url_clean,
                        candidate_text=f"{cand.candidate_text} | {text}".strip(" |"),
                        candidate_kind=kind,
                        geography_guess=classify_geography(url_clean, f"{cand.candidate_text} {text}"),
                        topic_guess=classify_topic(url_clean, f"{cand.candidate_text} {text}"),
                        priority_score=score,
                        selected_for_download=0,
                        notes="discovered_from_candidate_page",
                    )
                )
    candidates.extend(extra)
    return candidates


def choose_download_candidates(candidates: list[Candidate], max_downloads: int) -> list[Candidate]:
    downloadable = [
        c for c in candidates
        if c.candidate_kind in {
            "workbook_xlsx_candidate",
            "workbook_xls_candidate",
            "csv_candidate",
            "zip_candidate",
        }
        or c.priority_score >= 12
    ]
    downloadable.sort(key=lambda c: (c.priority_score, c.candidate_kind != "html_or_resource_page_candidate"), reverse=True)

    selected: list[Candidate] = []
    seen_topic_geo: set[tuple[str, str]] = set()
    # Prefer diversity first.
    for c in downloadable:
        key = (c.geography_guess, c.topic_guess)
        if key in seen_topic_geo:
            continue
        selected.append(c)
        seen_topic_geo.add(key)
        if len(selected) >= max_downloads:
            break

    if len(selected) < max_downloads:
        selected_urls = {c.candidate_url for c in selected}
        for c in downloadable:
            if c.candidate_url in selected_urls:
                continue
            selected.append(c)
            selected_urls.add(c.candidate_url)
            if len(selected) >= max_downloads:
                break

    for c in selected:
        c.selected_for_download = 1
    return selected


def extension_from_url(url: str, fallback: str = ".xlsx") -> str:
    path = urlparse(url).path.lower()
    for ext in [".xlsx", ".xls", ".csv", ".zip"]:
        if ext in path:
            return ext
    return fallback


def download_candidates(
    candidates: list[Candidate],
    raw_dir: Path,
    force_download: bool,
    logger: logging.Logger,
) -> list[DownloadRecord]:
    records: list[DownloadRecord] = []

    for i, cand in enumerate(candidates, start=1):
        ext = extension_from_url(cand.candidate_url)
        file_name = f"{i:02d}_{cand.geography_guess.lower()}_{cand.topic_guess}_{slugify(cand.candidate_text or cand.candidate_url, 90)}{ext}"
        local_path = raw_dir / file_name

        if local_path.exists() and not force_download and local_path.stat().st_size > 0:
            logger.info("Using cached PHIDU file: %s", local_path)
            records.append(
                DownloadRecord(
                    candidate_url=cand.candidate_url,
                    candidate_text=cand.candidate_text,
                    geography_guess=cand.geography_guess,
                    topic_guess=cand.topic_guess,
                    local_path=str(local_path),
                    status="cached",
                    http_status_or_error="cached",
                    bytes_downloaded=local_path.stat().st_size,
                    sha256=sha256_file(local_path),
                    notes="",
                )
            )
            continue

        logger.info("Downloading PHIDU candidate %s/%s: %s", i, len(candidates), cand.candidate_url)
        try:
            data = safe_request(cand.candidate_url, timeout=180)
            if len(data) < 1000:
                raise ValueError(f"Downloaded file is too small: {len(data)} bytes")
            local_path.write_bytes(data)
            records.append(
                DownloadRecord(
                    candidate_url=cand.candidate_url,
                    candidate_text=cand.candidate_text,
                    geography_guess=cand.geography_guess,
                    topic_guess=cand.topic_guess,
                    local_path=str(local_path),
                    status="downloaded",
                    http_status_or_error="200",
                    bytes_downloaded=len(data),
                    sha256=sha256_file(local_path),
                    notes="",
                )
            )
            time.sleep(0.5)
        except Exception as exc:
            logger.warning("PHIDU candidate failed: %s :: %s", cand.candidate_url, exc)
            records.append(
                DownloadRecord(
                    candidate_url=cand.candidate_url,
                    candidate_text=cand.candidate_text,
                    geography_guess=cand.geography_guess,
                    topic_guess=cand.topic_guess,
                    local_path="",
                    status="failed",
                    http_status_or_error=f"{type(exc).__name__}: {exc}",
                    bytes_downloaded=0,
                    sha256="",
                    notes="download_failed",
                )
            )
    return records


def normalise_col(col: object) -> str:
    text = str(col or "")
    text = html.unescape(text)
    text = text.replace("\n", " ").replace("\r", " ")
    text = re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_").lower()
    return text or "unnamed"


def guess_header_row(sample: pd.DataFrame) -> int:
    # Prefer rows with many non-empty string-like cells and geography/code hints.
    best_row = 0
    best_score = -1
    for idx in range(min(len(sample), 20)):
        row = sample.iloc[idx]
        values = [str(v).strip() for v in row.tolist() if pd.notna(v) and str(v).strip()]
        joined = " ".join(values).lower()
        score = len(values)
        if any(tok in joined for tok in ["code", "area", "name", "lga", "phn", "pha", "sa2", "indicator"]):
            score += 10
        if len(values) >= 4 and score > best_score:
            best_score = score
            best_row = idx
    return int(best_row)


def detect_geo_cols(columns: list[str]) -> tuple[list[str], list[str], list[str]]:
    code_cols: list[str] = []
    name_cols: list[str] = []
    other_geo_cols: list[str] = []

    for col in columns:
        n = normalise_col(col)
        if re.search(r"(sa2|sa3|sa4|lga|phn|pha|iare|ra|remoteness).*code|code.*(sa2|sa3|sa4|lga|phn|pha|iare|ra)", n):
            code_cols.append(col)
        elif re.search(r"(sa2|sa3|sa4|lga|phn|pha|iare|area|region|remoteness).*name|name.*(sa2|sa3|sa4|lga|phn|pha|iare|area|region)", n):
            name_cols.append(col)
        elif any(tok in n for tok in ["sa2", "sa3", "sa4", "lga", "phn", "pha", "iare", "remoteness", "area_code", "area_name"]):
            other_geo_cols.append(col)

    return code_cols, name_cols, other_geo_cols


def infer_sheet_geography(file_geo_guess: str, sheet_name: str, columns: list[str]) -> str:
    joined = normalise_text(" ".join([file_geo_guess, sheet_name, " ".join(columns)]))
    if re.search(r"\bsa2\b|statistical area level 2", joined):
        return "SA2"
    if re.search(r"\bsa3\b|statistical area level 3", joined):
        return "SA3"
    if "local government" in joined or re.search(r"\blga\b", joined):
        return "LGA"
    if "primary health network" in joined or re.search(r"\bphn\b", joined):
        return "PHN"
    if "population health area" in joined or re.search(r"\bpha\b", joined):
        return "PHA"
    if "indigenous area" in joined or re.search(r"\biare\b", joined):
        return "IARE"
    if "remoteness" in joined:
        return "REMOTENESS"
    if "seifa" in joined or "socioeconomic disadvantage" in joined:
        return "SEIFA_QUINTILE"
    return file_geo_guess or "UNKNOWN"


def read_sheet_sample(path: Path, sheet_name: str, nrows: int, logger: logging.Logger) -> tuple[pd.DataFrame | None, str]:
    try:
        sample = pd.read_excel(path, sheet_name=sheet_name, header=None, nrows=max(25, min(nrows, 250)), dtype=object)
        return sample, ""
    except Exception as exc:
        logger.debug("Failed reading sample %s :: %s :: %s", path.name, sheet_name, exc)
        return None, f"{type(exc).__name__}: {exc}"




def make_unique_columns(raw_cols: list[str]) -> list[str]:
    """Return normalised, non-empty, unique column names.

    PHIDU workbooks sometimes contain repeated heading cells or blank columns. Pandas
    returns a DataFrame, not a Series, when selecting a duplicated column label. This
    helper prevents that by making names unique after normalisation, including cases
    where an auto-suffixed name would collide with a real existing name.
    """
    out: list[str] = []
    used: set[str] = set()
    counts: dict[str, int] = {}
    for i, c in enumerate(raw_cols):
        base = normalise_col(str(c))
        if not base or base in {"nan", "none", "unnamed"}:
            base = f"unnamed_{i+1}"
        counts[base] = counts.get(base, 0) + 1
        candidate = base if counts[base] == 1 else f"{base}_{counts[base]}"
        j = counts[base]
        while candidate in used:
            j += 1
            candidate = f"{base}_{j}"
        used.add(candidate)
        out.append(candidate)
    return out


def numeric_series_from_column(df: pd.DataFrame, col: str) -> pd.Series:
    """Safely coerce a column to numeric even if duplicate labels slipped through."""
    if col not in df.columns:
        return pd.Series(dtype=float)
    obj = df.loc[:, col]
    if isinstance(obj, pd.DataFrame):
        # Duplicate column labels can still happen in very odd workbooks. Use the first
        # non-empty duplicate column rather than crashing the whole inventory.
        if obj.shape[1] == 0:
            return pd.Series(dtype=float)
        best = None
        best_count = -1
        for c in obj.columns:
            ser = pd.to_numeric(obj[c], errors="coerce")
            count = int(ser.notna().sum())
            if count > best_count:
                best = ser
                best_count = count
        return best if best is not None else pd.Series(dtype=float)
    return pd.to_numeric(obj, errors="coerce")


def inspect_workbook(path: Path, download_record: DownloadRecord, row_sample: int, logger: logging.Logger) -> tuple[list[dict], list[dict], list[dict], list[dict]]:
    sheet_rows: list[dict] = []
    schema_rows: list[dict] = []
    indicator_rows: list[dict] = []
    readiness_rows: list[dict] = []

    suffix = path.suffix.lower()
    if suffix not in [".xlsx", ".xls"]:
        readiness_rows.append(
            {
                "local_path": str(path),
                "file_name": path.name,
                "geography_guess": download_record.geography_guess,
                "status": "not_workbook_review_manually",
                "join_readiness": "pending_manual_review",
                "notes": f"File extension {suffix}; workbook inventory skipped.",
            }
        )
        return sheet_rows, schema_rows, indicator_rows, readiness_rows

    try:
        xls = pd.ExcelFile(path)
    except Exception as exc:
        readiness_rows.append(
            {
                "local_path": str(path),
                "file_name": path.name,
                "geography_guess": download_record.geography_guess,
                "status": "workbook_open_failed",
                "join_readiness": "not_ready",
                "notes": f"{type(exc).__name__}: {exc}",
            }
        )
        return sheet_rows, schema_rows, indicator_rows, readiness_rows

    logger.info("Inspecting workbook: %s; sheets: %s", path.name, len(xls.sheet_names))

    workbook_geo_statuses: set[str] = set()
    workbook_join_notes: list[str] = []

    for sheet_name in xls.sheet_names:
        sample, error = read_sheet_sample(path, sheet_name, row_sample, logger)
        if sample is None:
            sheet_rows.append(
                {
                    "file_name": path.name,
                    "local_path": str(path),
                    "source_url": download_record.candidate_url,
                    "candidate_text": download_record.candidate_text,
                    "file_geography_guess": download_record.geography_guess,
                    "sheet_name": sheet_name,
                    "sheet_read_status": "failed",
                    "sample_rows": 0,
                    "sample_columns": 0,
                    "non_empty_sample_cells": 0,
                    "header_row_guess": "",
                    "likely_data_sheet": 0,
                    "error": error,
                }
            )
            continue

        non_empty = int(sample.notna().sum().sum())
        header_idx = guess_header_row(sample)
        likely_data_sheet = int(non_empty > 20 and sample.shape[1] >= 4)

        # Re-read with guessed header for column-level inventory.
        try:
            df_sample = pd.read_excel(path, sheet_name=sheet_name, header=header_idx, nrows=row_sample, dtype=object)
            df_sample = df_sample.dropna(axis=1, how="all")
            # Deduplicate / normalise empty column names while retaining source names.
            raw_cols = [str(c) for c in df_sample.columns]
            norm_cols = make_unique_columns(raw_cols)
            df_sample.columns = norm_cols
        except Exception as exc:
            df_sample = pd.DataFrame()
            raw_cols = []
            norm_cols = []
            error = f"{error}; header_read_failed {type(exc).__name__}: {exc}".strip("; ")

        code_cols, name_cols, other_geo_cols = detect_geo_cols(norm_cols)
        sheet_geo = infer_sheet_geography(download_record.geography_guess, sheet_name, norm_cols)
        workbook_geo_statuses.add(sheet_geo)

        numeric_count = 0
        numeric_cols: list[str] = []
        for col in norm_cols:
            numeric = numeric_series_from_column(df_sample, col)
            if len(numeric) and numeric.notna().mean() >= 0.5:
                numeric_count += 1
                numeric_cols.append(col)

        sheet_rows.append(
            {
                "file_name": path.name,
                "local_path": str(path),
                "source_url": download_record.candidate_url,
                "candidate_text": download_record.candidate_text,
                "file_geography_guess": download_record.geography_guess,
                "sheet_name": sheet_name,
                "sheet_read_status": "read",
                "sample_rows": int(sample.shape[0]),
                "sample_columns": int(sample.shape[1]),
                "non_empty_sample_cells": non_empty,
                "header_row_guess": header_idx,
                "likely_data_sheet": likely_data_sheet,
                "sheet_geography_guess": sheet_geo,
                "detected_code_columns": " | ".join(code_cols),
                "detected_name_columns": " | ".join(name_cols),
                "detected_other_geo_columns": " | ".join(other_geo_cols),
                "numeric_column_count_sample": numeric_count,
                "error": error,
            }
        )

        if not likely_data_sheet or not norm_cols:
            continue

        schema_rows.append(
            {
                "file_name": path.name,
                "sheet_name": sheet_name,
                "sheet_geography_guess": sheet_geo,
                "column_count_sample": len(norm_cols),
                "row_sample_count": len(df_sample),
                "detected_code_columns": " | ".join(code_cols),
                "detected_name_columns": " | ".join(name_cols),
                "detected_other_geo_columns": " | ".join(other_geo_cols),
                "numeric_column_count_sample": numeric_count,
                "all_columns_sample": " | ".join(norm_cols[:120]),
            }
        )

        # Indicator candidates: numeric non-geo columns.
        geo_col_set = set(code_cols + name_cols + other_geo_cols)
        for col in numeric_cols:
            if col in geo_col_set:
                continue
            numeric = numeric_series_from_column(df_sample, col)
            indicator_rows.append(
                {
                    "file_name": path.name,
                    "sheet_name": sheet_name,
                    "sheet_geography_guess": sheet_geo,
                    "indicator_column": col,
                    "indicator_topic_guess": classify_indicator_topic(col),
                    "sample_non_missing_count": int(numeric.notna().sum()),
                    "sample_min": float(numeric.min()) if numeric.notna().any() else None,
                    "sample_max": float(numeric.max()) if numeric.notna().any() else None,
                }
            )

        # Sheet-level join readiness.
        if sheet_geo == "SA2":
            readiness = "candidate_direct_sa2_join_after_schema_validation"
            notes = "Sheet appears to contain SA2 fields. Validate ASGS year, codes and indicator definitions before join."
        elif sheet_geo == "SA3":
            readiness = "candidate_sa3_context_join_after_schema_validation"
            notes = "Sheet appears to contain SA3 fields. Can be repeated across SA2 only after ASGS year and indicator period review."
        elif sheet_geo == "LGA":
            readiness = "candidate_lga_context_join_after_lga_code_validation"
            notes = "v08 has dominant LGA 2021 context, but LGA joins require year/code validation and area-share caveats."
        elif sheet_geo == "PHN":
            readiness = "candidate_phn_context_join_after_phn_boundary_validation"
            notes = "v08 has PHN 2017 context. PHIDU PHN boundary year must be reviewed before join."
        elif sheet_geo == "PHA":
            readiness = "pending_pha_to_sa2_bridge_or_hold_context_only"
            notes = "Population Health Area is not SA2. Do not join until a validated PHA-to-SA2/SA3 method exists."
        elif sheet_geo in {"REMOTENESS", "SEIFA_QUINTILE"}:
            readiness = "not_sa2_area_join_hold_for_context_or_domain_analysis"
            notes = "This is a stratification geography, not an SA2 area layer."
        elif sheet_geo == "IARE":
            readiness = "not_ready_requires_indigenous_area_bridge_and_first_nations_data_governance_review"
            notes = "Indigenous Area join requires specific bridge and First Nations data governance review."
        else:
            readiness = "pending_manual_schema_review"
            notes = "Geography unclear from sampled sheet."

        workbook_join_notes.append(f"{sheet_name}: {readiness}")
        readiness_rows.append(
            {
                "local_path": str(path),
                "file_name": path.name,
                "sheet_name": sheet_name,
                "sheet_geography_guess": sheet_geo,
                "status": "review",
                "join_readiness": readiness,
                "detected_code_columns": " | ".join(code_cols),
                "detected_name_columns": " | ".join(name_cols),
                "numeric_column_count_sample": numeric_count,
                "notes": notes,
            }
        )

    # Workbook-level summary.
    readiness_rows.append(
        {
            "local_path": str(path),
            "file_name": path.name,
            "sheet_name": "__workbook_summary__",
            "sheet_geography_guess": " | ".join(sorted(workbook_geo_statuses)),
            "status": "info",
            "join_readiness": summarise_workbook_readiness(workbook_geo_statuses),
            "detected_code_columns": "",
            "detected_name_columns": "",
            "numeric_column_count_sample": "",
            "notes": " ; ".join(workbook_join_notes[:20]),
        }
    )

    return sheet_rows, schema_rows, indicator_rows, readiness_rows


def classify_indicator_topic(col: str) -> str:
    n = normalise_col(col)
    if any(tok in n for tok in ["mental", "psychological", "distress", "suicide", "self_harm"]):
        return "mental_health"
    if any(tok in n for tok in ["disability", "carer", "assistance", "ndis"]):
        return "disability_carer"
    if any(tok in n for tok in ["gp", "medicare", "hospital", "ed_", "emergency", "service", "pbs", "prescription"]):
        return "health_service_use"
    if any(tok in n for tok in ["income", "unemployment", "jobless", "education", "occupation", "housing", "rent"]):
        return "social_determinants"
    if any(tok in n for tok in ["age", "population", "born", "language", "indigenous"]):
        return "demographic"
    if any(tok in n for tok in ["death", "mortality", "cancer", "diabetes", "respiratory", "circulatory"]):
        return "health_status"
    return "unknown"


def summarise_workbook_readiness(geo_statuses: set[str]) -> str:
    if "SA2" in geo_statuses:
        return "candidate_direct_sa2_join_after_schema_validation"
    if "SA3" in geo_statuses:
        return "candidate_sa3_context_join_after_schema_validation"
    if "LGA" in geo_statuses:
        return "candidate_lga_context_join_after_lga_code_validation"
    if "PHN" in geo_statuses:
        return "candidate_phn_context_join_after_phn_boundary_validation"
    if "PHA" in geo_statuses:
        return "pending_pha_to_sa2_bridge_or_context_only"
    if "IARE" in geo_statuses:
        return "requires_first_nations_data_governance_and_bridge_review"
    if "REMOTENESS" in geo_statuses or "SEIFA_QUINTILE" in geo_statuses:
        return "stratification_context_only"
    return "pending_manual_review"


def validate_base_master(path: Path, logger: logging.Logger) -> pd.DataFrame:
    logger.info("Reading base master: %s", path)
    if not path.exists():
        raise FileNotFoundError(f"Base master not found: {path}")
    df = pd.read_parquet(path) if path.suffix.lower() == ".parquet" else pd.read_csv(path, dtype=str, low_memory=False)
    logger.info("Base master rows: %s; columns: %s", len(df), len(df.columns))
    if "sa2_code_2021" not in df.columns:
        raise ValueError("Base master must contain sa2_code_2021")
    duplicate_count = int(df["sa2_code_2021"].duplicated().sum())
    if duplicate_count:
        raise ValueError(f"Base master contains duplicate sa2_code_2021 rows: {duplicate_count}")
    if len(df) != 2472:
        logger.warning("Base master row count is %s, expected 2472 for the current spine.", len(df))
    return df


def write_methodology_note(path: Path, log_path: Path, base_master: Path, candidates_count: int, downloads_count: int) -> None:
    text = f"""# PHIDU Social Health Atlas inventory note {SCRIPT_VERSION}

Generated: {datetime.now().isoformat(timespec="seconds")}

## Purpose

This step inventories PHIDU Social Health Atlas workbooks before any join to the SA2 master.

It does not join PHIDU indicators to the master. PHIDU data are published across multiple geographies, including Population Health Area, Local Government Area and Primary Health Network. These are not automatically equivalent to SA2.

## Base master

`{base_master}`

## Source pages

- `{PHIDU_DATA_URL}`
- `{PHIDU_CONTENTS_URL}`

## Outputs

- `outputs/audits/phidu_source_candidate_audit_v09.csv`
- `outputs/audits/phidu_download_audit_v09.csv`
- `outputs/audits/phidu_workbook_sheet_inventory_v09.csv`
- `outputs/audits/phidu_schema_audit_v09.csv`
- `outputs/audits/phidu_indicator_inventory_v09.csv`
- `outputs/audits/phidu_join_readiness_audit_v09.csv`
- `docs/source_registers/phidu_social_health_atlas_inventory_register_v09.csv`

## Summary

Candidate links discovered: {candidates_count}

Downloaded or cached files: {downloads_count}

## Interpretation rules

- Direct SA2 joins are acceptable only if SA2 codes and ASGS year are explicit.
- SA3 indicators may be joined as SA3 context only after year and definition review.
- LGA indicators may be joined only after LGA code/year validation and with dominant-LGA caveats.
- PHN indicators require PHN boundary-year validation. Current master contains PHN 2017 context.
- PHA indicators require a validated PHA-to-SA2/SA3 bridge or should be held as context-only.
- Indigenous Area data require separate bridge review and First Nations data governance review.

## Log

`{log_path}`
"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def main() -> None:
    args = parse_args()
    root = Path(args.project_root).resolve()
    dirs = ensure_dirs(root)
    logger, log_path = setup_logging(root, args.debug)

    logger.info("PHIDU Social Health Atlas inventory %s", SCRIPT_VERSION)
    logger.info("Project root: %s", root)
    logger.info("Log path: %s", log_path)

    base_master_path = Path(args.base_master)
    if not base_master_path.is_absolute():
        base_master_path = root / base_master_path

    base = validate_base_master(base_master_path, logger)

    source_pages = [PHIDU_DATA_URL, PHIDU_CONTENTS_URL]
    if args.include_archive:
        source_pages.append(PHIDU_ARCHIVE_URL)

    requested_geos = {g.strip().lower() for g in str(args.geographies).split(",") if g.strip()}
    logger.info("Requested geography priorities: %s", ", ".join(sorted(requested_geos)))

    candidates = discover_candidates(source_pages, requested_geos, logger)
    selected = choose_download_candidates(candidates, max_downloads=max(0, args.max_downloads))

    selected_urls = {c.candidate_url for c in selected}
    for c in candidates:
        c.selected_for_download = int(c.candidate_url in selected_urls)

    candidate_audit = [asdict(c) for c in sorted(candidates, key=lambda x: (x.selected_for_download, x.priority_score), reverse=True)]
    write_csv(dirs["audits"] / "phidu_source_candidate_audit_v09.csv", candidate_audit, logger)

    if args.skip_download:
        download_records: list[DownloadRecord] = []
        logger.info("--skip-download supplied. Candidate audit only.")
    else:
        download_records = download_candidates(selected, dirs["raw_phidu"], args.force_download, logger)

    download_audit = [asdict(d) for d in download_records]
    write_csv(dirs["audits"] / "phidu_download_audit_v09.csv", download_audit, logger)

    successful_downloads = [
        d for d in download_records
        if d.status in {"downloaded", "cached"} and d.local_path and Path(d.local_path).exists()
    ]

    sheet_rows: list[dict] = []
    schema_rows: list[dict] = []
    indicator_rows: list[dict] = []
    readiness_rows: list[dict] = []

    if successful_downloads:
        for record in successful_downloads:
            path = Path(record.local_path)
            s, sc, ind, ready = inspect_workbook(path, record, args.workbook_row_sample, logger)
            sheet_rows.extend(s)
            schema_rows.extend(sc)
            indicator_rows.extend(ind)
            readiness_rows.extend(ready)
    else:
        readiness_rows.append(
            {
                "local_path": "",
                "file_name": "",
                "sheet_name": "",
                "sheet_geography_guess": "",
                "status": "no_workbooks_downloaded",
                "join_readiness": "pending_download_or_manual_file_review",
                "detected_code_columns": "",
                "detected_name_columns": "",
                "numeric_column_count_sample": "",
                "notes": "No PHIDU workbooks were downloaded. Review candidate and download audits.",
            }
        )

    write_csv(dirs["audits"] / "phidu_workbook_sheet_inventory_v09.csv", sheet_rows, logger)
    write_csv(dirs["audits"] / "phidu_schema_audit_v09.csv", schema_rows, logger)
    write_csv(dirs["audits"] / "phidu_indicator_inventory_v09.csv", indicator_rows, logger)
    write_csv(dirs["audits"] / "phidu_join_readiness_audit_v09.csv", readiness_rows, logger)

    # Source register combines candidate and readiness summaries.
    readiness_df = pd.DataFrame(readiness_rows)
    register_rows = []
    if not readiness_df.empty:
        summary = (
            readiness_df
            .groupby(["file_name", "sheet_geography_guess", "join_readiness"], dropna=False)
            .size()
            .reset_index(name="sheet_count")
        )
        for row in summary.to_dict("records"):
            register_rows.append({
                "source_family": "phidu_social_health_atlas",
                "file_name": row.get("file_name", ""),
                "geography_guess": row.get("sheet_geography_guess", ""),
                "join_readiness": row.get("join_readiness", ""),
                "sheet_count": row.get("sheet_count", ""),
                "recommended_action": recommended_action(str(row.get("join_readiness", ""))),
            })
    else:
        register_rows.append({
            "source_family": "phidu_social_health_atlas",
            "file_name": "",
            "geography_guess": "",
            "join_readiness": "pending_manual_review",
            "sheet_count": "",
            "recommended_action": "Review candidate/download audits.",
        })

    write_csv(dirs["source_registers"] / "phidu_social_health_atlas_inventory_register_v09.csv", register_rows, logger)

    write_methodology_note(
        dirs["methodology"] / "phidu_social_health_atlas_inventory_note_v09.md",
        log_path,
        base_master_path,
        len(candidates),
        len(successful_downloads),
    )

    logger.info("PHIDU inventory completed.")
    logger.info("Candidates discovered: %s", len(candidates))
    logger.info("Workbook files downloaded/cached: %s", len(successful_downloads))
    logger.info("Next action: review phidu_join_readiness_audit_v09.csv before any PHIDU join.")


def recommended_action(readiness: str) -> str:
    if "direct_sa2" in readiness:
        return "Review ASGS year, indicator metadata and missingness; then consider direct SA2 join."
    if "sa3_context" in readiness:
        return "Review ASGS year and indicator definitions; consider SA3 context join repeated across SA2."
    if "lga_context" in readiness:
        return "Validate LGA code/year against dominant_lga_code_2021 before any join."
    if "phn_context" in readiness:
        return "Validate PHN boundary year against phn_2017_code or add PHN 2023 context before any join."
    if "pha" in readiness:
        return "Hold as context until PHA bridge is acquired/validated."
    if "first_nations" in readiness or "indigenous" in readiness:
        return "Do not join until bridge and First Nations data governance approach are reviewed."
    if "stratification" in readiness:
        return "Use as contextual stratification only, not direct SA2 predictor."
    return "Manual review required."


if __name__ == "__main__":
    _started = datetime.now()
    try:
        main()
    except Exception as exc:
        notify_script_completion(False, SCRIPT_NAME, _started, detail=str(exc))
        raise
    else:
        notify_script_completion(True, SCRIPT_NAME, _started, detail="Review phidu_join_readiness_audit_v09.csv.")
