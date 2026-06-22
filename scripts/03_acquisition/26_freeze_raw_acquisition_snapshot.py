#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
MentalWellbeingByGeography
Script 26: Freeze raw acquisition snapshot v15, root-safe active-raw version

Purpose
-------
Create a reproducible raw-acquisition freeze snapshot after raw folder cleanup,
path reconciliation and validation. This script does not move, delete, process,
transform, join or model data. It records active raw files, validation evidence,
source-family readiness, and the native-geography processing sequence.

Default project root
--------------------
D:\\Good Measure\\MentalWellbeingbyGeography

By default this freezes active raw files under data/raw and excludes archive-style
folders such as data/raw/_archive. Use --include-raw-archive only if you want
archived raw caches included in the checksum manifest.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import sys
from datetime import datetime
from pathlib import Path
from typing import Iterable

import pandas as pd

SCRIPT_VERSION = "v15"
DEFAULT_PROJECT_ROOT = Path(r"D:\Good Measure\MentalWellbeingbyGeography")

EXCLUDED_RAW_DIR_NAMES = {"_archive", "archive", "_quarantine", "quarantine", "tmp", "temp", "__pycache__"}

PROCESSING_SEQUENCE = [
    {
        "processing_order": 1,
        "source_family": "abs_homelessness_census",
        "native_geography_target": "SA2/SA3/SA4/state if exposed by workbook/tablebuilder; otherwise hold as higher-level context",
        "processing_action": "inspect_abs_homelessness_workbook_tables_then_extract_native_geography_tables",
        "why_next": "Census-derived homelessness aligns with the 2021 Census base and should be assessed before newer service-system sources.",
        "join_timing": "after native geography validation",
        "primary_model_status": "candidate predictor if SA2/SA3 geography is valid; otherwise contextual only",
        "caveat": "ABS homelessness estimates are derived from Census characteristics and assumptions, not direct observed homelessness status for every person.",
    },
    {
        "processing_order": 2,
        "source_family": "aedc_child_development",
        "native_geography_target": "SA2/SA3/SA4/LGA/state if public tables expose usable codes",
        "processing_action": "inspect_aedc_release_scope_and_extract_usable_geography_tables",
        "why_next": "Child development context is relevant to community need and life-course disadvantage, but geography and suppression need validation.",
        "join_timing": "after geography and suppression checks",
        "primary_model_status": "candidate contextual predictor, likely sensitivity/context layer unless geography is clean",
        "caveat": "AEDC public data may contain suppression, small-cell limits and mixed geography across files.",
    },
    {
        "processing_order": 3,
        "source_family": "aihw_mental_health_regional_activity",
        "native_geography_target": "PHN/SA4/SA3/state depending on ZIP member",
        "processing_action": "unzip_inventory_and_extract_phn_sa4_service_activity_tables",
        "why_next": "Mental health Medicare, prescriptions, ED, admitted patient and workforce files are directly relevant but mostly higher-level service-system context.",
        "join_timing": "after PHN/SA4/native-key validation",
        "primary_model_status": "context/sensitivity only unless matched at SA3 or validated lower geography",
        "caveat": "Do not treat repeated PHN or SA4 values as direct SA2 measurements; use grouped CV and leakage checks.",
    },
    {
        "processing_order": 4,
        "source_family": "phidu_official_lga_phn_context",
        "native_geography_target": "LGA and PHN",
        "processing_action": "retain_processed_phidu_lga_phn_context_tables_and_prepare_for_scoped_lga_phn_masters",
        "why_next": "PHIDU has already been extracted into native LGA and PHN context tables; include it in scoped masters, not the SA2 master.",
        "join_timing": "scoped-master phase",
        "primary_model_status": "higher-level contextual predictors only",
        "caveat": "PHIDU LGA/PHN values repeat across SA2s and require grouped validation.",
    },
    {
        "processing_order": 5,
        "source_family": "aihw_mbs_primary_care_geography",
        "native_geography_target": "SA3/PHN/state if report tables expose usable downloads",
        "processing_action": "inspect_mbs_local_area_report_and_identify_downloadable_tables",
        "why_next": "Primary-care access and service-use are relevant, but currently staged mainly as report/page/PDF rather than clean tables.",
        "join_timing": "after table extraction or hold as report context",
        "primary_model_status": "context only until table data and geography are validated",
        "caveat": "PDF/report data may not be reproducible enough for automated joins unless source tables are located.",
    },
    {
        "processing_order": 6,
        "source_family": "aihw_specialist_homelessness_services",
        "native_geography_target": "state or service-region only unless usable lower geography is exposed",
        "processing_action": "inspect_public_tables_for_geography_or_hold_report_context",
        "why_next": "SHS data is important but likely not granular enough for SA2/SA3 modelling from public annual-report files.",
        "join_timing": "hold unless geography is confirmed",
        "primary_model_status": "report/context only unless lower geography appears",
        "caveat": "SHS records only the service-presenting population and does not measure total homelessness need.",
    },
    {
        "processing_order": 7,
        "source_family": "ndis_service_area_candidate",
        "native_geography_target": "NDIS service district/service area only if a true key is found",
        "processing_action": "hold_until_true_ndis_service_area_key_identified",
        "why_next": "NDIS public participant context is already staged separately; service-area variables should not be invented from participant tables.",
        "join_timing": "hold",
        "primary_model_status": "hold/sensitivity only",
        "caveat": "Do not label NDIA participant geography as service-area geography without a validated service-area key.",
    },
    {
        "processing_order": 8,
        "source_family": "state_health_geography_inventory",
        "native_geography_target": "state-specific LHD/HHS context",
        "processing_action": "hold_state_specific_boundary_context_only",
        "why_next": "State health geographies are useful context but not nationally consistent without a separate harmonisation layer.",
        "join_timing": "hold unless state-specific analysis is required",
        "primary_model_status": "not for national primary model",
        "caveat": "NSW LHD and Queensland HHS are not nationally harmonised predictors.",
    },
]


