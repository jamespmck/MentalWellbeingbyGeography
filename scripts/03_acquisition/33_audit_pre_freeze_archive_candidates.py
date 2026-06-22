#!/usr/bin/env python3
"""
Audit candidate files/folders that may be archived before raw acquisition freeze.
Read-only. Does not move, rename or delete anything.

Designed for MentalWellbeingByGeography on Windows.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import os
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Iterable

EXCLUDE_DIR_PARTS = {
    ".git", ".venv", "venv", "__pycache__", ".mypy_cache", ".pytest_cache",
    "data\\raw\\_archive", "data/raw/_archive",
    "data\\archive", "data/archive",
    "outputs\\archive", "outputs/archive",
    "docs\\archive", "docs/archive",
    "data\\_quarantine", "data/_quarantine",
    "data\\processed\\legacy_wide_sa2_masters", "data/processed/legacy_wide_sa2_masters",
}

BROWSER_DEBRIS_EXT = {
    ".js", ".css", ".map", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp",
    ".ico", ".woff", ".woff2", ".ttf", ".eot", ".avif",
}

RAW_DATA_EXT = {".csv", ".xlsx", ".xls", ".zip", ".json", ".parquet", ".pdf", ".html", ".txt"}


def norm_rel(path: Path, root: Path) -> str:
    return str(path.relative_to(root)).replace("/", "\\")


def is_under(path: Path, root: Path, rel_prefix: str) -> bool:
    try:
        rel = norm_rel(path, root).lower()
    except Exception:
        return False
    p = rel_prefix.replace("/", "\\").lower().rstrip("\\")
    return rel == p or rel.startswith(p + "\\")


def skip_path(path: Path, root: Path) -> bool:
    rel = norm_rel(path, root).lower()
    for ex in EXCLUDE_DIR_PARTS:
        exn = ex.replace("/", "\\").lower().rstrip("\\")
        if rel == exn or rel.startswith(exn + "\\"):
            return True
    return False


def file_sha256(path: Path, chunk_size: int = 1024 * 1024) -> str | None:
    try:
        h = hashlib.sha256()
        with path.open("rb") as f:
            while True:
                chunk = f.read(chunk_size)
                if not chunk:
                    break
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        return None


def get_size_mb(path: Path) -> float:
    try:
        return round(path.stat().st_size / (1024 * 1024), 6)
    except Exception:
        return 0.0


def add_candidate(rows: list[dict], root: Path, path: Path, level: str, recommendation: str, rationale: str,
                  suggested_archive_subfolder: str, priority: str = "medium") -> None:
    rows.append({
        "path": norm_rel(path, root),
        "path_type": "folder" if path.is_dir() else "file",
        "extension": "" if path.is_dir() else path.suffix.lower(),
        "size_mb": get_size_mb(path) if path.is_file() else "",
        "level": level,
        "priority": priority,
        "recommendation": recommendation,
        "rationale": rationale,
        "suggested_archive_subfolder": suggested_archive_subfolder,
    })


def collect_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dp = Path(dirpath)
        # prune excluded folders
        keep_dirs = []
        for d in dirnames:
            candidate = dp / d
            if not skip_path(candidate, root):
                keep_dirs.append(d)
        dirnames[:] = keep_dirs
        for fn in filenames:
            p = dp / fn
            if not skip_path(p, root):
                files.append(p)
    return files


def find_latest_by_prefix(files: Iterable[Path], root: Path, regex: str) -> set[str]:
    """Return rel paths of latest timestamp/version-ish file per prefix match.
    This is intentionally conservative and only used for audit hints.
    """
    groups: dict[str, list[Path]] = defaultdict(list)
    rx = re.compile(regex, re.I)
    for p in files:
        m = rx.search(p.name)
        if not m:
            continue
        prefix = m.group(1)
        groups[prefix].append(p)
    keep: set[str] = set()
    for _, ps in groups.items():
        ps2 = sorted(ps, key=lambda x: (x.stat().st_mtime if x.exists() else 0, x.name), reverse=True)
        if ps2:
            keep.add(norm_rel(ps2[0], root))
    return keep


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project-root", default=r"D:\Good Measure\MentalWellbeingbyGeography")
    ap.add_argument("--debug", action="store_true")
    args = ap.parse_args()

    root = Path(args.project_root)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    audits = root / "outputs" / "audits"
    docs = root / "docs" / "methodology"
    audits.mkdir(parents=True, exist_ok=True)
    docs.mkdir(parents=True, exist_ok=True)

    rows: list[dict] = []

    if not root.exists():
        raise SystemExit(f"Project root not found: {root}")

    files = collect_files(root)

    # 1. Integrated wide masters: anything except v08 active file should not be active.
    integrated = root / "data" / "processed" / "integrated"
    if integrated.exists():
        for p in integrated.glob("sa2_predictor_universe_*"):
            name = p.name.lower()
            if "v08_with_clean_housing_context" in name:
                add_candidate(rows, root, p, "keep_active_for_now", "keep_active", "Current wide SA2 master; keep until scoped native-geography masters are built.", "", "low")
            else:
                add_candidate(rows, root, p, "archive_now", "archive", "Superseded wide SA2 integrated master should not remain in active integrated folder.", "data/processed/legacy_wide_sa2_masters", "high")

    # 2. processed sources discarded_or_archived folder under active processed tree.
    discarded = root / "data" / "processed" / "sources" / "discarded_or_archived"
    if discarded.exists():
        add_candidate(rows, root, discarded, "archive_now", "rehome_folder", "Discarded processed-source artefacts should live under data/archive, not active processed/sources.", "data/archive/discarded_processed_sources", "high")

    # 3. Raw archive still inside raw/abs.
    abs_archive = root / "data" / "raw" / "abs" / "_archive"
    if abs_archive.exists():
        add_candidate(rows, root, abs_archive, "archive_now", "rehome_folder", "ABS archive folder is inside active raw/abs and will inflate audits.", "data/raw/_archive/abs", "high")

    # 4. NDIA large cache if still active.
    ndia_cache = root / "data" / "raw" / "ndia" / "public_data_downloads"
    if ndia_cache.exists():
        add_candidate(rows, root, ndia_cache, "archive_now_if_not_needed", "archive_folder", "Large NDIA full public download cache; keep selected POC files active and archive bulk cache.", "data/raw/_archive/large_caches/ndia_public_data_downloads", "medium")

    # 5. browser capture debris in raw outside archives.
    for p in files:
        rel = norm_rel(p, root).lower()
        if not rel.startswith("data\\raw\\"):
            continue
        if "\\_archive\\" in rel or "\\archive\\" in rel:
            continue
        if p.suffix.lower() in BROWSER_DEBRIS_EXT:
            add_candidate(rows, root, p, "archive_now", "archive", "Browser/static asset debris in raw folder; not source data.", "data/raw/_archive/browser_capture_assets", "medium")

    # 6. generic .download files that are HTML endpoints, not datasets.
    for p in files:
        rel = norm_rel(p, root).lower()
        if rel.startswith("data\\raw\\") and p.suffix.lower() == ".download":
            add_candidate(rows, root, p, "archive_now_or_keep_as_page_snapshot", "review_archive", "Generic .download endpoint file. Usually an HTML page snapshot, not tabular source data.", "data/raw/_archive/generic_download_endpoints", "low")

    # 7. ABS out-of-scope active raw geography files.
    for p in files:
        rel = norm_rel(p, root).lower()
        if not rel.startswith("data\\raw\\abs\\"):
            continue
        if "\\_archive\\" in rel:
            continue
        nm = p.name.lower()
        if re.search(r"202[2-9]|2024|2025", nm):
            add_candidate(rows, root, p, "archive_now_if_not_part_of_2021_alignment", "review_archive", "Non-2021 ABS geography/source file; likely outside the current 2021-aligned scope.", "data/raw/_archive/abs/out_of_scope_years", "medium")
        elif re.search(r"\bsa1\b|sa1_", nm) and "2021" in nm:
            add_candidate(rows, root, p, "optional_archive_after_remoteness_provenance_checked", "review_archive", "SA1 file is not a modelling input; keep only if needed to document remoteness derivation.", "data/raw/_archive/abs/optional_sa1_provenance", "low")
        elif any(tok in nm for tok in ["ced", "sed", "electoral", "poa", "postal", "sal", "suburb", "mb_", "mesh", "dzn", "sua", "ucl", "iare", "ireg", "iloc"]):
            add_candidate(rows, root, p, "archive_now_if_not_used", "review_archive", "ABS geography type not used in current SA2/SA3/LGA/PHN/NDIA pipeline.", "data/raw/_archive/abs/out_of_scope_geographies", "medium")

    # 8. Old script variants and exploration scripts.
    script_archive_rules = [
        (r"^19_inventory_phidu_social_health_atlas.*\.py$", "PHIDU exploration superseded by official LGA/PHN v12 extraction."),
        (r"^20_validate_phidu_join_candidates.*\.py$", "PHIDU exploration superseded by official LGA/PHN v12 extraction."),
        (r"^21_deep_probe_phidu_workbook_keys.*\.py$", "PHIDU exploration superseded by official LGA/PHN v12 extraction."),
        (r".*(_corrected|_fix|_v2|_v3|_v4|_v5|_v6)\.py$", "Old variant script; archive if final canonical script exists and has been run successfully."),
    ]
    scripts_root = root / "scripts"
    if scripts_root.exists():
        for p in scripts_root.rglob("*.py"):
            if "archive" in [part.lower() for part in p.parts]:
                continue
            for pat, why in script_archive_rules:
                if re.match(pat, p.name, re.I):
                    add_candidate(rows, root, p, "optional_archive_after_confirming_final_script", "review_archive", why, "scripts/archive/pre_freeze_superseded_scripts", "low")
                    break

    # 9. Old logs: keep latest per script stem, archive older repeats.
    logs = root / "outputs" / "logs"
    if logs.exists():
        log_files = [p for p in logs.glob("*.log") if p.is_file()]
        groups: dict[str, list[Path]] = defaultdict(list)
        for p in log_files:
            # e.g. 25_validate_remaining_raw_source_inventory_20260622_115022.log
            stem = re.sub(r"_\d{8}_\d{6}$", "", p.stem)
            groups[stem].append(p)
        for stem, ps in groups.items():
            if len(ps) <= 1:
                continue
            ps_sorted = sorted(ps, key=lambda x: x.stat().st_mtime, reverse=True)
            for old in ps_sorted[1:]:
                add_candidate(rows, root, old, "archive_now", "archive", f"Older log for script/run family '{stem}'. Keep latest log active, archive older logs.", "outputs/archive/old_logs_pre_freeze", "low")

    # 10. Audit history: keep latest raw cleanup/audit files, archive older generated runs.
    audit_files = [p for p in (root / "outputs" / "audits").glob("*.csv")] if (root / "outputs" / "audits").exists() else []
    prefix_groups: dict[str, list[Path]] = defaultdict(list)
    for p in audit_files:
        m = re.match(r"(.+)_v(\d+)_\d{8}_\d{6}.*\.csv$", p.name, re.I)
        if m:
            prefix_groups[m.group(1) + "_v" + m.group(2)].append(p)
    for prefix, ps in prefix_groups.items():
        if len(ps) <= 1:
            continue
        ps_sorted = sorted(ps, key=lambda x: x.stat().st_mtime, reverse=True)
        for old in ps_sorted[1:]:
            add_candidate(rows, root, old, "archive_now_after_latest_confirmed", "archive", f"Older timestamped audit output for '{prefix}'. Keep latest active, archive prior runs.", "outputs/archive/audit_history_pre_freeze", "low")

    # 11. Exact duplicates in active space, by size+sha for modest number of files. Compute for files >=1 byte, excluding logs maybe okay.
    size_groups: dict[int, list[Path]] = defaultdict(list)
    for p in files:
        if p.is_file() and not skip_path(p, root):
            try:
                sz = p.stat().st_size
            except Exception:
                continue
            if sz > 0:
                size_groups[sz].append(p)
    sha_groups: dict[str, list[Path]] = defaultdict(list)
    for sz, ps in size_groups.items():
        if len(ps) < 2:
            continue
        # avoid hashing enormous video/cache; but most project files okay. Cap at 100MB for audit speed.
        if sz > 100 * 1024 * 1024:
            continue
        for p in ps:
            sh = file_sha256(p)
            if sh:
                sha_groups[sh].append(p)
    for sh, ps in sha_groups.items():
        if len(ps) < 2:
            continue
        ps_sorted = sorted(ps, key=lambda x: ("_archive" in norm_rel(x, root).lower(), norm_rel(x, root).lower()))
        # do not recommend the first, recommend the rest as duplicate review
        for dup in ps_sorted[1:]:
            add_candidate(rows, root, dup, "duplicate_review", "review_archive", f"Exact duplicate SHA256 of another active/non-excluded file: {sh[:12]}...", "data/archive/exact_duplicate_review", "medium")

    # Write outputs
    cand_path = audits / f"pre_freeze_archive_candidate_audit_v19_{ts}.csv"
    summary_path = audits / f"pre_freeze_archive_candidate_summary_v19_{ts}.csv"
    note_path = docs / f"pre_freeze_archive_candidate_audit_note_v19_{ts}.md"

    fieldnames = ["path", "path_type", "extension", "size_mb", "level", "priority", "recommendation", "rationale", "suggested_archive_subfolder"]
    with cand_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in rows:
            w.writerow(r)

    summary: dict[tuple[str, str, str], dict] = {}
    for r in rows:
        key = (r["level"], r["priority"], r["recommendation"])
        if key not in summary:
            summary[key] = {"level": key[0], "priority": key[1], "recommendation": key[2], "n": 0, "size_mb": 0.0}
        summary[key]["n"] += 1
        try:
            summary[key]["size_mb"] += float(r["size_mb"] or 0)
        except Exception:
            pass
    with summary_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["level", "priority", "recommendation", "n", "size_mb"])
        w.writeheader()
        for r in sorted(summary.values(), key=lambda x: (x["level"], x["priority"], x["recommendation"])):
            r["size_mb"] = round(r["size_mb"], 6)
            w.writerow(r)

    note_lines = [
        "# Pre-freeze archive candidate audit",
        "",
        f"Run timestamp: {ts}",
        f"Project root: {root}",
        "",
        "Read-only audit. No files were moved, renamed or deleted.",
        "",
        "## Outputs",
        f"- {cand_path}",
        f"- {summary_path}",
        "",
        "## Interpretation",
        "Archive recommendations are conservative. Review `archive_now` rows first. Rows marked `review_archive`, `duplicate_review`, or `keep_active_for_now` should not be moved without confirmation.",
    ]
    note_path.write_text("\n".join(note_lines), encoding="utf-8")

    print("Pre-freeze archive candidate audit complete.")
    print(f"Candidates: {cand_path}")
    print(f"Summary:    {summary_path}")
    print(f"Note:       {note_path}")
    print("")
    print(f"Candidate rows: {len(rows)}")
    if rows:
        print("Summary:")
        for r in sorted(summary.values(), key=lambda x: (x["level"], x["priority"], x["recommendation"])):
            print(f"  {r['level']:<48} {r['priority']:<8} {r['recommendation']:<18} n={r['n']:<5} size_mb={round(r['size_mb'],3)}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
