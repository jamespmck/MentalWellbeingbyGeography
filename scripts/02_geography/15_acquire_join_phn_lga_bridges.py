from __future__ import annotations

from pathlib import Path
from urllib.parse import urljoin
from urllib.request import Request, urlopen
from html.parser import HTMLParser
import re
import json
import shutil
import sys
import time
from datetime import datetime, timezone

import pandas as pd

PROJECT_ROOT = Path(r"D:\Good Measure\MentalWellbeingbyGeography")

# Official source pages / files
PHN_2017_ASGS2021_SA2_PAGE = (
    "https://www.health.gov.au/resources/publications/"
    "primary-health-networks-phn-2017-concordance-files-"
    "australian-statistical-geography-standards-2021-statistical-area-level-2?language=en"
)
PHN_2023_ASGS2021_SA2_PAGE = (
    "https://www.health.gov.au/resources/publications/"
    "primary-health-networks-phn-2023-statistical-area-level-2-2021?language=en"
)
ABS_LGA_2021_ALLOCATION_URL = (
    "https://www.abs.gov.au/statistics/standards/australian-statistical-geography-standard-asgs/"
    "edition-3-july-2021-june-2026/access-and-downloads/allocation-files/LGA_2021_AUST.xlsx"
)
ABS_MB_2021_ALLOCATION_URL = (
    "https://www.abs.gov.au/statistics/standards/australian-statistical-geography-standard-asgs/"
    "edition-3-july-2021-june-2026/access-and-downloads/allocation-files/MB_2021_AUST.xlsx"
)

RAW_DIR = PROJECT_ROOT / "data" / "raw"
RAW_HEALTH_DIR = RAW_DIR / "health" / "phn_concordance"
RAW_ABS_DIR = RAW_DIR / "abs" / "allocation_files"
PROCESSED_GEO_DIR = PROJECT_ROOT / "data" / "processed" / "geography"
PROCESSED_INTEGRATED_DIR = PROJECT_ROOT / "data" / "processed" / "integrated"
AUDIT_DIR = PROJECT_ROOT / "outputs" / "audits"
DICT_DIR = PROJECT_ROOT / "docs" / "data_dictionaries"
METHODOLOGY_DIR = PROJECT_ROOT / "docs" / "methodology"

BASE_MASTER_CANDIDATES = [
    PROCESSED_INTEGRATED_DIR / "sa2_predictor_universe_v04_with_ndia_phn_lga_context.parquet",
    PROCESSED_INTEGRATED_DIR / "sa2_predictor_universe_v03_with_ndia_public_poc_context.parquet",
    PROCESSED_INTEGRATED_DIR / "sa2_predictor_universe_v02_with_aihw_sa3.parquet",
]

OUT_MASTER_PARQUET = PROCESSED_INTEGRATED_DIR / "sa2_predictor_universe_v05_with_phn_lga_context.parquet"
OUT_MASTER_CSV = PROCESSED_INTEGRATED_DIR / "sa2_predictor_universe_v05_with_phn_lga_context.csv"

PHN_FULL_BRIDGE_OUT = PROCESSED_GEO_DIR / "bridge_sa2_2021_to_phn_2017_full.csv"
PHN_DOMINANT_BRIDGE_OUT = PROCESSED_GEO_DIR / "bridge_sa2_2021_to_phn_2017_dominant.csv"
PHN_2023_FULL_BRIDGE_OUT = PROCESSED_GEO_DIR / "bridge_sa2_2021_to_phn_2023_full.csv"
PHN_2023_DOMINANT_BRIDGE_OUT = PROCESSED_GEO_DIR / "bridge_sa2_2021_to_phn_2023_dominant.csv"

LGA_FULL_BRIDGE_OUT = PROCESSED_GEO_DIR / "bridge_sa2_2021_to_lga_2021_area_full.csv"
LGA_DOMINANT_BRIDGE_OUT = PROCESSED_GEO_DIR / "bridge_sa2_2021_to_lga_2021_area_dominant.csv"

SOURCE_AUDIT_OUT = AUDIT_DIR / "phn_lga_bridge_source_audit_v05.csv"
BRIDGE_AUDIT_OUT = AUDIT_DIR / "phn_lga_bridge_build_audit_v05.csv"
JOIN_AUDIT_OUT = AUDIT_DIR / "sa2_predictor_universe_v05_phn_lga_join_audit.csv"
UNMATCHED_OUT = AUDIT_DIR / "sa2_predictor_universe_v05_phn_lga_unmatched_audit.csv"
FIELD_DICT_OUT = DICT_DIR / "phn_lga_context_field_dictionary_v05.csv"
METHOD_NOTE_OUT = METHODOLOGY_DIR / "phn_lga_bridge_context_layer_note_v05.md"

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Python acquisition script"


class LinkExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links: list[dict] = []
        self._current_href = None
        self._current_text = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() == "a":
            attrs_d = dict(attrs)
            self._current_href = attrs_d.get("href")
            self._current_text = []

    def handle_data(self, data):
        if self._current_href is not None:
            self._current_text.append(data)

    def handle_endtag(self, tag):
        if tag.lower() == "a" and self._current_href is not None:
            self.links.append({"href": self._current_href, "text": " ".join(self._current_text).strip()})
            self._current_href = None
            self._current_text = []


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_dirs():
    for p in [RAW_HEALTH_DIR, RAW_ABS_DIR, PROCESSED_GEO_DIR, PROCESSED_INTEGRATED_DIR, AUDIT_DIR, DICT_DIR, METHODOLOGY_DIR]:
        p.mkdir(parents=True, exist_ok=True)


def get_url_bytes(url: str, timeout: int = 120) -> bytes:
    req = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(req, timeout=timeout) as resp:
        return resp.read()


def download_file(url: str, out_path: Path, force: bool = False, timeout: int = 180) -> tuple[bool, str]:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if out_path.exists() and out_path.stat().st_size > 0 and not force:
        return True, "already_exists"
    try:
        data = get_url_bytes(url, timeout=timeout)
        out_path.write_bytes(data)
        return True, f"downloaded_{len(data)}_bytes"
    except Exception as exc:
        return False, str(exc)


def discover_download_links(page_url: str) -> list[dict]:
    html = get_url_bytes(page_url, timeout=120).decode("utf-8", errors="replace")
    parser = LinkExtractor()
    parser.feed(html)
    out = []
    for link in parser.links:
        href = link.get("href") or ""
        text = link.get("text") or ""
        abs_url = urljoin(page_url, href)
        lower = (abs_url + " " + text).lower()
        if any(term in lower for term in ["download", ".xlsx", ".csv", ".zip", "attachment"]):
            out.append({"page_url": page_url, "candidate_url": abs_url, "candidate_text": text})
    return out


def clean_col_name(col) -> str:
    text = str(col).strip()
    text = re.sub(r"\s+", " ", text)
    return text


def norm_key(text) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(text).lower())


def normalise_code(value) -> str | pd.NA:
    if pd.isna(value):
        return pd.NA
    text = str(value).strip()
    if text == "" or text.lower() in {"nan", "none", "null", "na", "n.a."}:
        return pd.NA
    text = re.sub(r"\.0$", "", text)
    text = re.sub(r"\s+", "", text)
    return text


def read_excel_candidate_tables(path: Path, max_header_rows: int = 12) -> list[tuple[str, int, pd.DataFrame]]:
    tables = []
    xl = pd.ExcelFile(path)
    for sheet in xl.sheet_names:
        for header in range(max_header_rows + 1):
            try:
                df = pd.read_excel(path, sheet_name=sheet, header=header, dtype=str)
            except Exception:
                continue
            df = df.dropna(how="all")
            if df.empty or len(df.columns) < 2:
                continue
            df.columns = [clean_col_name(c) for c in df.columns]
            unnamed_ratio = sum(str(c).lower().startswith("unnamed") for c in df.columns) / max(len(df.columns), 1)
            if unnamed_ratio > 0.6:
                continue
            tables.append((sheet, header, df))
    return tables


def find_col(columns: list[str], must: list[str], any_of: list[str] | None = None, exclude: list[str] | None = None) -> str | None:
    exclude = exclude or []
    any_of = any_of or []
    for col in columns:
        n = norm_key(col)
        if all(term in n for term in must) and not any(term in n for term in exclude):
            if not any_of or any(term in n for term in any_of):
                return col
    return None


def numeric_series(series: pd.Series) -> pd.Series:
    return pd.to_numeric(
        series.astype(str).str.replace(",", "", regex=False).str.replace("%", "", regex=False).str.strip(),
        errors="coerce",
    )


