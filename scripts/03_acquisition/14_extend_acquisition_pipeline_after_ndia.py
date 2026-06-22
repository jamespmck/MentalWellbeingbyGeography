"""
MentalWellbeingByGeography
Script 14: Extended acquisition controller after NDIA public POC join

Purpose
-------
This script does three things safely:

1. Validates that the NDIA public POC context join has already been created.
2. Attempts only low-risk geography bridge joins that can be validated locally:
   - SA2 -> PHN, if a PHN/SA2 correspondence file is present
   - SA2 -> LGA, if an LGA/SA2 correspondence file is present
3. Creates an acquisition backlog and link-discovery audit for remaining source families:
   - LHD / local health district / state health district boundaries
   - DSS social-security data
   - housing stress / homelessness data
   - PHIDU Social Health Atlas indicators
   - AEDC child development indicators
   - carer / disability / psychosocial demand sources beyond public NDIA participant counts

Important
---------
This is not a brute-force joiner. It deliberately refuses to join sources unless:
- a stable geography key is present,
- the join geography is SA2/SA3 or has a validated bridge to SA2/SA3,
- row counts and duplicate checks pass,
- the source layer can be labelled as primary, sensitivity, context-only or pending.

The current project position is:
- v02 = primary 2021-aligned master with AIHW SA3
- v03 = sensitivity/POC master with NDIA public participant context
- this script starts from v03 and only creates v04 if PHN/LGA bridges are validated

Run from PowerShell:
    cd "D:\\Good Measure\\MentalWellbeingbyGeography"
    python "D:\\Good Measure\\MentalWellbeingbyGeography\\scripts\\03_acquisition\\14_extend_acquisition_pipeline_after_ndia.py"

Optional:
    python ... --no-web-discovery
    python ... --do-not-write-v04
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import shutil
import sys
import time
import urllib.parse
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

import pandas as pd

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None

PROJECT_ROOT = Path(r"D:\Good Measure\MentalWellbeingbyGeography")

MASTER_V02 = PROJECT_ROOT / "data" / "processed" / "integrated" / "sa2_predictor_universe_v02_with_aihw_sa3.parquet"
MASTER_V03 = PROJECT_ROOT / "data" / "processed" / "integrated" / "sa2_predictor_universe_v03_with_ndia_public_poc_context.parquet"
MASTER_V04_CSV = PROJECT_ROOT / "data" / "processed" / "integrated" / "sa2_predictor_universe_v04_with_ndia_phn_lga_context.csv"
MASTER_V04_PARQUET = PROJECT_ROOT / "data" / "processed" / "integrated" / "sa2_predictor_universe_v04_with_ndia_phn_lga_context.parquet"

RAW_DIR = PROJECT_ROOT / "data" / "raw"
RAW_EXTENDED_DIR = RAW_DIR / "extended_sources"
PROCESSED_GEO_DIR = PROJECT_ROOT / "data" / "processed" / "geography"
PROCESSED_SOURCE_DIR = PROJECT_ROOT / "data" / "processed" / "sources"
AUDIT_DIR = PROJECT_ROOT / "outputs" / "audits"
REGISTER_DIR = PROJECT_ROOT / "docs" / "source_registers"
METHODOLOGY_DIR = PROJECT_ROOT / "docs" / "methodology"
DICT_DIR = PROJECT_ROOT / "docs" / "data_dictionaries"

for d in [RAW_EXTENDED_DIR, PROCESSED_GEO_DIR, PROCESSED_SOURCE_DIR, AUDIT_DIR, REGISTER_DIR, METHODOLOGY_DIR, DICT_DIR]:
    d.mkdir(parents=True, exist_ok=True)

# These pages are deliberately official or source-owner pages. The script uses them only for discovery/link audit.
SOURCE_FAMILIES = [
    {
        "source_family": "phn_bridge_to_sa2",
        "status_target": "pending_bridge_or_join_if_local_file_exists",
        "priority": "high",
        "owner": "Australian Government Department of Health, Disability and Ageing",
        "official_pages": [
            "https://www.health.gov.au/resources/collections/primary-health-networks-phns-collection-of-concordance-files",
            "https://digital.atlas.gov.au/datasets/primary-health-networks",
        ],
        "target_geography": "SA2_2021 to PHN",
        "integration_rule": "Join to SA2 only if an official PHN-to-2021-ABS concordance is present. Prefer 2017 PHN boundaries if aligning strictly to 2021 Census context; keep 2023 PHN as current-service context if used.",
        "model_role": "context_geography_or_grouping; not a raw socioeconomic predictor",
    },
    {
        "source_family": "lga_bridge_to_sa2",
        "status_target": "pending_bridge_or_join_if_local_file_exists",
        "priority": "high",
        "owner": "Australian Bureau of Statistics",
        "official_pages": [
            "https://www.abs.gov.au/statistics/standards/australian-statistical-geography-standard-asgs-edition-3/jul2021-jun2026/access-and-downloads/correspondences",
        ],
        "target_geography": "SA2_2021 to LGA_2021/2022/2023",
        "integration_rule": "Join to SA2 using official ABS correspondence only. Where an SA2 intersects multiple LGAs, retain largest allocation and audit multi-LGA SA2s.",
        "model_role": "context_geography_or_grouping; use cautiously because LGA is political/administrative geography",
    },
    {
        "source_family": "lhd_state_health_districts",
        "status_target": "pending_acquisition_state_specific",
        "priority": "medium",
        "owner": "State and territory health departments",
        "official_pages": [
            "https://www.health.nsw.gov.au/lhd/Pages/default.aspx",
            "https://www.aihw.gov.au/about-our-data/aihw-data-by-geography",
        ],
        "target_geography": "state health district / local hospital network / local health district to SA2",
        "integration_rule": "Do not join until state-specific boundaries or correspondences are validated. Names and boundary systems differ by jurisdiction.",
        "model_role": "service-system context only; may be grouped validation or descriptive layer",
    },
    {
        "source_family": "dss_social_security_sa2",
        "status_target": "pending_acquisition_high_priority",
        "priority": "high",
        "owner": "Department of Social Services / data.gov.au",
        "official_pages": [
            "https://data.gov.au/data/dataset/dss-payment-demographic-data",
            "https://researchdata.edu.au/dss-payments-statistical-area-2/2976013",
            "https://researchdata.edu.au/dss-benefit-payment-quarterly-data/2975938",
        ],
        "target_geography": "SA2, LGA, postcode, electorate depending resource",
        "integration_rule": "Prioritise direct SA2 2021 resources. Select a reference period close to 2021-12-31 or 2022-06-30. Avoid postcode/electorate unless bridged.",
        "model_role": "candidate socioeconomic/service-demand predictors; needs payment-type scoping to avoid huge sparse feature set",
    },
    {
        "source_family": "housing_homelessness",
        "status_target": "pending_acquisition_review_geography",
        "priority": "high",
        "owner": "ABS / AIHW / Housing Data Australia",
        "official_pages": [
            "https://www.abs.gov.au/statistics/people/housing/estimating-homelessness-census/latest-release",
            "https://www.aihw.gov.au/reports/homelessness-services/specialist-homelessness-services-annual-report/data",
            "https://www.housingdata.gov.au/dashboard/meovkmx92o8jo45",
        ],
        "target_geography": "SA2/SA3 where available; otherwise LGA/PHN/state context only",
        "integration_rule": "Use direct SA2/SA3 Census homelessness or housing-stress data if downloadable. AIHW SHS is usually service-system data and may be state/jurisdiction or service-region level.",
        "model_role": "candidate predictor domain: housing stress, homelessness, service demand; strong conceptual relevance",
    },
    {
        "source_family": "phidu_social_health_atlas",
        "status_target": "pending_acquisition_bridge_required",
        "priority": "medium",
        "owner": "PHIDU, Torrens University Australia",
        "official_pages": [
            "https://phidu.torrens.edu.au/social-health-atlases/data",
            "https://phidu.torrens.edu.au/social-health-atlases/indicators-and-notes-on-the-data/social-health-atlases-of-australia-contents",
            "https://phidu.torrens.edu.au/social-health-atlases/data-archive/data-archive-social-health-atlases-of-australia",
        ],
        "target_geography": "PHA, LGA, PHN, Indigenous Area; usually not direct SA2",
        "integration_rule": "Do not join to SA2 until geography is confirmed. LGA/PHN workbooks can be held as context if LGA/PHN bridge is validated.",
        "model_role": "rich health/social indicator source; high value but bridge-dependent",
    },
    {
        "source_family": "aedc_child_development",
        "status_target": "pending_acquisition_bridge_required",
        "priority": "medium",
        "owner": "Australian Early Development Census / Australian Government Department of Education",
        "official_pages": [
            "https://www.aedc.gov.au/data-hub/accessing-aedc-data",
            "https://www.aedc.gov.au/data-hub/public-data/additional-data",
            "https://www.aedc.gov.au/data-hub/public-data/2024-aedc-results",
            "https://www.education.gov.au/early-childhood/about/data-and-reports/australian-early-development-census",
        ],
        "target_geography": "AEDC community, national, state/territory and other public table geographies",
        "integration_rule": "Audit geography first. AEDC community boundaries are not the same as SA2. Do not join until a correspondence or stable aggregation rule exists.",
        "model_role": "child/family context optional predictor domain; likely sensitivity/context unless geography maps cleanly",
    },
    {
        "source_family": "carer_disability_psychosocial_support_demand",
        "status_target": "pending_acquisition_review",
        "priority": "medium",
        "owner": "NDIA / DSS / AIHW / state departments / public data portals",
        "official_pages": [
            "https://dataresearch.ndis.gov.au/datasets/participant-datasets",
            "https://dataresearch.ndis.gov.au/reports-and-analyses/participant-dashboards/psychosocial",
            "https://www.aihw.gov.au/about-our-data/aihw-data-by-geography",
            "https://data.gov.au/data/dataset/dss-payment-demographic-data",
        ],
        "target_geography": "SA2/SA3 preferred; PHN/LGA/service district only after bridge",
        "integration_rule": "Public NDIA participant counts are already integrated as POC. Further psychosocial, carer and disability-demand layers need direct SA2/SA3 or validated bridge.",
        "model_role": "service-demand and equity context; avoid overclaiming because service use does not equal underlying need",
    },
]

DOWNLOAD_EXT_RE = re.compile(r"\.(csv|xlsx|xls|zip|json|geojson|shp|pdf)(\?|$)", re.I)
KEYWORD_RE = re.compile(
    r"(sa2|sa3|statistical area|phn|primary health network|lga|local government|lhd|local health|hospital network|dss|payment|homeless|housing|phidu|social health atlas|aedc|community data|psychosocial|carer|disability)",
    re.I,
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def norm_code(value) -> str | None:
    if pd.isna(value):
        return None
    text = str(value).strip()
    if not text or text.lower() in {"nan", "none", "null"}:
        return None
    text = re.sub(r"\.0$", "", text)
    return text


def safe_slug(value: str, max_len: int = 100) -> str:
    text = str(value).lower().strip()
    text = re.sub(r"[^a-z0-9]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    return (text or "blank")[:max_len]


def read_any_table(path: Path) -> pd.DataFrame | dict[str, pd.DataFrame]:
    suffix = path.suffix.lower()
    if suffix == ".parquet":
        return pd.read_parquet(path)
    if suffix == ".csv":
        return pd.read_csv(path, dtype=str, low_memory=False)
    if suffix in {".xlsx", ".xls"}:
        return pd.read_excel(path, sheet_name=None, dtype=str)
    raise ValueError(f"Unsupported table format: {path}")


def write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        pd.DataFrame().to_csv(path, index=False, encoding="utf-8-sig")
        return
    pd.DataFrame(rows).to_csv(path, index=False, encoding="utf-8-sig")


def validate_master() -> tuple[pd.DataFrame, list[dict]]:
    audit = []
    if not MASTER_V03.exists():
        raise FileNotFoundError(
            f"Expected NDIA POC context master not found: {MASTER_V03}\n"
            "Run script 12_process_and_join_ndia_public_poc_participants.py first."
        )
    master = pd.read_parquet(MASTER_V03)
    row_count = len(master)
    col_count = len(master.columns)
    duplicate_sa2 = int(master.duplicated(subset=["sa2_code_2021"]).sum()) if "sa2_code_2021" in master.columns else None
    ndia_cols = [c for c in master.columns if c.startswith("ndia_poc_")]

    audit.extend([
        {"check_name": "master_v03_exists", "value": str(MASTER_V03), "status": "pass", "notes": ""},
        {"check_name": "master_v03_row_count", "value": row_count, "status": "pass" if row_count == 2472 else "review", "notes": "Expected current SA2 spine row count."},
        {"check_name": "master_v03_column_count", "value": col_count, "status": "info", "notes": ""},
        {"check_name": "duplicate_sa2_rows", "value": duplicate_sa2, "status": "pass" if duplicate_sa2 == 0 else "fail", "notes": ""},
        {"check_name": "ndia_poc_column_count", "value": len(ndia_cols), "status": "pass" if ndia_cols else "review", "notes": "NDIA POC context columns should be present from previous processor."},
    ])
    return master, audit


def candidate_table_files() -> list[Path]:
    candidates = []
    for root in [RAW_DIR, PROCESSED_GEO_DIR, PROCESSED_SOURCE_DIR, PROJECT_ROOT / "oldproj_inspect_csv"]:
        if not root.exists():
            continue
        for pattern in ["*.csv", "*.xlsx", "*.xls", "*.parquet"]:
            candidates.extend(root.rglob(pattern))
    # Keep deterministic order and remove duplicates.
    seen = set()
    out = []
    for p in sorted(candidates):
        rp = str(p.resolve()).lower()
        if rp in seen:
            continue
        seen.add(rp)
        out.append(p)
    return out


def classify_bridge_file(path: Path) -> set[str]:
    name = path.name.lower()
    tags = set()
    if "phn" in name or "primary_health" in name or "primary health" in name:
        tags.add("phn")
    if "lga" in name or "local_government" in name or "local government" in name:
        tags.add("lga")
    if "sa2" in name:
        tags.add("sa2")
    return tags


def detect_column(columns: Iterable[str], include_terms: list[str], exclude_terms: Optional[list[str]] = None) -> str | None:
    exclude_terms = exclude_terms or []
    cols = list(columns)
    for col in cols:
        c = col.lower().strip()
        if all(term.lower() in c for term in include_terms) and not any(term.lower() in c for term in exclude_terms):
            return col
    return None


def detect_ratio_column(columns: Iterable[str]) -> str | None:
    cols = list(columns)
    priority = ["ratio_from_to", "ratio", "percentage", "percent", "population", "pop"]
    for key in priority:
        for col in cols:
            if key in col.lower():
                return col
    return None


def clean_ratio(series: pd.Series) -> pd.Series:
    text = series.astype(str).str.replace("%", "", regex=False).str.replace(",", "", regex=False).str.strip()
    out = pd.to_numeric(text, errors="coerce")
    # If values look like percentages, convert to proportions.
    if out.dropna().max() is not None and out.dropna().max() > 1.5:
        out = out / 100.0
    return out


def try_build_bridge_from_df(df: pd.DataFrame, family: str, source_path: Path, sheet_name: str = "") -> tuple[pd.DataFrame | None, dict]:
    # Flatten column names for detection but keep original names.
    columns = list(df.columns)
    lower_cols = {c: c.lower().strip() for c in columns}

    sa2_code_col = None
    for col in columns:
        c = lower_cols[col]
        if ("sa2" in c and ("code" in c or "maincode" in c or "main_code" in c)) or c in {"sa2_code_2021", "sa2_maincode_2021", "sa2_code"}:
            sa2_code_col = col
            break

    if family == "phn":
        region_code_col = None
        region_name_col = None
        for col in columns:
            c = lower_cols[col]
            if "phn" in c and ("code" in c or "identifier" in c or c.endswith("cd")):
                region_code_col = col
                break
        for col in columns:
            c = lower_cols[col]
            if "phn" in c and "name" in c:
                region_name_col = col
                break
    elif family == "lga":
        region_code_col = None
        region_name_col = None
        for col in columns:
            c = lower_cols[col]
            if "lga" in c and ("code" in c or "maincode" in c or c.endswith("cd")):
                region_code_col = col
                break
        for col in columns:
            c = lower_cols[col]
            if "lga" in c and "name" in c:
                region_name_col = col
                break
    else:
        raise ValueError(f"Unsupported bridge family: {family}")

    ratio_col = detect_ratio_column(columns)

    audit = {
        "bridge_family": family,
        "source_path": str(source_path),
        "sheet_name": sheet_name,
        "row_count": len(df),
        "column_count": len(columns),
        "sa2_code_col": sa2_code_col or "",
        "region_code_col": region_code_col or "",
        "region_name_col": region_name_col or "",
        "ratio_col": ratio_col or "",
        "status": "review",
        "notes": "",
    }

    if not sa2_code_col or not region_code_col:
        audit["status"] = "not_bridge"
        audit["notes"] = "Could not identify both SA2 code and target-region code columns."
        return None, audit

    bridge = pd.DataFrame()
    bridge["sa2_code_2021"] = df[sa2_code_col].map(norm_code)
    bridge[f"{family}_code"] = df[region_code_col].map(norm_code)
    if region_name_col:
        bridge[f"{family}_name"] = df[region_name_col].astype(str).str.strip()
    else:
        bridge[f"{family}_name"] = pd.NA

    if ratio_col:
        bridge["allocation_ratio"] = clean_ratio(df[ratio_col])
    else:
        bridge["allocation_ratio"] = 1.0

    bridge = bridge.dropna(subset=["sa2_code_2021", f"{family}_code"]).copy()
    bridge = bridge[bridge["sa2_code_2021"].str.fullmatch(r"\d{9,11}", na=False)]

    if len(bridge) < 100:
        audit["status"] = "not_bridge"
        audit["notes"] = "Too few plausible SA2 bridge rows after cleaning."
        return None, audit

    audit["status"] = "candidate_bridge"
    audit["cleaned_row_count"] = len(bridge)
    audit["unique_sa2_count"] = bridge["sa2_code_2021"].nunique(dropna=True)
    audit["unique_region_count"] = bridge[f"{family}_code"].nunique(dropna=True)
    audit["notes"] = "Candidate bridge detected. Requires validation against master SA2 before join."
    return bridge, audit


def find_best_bridge(family: str) -> tuple[pd.DataFrame | None, list[dict]]:
    rows = []
    best_bridge = None
    best_score = -1
    best_source = None

    for path in candidate_table_files():
        tags = classify_bridge_file(path)
        name = path.name.lower()
        if family not in tags and family not in name:
            continue
        if "sa2" not in tags and "sa2" not in name:
            continue
        try:
            table = read_any_table(path)
        except Exception as exc:
            rows.append({"bridge_family": family, "source_path": str(path), "status": "read_failed", "notes": str(exc)})
            continue

        if isinstance(table, dict):
            for sheet, df in table.items():
                bridge, audit = try_build_bridge_from_df(df, family, path, sheet)
                rows.append(audit)
                if bridge is not None:
                    score = int(bridge["sa2_code_2021"].nunique(dropna=True))
                    # Prefer exact-looking filenames.
                    if family in path.name.lower() and "sa2" in path.name.lower():
                        score += 1000
                    if score > best_score:
                        best_score = score
                        best_bridge = bridge
                        best_source = (path, sheet)
        else:
            bridge, audit = try_build_bridge_from_df(table, family, path, "")
            rows.append(audit)
            if bridge is not None:
                score = int(bridge["sa2_code_2021"].nunique(dropna=True))
                if family in path.name.lower() and "sa2" in path.name.lower():
                    score += 1000
                if score > best_score:
                    best_score = score
                    best_bridge = bridge
                    best_source = (path, "")

    if best_bridge is not None and best_source:
        # Mark chosen source in audit rows.
        for r in rows:
            if r.get("source_path") == str(best_source[0]) and r.get("sheet_name", "") == best_source[1]:
                r["chosen_for_join"] = True
            else:
                r["chosen_for_join"] = False

    return best_bridge, rows


def reduce_bridge_to_primary_region(bridge: pd.DataFrame, family: str, master: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    bridge = bridge.copy()
    bridge["allocation_ratio"] = pd.to_numeric(bridge["allocation_ratio"], errors="coerce").fillna(0)

    master_sa2 = set(master["sa2_code_2021"].map(norm_code).dropna())
    bridge["sa2_in_master"] = bridge["sa2_code_2021"].isin(master_sa2)

    # If no useful ratio, preserve deterministic first region but mark as no_ratio.
    ratio_missing_all = bridge["allocation_ratio"].fillna(0).sum() == 0
    if ratio_missing_all:
        bridge["allocation_ratio"] = 1.0
        allocation_note = "No usable allocation ratio detected; primary region selected deterministically."
    else:
        allocation_note = "Primary region selected by largest allocation_ratio."

    bridge = bridge.sort_values(["sa2_code_2021", "allocation_ratio", f"{family}_code"], ascending=[True, False, True])
    primary = bridge.drop_duplicates(subset=["sa2_code_2021"], keep="first").copy()
    primary = primary[["sa2_code_2021", f"{family}_code", f"{family}_name", "allocation_ratio", "sa2_in_master"]]
    primary = primary.rename(columns={"allocation_ratio": f"{family}_primary_allocation_ratio"})

    multi = (
        bridge.groupby("sa2_code_2021", dropna=False)
        .agg(
            region_count=(f"{family}_code", "nunique"),
            ratio_sum=("allocation_ratio", "sum"),
            in_master=("sa2_in_master", "max"),
        )
        .reset_index()
    )
    multi = multi[multi["region_count"] > 1].copy()

    audit = pd.DataFrame([
        {"check_name": f"{family}_bridge_rows", "value": len(bridge), "status": "info", "notes": allocation_note},
        {"check_name": f"{family}_unique_sa2", "value": bridge["sa2_code_2021"].nunique(dropna=True), "status": "info", "notes": ""},
        {"check_name": f"{family}_unique_regions", "value": bridge[f"{family}_code"].nunique(dropna=True), "status": "info", "notes": ""},
        {"check_name": f"{family}_sa2_in_master", "value": int(primary["sa2_in_master"].sum()), "status": "info", "notes": ""},
        {"check_name": f"{family}_multi_region_sa2", "value": len(multi), "status": "review" if len(multi) else "pass", "notes": "SA2s with more than one target region; primary region uses largest allocation ratio."},
    ])
    return primary, multi, audit


def join_available_bridges(master: pd.DataFrame) -> tuple[pd.DataFrame, list[dict], dict[str, pd.DataFrame]]:
    joined = master.copy()
    all_audits = []
    outputs = {}

    for family in ["phn", "lga"]:
        bridge, source_rows = find_best_bridge(family)
        write_csv(AUDIT_DIR / f"extended_{family}_bridge_source_candidates_v04.csv", source_rows)
        if bridge is None:
            all_audits.append({
                "layer": family,
                "check_name": "bridge_found",
                "value": 0,
                "status": "pending",
                "notes": f"No validated local SA2->{family.upper()} bridge found. Source remains pending acquisition/bridge.",
            })
            continue

        primary, multi, audit_df = reduce_bridge_to_primary_region(bridge, family, joined)
        primary_path = PROCESSED_GEO_DIR / f"bridge_sa2_2021_to_{family}_primary.csv"
        multi_path = AUDIT_DIR / f"bridge_sa2_2021_to_{family}_multi_region_sa2_audit.csv"
        audit_path = AUDIT_DIR / f"bridge_sa2_2021_to_{family}_join_audit.csv"

        primary.to_csv(primary_path, index=False, encoding="utf-8-sig")
        multi.to_csv(multi_path, index=False, encoding="utf-8-sig")
        audit_df.to_csv(audit_path, index=False, encoding="utf-8-sig")

        before_rows = len(joined)
        before_cols = len(joined.columns)
        join_cols = ["sa2_code_2021", f"{family}_code", f"{family}_name", f"{family}_primary_allocation_ratio"]
        add = primary[join_cols].copy()
        collision_cols = [c for c in add.columns if c in joined.columns and c != "sa2_code_2021"]
        if collision_cols:
            for c in collision_cols:
                add = add.rename(columns={c: f"{c}_extended_bridge"})
        joined = joined.merge(add, on="sa2_code_2021", how="left", validate="one_to_one")
        after_rows = len(joined)
        after_cols = len(joined.columns)
        matched = int(joined[f"{family}_code" if f"{family}_code" in joined.columns else f"{family}_code_extended_bridge"].notna().sum())

        for _, r in audit_df.iterrows():
            all_audits.append({"layer": family, **r.to_dict()})
        all_audits.extend([
            {"layer": family, "check_name": "rows_before_join", "value": before_rows, "status": "info", "notes": ""},
            {"layer": family, "check_name": "rows_after_join", "value": after_rows, "status": "pass" if after_rows == before_rows else "fail", "notes": "Join must not change master row count."},
            {"layer": family, "check_name": "columns_added", "value": after_cols - before_cols, "status": "info", "notes": ""},
            {"layer": family, "check_name": "matched_sa2_rows", "value": matched, "status": "review" if matched < 2400 else "pass", "notes": "Rows with bridge match in master."},
        ])
        outputs[family] = primary

    return joined, all_audits, outputs


def discover_links_for_source_families(timeout: int = 45, sleep_seconds: float = 0.25) -> list[dict]:
    rows = []
    if requests is None:
        rows.append({"status": "requests_not_installed", "notes": "Install requests to enable web discovery."})
        return rows

    headers = {"User-Agent": "Mozilla/5.0 MentalWellbeingByGeography source discovery"}
    for src in SOURCE_FAMILIES:
        family = src["source_family"]
        for page_url in src["official_pages"]:
            try:
                resp = requests.get(page_url, headers=headers, timeout=timeout)
                status = resp.status_code
                content_type = resp.headers.get("content-type", "")
                text = resp.text if "text" in content_type or "html" in content_type or not content_type else ""
            except Exception as exc:
                rows.append({
                    "source_family": family,
                    "page_url": page_url,
                    "status_code": "",
                    "candidate_url": "",
                    "candidate_text": "",
                    "candidate_kind": "page_fetch_failed",
                    "notes": str(exc),
                })
                continue

            rows.append({
                "source_family": family,
                "page_url": page_url,
                "status_code": status,
                "candidate_url": page_url,
                "candidate_text": "source_page",
                "candidate_kind": "source_page",
                "notes": content_type,
            })

            if status != 200 or not text:
                continue

            # Very light link extraction without BeautifulSoup dependency.
            for match in re.finditer(r'<a\s+[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', text, flags=re.I | re.S):
                href = html_unescape(match.group(1).strip())
                label = html_unescape(re.sub(r"<[^>]+>", " ", match.group(2)))
                label = re.sub(r"\s+", " ", label).strip()
                candidate_url = urllib.parse.urljoin(page_url, href)
                lower = candidate_url.lower()
                kind = "download" if DOWNLOAD_EXT_RE.search(lower) or "download" in lower else "link"
                score_text = f"{candidate_url} {label}"
                if kind == "download" or KEYWORD_RE.search(score_text):
                    rows.append({
                        "source_family": family,
                        "page_url": page_url,
                        "status_code": status,
                        "candidate_url": candidate_url,
                        "candidate_text": label[:500],
                        "candidate_kind": kind,
                        "notes": "keyword/download candidate",
                    })
            time.sleep(sleep_seconds)
    return rows


def html_unescape(text: str) -> str:
    return (
        text.replace("&amp;", "&")
        .replace("&quot;", '"')
        .replace("&#39;", "'")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
    )


def write_acquisition_register() -> pd.DataFrame:
    rows = []
    for src in SOURCE_FAMILIES:
        row = dict(src)
        row["official_pages"] = " | ".join(src["official_pages"])
        row["created_utc"] = utc_now()
        row["current_decision"] = (
            "Do not join until acquisition/bridge validation passes. "
            "Use current v03 NDIA context master as latest POC master."
        )
        rows.append(row)
    df = pd.DataFrame(rows)
    out = REGISTER_DIR / "extended_predictor_acquisition_register_v04.csv"
    df.to_csv(out, index=False, encoding="utf-8-sig")
    return df


def write_methodology_note() -> None:
    note = f"""# Extended predictor acquisition status after NDIA POC join