def log(message: str) -> None:
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [INFO] {message}")


def resolve_project_root(project_root_arg: str | None) -> Path:
    if project_root_arg:
        return Path(project_root_arg).expanduser().resolve()

    cwd = Path.cwd().resolve()
    candidates = [cwd] + list(cwd.parents)
    for candidate in candidates:
        if (candidate / "data" / "raw").exists() and (candidate / "scripts").exists():
            return candidate
    if DEFAULT_PROJECT_ROOT.exists():
        return DEFAULT_PROJECT_ROOT
    return cwd


def ensure_dirs(root: Path) -> None:
    for rel in ["outputs/audits", "outputs/logs", "docs/source_registers", "docs/methodology"]:
        (root / rel).mkdir(parents=True, exist_ok=True)


def read_csv_if_exists(path: Path) -> pd.DataFrame:
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame()
    try:
        return pd.read_csv(path)
    except Exception as exc:
        log(f"Could not read CSV {path}: {exc!r}")
        return pd.DataFrame()


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(chunk_size), b""):
            h.update(chunk)
    return h.hexdigest()


def safe_rel(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def is_excluded_raw_path(path: Path, raw_root: Path, include_raw_archive: bool) -> bool:
    if include_raw_archive:
        return False
    try:
        rel = path.relative_to(raw_root)
    except ValueError:
        return False
    return any(part.lower() in EXCLUDED_RAW_DIR_NAMES for part in rel.parts[:-1])


def raw_top_level(path: Path, raw_root: Path) -> str:
    try:
        rel = path.relative_to(raw_root)
        return rel.parts[0] if rel.parts else "unknown"
    except ValueError:
        return "unknown"


def infer_raw_source_family(path: Path, raw_root: Path) -> str:
    try:
        rel = path.relative_to(raw_root)
        parts = [p.lower() for p in rel.parts]
    except ValueError:
        return "unknown"

    joined = "/".join(parts)
    name = path.name.lower()
    if parts and parts[0] == "abs":
        if "homeless" in joined:
            return "abs_homelessness_census"
        if "nsmhw" in joined or "mental_disorder" in name or "modelled" in name:
            return "abs_nsmhw_sa2_modelled_estimates"
        if "seifa" in joined:
            return "abs_seifa_2021"
        if "geography" in joined or "sa2_2021" in name or "lga_2021" in name or "ra_2021" in name:
            return "abs_geography_2021"
        if "census" in joined or "gcp" in joined:
            return "abs_census_2021_sa2"
        return "abs_other"
    if parts and parts[0] == "aedc":
        return "aedc_child_development"
    if parts and parts[0] == "aihw":
        if "regional_activity" in joined or "regional_activity_data" in joined:
            return "aihw_mental_health_regional_activity"
        if "mbs" in joined or "primary_care" in joined:
            return "aihw_mbs_primary_care_geography"
        if "specialist_homelessness" in joined or "shs" in joined:
            return "aihw_specialist_homelessness_services"
        if "mental_health_data_tables" in joined:
            return "aihw_mental_health_data_tables"
        if "regional_profiles" in joined:
            return "aihw_regional_profiles_sa3"
        return "aihw_other"
    if parts and parts[0] in {"ndia", "ndis", "ndis_service_area"}:
        return "ndis_service_area_candidate"
    if parts and parts[0] == "phidu":
        return "phidu_raw"
    if parts and parts[0] == "state_health_geography":
        return "state_health_geography_inventory"
    return parts[0] if parts else "unknown"


def iter_raw_files(root: Path, include_raw_archive: bool) -> Iterable[Path]:
    raw_root = root / "data" / "raw"
    if not raw_root.exists():
        return []
    files = []
    for path in raw_root.rglob("*"):
        if path.is_file() and not is_excluded_raw_path(path, raw_root, include_raw_archive):
            files.append(path)
    return sorted(files, key=lambda p: str(p).lower())


def build_raw_file_manifest(root: Path, include_raw_archive: bool, debug: bool = False) -> pd.DataFrame:
    raw_root = root / "data" / "raw"
    rows: list[dict] = []
    files = list(iter_raw_files(root, include_raw_archive))
    for i, path in enumerate(files, start=1):
        try:
            stat = path.stat()
            digest = sha256_file(path)
            status = "ok"
            error = ""
        except Exception as exc:  # defensive for local file system quirks
            stat = None
            digest = ""
            status = "failed"
            error = repr(exc)
        rows.append(
            {
                "freeze_version": SCRIPT_VERSION,
                "freeze_timestamp": datetime.now().isoformat(timespec="seconds"),
                "raw_top_level_dir": raw_top_level(path, raw_root),
                "inferred_source_family": infer_raw_source_family(path, raw_root),
                "file_name": path.name,
                "file_extension": path.suffix.lower(),
                "file_size_bytes": stat.st_size if stat else None,
                "file_size_mb": round((stat.st_size if stat else 0) / 1024 / 1024, 6),
                "sha256": digest,
                "raw_file_path": str(path),
                "raw_file_relative_path": safe_rel(path, root),
                "file_status": status,
                "error": error,
            }
        )
        if debug and (i == 1 or i % 25 == 0 or i == len(files)):
            log(f"  hashed {i}/{len(files)} active raw files")
    return pd.DataFrame(rows)


def build_archive_exclusion_summary(root: Path) -> pd.DataFrame:
    raw_root = root / "data" / "raw"
    rows: list[dict] = []
    if not raw_root.exists():
        return pd.DataFrame(rows)
    for folder in sorted([p for p in raw_root.rglob("*") if p.is_dir()]):
        try:
            rel = folder.relative_to(raw_root)
        except ValueError:
            continue
        if any(part.lower() in EXCLUDED_RAW_DIR_NAMES for part in rel.parts):
            files = [p for p in folder.rglob("*") if p.is_file()]
            size = sum(p.stat().st_size for p in files if p.exists())
            rows.append(
                {
                    "excluded_folder": str(folder),
                    "excluded_folder_relative": safe_rel(folder, root),
                    "file_count": len(files),
                    "size_mb": round(size / 1024 / 1024, 6),
                    "reason": "Excluded from active raw freeze manifest by default.",
                }
            )
    return pd.DataFrame(rows)


def build_source_family_rollup(root: Path, manifest: pd.DataFrame) -> pd.DataFrame:
    validation_summary = read_csv_if_exists(root / "outputs" / "audits" / "remaining_raw_source_validation_summary_v14.csv")
    acquisition = read_csv_if_exists(root / "outputs" / "audits" / "remaining_raw_source_acquisition_register_v13.csv")
    candidate_links = read_csv_if_exists(root / "outputs" / "audits" / "remaining_raw_source_candidate_link_audit_v13.csv")
    phidu_ready = read_csv_if_exists(root / "outputs" / "audits" / "phidu_official_lga_phn_join_readiness_v12.csv")

    families: set[str] = set()
    if not manifest.empty and "inferred_source_family" in manifest.columns:
        families.update(manifest["inferred_source_family"].dropna().astype(str).unique().tolist())
    for df in [validation_summary, acquisition, candidate_links]:
        if not df.empty and "source_family" in df.columns:
            families.update(df["source_family"].dropna().astype(str).unique().tolist())
    families.add("phidu_official_lga_phn_context")

    rows: list[dict] = []
    for family in sorted(families):
        man_rows = manifest[manifest["inferred_source_family"].astype(str).eq(family)] if not manifest.empty else pd.DataFrame()
        val_rows = validation_summary[validation_summary["source_family"].astype(str).eq(family)] if not validation_summary.empty and "source_family" in validation_summary.columns else pd.DataFrame()
        acq_rows = acquisition[acquisition["source_family"].astype(str).eq(family)] if not acquisition.empty and "source_family" in acquisition.columns else pd.DataFrame()
        link_rows = candidate_links[candidate_links["source_family"].astype(str).eq(family)] if not candidate_links.empty and "source_family" in candidate_links.columns else pd.DataFrame()

        validation_status = str(val_rows.iloc[0].get("validation_status", "")) if not val_rows.empty else ""
        detected_geographies = str(val_rows.iloc[0].get("detected_geographies", "")) if not val_rows.empty else ""
        next_actions = str(val_rows.iloc[0].get("recommended_next_actions", "")) if not val_rows.empty else ""

        if family == "phidu_official_lga_phn_context":
            freeze_status = "ready_for_scoped_lga_phn_context_masters_after_manual_review"
            detected_geographies = "LGA;PHN"
            validation_status = "pass_context_tables_created" if not phidu_ready.empty else "review_missing_v12_readiness_audit"
            next_actions = "retain PHIDU native LGA and PHN context tables; do not join directly to SA2"
        elif validation_status and "pass" in validation_status.lower():
            freeze_status = "ready_for_native_geography_processing_or_context_hold"
        elif len(man_rows) > 0:
            freeze_status = "active_raw_files_present_not_in_v14_summary"
        else:
            freeze_status = "review_no_active_raw_manifest_files"

        rows.append(
            {
                "freeze_version": SCRIPT_VERSION,
                "source_family": family,
                "active_raw_file_count": int(len(man_rows)),
                "active_raw_file_extensions": ";".join(sorted(man_rows["file_extension"].dropna().astype(str).unique())) if not man_rows.empty else "",
                "active_raw_size_mb": round(float(man_rows["file_size_mb"].fillna(0).sum()), 6) if not man_rows.empty else 0.0,
                "v13_register_rows": int(len(acq_rows)),
                "v13_candidate_link_rows": int(len(link_rows)),
                "v13_candidate_downloads": int((link_rows.get("download_status", pd.Series(dtype=str)).astype(str).str.lower() == "downloaded").sum()) if not link_rows.empty else 0,
                "v14_validation_status": validation_status,
                "detected_geographies": detected_geographies,
                "freeze_status": freeze_status,
                "recommended_next_action": next_actions,
            }
        )
    return pd.DataFrame(rows)


def write_csv(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False, quoting=csv.QUOTE_MINIMAL)


def write_methodology_note(root: Path, manifest: pd.DataFrame, rollup: pd.DataFrame, archive_summary: pd.DataFrame) -> None:
    path = root / "docs" / "methodology" / f"raw_acquisition_freeze_note_{SCRIPT_VERSION}.md"
    total_files = len(manifest)
    total_mb = float(manifest["file_size_mb"].fillna(0).sum()) if not manifest.empty else 0.0
    failed_files = int((manifest.get("file_status", pd.Series(dtype=str)).astype(str) != "ok").sum()) if not manifest.empty else 0
    ready_families = int(rollup["freeze_status"].astype(str).str.contains("ready|active_raw_files_present", case=False, na=False).sum()) if not rollup.empty else 0
    archive_files = int(archive_summary["file_count"].fillna(0).sum()) if not archive_summary.empty and "file_count" in archive_summary.columns else 0
    archive_mb = float(archive_summary["size_mb"].fillna(0).sum()) if not archive_summary.empty and "size_mb" in archive_summary.columns else 0.0

    lines = [
        f"# Raw acquisition freeze note {SCRIPT_VERSION}",
        "",
        f"Generated: {datetime.now().isoformat(timespec='seconds')}",
        "",
        "## Purpose",
        "",
        "This freeze records the cleaned active raw/source acquisition state for MentalWellbeingByGeography before native-geography processing and scoped master construction. It is a provenance checkpoint, not an analytical dataset.",
        "",
        "## Freeze summary",
        "",
        f"- Active raw files hashed: {total_files}",
        f"- Active raw size MB: {total_mb:.6f}",
        f"- Raw file hash/read failures: {failed_files}",
        f"- Source families ready or active: {ready_families}",
        f"- Archived raw files excluded from active manifest: {archive_files}",
        f"- Archived raw size MB excluded from active manifest: {archive_mb:.6f}",
        "",
        "## Processing principle",
        "",
        "Raw data remains in its native geography. SA2, SA3, LGA, PHN and NDIS/service-area variables should be processed into separate native-geography source tables. SA2 modelling data should then be assembled through explicit foreign keys, not by permanently widening the SA2 master with every higher-level source.",
        "",
        "## Current source-family treatment",
        "",
        "- ABS homelessness: process next, with attention to whether usable SA2/SA3 tables are available.",
        "- AEDC: inspect geography and suppression/release scope before use.",
        "- AIHW mental health regional activity: unzip and process PHN/SA4/SA3/state tables separately.",
        "- PHIDU: retain as LGA and PHN context only. Do not treat as SA2 measurement.",
        "- AIHW MBS primary-care geography: inspect report/source tables before extraction.",
        "- AIHW SHS: hold as report/context unless usable lower geography is found.",
        "- NDIS service-area candidate: hold until a true service-area key is identified.",
        "- State health geography: hold as state-specific context only.",
        "",
        "## Key outputs",
        "",
        f"- outputs/audits/raw_acquisition_freeze_manifest_{SCRIPT_VERSION}.csv",
        f"- outputs/audits/raw_acquisition_source_family_rollup_{SCRIPT_VERSION}.csv",
        f"- outputs/audits/raw_acquisition_processing_sequence_{SCRIPT_VERSION}.csv",
        f"- outputs/audits/raw_acquisition_archive_exclusion_summary_{SCRIPT_VERSION}.csv",
        f"- docs/source_registers/raw_acquisition_freeze_manifest_{SCRIPT_VERSION}.csv",
        f"- docs/source_registers/raw_acquisition_processing_sequence_{SCRIPT_VERSION}.csv",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Freeze raw acquisition snapshot for MentalWellbeingByGeography.")
    parser.add_argument("--project-root", default=None, help="Project root path. Defaults to current project root if detected.")
    parser.add_argument("--include-raw-archive", action="store_true", help="Include data/raw/_archive and similar archive folders in the checksum manifest.")
    parser.add_argument("--debug", action="store_true", help="Print progress detail.")
    args = parser.parse_args()

    root = resolve_project_root(args.project_root)
    ensure_dirs(root)

    log(f"Raw acquisition freeze snapshot {SCRIPT_VERSION}")
    log(f"Project root: {root}")
    log(f"Include raw archive folders: {args.include_raw_archive}")

    required_inputs = [
        root / "outputs" / "audits" / "remaining_raw_source_acquisition_register_v13.csv",
        root / "outputs" / "audits" / "remaining_raw_source_candidate_link_audit_v13.csv",
        root / "outputs" / "audits" / "remaining_raw_source_validation_summary_v14.csv",
        root / "outputs" / "audits" / "remaining_raw_source_file_inventory_v14.csv",
        root / "outputs" / "audits" / "remaining_raw_zip_member_inventory_v14.csv",
    ]
    optional_inputs = [
        root / "outputs" / "audits" / "phidu_official_lga_phn_join_readiness_v12.csv",
        root / "outputs" / "audits" / "phidu_official_lga_phn_indicator_inventory_v12.csv",
        root / "outputs" / "audits" / "phidu_official_lga_phn_key_validation_v12.csv",
        root / "outputs" / "audits" / "pre_freeze_archive_candidate_summary_v19_20260622_125146.csv",
    ]

    input_rows: list[dict] = []
    missing_required: list[str] = []
    for p in required_inputs:
        exists = p.exists() and p.stat().st_size > 0
        input_rows.append({"path": str(p), "relative_path": safe_rel(p, root), "input_type": "required", "exists_non_empty": int(exists)})
        if not exists:
            missing_required.append(str(p))
    for p in optional_inputs:
        exists = p.exists() and p.stat().st_size > 0
        input_rows.append({"path": str(p), "relative_path": safe_rel(p, root), "input_type": "optional", "exists_non_empty": int(exists)})

    if missing_required:
        log("Missing required inputs:")
        for p in missing_required:
            log(f"  {p}")
        return 2

    log("Building active raw file manifest and checksums")
    manifest = build_raw_file_manifest(root, include_raw_archive=args.include_raw_archive, debug=args.debug)
    log(f"Active raw files hashed: {len(manifest)}")

    archive_summary = build_archive_exclusion_summary(root)
    rollup = build_source_family_rollup(root, manifest)
    sequence = pd.DataFrame(PROCESSING_SEQUENCE)

    manifest_failed = int((manifest.get("file_status", pd.Series(dtype=str)).astype(str) != "ok").sum()) if not manifest.empty else 0
    run_audit = pd.DataFrame(
        input_rows
        + [
            {"path": "active_raw_file_manifest_rows", "relative_path": "", "input_type": "output_metric", "exists_non_empty": int(len(manifest))},
            {"path": "active_raw_manifest_failed_file_count", "relative_path": "", "input_type": "output_metric", "exists_non_empty": manifest_failed},
            {"path": "source_family_rollup_rows", "relative_path": "", "input_type": "output_metric", "exists_non_empty": int(len(rollup))},
            {"path": "processing_sequence_rows", "relative_path": "", "input_type": "output_metric", "exists_non_empty": int(len(sequence))},
            {"path": "archive_exclusion_summary_rows", "relative_path": "", "input_type": "output_metric", "exists_non_empty": int(len(archive_summary))},
        ]
    )

    outputs = {
        "manifest_audit": root / "outputs" / "audits" / f"raw_acquisition_freeze_manifest_{SCRIPT_VERSION}.csv",
        "rollup_audit": root / "outputs" / "audits" / f"raw_acquisition_source_family_rollup_{SCRIPT_VERSION}.csv",
        "sequence_audit": root / "outputs" / "audits" / f"raw_acquisition_processing_sequence_{SCRIPT_VERSION}.csv",
        "archive_summary": root / "outputs" / "audits" / f"raw_acquisition_archive_exclusion_summary_{SCRIPT_VERSION}.csv",
        "run_audit": root / "outputs" / "audits" / f"raw_acquisition_freeze_run_audit_{SCRIPT_VERSION}.csv",
        "manifest_register": root / "docs" / "source_registers" / f"raw_acquisition_freeze_manifest_{SCRIPT_VERSION}.csv",
        "sequence_register": root / "docs" / "source_registers" / f"raw_acquisition_processing_sequence_{SCRIPT_VERSION}.csv",
    }

    log("Writing freeze outputs")
    write_csv(manifest, outputs["manifest_audit"])
    write_csv(rollup, outputs["rollup_audit"])
    write_csv(sequence, outputs["sequence_audit"])
    write_csv(archive_summary, outputs["archive_summary"])
    write_csv(run_audit, outputs["run_audit"])
    write_csv(manifest, outputs["manifest_register"])
    write_csv(sequence, outputs["sequence_register"])
    write_methodology_note(root, manifest, rollup, archive_summary)

    log("Raw acquisition freeze complete")
    log("Recommended next action: process ABS homelessness native geography first, then AEDC, then AIHW mental-health ZIPs.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