def select_phn_download(page_url: str, raw_dir: Path, force: bool, label: str) -> tuple[Path | None, list[dict]]:
    audit = []
    try:
        candidates = discover_download_links(page_url)
    except Exception as exc:
        audit.append({"source_family": label, "stage": "discover", "url": page_url, "status": "fail", "notes": str(exc)})
        return None, audit

    # Prefer xlsx/csv attachment URLs and avoid accessibility/contact links.
    ranked = []
    for c in candidates:
        url = c["candidate_url"]
        text = c["candidate_text"]
        lower = (url + " " + text).lower()
        score = 0
        if ".xlsx" in lower: score += 20
        if ".csv" in lower: score += 15
        if "download" in lower or "attachment" in lower: score += 10
        if "statistical-area-level-2" in lower or "sa2" in lower: score += 10
        if "2021" in lower: score += 5
        if "request-accessible" in lower or "contact" in lower: score -= 50
        ranked.append((score, c))

    ranked = sorted(ranked, key=lambda x: x[0], reverse=True)
    for score, c in ranked[:10]:
        url = c["candidate_url"]
        suffix = ".xlsx" if ".xlsx" in url.lower() else ".csv" if ".csv" in url.lower() else ".bin"
        out_path = raw_dir / f"{label}{suffix}"
        ok, note = download_file(url, out_path, force=force)
        audit.append({
            "source_family": label,
            "stage": "download_candidate",
            "url": url,
            "candidate_text": c.get("candidate_text", ""),
            "score": score,
            "status": "pass" if ok else "fail",
            "path": str(out_path) if ok else "",
            "notes": note,
        })
        if ok and out_path.exists() and out_path.stat().st_size > 0 and suffix in {".xlsx", ".csv"}:
            return out_path, audit
    return None, audit


def parse_phn_bridge(path: Path, phn_reference_year: str) -> tuple[pd.DataFrame, pd.DataFrame, list[dict]]:
    audit = []
    candidate_tables = []
    if path.suffix.lower() in {".xlsx", ".xls"}:
        candidate_tables = read_excel_candidate_tables(path)
    elif path.suffix.lower() == ".csv":
        for header in range(8):
            try:
                df = pd.read_csv(path, dtype=str, header=header)
                df.columns = [clean_col_name(c) for c in df.columns]
                candidate_tables.append(("csv", header, df))
            except Exception:
                continue

    best = None
    best_score = -1
    best_cols = None

    for sheet, header, df in candidate_tables:
        cols = list(df.columns)
        sa2_code_col = find_col(cols, ["sa2"], ["code", "maincode"], exclude=["name"])
        sa2_name_col = find_col(cols, ["sa2", "name"], exclude=[])
        phn_code_col = find_col(cols, ["phn"], ["code"], exclude=["name"])
        phn_name_col = find_col(cols, ["phn", "name"], exclude=[])
        ratio_col = find_col(cols, ["ratio"], exclude=[]) or find_col(cols, ["proportion"], exclude=[]) or find_col(cols, ["percent"], exclude=[])
        score = sum(x is not None for x in [sa2_code_col, phn_code_col, phn_name_col]) * 10
        if sa2_name_col: score += 2
        if ratio_col: score += 2
        # A plausible SA2 table should have at least hundreds/thousands of rows.
        if len(df) > 1000: score += 3
        if score > best_score:
            best_score = score
            best = (sheet, header, df)
            best_cols = (sa2_code_col, sa2_name_col, phn_code_col, phn_name_col, ratio_col)

    if best is None or best_score < 20:
        raise ValueError(f"Could not identify PHN-SA2 bridge table in {path}. Best score={best_score}")

    sheet, header, df = best
    sa2_code_col, sa2_name_col, phn_code_col, phn_name_col, ratio_col = best_cols
    audit.append({
        "source_family": f"phn_{phn_reference_year}_sa2_2021",
        "stage": "parse",
        "path": str(path),
        "sheet": sheet,
        "header_row": header,
        "status": "pass",
        "notes": json.dumps({
            "sa2_code_col": sa2_code_col,
            "sa2_name_col": sa2_name_col,
            "phn_code_col": phn_code_col,
            "phn_name_col": phn_name_col,
            "ratio_col": ratio_col,
            "rows": len(df),
            "columns": len(df.columns),
        }),
    })

    out = pd.DataFrame()
    out["sa2_code_2021"] = df[sa2_code_col].map(normalise_code).astype("string")
    out["sa2_name_2021_phn_source"] = df[sa2_name_col].astype(str).str.strip() if sa2_name_col else pd.NA
    out[f"phn_{phn_reference_year}_code"] = df[phn_code_col].map(normalise_code).astype("string")
    out[f"phn_{phn_reference_year}_name"] = df[phn_name_col].astype(str).str.strip() if phn_name_col else pd.NA
    if ratio_col:
        ratio = numeric_series(df[ratio_col])
        # If ratio looks like 0-100 percentages, convert to 0-1.
        if ratio.dropna().median() > 1.0:
            ratio = ratio / 100.0
        out[f"phn_{phn_reference_year}_ratio_from_sa2"] = ratio
    else:
        out[f"phn_{phn_reference_year}_ratio_from_sa2"] = 1.0

    out = out.dropna(subset=["sa2_code_2021", f"phn_{phn_reference_year}_code"]).copy()
    out = out[out["sa2_code_2021"].str.fullmatch(r"\d{9}", na=False)].copy()
    out[f"phn_{phn_reference_year}_ratio_from_sa2"] = out[f"phn_{phn_reference_year}_ratio_from_sa2"].fillna(1.0)
    out["phn_reference_year"] = phn_reference_year
    out["phn_source_file"] = str(path)

    ratio_col_out = f"phn_{phn_reference_year}_ratio_from_sa2"
    out = out.sort_values(["sa2_code_2021", ratio_col_out, f"phn_{phn_reference_year}_code"], ascending=[True, False, True])
    dominant = out.drop_duplicates("sa2_code_2021", keep="first").copy()
    dominant[f"phn_{phn_reference_year}_allocation_rank"] = 1
    # Flag SA2s that had multiple PHN rows in the concordance.
    dup_counts = out.groupby("sa2_code_2021").size().rename("phn_rows_for_sa2").reset_index()
    dominant = dominant.merge(dup_counts, on="sa2_code_2021", how="left")
    dominant[f"phn_{phn_reference_year}_multiple_phn_rows_flag"] = dominant["phn_rows_for_sa2"].fillna(0).astype(int) > 1
    dominant = dominant.drop(columns=["phn_rows_for_sa2"])

    return out, dominant, audit


