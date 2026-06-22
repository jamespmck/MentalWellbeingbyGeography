#!/usr/bin/env python3
"""
24_acquire_remaining_raw_source_register.py

Purpose
-------
Acquire or catalogue the remaining raw/source data candidates for the
MentalWellbeingByGeography project without joining anything into the analytical master.

This script is intentionally conservative. It:
  * downloads known direct files where stable URLs are available;
  * caches source pages as HTML where the data are behind interactive tools or data tabs;
  * discovers downloadable links from cached pages;
  * writes a raw source acquisition register and candidate-link audit;
  * does not reshape, join, or scope features for modelling.

Run from PowerShell:
    cd "D:\\Good Measure\\MentalWellbeingbyGeography"
    python "D:\\Good Measure\\MentalWellbeingbyGeography\\scripts\\03_acquisition\\24_acquire_remaining_raw_source_register.py" --debug

Optional:
    --force-download       Re-download files/pages even if cached.
    --download-discovered  Download discovered .xlsx/.csv/.zip/.pdf links from cached pages.
    --max-discovered-downloads 30
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import html
import os
import re
import sys
import time
import traceback
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

try:
    import pandas as pd
except Exception:  # pragma: no cover
    pd = None


SCRIPT_VERSION = "v13"
DEFAULT_PROJECT_ROOT = Path(r"D:\Good Measure\MentalWellbeingbyGeography")

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0 Safari/537.36 MentalWellbeingByGeographySourceAudit/1.0"
)

DOWNLOAD_EXTENSIONS = (".xlsx", ".xls", ".csv", ".zip", ".pdf", ".json")


@dataclass
class SourceSpec:
    source_family: str
    publisher: str
    source_name: str
    url: str
    acquisition_mode: str  # direct_download | page_cache | page_discovery
    native_geography_expected: str
    reference_period_expected: str
    recommended_scope: str
    access_notes: str
    priority: str
    raw_subdir: str
    target_filename: str = ""


@dataclass
class AcquisitionRecord:
    run_timestamp: str
    source_family: str
    publisher: str
    source_name: str
    url: str
    acquisition_mode: str
    native_geography_expected: str
    reference_period_expected: str
    recommended_scope: str
    priority: str
    download_status: str
    http_status: str
    content_type: str
    bytes_written: int
    sha256: str
    raw_file_path: str
    discovered_link_count: int
    licence_or_access_notes: str
    join_status: str
    notes: str


@dataclass
class CandidateLinkRecord:
    run_timestamp: str
    source_family: str
    publisher: str
    source_name: str
    page_url: str
    discovered_url: str
    link_text: str
    extension: str
    candidate_rank: int
    download_attempted: int
    download_status: str
    raw_file_path: str
    notes: str


class Logger:
    def __init__(self, log_path: Path, debug: bool = False) -> None:
        self.log_path = log_path
        self.debug_enabled = debug
        self.log_path.parent.mkdir(parents=True, exist_ok=True)

    def _write(self, level: str, message: str) -> None:
        line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [{level}] {message}"
        print(line)
        with self.log_path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")

    def info(self, message: str) -> None:
        self._write("INFO", message)

    def warning(self, message: str) -> None:
        self._write("WARNING", message)

    def debug(self, message: str) -> None:
        if self.debug_enabled:
            self._write("DEBUG", message)

    def error(self, message: str) -> None:
        self._write("ERROR", message)


def slugify(value: str, max_len: int = 140) -> str:
    value = html.unescape(str(value)).strip().lower()
    value = re.sub(r"[^a-z0-9]+", "_", value)
    value = re.sub(r"_+", "_", value).strip("_")
    if not value:
        value = "source"
    return value[:max_len].strip("_")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def ensure_dirs(project_root: Path) -> dict[str, Path]:
    dirs = {
        "raw": project_root / "data" / "raw",
        "processed_sources": project_root / "data" / "processed" / "sources",
        "audits": project_root / "outputs" / "audits",
        "logs": project_root / "outputs" / "logs",
        "source_registers": project_root / "docs" / "source_registers",
        "methodology": project_root / "docs" / "methodology",
    }
    for p in dirs.values():
        p.mkdir(parents=True, exist_ok=True)
    return dirs


def build_sources() -> list[SourceSpec]:
    """Curated remaining raw-source acquisition list.

    These are source candidates not yet fully integrated into the modelling source stack.
    PHIDU has been acquired separately in script 22, so it is not repeated here.
    """
    return [
        SourceSpec(
            source_family="aedc_child_development",
            publisher="Australian Early Development Census",
            source_name="AEDC Community Profiles page",
            url="https://www.aedc.gov.au/data-hub/public-data/community-profiles",
            acquisition_mode="page_discovery",
            native_geography_expected="AEDC community / local community / state / national; validate before join",
            reference_period_expected="2024 and prior AEDC cycles depending on downloadable file",
            recommended_scope="raw_inventory_then_geography_validation",
            access_notes="Interactive/community profile downloads may require manual or discovered download handling.",
            priority="high",
            raw_subdir="aedc",
            target_filename="aedc_community_profiles_page.html",
        ),
        SourceSpec(
            source_family="aedc_child_development",
            publisher="Australian Early Development Census",
            source_name="AEDC accessing data page",
            url="https://www.aedc.gov.au/data-hub/accessing-aedc-data",
            acquisition_mode="page_discovery",
            native_geography_expected="AEDC community / local community / state / national; validate before join",
            reference_period_expected="2024 and prior AEDC cycles depending on downloadable file",
            recommended_scope="raw_inventory_then_geography_validation",
            access_notes="Community Data Explorer can expose community profiles and local community time-series tables.",
            priority="high",
            raw_subdir="aedc",
            target_filename="aedc_accessing_data_page.html",
        ),
        SourceSpec(
            source_family="aedc_child_development",
            publisher="Australian Early Development Census",
            source_name="AEDC 2024 results page",
            url="https://www.aedc.gov.au/data-hub/public-data/2024-aedc-results",
            acquisition_mode="page_discovery",
            native_geography_expected="national / state / community depending on file",
            reference_period_expected="2024",
            recommended_scope="raw_inventory_then_geography_validation",
            access_notes="National report/fact sheet likely PDF; data table access may be via Community Data Explorer.",
            priority="high",
            raw_subdir="aedc",
            target_filename="aedc_2024_results_page.html",
        ),
        SourceSpec(
            source_family="abs_homelessness_census",
            publisher="Australian Bureau of Statistics",
            source_name="Estimating Homelessness Census latest release page",
            url="https://www.abs.gov.au/statistics/people/housing/estimating-homelessness-census/latest-release",
            acquisition_mode="page_discovery",
            native_geography_expected="varies by data cube; validate geography before join",
            reference_period_expected="2021",
            recommended_scope="raw_inventory_then_geography_validation",
            access_notes="Derived Census homelessness estimates; not directly comparable with normal QuickStats variables.",
            priority="high",
            raw_subdir="abs_homelessness",
            target_filename="abs_estimating_homelessness_latest_release.html",
        ),
        SourceSpec(
            source_family="abs_homelessness_census",
            publisher="Australian Bureau of Statistics",
            source_name="Estimating Homelessness 2021 data cube 005",
            url="https://www.abs.gov.au/statistics/people/housing/estimating-homelessness-census/2021/20490do005_2021.xlsx",
            acquisition_mode="direct_download",
            native_geography_expected="validate workbook tables; likely homelessness/marginal housing breakdowns",
            reference_period_expected="2021 Census",
            recommended_scope="raw_download_then_schema_geography_validation",
            access_notes="ABS xlsx data cube from Estimating Homelessness: Census 2021.",
            priority="high",
            raw_subdir="abs_homelessness",
            target_filename="abs_estimating_homelessness_2021_data_cube_005.xlsx",
        ),
        SourceSpec(
            source_family="abs_homelessness_census",
            publisher="Australian Bureau of Statistics",
            source_name="Estimating Homelessness 2021 data cube 006",
            url="https://www.abs.gov.au/statistics/people/housing/estimating-homelessness-census/2021/20490do006_2021.xlsx",
            acquisition_mode="direct_download",
            native_geography_expected="validate workbook tables; likely homelessness operational groups by selected geographies",
            reference_period_expected="2021 Census",
            recommended_scope="raw_download_then_schema_geography_validation",
            access_notes="ABS xlsx data cube from Estimating Homelessness: Census 2021.",
            priority="high",
            raw_subdir="abs_homelessness",
            target_filename="abs_estimating_homelessness_2021_data_cube_006.xlsx",
        ),
        SourceSpec(
            source_family="abs_homelessness_census_tablebuilder",
            publisher="Australian Bureau of Statistics",
            source_name="ABS TableBuilder Estimating Homelessness page",
            url="https://www.abs.gov.au/statistics/microdata-tablebuilder/available-microdata-tablebuilder/census-population-and-housing-estimating-homelessness",
            acquisition_mode="page_cache",
            native_geography_expected="TableBuilder custom extract; restricted/manual",
            reference_period_expected="2021",
            recommended_scope="manual_access_note_only",
            access_notes="TableBuilder access may be required for detailed custom geography. Do not assume accessible as direct file.",
            priority="medium",
            raw_subdir="abs_homelessness",
            target_filename="abs_tablebuilder_estimating_homelessness_page.html",
        ),
        SourceSpec(
            source_family="aihw_specialist_homelessness_services",
            publisher="Australian Institute of Health and Welfare",
            source_name="SHS annual report 2024-25 data page",
            url="https://www.aihw.gov.au/reports/homelessness-services/specialist-homelessness-services-annual-report/data",
            acquisition_mode="page_discovery",
            native_geography_expected="likely national/state/territory and selected breakdowns; validate before join",
            reference_period_expected="2024-25",
            recommended_scope="raw_inventory_then_geography_validation",
            access_notes="SHS describes service users, not all people experiencing homelessness. Geography may be too coarse for SA2 modelling.",
            priority="medium",
            raw_subdir="aihw_shs",
            target_filename="aihw_shs_annual_report_2024_25_data_page.html",
        ),
        SourceSpec(
            source_family="aihw_mental_health_regional_activity",
            publisher="Australian Institute of Health and Welfare",
            source_name="Geospatial mental health services activity data",
            url="https://www.aihw.gov.au/nmhspf/support-material/regional-activity-data",
            acquisition_mode="page_discovery",
            native_geography_expected="PHN / SA3 / other geospatial tables depending on topic",
            reference_period_expected="latest available; validate file metadata",
            recommended_scope="raw_inventory_then_native_geography_table",
            access_notes="Likely overlaps with existing AIHW Regional Profiles work, but may expose direct downloadable geospatial activity tables.",
            priority="high",
            raw_subdir="aihw_mental_health_regional_activity",
            target_filename="aihw_regional_activity_data_page.html",
        ),
        SourceSpec(
            source_family="aihw_mental_health_data_tables",
            publisher="Australian Institute of Health and Welfare",
            source_name="AIHW mental health data tables page",
            url="https://www.aihw.gov.au/mental-health/resources/data-tables",
            acquisition_mode="page_discovery",
            native_geography_expected="varies by table; validate before join",
            reference_period_expected="latest available",
            recommended_scope="raw_inventory_then_relevance_filter",
            access_notes="JavaScript-heavy page may not expose all links through static HTML.",
            priority="medium",
            raw_subdir="aihw_mental_health_data_tables",
            target_filename="aihw_mental_health_data_tables_page.html",
        ),
        SourceSpec(
            source_family="aihw_mbs_primary_care_geography",
            publisher="Australian Institute of Health and Welfare",
            source_name="Medicare-subsidised GP allied health and specialist services geography page",
            url="https://www.aihw.gov.au/reports/primary-health-care/medicare-subsidised-gp-allied-health-specialist/contents/summary/variation-by-geography",
            acquisition_mode="page_discovery",
            native_geography_expected="PHN / SA3 based on report data tab; validate downloadable tables",
            reference_period_expected="2017-18 to 2024-25 according to report page; validate extracted files",
            recommended_scope="raw_inventory_then_native_geography_table",
            access_notes="Useful for general service access context; distinguish from mental-health-specific MBS tables.",
            priority="medium",
            raw_subdir="aihw_mbs_primary_care_geography",
            target_filename="aihw_mbs_gp_allied_specialist_geography_page.html",
        ),
        SourceSpec(
            source_family="ndis_service_area_candidate",
            publisher="National Disability Insurance Agency",
            source_name="NDIS Data Research Explore Data page",
            url="https://dataresearch.ndis.gov.au/explore-data",
            acquisition_mode="page_discovery",
            native_geography_expected="service district / LGA / PHN / state or other NDIA public geographies; validate before use",
            reference_period_expected="latest and historical public releases",
            recommended_scope="raw_inventory_then_service_area_key_validation",
            access_notes="Do not treat as NDIS service-area data until a true service-area geography/key is identified.",
            priority="high",
            raw_subdir="ndis_service_area",
            target_filename="ndis_explore_data_page.html",
        ),
        SourceSpec(
            source_family="state_health_geography_inventory",
            publisher="NSW Health",
            source_name="NSW Local Health Districts page",
            url="https://www.health.nsw.gov.au/lhd/Pages/default.aspx",
            acquisition_mode="page_discovery",
            native_geography_expected="LHD; NSW only; bridge required before SA2 use",
            reference_period_expected="current web page",
            recommended_scope="raw_inventory_only_state_specific",
            access_notes="State-specific source. Use only for state health geography inventory unless a national-equivalent boundary source is identified.",
            priority="low",
            raw_subdir="state_health_geography",
            target_filename="nsw_health_lhd_page.html",
        ),
        SourceSpec(
            source_family="state_health_geography_inventory",
            publisher="Queensland Health",
            source_name="Queensland Hospital and Health Services page",
            url="https://www.health.qld.gov.au/system-governance/health-system/hhs",
            acquisition_mode="page_discovery",
            native_geography_expected="HHS; Queensland only; bridge required before SA2 use",
            reference_period_expected="current web page",
            recommended_scope="raw_inventory_only_state_specific",
            access_notes="State-specific source. Use only for state health geography inventory unless a national-equivalent boundary source is identified.",
            priority="low",
            raw_subdir="state_health_geography",
            target_filename="qld_health_hhs_page.html",
        ),
    ]


def request_url(url: str, timeout: int = 60) -> tuple[bytes, str, str]:
    req = Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml,application/pdf,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,*/*;q=0.8",
            "Accept-Language": "en-AU,en;q=0.9",
        },
    )
    with urlopen(req, timeout=timeout) as resp:
        data = resp.read()
        status = str(getattr(resp, "status", ""))
        content_type = resp.headers.get("Content-Type", "")
    return data, status, content_type


def write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as f:
        f.write(data)


def target_path_for_source(project_root: Path, spec: SourceSpec) -> Path:
    raw_dir = project_root / "data" / "raw" / spec.raw_subdir
    filename = spec.target_filename or f"{slugify(spec.source_name)}{guess_extension_from_url(spec.url) or '.html'}"
    return raw_dir / filename


def guess_extension_from_url(url: str) -> str:
    path = urlparse(url).path.lower()
    for ext in DOWNLOAD_EXTENSIONS + (".html",):
        if path.endswith(ext):
            return ext
    return ""


def is_probably_html(path: Path, content_type: str) -> bool:
    return path.suffix.lower() in (".html", ".htm") or "text/html" in content_type.lower()


def fetch_to_file(url: str, target_path: Path, force: bool, logger: Logger) -> tuple[str, str, str, int, str, str]:
    """Return download_status, http_status, content_type, bytes_written, sha256, notes."""
    if target_path.exists() and target_path.stat().st_size > 0 and not force:
        return (
            "cached_existing",
            "cached",
            "",
            int(target_path.stat().st_size),
            sha256_file(target_path),
            "Existing cached file reused. Use --force-download to refresh.",
        )

    try:
        logger.debug(f"Fetching {url}")
        data, http_status, content_type = request_url(url)
        write_bytes(target_path, data)
        return (
            "downloaded",
            http_status,
            content_type,
            len(data),
            sha256_file(target_path),
            "",
        )
    except HTTPError as e:
        return "failed", str(e.code), "", 0, "", f"HTTPError: {e}"
    except URLError as e:
        return "failed", "", "", 0, "", f"URLError: {e}"
    except Exception as e:
        return "failed", "", "", 0, "", f"{type(e).__name__}: {e}"


def extract_links_from_html(html_text: str, base_url: str) -> list[tuple[str, str]]:
    """Return list of (absolute_url, link_text_or_title)."""
    records: list[tuple[str, str]] = []

    # Basic anchor extraction.
    anchor_re = re.compile(
        r"<a\b[^>]*?href=[\"'](?P<href>[^\"']+)[\"'][^>]*>(?P<text>.*?)</a>",
        flags=re.IGNORECASE | re.DOTALL,
    )
    for m in anchor_re.finditer(html_text):
        href = html.unescape(m.group("href")).strip()
        text = re.sub(r"<[^>]+>", " ", m.group("text"))
        text = re.sub(r"\s+", " ", html.unescape(text)).strip()
        if not href or href.startswith("mailto:") or href.startswith("javascript:"):
            continue
        records.append((urljoin(base_url, href), text))

    # Also catch plain URLs embedded in scripts/static config.
    url_re = re.compile(r"https?://[^\s\"'<>]+", flags=re.IGNORECASE)
    for m in url_re.finditer(html_text):
        url = html.unescape(m.group(0)).rstrip("),.;]")
        records.append((url, "embedded_url"))

    # Deduplicate while preserving order.
    seen = set()
    deduped: list[tuple[str, str]] = []
    for url, text in records:
        key = url.split("#", 1)[0]
        if key not in seen:
            seen.add(key)
            deduped.append((url, text))
    return deduped


def candidate_extension(url: str) -> str:
    path = urlparse(url).path.lower()
    for ext in DOWNLOAD_EXTENSIONS:
        if path.endswith(ext):
            return ext
    # CKAN/resource download URLs can omit extension.
    if "/download" in path or "download" in path:
        return "download_endpoint"
    return ""


def rank_candidate(url: str, text: str) -> int:
    combined = f"{url} {text}".lower()
    score = 0
    for term, weight in [
        ("xlsx", 100),
        ("csv", 95),
        ("zip", 80),
        ("download", 60),
        ("data", 50),
        ("table", 45),
        ("community", 35),
        ("sa3", 35),
        ("phn", 30),
        ("lga", 30),
        ("homeless", 30),
        ("mental", 25),
        ("pdf", 5),
    ]:
        if term in combined:
            score += weight
    return score


def safe_csv_write(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if pd is not None:
        pd.DataFrame(rows).to_csv(path, index=False)
        return
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def write_methodology_note(path: Path, run_timestamp: str) -> None:
    text = f"""# Remaining raw source acquisition register {SCRIPT_VERSION}