Generated: {utc_now()}

## Current master files

Primary aligned master remains:

`data/processed/integrated/sa2_predictor_universe_v02_with_aihw_sa3.parquet`

NDIA public proof-of-concept context master is:

`data/processed/integrated/sa2_predictor_universe_v03_with_ndia_public_poc_context.parquet`

If PHN or LGA bridges were validated locally, this script may also create:

`data/processed/integrated/sa2_predictor_universe_v04_with_ndia_phn_lga_context.parquet`

## Modelling rule

NDIA public POC variables use public participant-count data aligned to 2021-12-31 where available, bridged from ASGS 2016 to ASGS 2021. These variables remain a proof-of-concept/sensitivity layer unless explicitly included in a separate sensitivity model.

PHN and LGA fields are administrative/context geography fields. They should not be treated as individual-level predictors. They may support grouped summaries, local commissioning interpretation and service-system context.

The following source families remain pending until geography and source validity are confirmed:

- LHD / local health district / state health district boundaries
- DSS social-security data
- housing stress and homelessness data
- PHIDU Social Health Atlas indicators
- AEDC child development indicators
- carer, disability and psychosocial support demand sources beyond public NDIA participant counts

## Guardrail

This project should not join PHN, LGA, LHD, DSS, PHIDU, AEDC or housing/homelessness files unless the native geography is direct SA2/SA3 or a validated bridge exists. Where source geography is LGA/PHN/PHA/AEDC community/state/service district, hold as context-only or pending until bridge quality is documented.
"""
    (METHODOLOGY_DIR / "extended_predictor_acquisition_status_v04.md").write_text(note, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate NDIA POC join and stage extended source acquisition/backlog.")
    parser.add_argument("--no-web-discovery", action="store_true", help="Skip web link discovery.")
    parser.add_argument("--do-not-write-v04", action="store_true", help="Do not write v04 master even if bridges are found.")
    parser.add_argument("--timeout", type=int, default=45, help="HTTP timeout for discovery pages.")
    args = parser.parse_args()

    print("Extended acquisition controller after NDIA POC join")
    print(f"Project root: {PROJECT_ROOT}")

    master, master_audit = validate_master()
    write_csv(AUDIT_DIR / "extended_acquisition_master_v03_validation_audit.csv", master_audit)
    print("Validated v03 NDIA POC context master.")

    acquisition_register = write_acquisition_register()
    print(f"Created acquisition register: {REGISTER_DIR / 'extended_predictor_acquisition_register_v04.csv'}")

    joined, bridge_audits, bridge_outputs = join_available_bridges(master)
    write_csv(AUDIT_DIR / "extended_phn_lga_bridge_join_audit_v04.csv", bridge_audits)

    bridge_layers_joined = [k for k, v in bridge_outputs.items() if v is not None]

    if not args.do_not_write_v04:
        joined.to_csv(MASTER_V04_CSV, index=False, encoding="utf-8-sig")
        joined.to_parquet(MASTER_V04_PARQUET, index=False)
        print(f"Created v04 candidate/context master: {MASTER_V04_PARQUET}")
    else:
        print("Did not write v04 master because --do-not-write-v04 was used.")

    if args.no_web_discovery:
        link_rows = []
        print("Skipped web discovery.")
    else:
        print("Discovering source-page links for pending source families...")
        link_rows = discover_links_for_source_families(timeout=args.timeout)
    write_csv(AUDIT_DIR / "extended_pending_source_link_discovery_v04.csv", link_rows)

    # Completion summary.
    completion_rows = []
    completion_rows.append({
        "source_family": "ndia_public_poc_participants",
        "status": "integrated_sensitivity",
        "master_file": str(MASTER_V03),
        "notes": "NDIA public POC participant context already joined in v03; exclude from primary model unless sensitivity model.",
    })
    for fam in ["phn", "lga"]:
        completion_rows.append({
            "source_family": f"{fam}_bridge_to_sa2",
            "status": "integrated_context" if fam in bridge_layers_joined else "pending_bridge",
            "master_file": str(MASTER_V04_PARQUET) if fam in bridge_layers_joined else "",
            "notes": "Joined to v04 by validated local bridge." if fam in bridge_layers_joined else "No validated local bridge found by this script.",
        })
    for src in SOURCE_FAMILIES:
        if src["source_family"] in {"phn_bridge_to_sa2", "lga_bridge_to_sa2"}:
            continue
        completion_rows.append({
            "source_family": src["source_family"],
            "status": src["status_target"],
            "master_file": "",
            "notes": src["integration_rule"],
        })
    write_csv(AUDIT_DIR / "extended_acquisition_completion_status_v04.csv", completion_rows)

    write_methodology_note()

    print("\nCreated outputs:")
    for p in [
        AUDIT_DIR / "extended_acquisition_master_v03_validation_audit.csv",
        AUDIT_DIR / "extended_phn_lga_bridge_join_audit_v04.csv",
        AUDIT_DIR / "extended_pending_source_link_discovery_v04.csv",
        AUDIT_DIR / "extended_acquisition_completion_status_v04.csv",
        REGISTER_DIR / "extended_predictor_acquisition_register_v04.csv",
        METHODOLOGY_DIR / "extended_predictor_acquisition_status_v04.md",
    ]:
        print(f"  {p}")
    if not args.do_not_write_v04:
        print(f"  {MASTER_V04_CSV}")
        print(f"  {MASTER_V04_PARQUET}")

    print("\nBridge layers joined:")
    if bridge_layers_joined:
        for fam in bridge_layers_joined:
            print(f"  {fam}")
    else:
        print("  none - pending bridge acquisition/validation")

    print("\nNext action:")
    print("  Review outputs/audits/extended_acquisition_completion_status_v04.csv")
    print("  Review outputs/audits/extended_pending_source_link_discovery_v04.csv")
    print("  Do not model from v04 until any new bridge/context fields are explicitly accepted.")


if __name__ == "__main__":
    main()