def read_excel_first_good(path: Path, required_cols: list[str]) -> pd.DataFrame:
    xl = pd.ExcelFile(path)
    best = None
    best_score = -1
    for sheet in xl.sheet_names:
        for header in range(12):
            try:
                df = pd.read_excel(path, sheet_name=sheet, header=header, dtype=str)
            except Exception:
                continue
            df = df.dropna(how="all")
            if df.empty:
                continue
            df.columns = [clean_col_name(c) for c in df.columns]
            ncols = {norm_key(c): c for c in df.columns}
            score = 0
            for req in required_cols:
                reqn = norm_key(req)
                if any(reqn == norm_key(c) for c in df.columns):
                    score += 10
                elif any(reqn in norm_key(c) for c in df.columns):
                    score += 5
            if score > best_score:
                best_score = score
                best = df
    if best is None or best_score < 10:
        raise ValueError(f"Could not identify table in {path}")
    return best


def build_lga_bridge(force: bool = False) -> tuple[pd.DataFrame, pd.DataFrame, list[dict]]:
    audit = []
    lga_path = RAW_ABS_DIR / "LGA_2021_AUST.xlsx"
    mb_path = RAW_ABS_DIR / "MB_2021_AUST.xlsx"

    for label, url, path in [
        ("abs_lga_2021_allocation", ABS_LGA_2021_ALLOCATION_URL, lga_path),
        ("abs_mb_2021_allocation", ABS_MB_2021_ALLOCATION_URL, mb_path),
    ]:
        ok, note = download_file(url, path, force=force, timeout=300)
        audit.append({"source_family": label, "stage": "download", "url": url, "status": "pass" if ok else "fail", "path": str(path), "notes": note})
        if not ok:
            raise RuntimeError(f"Could not download {label}: {note}")

    lga = read_excel_first_good(lga_path, ["MB_CODE_2021", "LGA_CODE_2021", "LGA_NAME_2021"])
    mb = read_excel_first_good(mb_path, ["MB_CODE_2021", "SA2_CODE_2021", "SA2_NAME_2021"])

    def get_required(df: pd.DataFrame, patterns: list[str]) -> str:
        for p in patterns:
            for c in df.columns:
                if norm_key(c) == norm_key(p):
                    return c
        for p in patterns:
            for c in df.columns:
                if norm_key(p) in norm_key(c):
                    return c
        raise ValueError(f"Missing required column matching {patterns}; columns={list(df.columns)[:30]}")

    lga_mb_col = get_required(lga, ["MB_CODE_2021"])
    lga_code_col = get_required(lga, ["LGA_CODE_2021"])
    lga_name_col = get_required(lga, ["LGA_NAME_2021"])
    lga_area_col = get_required(lga, ["AREA_ALBERS_SQKM", "AREA_ALBERS_SQKM_2021"])
    lga_state_code_col = None
    lga_state_name_col = None
    try:
        lga_state_code_col = get_required(lga, ["STATE_CODE_2021"])
        lga_state_name_col = get_required(lga, ["STATE_NAME_2021"])
    except Exception:
        pass

    mb_code_col = get_required(mb, ["MB_CODE_2021"])
    sa2_code_col = get_required(mb, ["SA2_CODE_2021"])
    sa2_name_col = get_required(mb, ["SA2_NAME_2021"])

    lga_s = pd.DataFrame({
        "mb_code_2021": lga[lga_mb_col].map(normalise_code).astype("string"),
        "lga_code_2021": lga[lga_code_col].map(normalise_code).astype("string"),
        "lga_name_2021": lga[lga_name_col].astype(str).str.strip(),
        "lga_mb_area_albers_sqkm": numeric_series(lga[lga_area_col]),
    })
    if lga_state_code_col:
        lga_s["state_code_2021_lga_source"] = lga[lga_state_code_col].map(normalise_code).astype("string")
    if lga_state_name_col:
        lga_s["state_name_2021_lga_source"] = lga[lga_state_name_col].astype(str).str.strip()

    mb_s = pd.DataFrame({
        "mb_code_2021": mb[mb_code_col].map(normalise_code).astype("string"),
        "sa2_code_2021": mb[sa2_code_col].map(normalise_code).astype("string"),
        "sa2_name_2021_lga_source": mb[sa2_name_col].astype(str).str.strip(),
    })

    merged = lga_s.merge(mb_s, on="mb_code_2021", how="left", validate="many_to_one")
    merged = merged.dropna(subset=["sa2_code_2021", "lga_code_2021"]).copy()
    merged = merged[merged["sa2_code_2021"].str.fullmatch(r"\d{9}", na=False)].copy()

    group_cols = ["sa2_code_2021", "sa2_name_2021_lga_source", "lga_code_2021", "lga_name_2021"]
    optional_cols = [c for c in ["state_code_2021_lga_source", "state_name_2021_lga_source"] if c in merged.columns]
    group_cols += optional_cols

    full = (
        merged.groupby(group_cols, dropna=False, as_index=False)
        .agg(
            sa2_lga_area_albers_sqkm=("lga_mb_area_albers_sqkm", "sum"),
            mb_count=("mb_code_2021", "nunique"),
        )
    )
    totals = full.groupby("sa2_code_2021", as_index=False)["sa2_lga_area_albers_sqkm"].sum().rename(columns={"sa2_lga_area_albers_sqkm": "sa2_total_area_in_lga_bridge_sqkm"})
    full = full.merge(totals, on="sa2_code_2021", how="left")
    full["lga_ratio_from_sa2_area"] = full["sa2_lga_area_albers_sqkm"] / full["sa2_total_area_in_lga_bridge_sqkm"]
    full["lga_bridge_method"] = "MB area allocation to dominant LGA 2021"
    full["lga_source_file"] = str(lga_path)

    full = full.sort_values(["sa2_code_2021", "lga_ratio_from_sa2_area", "lga_code_2021"], ascending=[True, False, True])
    dominant = full.drop_duplicates("sa2_code_2021", keep="first").copy()
    counts = full.groupby("sa2_code_2021").size().rename("lga_rows_for_sa2").reset_index()
    dominant = dominant.merge(counts, on="sa2_code_2021", how="left")
    dominant["lga_multiple_lga_rows_flag"] = dominant["lga_rows_for_sa2"].fillna(0).astype(int) > 1
    dominant = dominant.drop(columns=["lga_rows_for_sa2"])

    audit.append({
        "source_family": "lga_2021_sa2_area_bridge",
        "stage": "parse_and_build",
        "url": ABS_LGA_2021_ALLOCATION_URL,
        "status": "pass",
        "path": str(lga_path),
        "notes": json.dumps({
            "lga_rows": len(lga),
            "mb_rows": len(mb),
            "merged_mb_rows": len(merged),
            "full_bridge_rows": len(full),
            "dominant_sa2_rows": dominant["sa2_code_2021"].nunique(),
        }),
    })
    return full, dominant, audit