Run timestamp: {run_timestamp}

This note documents the conservative raw acquisition pass for the remaining MentalWellbeingByGeography source candidates. The script downloads stable direct files where available, caches source pages, and discovers candidate downloadable files from those pages. It does not join, reshape or model any source.

## Scope

The acquisition pass focuses on source families still requiring raw/source staging after the existing SA2/SA3 foundation layers and PHIDU LGA/PHN context extraction:

- AEDC child development data
- ABS Estimating Homelessness: Census data cubes and access pages
- AIHW Specialist Homelessness Services annual report data page
- AIHW mental health regional activity and data tables
- AIHW Medicare-subsidised primary care/service-use geography pages
- NDIS service-area candidate discovery
- selected state health geography inventory pages

## Interpretation

A successful download or cached page is not a validated modelling source. Every file still needs native-geography validation, reference-period validation, denominator review and source-specific limitations before inclusion in a scoped master table.

## Modelling rule

Do not join higher-level geographies into the SA2 modelling table until the scoped master architecture is built. Native LGA, PHN, SA3 and service-area tables should stay separate and connect through a foreign-key master during feature-matrix assembly.
"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Acquire remaining raw/source candidates and build a raw acquisition register.")
    parser.add_argument("--project-root", type=str, default=str(DEFAULT_PROJECT_ROOT))
    parser.add_argument("--force-download", action="store_true")
    parser.add_argument("--download-discovered", action="store_true")
    parser.add_argument("--max-discovered-downloads", type=int, default=30)
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    project_root = Path(args.project_root)
    dirs = ensure_dirs(project_root)
    run_timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    logger = Logger(dirs["logs"] / f"24_acquire_remaining_raw_source_register_{run_id}.log", debug=args.debug)

    logger.info(f"Remaining raw source acquisition register {SCRIPT_VERSION}")
    logger.info(f"Project root: {project_root}")
    logger.info(f"Force download: {args.force_download}")
    logger.info(f"Download discovered links: {args.download_discovered}")

    sources = build_sources()
    acquisition_records: list[AcquisitionRecord] = []
    candidate_link_records: list[CandidateLinkRecord] = []

    discovered_downloads_attempted = 0

    for idx, spec in enumerate(sources, start=1):
        logger.info(f"[{idx}/{len(sources)}] {spec.source_family}: {spec.source_name}")
        target_path = target_path_for_source(project_root, spec)
        status, http_status, content_type, bytes_written, digest, notes = fetch_to_file(
            spec.url, target_path, args.force_download, logger
        )

        discovered: list[tuple[str, str]] = []
        if status in ("downloaded", "cached_existing") and target_path.exists() and target_path.stat().st_size > 0:
            if target_path.suffix.lower() in (".html", ".htm") or spec.acquisition_mode in ("page_cache", "page_discovery"):
                try:
                    text = target_path.read_text(encoding="utf-8", errors="replace")
                    discovered = extract_links_from_html(text, spec.url)
                    logger.info(f"  discovered links: {len(discovered)}")
                except Exception as e:
                    notes = (notes + " | " if notes else "") + f"Could not parse page links: {type(e).__name__}: {e}"

        ranked_links = []
        for url, link_text in discovered:
            ext = candidate_extension(url)
            if not ext:
                continue
            ranked_links.append((rank_candidate(url, link_text), url, link_text, ext))
        ranked_links.sort(reverse=True, key=lambda x: x[0])

        for rank_index, (score, url, link_text, ext) in enumerate(ranked_links, start=1):
            attempted = 0
            dl_status = "not_attempted"
            raw_file_path = ""
            dl_notes = f"candidate_score={score}"

            if args.download_discovered and discovered_downloads_attempted < args.max_discovered_downloads:
                attempted = 1
                discovered_downloads_attempted += 1
                parsed = urlparse(url)
                ext_for_file = guess_extension_from_url(url)
                if not ext_for_file or ext_for_file == "download_endpoint":
                    ext_for_file = ".download"
                filename = f"{slugify(spec.source_family)}__{rank_index:03d}__{slugify(link_text or Path(parsed.path).name or 'download')}{ext_for_file}"
                dest = project_root / "data" / "raw" / spec.raw_subdir / "discovered_downloads" / filename
                ds, hs, ct, bw, sh, nt = fetch_to_file(url, dest, args.force_download, logger)
                dl_status = ds
                raw_file_path = str(dest) if dest.exists() else ""
                dl_notes = f"candidate_score={score}; http_status={hs}; content_type={ct}; bytes={bw}; notes={nt}"

            candidate_link_records.append(
                CandidateLinkRecord(
                    run_timestamp=run_timestamp,
                    source_family=spec.source_family,
                    publisher=spec.publisher,
                    source_name=spec.source_name,
                    page_url=spec.url,
                    discovered_url=url,
                    link_text=link_text,
                    extension=ext,
                    candidate_rank=rank_index,
                    download_attempted=attempted,
                    download_status=dl_status,
                    raw_file_path=raw_file_path,
                    notes=dl_notes,
                )
            )

        join_status = "not_joined_raw_acquisition_only"
        if status == "failed":
            join_status = "not_joined_source_access_failed_or_manual_required"
        elif spec.recommended_scope == "manual_access_note_only":
            join_status = "not_joined_manual_access_required"

        acquisition_records.append(
            AcquisitionRecord(
                run_timestamp=run_timestamp,
                source_family=spec.source_family,
                publisher=spec.publisher,
                source_name=spec.source_name,
                url=spec.url,
                acquisition_mode=spec.acquisition_mode,
                native_geography_expected=spec.native_geography_expected,
                reference_period_expected=spec.reference_period_expected,
                recommended_scope=spec.recommended_scope,
                priority=spec.priority,
                download_status=status,
                http_status=http_status,
                content_type=content_type,
                bytes_written=bytes_written,
                sha256=digest,
                raw_file_path=str(target_path) if target_path.exists() else "",
                discovered_link_count=len(ranked_links),
                licence_or_access_notes=spec.access_notes,
                join_status=join_status,
                notes=notes,
            )
        )

    # Write primary outputs.
    acquisition_csv = dirs["audits"] / f"remaining_raw_source_acquisition_register_{SCRIPT_VERSION}.csv"
    candidate_csv = dirs["audits"] / f"remaining_raw_source_candidate_link_audit_{SCRIPT_VERSION}.csv"
    register_csv = dirs["source_registers"] / f"remaining_raw_source_acquisition_register_{SCRIPT_VERSION}.csv"
    run_audit_csv = dirs["audits"] / f"remaining_raw_source_run_audit_{SCRIPT_VERSION}.csv"
    methodology_path = dirs["methodology"] / f"remaining_raw_source_acquisition_note_{SCRIPT_VERSION}.md"

    safe_csv_write(acquisition_csv, [asdict(r) for r in acquisition_records])
    safe_csv_write(candidate_csv, [asdict(r) for r in candidate_link_records])
    safe_csv_write(register_csv, [asdict(r) for r in acquisition_records])

    # Simple run audit summary.
    total = len(acquisition_records)
    downloaded = sum(1 for r in acquisition_records if r.download_status == "downloaded")
    cached = sum(1 for r in acquisition_records if r.download_status == "cached_existing")
    failed = sum(1 for r in acquisition_records if r.download_status == "failed")
    discovered_count = len(candidate_link_records)
    attempted_discovered = sum(r.download_attempted for r in candidate_link_records)

    run_audit = [
        {"check_name": "source_specs_total", "value": total, "status": "info", "notes": "Curated remaining source candidates."},
        {"check_name": "downloaded_sources", "value": downloaded, "status": "info", "notes": "Direct/page source files downloaded this run."},
        {"check_name": "cached_existing_sources", "value": cached, "status": "info", "notes": "Existing cached files reused."},
        {"check_name": "failed_sources", "value": failed, "status": "review" if failed else "pass", "notes": "Failed sources may require manual download or URL revision."},
        {"check_name": "candidate_download_links_discovered", "value": discovered_count, "status": "info", "notes": "Download-like links discovered from cached pages."},
        {"check_name": "candidate_downloads_attempted", "value": attempted_discovered, "status": "info", "notes": "Only attempted if --download-discovered was supplied."},
        {"check_name": "raw_join_status", "value": "not_joined", "status": "pass", "notes": "Script performs raw acquisition/register only."},
    ]
    safe_csv_write(run_audit_csv, run_audit)
    write_methodology_note(methodology_path, run_timestamp)

    logger.info(f"Writing CSV: {acquisition_csv}")
    logger.info(f"Writing CSV: {candidate_csv}")
    logger.info(f"Writing CSV: {register_csv}")
    logger.info(f"Writing CSV: {run_audit_csv}")
    logger.info(f"Writing methodology note: {methodology_path}")
    logger.info("Remaining raw source acquisition complete.")
    logger.info("Summary:")
    logger.info(f"  source specs: {total}")
    logger.info(f"  downloaded: {downloaded}")
    logger.info(f"  cached existing: {cached}")
    logger.info(f"  failed: {failed}")
    logger.info(f"  candidate download links discovered: {discovered_count}")
    logger.info("Next action: inspect remaining_raw_source_acquisition_register_v13.csv and candidate link audit before any processing script.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        raise
    except Exception as e:
        print(f"Fatal error: {type(e).__name__}: {e}", file=sys.stderr)
        traceback.print_exc()
        raise
