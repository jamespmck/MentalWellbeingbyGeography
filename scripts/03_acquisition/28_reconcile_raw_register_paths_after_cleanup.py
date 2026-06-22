#!/usr/bin/env python3
"""
Reconcile raw acquisition register paths after raw-folder cleanup.

This script updates stale raw_file_path values in:
  outputs/audits/remaining_raw_source_acquisition_register_v13.csv
  outputs/audits/remaining_raw_source_candidate_link_audit_v13.csv
  docs/source_registers/remaining_raw_source_acquisition_register_v13.csv

It uses the applied raw-folder cleanup plans written by 27_cleanup_raw_folder_pre_freeze.ps1.
By default it writes reconciled audit files only. Use --apply to back up and overwrite
v13 registers so 25_validate_remaining_raw_source_inventory.py can run successfully.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

import pandas as pd

DEFAULT_ROOT = Path(r"D:\Good Measure\MentalWellbeingbyGeography")


@dataclass(frozen=True)
class PathHit:
    path: Path
    active: bool


def norm_path_string(value: object) -> str:
    if value is None:
        return ""
    s = str(value).strip()
    if not s or s.lower() in {"nan", "none", "null"}:
        return ""
    return s


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def is_under(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except Exception:
        return False


def rel_to_raw(path: Path, raw_root: Path) -> str:
    try:
        return str(path.resolve().relative_to(raw_root.resolve()))
    except Exception:
        return str(path)


def load_cleanup_mapping(root: Path) -> Tuple[Dict[str, Path], List[Path]]:
    """Return old absolute raw paths -> new absolute raw paths from applied cleanup plans."""
    raw_root = root / "data" / "raw"
    audit_dir = root / "outputs" / "audits"
    plans = sorted(audit_dir.glob("raw_folder_cleanup_plan_v16_*.csv"))
    mapping: Dict[str, Path] = {}
    used: List[Path] = []

    for plan in plans:
        try:
            df = pd.read_csv(plan)
        except Exception:
            continue
        if not {"source_relative_path", "actual_destination_relative_path", "status"}.issubset(df.columns):
            continue
        moved = df[df["status"].astype(str).str.lower().eq("moved")].copy()
        if moved.empty:
            continue
        used.append(plan)
        for _, row in moved.iterrows():
            src_rel = norm_path_string(row.get("source_relative_path"))
            dst_rel = norm_path_string(row.get("actual_destination_relative_path"))
            if not src_rel or not dst_rel:
                continue
            src_abs = (raw_root / src_rel).resolve()
            dst_abs = (raw_root / dst_rel).resolve()
            mapping[str(src_abs).lower()] = dst_abs
    return mapping, used


def build_filename_index(root: Path) -> Dict[str, List[PathHit]]:
    raw_root = root / "data" / "raw"
    hits: Dict[str, List[PathHit]] = {}
    if not raw_root.exists():
        return hits
    archive_root = raw_root / "_archive"

    for p in raw_root.rglob("*"):
        if not p.is_file():
            continue
        active = not is_under(p, archive_root)
        hits.setdefault(p.name.lower(), []).append(PathHit(p.resolve(), active))
    return hits


def choose_by_basename(old_path: Path, filename_index: Dict[str, List[PathHit]]) -> Tuple[Optional[Path], str]:
    hits = filename_index.get(old_path.name.lower(), [])
    if not hits:
        return None, "missing_no_basename_match"
    active_hits = [h.path for h in hits if h.active]
    archive_hits = [h.path for h in hits if not h.active]
    if len(active_hits) == 1:
        return active_hits[0], "reconciled_by_unique_active_basename"
    if len(active_hits) > 1:
        return active_hits[0], "reconciled_by_first_of_multiple_active_basename_matches"
    if len(archive_hits) == 1:
        return archive_hits[0], "reconciled_to_archive_by_unique_basename"
    return archive_hits[0], "reconciled_to_archive_by_first_of_multiple_basename_matches"


def reconcile_path(
    old_value: object,
    root: Path,
    cleanup_mapping: Dict[str, Path],
    filename_index: Dict[str, List[PathHit]],
) -> Tuple[str, str, bool, str, int, str]:
    """Return new_path, status, exists, active_or_archive, size, sha256."""
    raw_root = root / "data" / "raw"
    s = norm_path_string(old_value)
    if not s:
        return "", "blank_raw_file_path", False, "", 0, ""

    old_path = Path(s)
    if not old_path.is_absolute():
        old_path = (root / old_path).resolve()
    else:
        old_path = old_path.resolve()

    if old_path.exists():
        p = old_path
        status = "path_still_valid"
    else:
        mapped = cleanup_mapping.get(str(old_path).lower())
        if mapped is not None and mapped.exists():
            p = mapped
            status = "reconciled_from_cleanup_plan"
        else:
            p2, status2 = choose_by_basename(old_path, filename_index)
            if p2 is None:
                return str(old_path), "missing_after_cleanup_no_match", False, "", 0, ""
            p = p2
            status = status2

    active_or_archive = "active"
    if is_under(p, raw_root / "_archive"):
        active_or_archive = "archive"
    try:
        size = p.stat().st_size
    except Exception:
        size = 0
    try:
        digest = sha256_file(p)
    except Exception:
        digest = ""
    return str(p), status, p.exists(), active_or_archive, size, digest


def reconcile_dataframe(df: pd.DataFrame, root: Path, cleanup_mapping: Dict[str, Path], filename_index: Dict[str, List[PathHit]]) -> pd.DataFrame:
    out = df.copy()
    if "raw_file_path" not in out.columns:
        out["raw_file_path"] = ""

    new_paths = []
    statuses = []
    exists_list = []
    active_archive = []
    sizes = []
    digests = []
    old_paths = []

    for value in out["raw_file_path"].tolist():
        old_paths.append(norm_path_string(value))
        new_path, status, exists, layer, size, digest = reconcile_path(value, root, cleanup_mapping, filename_index)
        new_paths.append(new_path)
        statuses.append(status)
        exists_list.append(int(bool(exists)))
        active_archive.append(layer)
        sizes.append(size)
        digests.append(digest)

    out["raw_file_path_before_cleanup_reconcile"] = old_paths
    out["raw_file_path"] = new_paths
    out["path_reconciliation_status"] = statuses
    out["file_exists_after_reconcile"] = exists_list
    out["raw_layer_after_reconcile"] = active_archive
    out["bytes_after_reconcile"] = sizes
    out["sha256_after_reconcile"] = digests
    return out


def backup_file(path: Path, backup_dir: Path, timestamp: str) -> Optional[Path]:
    if not path.exists():
        return None
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup = backup_dir / f"{path.stem}_BACKUP_BEFORE_RAW_PATH_RECONCILE_{timestamp}{path.suffix}"
    shutil.copy2(path, backup)
    return backup


def write_df(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", default=str(DEFAULT_ROOT))
    parser.add_argument("--apply", action="store_true", help="Back up and overwrite v13 register files.")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    root = Path(args.project_root)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    audit_dir = root / "outputs" / "audits"
    docs_register_dir = root / "docs" / "source_registers"
    backup_dir = root / "outputs" / "archive" / "register_backups"
    audit_dir.mkdir(parents=True, exist_ok=True)

    paths = {
        "acquisition_register_audit": audit_dir / "remaining_raw_source_acquisition_register_v13.csv",
        "candidate_link_audit": audit_dir / "remaining_raw_source_candidate_link_audit_v13.csv",
        "acquisition_register_docs": docs_register_dir / "remaining_raw_source_acquisition_register_v13.csv",
    }

    cleanup_mapping, plans_used = load_cleanup_mapping(root)
    filename_index = build_filename_index(root)

    outputs = []
    summary_rows = []

    for label, path in paths.items():
        if not path.exists():
            summary_rows.append({
                "label": label,
                "path": str(path),
                "input_exists": 0,
                "rows": 0,
                "exists_after_reconcile": 0,
                "active_after_reconcile": 0,
                "archive_after_reconcile": 0,
                "missing_after_reconcile": 0,
                "written_path": "",
            })
            continue
        df = pd.read_csv(path)
        rec = reconcile_dataframe(df, root, cleanup_mapping, filename_index)
        out_path = audit_dir / f"{path.stem}_RECONCILED_AFTER_RAW_CLEANUP_v17_{timestamp}.csv"
        write_df(rec, out_path)
        outputs.append(out_path)

        if args.apply:
            backup_file(path, backup_dir, timestamp)
            write_df(rec, path)

        summary_rows.append({
            "label": label,
            "path": str(path),
            "input_exists": 1,
            "rows": len(rec),
            "exists_after_reconcile": int(rec["file_exists_after_reconcile"].sum()) if "file_exists_after_reconcile" in rec else 0,
            "active_after_reconcile": int((rec.get("raw_layer_after_reconcile", pd.Series(dtype=str)) == "active").sum()),
            "archive_after_reconcile": int((rec.get("raw_layer_after_reconcile", pd.Series(dtype=str)) == "archive").sum()),
            "missing_after_reconcile": int((rec.get("file_exists_after_reconcile", pd.Series(dtype=int)) == 0).sum()),
            "written_path": str(out_path),
        })

    summary = pd.DataFrame(summary_rows)
    summary_path = audit_dir / f"raw_register_path_reconciliation_summary_v17_{timestamp}.csv"
    write_df(summary, summary_path)

    plan_rows = [{"cleanup_plan_used": str(p)} for p in plans_used]
    plans_path = audit_dir / f"raw_register_path_reconciliation_cleanup_plans_used_v17_{timestamp}.csv"
    write_df(pd.DataFrame(plan_rows), plans_path)

    note_path = root / "docs" / "methodology" / f"raw_register_path_reconciliation_note_v17_{timestamp}.md"
    note_path.parent.mkdir(parents=True, exist_ok=True)
    note = [
        "# Raw register path reconciliation v17",
        "",
        f"Run timestamp: {timestamp}",
        f"Mode: {'APPLY' if args.apply else 'DRY RUN'}",
        "",
        "This step reconciles stale raw_file_path values after the raw folder cleanup.",
        "The cleanup moved files into clearer source-family folders and archives, but v13 acquisition registers still pointed to the old paths.",
        "The reconciliation uses applied raw_folder_cleanup_plan_v16 CSV files plus a filename scan of data/raw.",
        "",
        "After applying this reconciliation, rerun 25_validate_remaining_raw_source_inventory.py.",
        "",
        "Summary file:",
        str(summary_path),
    ]
    note_path.write_text("\n".join(note) + "\n", encoding="utf-8")

    print("Raw register path reconciliation complete.")
    print(f"Mode: {'APPLIED' if args.apply else 'DRY_RUN'}")
    print(f"Summary: {summary_path}")
    print(f"Note: {note_path}")
    print(summary.to_string(index=False))
    if not args.apply:
        print("\nDry run only. Re-run with --apply to overwrite v13 registers after backing them up.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