def choose_base_master() -> Path:
    for path in BASE_MASTER_CANDIDATES:
        if path.exists():
            return path
    raise FileNotFoundError("No base master found. Expected v04, v03 or v02 parquet in data/processed/integrated.")


def audit_bridge(name: str, full: pd.DataFrame | None, dominant: pd.DataFrame | None, master: pd.DataFrame, code_col: str = "sa2_code_2021") -> list[dict]:
    rows = []
    if full is None or dominant is None or full.empty or dominant.empty:
        rows.append({"layer": name, "check_name": "bridge_available", "value": 0, "status": "pending", "notes": "Bridge not available."})
        return rows
    master_codes = set(master[code_col].dropna().astype(str))
    dom_codes = set(dominant[code_col].dropna().astype(str))
    rows.extend([
        {"layer": name, "check_name": "bridge_available", "value": 1, "status": "pass", "notes": ""},
        {"layer": name, "check_name": "full_bridge_rows", "value": len(full), "status": "info", "notes": ""},
        {"layer": name, "check_name": "dominant_bridge_rows", "value": len(dominant), "status": "info", "notes": ""},
        {"layer": name, "check_name": "unique_sa2_in_dominant_bridge", "value": len(dom_codes), "status": "info", "notes": ""},
        {"layer": name, "check_name": "master_sa2_matched", "value": len(master_codes & dom_codes), "status": "pass" if len(master_codes & dom_codes) > 2300 else "review", "notes": "Matched SA2 codes in master."},
        {"layer": name, "check_name": "master_sa2_unmatched", "value": len(master_codes - dom_codes), "status": "review", "notes": "Unmatched are likely no usual address, migratory/offshore/shipping or special areas. Review unmatched audit."},
        {"layer": name, "check_name": "duplicate_sa2_in_dominant_bridge", "value": int(dominant.duplicated(code_col).sum()), "status": "pass" if int(dominant.duplicated(code_col).sum()) == 0 else "fail", "notes": "Dominant bridge must have one row per SA2."},
    ])
    return rows


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Acquire, validate and join PHN/LGA context bridges to the SA2 master.")
    parser.add_argument("--force-download", action="store_true", help="Re-download source files even if they already exist.")
    parser.add_argument("--include-phn-2023", action="store_true", help="Also build/join PHN 2023 SA2 2021 as a current-boundary context field.")
    args = parser.parse_args()

    ensure_dirs()
    source_audit = []
    bridge_audit = []

    base_master_path = choose_base_master()
    print("PHN/LGA bridge acquisition and join")
    print(f"Project root: {PROJECT_ROOT}")
    print(f"Base master: {base_master_path}")

    master = pd.read_parquet(base_master_path)
    if "sa2_code_2021" not in master.columns:
        raise ValueError("Base master missing sa2_code_2021")
    master = master.copy()
    master["sa2_code_2021"] = master["sa2_code_2021"].map(normalise_code).astype("string")

    # PHN 2017, aligned with the 2021 evidence window.
    print("\nBuilding PHN 2017 -> SA2 2021 bridge...")
    phn_full = phn_dom = None
    try:
        phn_path, audit = select_phn_download(PHN_2017_ASGS2021_SA2_PAGE, RAW_HEALTH_DIR, args.force_download, "phn_2017_asgs2021_sa2")
        source_audit.extend(audit)
        if phn_path is None:
            raise RuntimeError("No PHN 2017 SA2 2021 downloadable file found.")
        phn_full, phn_dom, audit2 = parse_phn_bridge(phn_path, "2017")
        source_audit.extend(audit2)
        phn_full.to_csv(PHN_FULL_BRIDGE_OUT, index=False, encoding="utf-8-sig")
        phn_dom.to_csv(PHN_DOMINANT_BRIDGE_OUT, index=False, encoding="utf-8-sig")
        print(f"  Created: {PHN_DOMINANT_BRIDGE_OUT}")
    except Exception as exc:
        source_audit.append({"source_family": "phn_2017_sa2_2021", "stage": "build", "status": "fail", "notes": str(exc)})
        print(f"  PHN bridge failed: {exc}")

    # Optional PHN 2023 context.
    phn2023_full = phn2023_dom = None
    if args.include_phn_2023:
        print("\nBuilding PHN 2023 -> SA2 2021 bridge...")
        try:
            phn23_path, audit = select_phn_download(PHN_2023_ASGS2021_SA2_PAGE, RAW_HEALTH_DIR, args.force_download, "phn_2023_asgs2021_sa2")
            source_audit.extend(audit)
            if phn23_path is None:
                raise RuntimeError("No PHN 2023 SA2 2021 downloadable file found.")
            phn2023_full, phn2023_dom, audit2 = parse_phn_bridge(phn23_path, "2023")
            source_audit.extend(audit2)
            phn2023_full.to_csv(PHN_2023_FULL_BRIDGE_OUT, index=False, encoding="utf-8-sig")
            phn2023_dom.to_csv(PHN_2023_DOMINANT_BRIDGE_OUT, index=False, encoding="utf-8-sig")
            print(f"  Created: {PHN_2023_DOMINANT_BRIDGE_OUT}")
        except Exception as exc:
            source_audit.append({"source_family": "phn_2023_sa2_2021", "stage": "build", "status": "fail", "notes": str(exc)})
            print(f"  PHN 2023 bridge failed: {exc}")

    # LGA bridge.
    print("\nBuilding LGA 2021 area bridge...")
    lga_full = lga_dom = None
    try:
        lga_full, lga_dom, audit = build_lga_bridge(force=args.force_download)
        source_audit.extend(audit)
        lga_full.to_csv(LGA_FULL_BRIDGE_OUT, index=False, encoding="utf-8-sig")
        lga_dom.to_csv(LGA_DOMINANT_BRIDGE_OUT, index=False, encoding="utf-8-sig")
        print(f"  Created: {LGA_DOMINANT_BRIDGE_OUT}")
    except Exception as exc:
        source_audit.append({"source_family": "lga_2021_sa2_area_bridge", "stage": "build", "status": "fail", "notes": str(exc)})
        print(f"  LGA bridge failed: {exc}")

    # Bridge audits.
    bridge_audit.extend(audit_bridge("phn_2017", phn_full, phn_dom, master))
    if args.include_phn_2023:
        bridge_audit.extend(audit_bridge("phn_2023", phn2023_full, phn2023_dom, master))
    bridge_audit.extend(audit_bridge("lga_2021_area", lga_full, lga_dom, master))

    joined = master.copy()
    joined_layers = []

    if phn_dom is not None and not phn_dom.empty:
        keep = [
            "sa2_code_2021",
            "phn_2017_code",
            "phn_2017_name",
            "phn_2017_ratio_from_sa2",
            "phn_2017_multiple_phn_rows_flag",
        ]
        phn_keep = phn_dom[[c for c in keep if c in phn_dom.columns]].copy()
        joined = joined.merge(phn_keep, on="sa2_code_2021", how="left", validate="one_to_one")
        joined["source_phn_2017_sa2_2021_present_flag"] = joined["phn_2017_code"].notna()
        joined_layers.append("phn_2017")

    if args.include_phn_2023 and phn2023_dom is not None and not phn2023_dom.empty:
        keep = [
            "sa2_code_2021",
            "phn_2023_code",
            "phn_2023_name",
            "phn_2023_ratio_from_sa2",
            "phn_2023_multiple_phn_rows_flag",
        ]
        phn23_keep = phn2023_dom[[c for c in keep if c in phn2023_dom.columns]].copy()
        joined = joined.merge(phn23_keep, on="sa2_code_2021", how="left", validate="one_to_one")
        joined["source_phn_2023_sa2_2021_present_flag"] = joined["phn_2023_code"].notna()
        joined_layers.append("phn_2023")

    if lga_dom is not None and not lga_dom.empty:
        keep = [
            "sa2_code_2021",
            "lga_code_2021",
            "lga_name_2021",
            "lga_ratio_from_sa2_area",
            "lga_multiple_lga_rows_flag",
        ]
        lga_keep = lga_dom[[c for c in keep if c in lga_dom.columns]].copy()
        # Rename to avoid collision if source already has LGA fields.
        lga_keep = lga_keep.rename(columns={
            "lga_code_2021": "dominant_lga_code_2021",
            "lga_name_2021": "dominant_lga_name_2021",
            "lga_ratio_from_sa2_area": "dominant_lga_ratio_from_sa2_area",
            "lga_multiple_lga_rows_flag": "dominant_lga_multiple_lga_rows_flag",
        })
        joined = joined.merge(lga_keep, on="sa2_code_2021", how="left", validate="one_to_one")
        joined["source_lga_2021_area_bridge_present_flag"] = joined["dominant_lga_code_2021"].notna()
        joined_layers.append("lga_2021_area")

    # Join audit.
    join_rows = [
        {"check_name": "base_master_file", "value": str(base_master_path), "status": "info", "notes": ""},
        {"check_name": "master_rows_before_join", "value": len(master), "status": "pass" if len(master) == 2472 else "review", "notes": "Expected SA2 row count."},
        {"check_name": "master_columns_before_join", "value": len(master.columns), "status": "info", "notes": ""},
        {"check_name": "joined_layers", "value": ", ".join(joined_layers) if joined_layers else "none", "status": "pass" if joined_layers else "review", "notes": ""},
        {"check_name": "master_rows_after_join", "value": len(joined), "status": "pass" if len(joined) == len(master) else "fail", "notes": "Join must not change SA2 row count."},
        {"check_name": "master_columns_after_join", "value": len(joined.columns), "status": "info", "notes": ""},
        {"check_name": "duplicate_sa2_rows_after_join", "value": int(joined.duplicated("sa2_code_2021").sum()), "status": "pass" if int(joined.duplicated("sa2_code_2021").sum()) == 0 else "fail", "notes": ""},
    ]
    if "source_phn_2017_sa2_2021_present_flag" in joined.columns:
        join_rows.append({"check_name": "sa2_rows_with_phn_2017", "value": int(joined["source_phn_2017_sa2_2021_present_flag"].sum()), "status": "info", "notes": ""})
    if "source_phn_2023_sa2_2021_present_flag" in joined.columns:
        join_rows.append({"check_name": "sa2_rows_with_phn_2023", "value": int(joined["source_phn_2023_sa2_2021_present_flag"].sum()), "status": "info", "notes": ""})
    if "source_lga_2021_area_bridge_present_flag" in joined.columns:
        join_rows.append({"check_name": "sa2_rows_with_lga_2021", "value": int(joined["source_lga_2021_area_bridge_present_flag"].sum()), "status": "info", "notes": ""})

    unmatched_records = []
    for flag, label in [
        ("source_phn_2017_sa2_2021_present_flag", "phn_2017"),
        ("source_phn_2023_sa2_2021_present_flag", "phn_2023"),
        ("source_lga_2021_area_bridge_present_flag", "lga_2021_area"),
    ]:
        if flag in joined.columns:
            subset = joined.loc[~joined[flag].fillna(False), [c for c in ["sa2_code_2021", "sa2_name_2021", "sa3_code_2021", "sa3_name_2021", "state_name_2021"] if c in joined.columns]].copy()
            subset.insert(0, "layer", label)
            unmatched_records.append(subset)

    unmatched = pd.concat(unmatched_records, ignore_index=True) if unmatched_records else pd.DataFrame()

    pd.DataFrame(source_audit).to_csv(SOURCE_AUDIT_OUT, index=False, encoding="utf-8-sig")
    pd.DataFrame(bridge_audit).to_csv(BRIDGE_AUDIT_OUT, index=False, encoding="utf-8-sig")
    pd.DataFrame(join_rows).to_csv(JOIN_AUDIT_OUT, index=False, encoding="utf-8-sig")
    unmatched.to_csv(UNMATCHED_OUT, index=False, encoding="utf-8-sig")

    field_rows = []
    for c in joined.columns:
        if c.startswith("phn_") or c.startswith("dominant_lga_") or c in ["source_phn_2017_sa2_2021_present_flag", "source_phn_2023_sa2_2021_present_flag", "source_lga_2021_area_bridge_present_flag"]:
            field_rows.append({
                "column_name": c,
                "source_family": "PHN/LGA bridge context",
                "native_geography": "SA2 2021 bridge/dominant geography",
                "field_role": "context_grouping_or_bridge_metadata",
                "primary_model_use": "exclude_by_default_from_primary_model; use for stratification, grouping, reporting, or future bridge joins",
                "notes": "Dominant LGA is area-based. Do not treat LGA context as direct SA2 service exposure without a source-specific method.",
            })
    pd.DataFrame(field_rows).to_csv(FIELD_DICT_OUT, index=False, encoding="utf-8-sig")

    METHOD_NOTE_OUT.write_text(
        "# PHN and LGA bridge context layer v05\n\n"
        "This layer adds validated PHN and LGA context fields to the SA2 master where source bridges can be built.\n\n"
        "PHN 2017 is the preferred PHN boundary context for the 2021/2022 evidence window. "
        "PHN 2023 may optionally be added as current-boundary context.\n\n"
        "The LGA field is a dominant-LGA assignment derived from the ABS 2021 LGA allocation file and 2021 Mesh Block to SA2 allocation. "
        "Where an SA2 spans multiple LGAs, the dominant LGA is selected by Mesh Block area share. The full area bridge is retained for future source-specific allocation.\n\n"
        "These fields are primarily bridge/context fields. They should not be treated as direct predictors without considering geography, boundary and allocation assumptions.\n",
        encoding="utf-8",
    )

    joined.to_parquet(OUT_MASTER_PARQUET, index=False)
    joined.to_csv(OUT_MASTER_CSV, index=False, encoding="utf-8-sig")

    print("\nCreated PHN/LGA outputs:")
    print(f"  PHN full bridge:      {PHN_FULL_BRIDGE_OUT}")
    print(f"  PHN dominant bridge:  {PHN_DOMINANT_BRIDGE_OUT}")
    if args.include_phn_2023:
        print(f"  PHN 2023 full bridge:     {PHN_2023_FULL_BRIDGE_OUT}")
        print(f"  PHN 2023 dominant bridge: {PHN_2023_DOMINANT_BRIDGE_OUT}")
    print(f"  LGA full bridge:      {LGA_FULL_BRIDGE_OUT}")
    print(f"  LGA dominant bridge:  {LGA_DOMINANT_BRIDGE_OUT}")
    print(f"  v05 master parquet:   {OUT_MASTER_PARQUET}")
    print(f"  v05 master csv:       {OUT_MASTER_CSV}")
    print("\nCreated audits:")
    print(f"  {SOURCE_AUDIT_OUT}")
    print(f"  {BRIDGE_AUDIT_OUT}")
    print(f"  {JOIN_AUDIT_OUT}")
    print(f"  {UNMATCHED_OUT}")
    print(f"  {FIELD_DICT_OUT}")
    print(f"  {METHOD_NOTE_OUT}")

    print("\nBridge audit summary:")
    print(pd.DataFrame(bridge_audit).to_string(index=False))
    print("\nJoin audit summary:")
    print(pd.DataFrame(join_rows).to_string(index=False))

    print("\nNext action:")
    print("  If PHN and LGA join counts are acceptable, proceed to source-specific acquisition for DSS/PHIDU/AEDC/housing.")
    print("  Do not model LGA/PHN codes as substantive predictors without deciding how to use geography grouping fields.")


if __name__ == "__main__":
    main()
