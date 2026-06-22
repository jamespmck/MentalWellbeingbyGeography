from pathlib import Path
from urllib.parse import urljoin, urlparse, unquote
from datetime import datetime, timezone
import argparse
import hashlib
import json
import re
import time

import pandas as pd
import requests

PROJECT_ROOT = Path(r"D:\Good Measure\MentalWellbeingbyGeography")

CAPTURE_ROOT = PROJECT_ROOT / "data" / "raw" / "ndia" / "explore_data_tool_capture"
RAW_OUT = PROJECT_ROOT / "data" / "raw" / "ndia" / "explore_data_tool_historical_probe"
AUDIT_DIR = PROJECT_ROOT / "outputs" / "audits"

BASE_BUILD_URL = "https://dataresearch.ndis.gov.au/sites/default/files/react_extract/public_market_data/build/"
BASE_DATA_URL = BASE_BUILD_URL + "data/"

DEFAULT_TARGET_PERIODS = ["2122_q4", "2122_q3", "2223_q1", "2122_q2", "2122_q1"]

TARGET_TERMS = {
    "period_2021_22": [
        "2122_q4", "2122_q3", "2122_q2", "2122_q1", "2021-22", "2021/22", "fy21/22",
        "q4 fy21/22", "q3 fy21/22", "june 2022", "march 2022", "2022", "2021",
    ],
    "geography": [
        "sa2", "sa3", "statistical area 2", "statistical area 3", "lga", "local government area",
        "service district", "region",
    ],
    "psychosocial": [
        "psychosocial", "psycho-social", "mental health", "primary disability", "disability group", "diagnosis",
    ],
    "participant_plan": [
        "active participants", "participants", "participant", "plan budget", "committed supports",
        "average committed", "total committed", "support class", "support category",
    ],
    "utilisation_payment": [
        "utilisation", "utilization", "payments", "payment", "average payments", "total payments",
    ],
    "provider_market": [
        "provider", "providers", "active providers", "market", "market concentration", "concentration",
        "top 10", "participants per provider", "provider growth", "provider shrinkage",
    ],
}


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def clean_text(value) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    text = re.sub(r"\s+", " ", text)
    return text


def slug(value, max_len=120) -> str:
    text = clean_text(value).lower()
    text = text.replace("&", " and ")
    text = re.sub(r"[^a-z0-9]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    return (text or "blank")[:max_len].strip("_")


def normalise_period(value: str) -> str:
    value = clean_text(value).lower().replace("-", "_").replace("/", "_")
    value = value.replace("fy", "")
    value = re.sub(r"[^0-9q]+", "_", value)
    value = re.sub(r"_+", "_", value).strip("_")
    return value


def decode_bytes(body: bytes) -> str:
    for enc in ["utf-8-sig", "utf-8", "cp1252", "latin-1"]:
        try:
            return body.decode(enc)
        except Exception:
            continue
    return ""


def score_text(value: str) -> tuple[int, dict]:
    text = clean_text(value).lower().replace("–", "-").replace("—", "-")
    hits = {}
    score = 0

    for group, terms in TARGET_TERMS.items():
        matched = []
        for term in terms:
            if clean_text(term).lower() in text:
                matched.append(term)
        if matched:
            hits[group] = sorted(set(matched))
            score += len(matched)

    return score, hits


def latest_capture_dir() -> Path | None:
    if not CAPTURE_ROOT.exists():
        return None

    candidates = [p for p in CAPTURE_ROOT.iterdir() if p.is_dir()]
    if not candidates:
        return None

    return max(candidates, key=lambda p: p.stat().st_mtime)


def collect_source_files(capture_dirs: list[Path]) -> list[Path]:
    files = []

    for d in capture_dirs:
        if not d.exists():
            continue
        for pattern in ["responses/*.js", "responses/*.json", "responses/*.txt", "*.json", "*.csv"]:
            files.extend(d.glob(pattern))

    # Keep only files likely to contain app paths or endpoint logs.
    keep = []
    for p in files:
        name = p.name.lower()
        if p.suffix.lower() in {".js", ".json", ".txt", ".csv"}:
            keep.append(p)

    return sorted(set(keep))


def extract_period_tokens(text: str) -> set[str]:
    tokens = set(re.findall(r"\b\d{4}_q[1-4]\b", text.lower()))
    tokens.update(re.findall(r"\b\d{2}\d{2}_q[1-4]\b", text.lower()))
    return tokens


def extract_candidate_paths(text: str) -> set[str]:
    paths = set()

    # Full URLs to the React build data folder.
    url_re = re.compile(
        r"https://dataresearch\.ndis\.gov\.au/sites/default/files/react_extract/public_market_data/build/[^\"'`<>\s)]+",
        flags=re.IGNORECASE,
    )
    paths.update(url_re.findall(text))

    # Relative build/data paths in JS strings.
    rel_re = re.compile(
        r"(?:(?:\.\./)+|/)?data/[A-Za-z0-9_./?=&%\-]+",
        flags=re.IGNORECASE,
    )
    paths.update(rel_re.findall(text))

    # Paths sometimes appear without the data/ prefix but with known folders.
    folder_re = re.compile(
        r"(?:participant_info|market|provider|providers|payments|plan|utilisation|utilization|filters)/[A-Za-z0-9_./?=&%\-]+",
        flags=re.IGNORECASE,
    )
    paths.update(folder_re.findall(text))

    cleaned = set()
    for p in paths:
        p = p.strip().strip("'\"`)")
        p = p.replace("\\/", "/")
        p = p.replace("&amp;", "&")
        if ".map" in p and not p.endswith(".map"):
            p = p.split(".map", 1)[0] + ".map"
        if any(ext in p.lower() for ext in [".json", ".csv", ".xlsx", ".zip", ".js"]):
            cleaned.add(p)

    return cleaned


def to_full_url(path: str) -> str | None:
    path = path.strip().strip("'\"`)")
    path = path.replace("\\/", "/")

    if not path:
        return None

    if path.startswith("http://") or path.startswith("https://"):
        return path

    while path.startswith("../"):
        path = path[3:]

    if path.startswith("/"):
        return urljoin("https://dataresearch.ndis.gov.au", path)

    if path.startswith("data/"):
        return urljoin(BASE_BUILD_URL, path)

    if re.match(r"^(participant_info|market|provider|providers|payments|plan|utilisation|utilization|filters)/", path, flags=re.I):
        return urljoin(BASE_DATA_URL, path)

    return None


def expand_period_candidates(urls: set[str], target_periods: list[str], discovered_periods: set[str]) -> set[str]:
    expanded = set(urls)

    all_source_periods = set(discovered_periods)
    all_source_periods.update(re.findall(r"\b\d{4}_q[1-4]\b", "\n".join(urls).lower()))

    for url in list(urls):
        for source_period in all_source_periods:
            if source_period in url.lower():
                for target in target_periods:
                    expanded.add(re.sub(source_period, target, url, flags=re.IGNORECASE))

    # Known endpoint seen in the capture and useful variants.
    for period in target_periods:
        expanded.add(urljoin(BASE_DATA_URL, f"participant_info/{period}/national/all_region.json"))
        expanded.add(urljoin(BASE_DATA_URL, f"participant_info/{period}/state/all_region.json"))
        expanded.add(urljoin(BASE_DATA_URL, f"participant_info/{period}/sa2/all_region.json"))
        expanded.add(urljoin(BASE_DATA_URL, f"participant_info/{period}/sa3/all_region.json"))
        expanded.add(urljoin(BASE_DATA_URL, f"market/{period}/national/all_region.json"))
        expanded.add(urljoin(BASE_DATA_URL, f"market/{period}/sa2/all_region.json"))
        expanded.add(urljoin(BASE_DATA_URL, f"market/{period}/sa3/all_region.json"))
        expanded.add(urljoin(BASE_DATA_URL, f"provider/{period}/national/all_region.json"))
        expanded.add(urljoin(BASE_DATA_URL, f"provider/{period}/sa2/all_region.json"))
        expanded.add(urljoin(BASE_DATA_URL, f"provider/{period}/sa3/all_region.json"))
        expanded.add(urljoin(BASE_DATA_URL, f"providers/{period}/national/all_region.json"))
        expanded.add(urljoin(BASE_DATA_URL, f"providers/{period}/sa2/all_region.json"))
        expanded.add(urljoin(BASE_DATA_URL, f"providers/{period}/sa3/all_region.json"))

    return expanded


def safe_out_path(url: str, out_dir: Path) -> Path:
    parsed = urlparse(url)
    path = unquote(parsed.path).strip("/")
    path = re.sub(r"[^A-Za-z0-9._/-]+", "_", path)
    pieces = path.split("/")

    # Keep the meaningful tail under build/data, but avoid enormous paths.
    if "data" in pieces:
        idx = pieces.index("data")
        pieces = pieces[idx:]
    else:
        pieces = pieces[-5:]

    filename = pieces[-1] if pieces else "response.json"
    parent = out_dir.joinpath(*pieces[:-1])
    parent.mkdir(parents=True, exist_ok=True)

    if not Path(filename).suffix:
        filename += ".txt"

    out_path = parent / filename

    if out_path.exists():
        h = hashlib.sha256(url.encode("utf-8")).hexdigest()[:10]
        out_path = parent / f"{Path(filename).stem}_{h}{Path(filename).suffix}"

    return out_path


def summarise_json_file(path: Path) -> dict:
    try:
        text = path.read_text(encoding="utf-8-sig", errors="replace")
        data = json.loads(text)
    except Exception as exc:
        return {"json_status": "fail", "json_error": str(exc)}

    summary = {"json_status": "pass", "json_type": type(data).__name__}

    if isinstance(data, dict):
        summary["top_level_keys"] = " | ".join(list(map(str, data.keys()))[:50])
        summary["top_level_key_count"] = len(data)
    elif isinstance(data, list):
        summary["top_level_list_len"] = len(data)
        if data and isinstance(data[0], dict):
            summary["first_row_keys"] = " | ".join(list(map(str, data[0].keys()))[:50])
    return summary


def inspect_saved_file(path: Path) -> dict:
    suffix = path.suffix.lower()
    result = {
        "saved_file_size_bytes": path.stat().st_size if path.exists() else 0,
        "saved_file_suffix": suffix,
    }

    try:
        if suffix == ".json":
            result.update(summarise_json_file(path))
            text = path.read_text(encoding="utf-8-sig", errors="replace")[:200000]
            score, hits = score_text(text)
            result["content_target_score"] = score
            result["content_target_hits_json"] = json.dumps(hits, ensure_ascii=False)
        elif suffix == ".csv":
            df = pd.read_csv(path, dtype=str, nrows=500, low_memory=False)
            result["csv_sample_rows"] = len(df)
            result["csv_column_count"] = len(df.columns)
            result["csv_columns"] = " | ".join(map(str, df.columns[:80]))
            text = " ".join(map(str, df.columns)) + " " + df.head(20).to_csv(index=False)
            score, hits = score_text(text)
            result["content_target_score"] = score
            result["content_target_hits_json"] = json.dumps(hits, ensure_ascii=False)
    except Exception as exc:
        result["inspect_error"] = str(exc)

    return result


def probe_urls(urls: list[str], out_dir: Path, sleep_seconds: float) -> pd.DataFrame:
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0"})

    for i, url in enumerate(urls, start=1):
        print(f"[{i}/{len(urls)}] {url}")
        row = {
            "probe_timestamp_utc": now_utc(),
            "probe_index": i,
            "url": url,
            "status_code": "",
            "content_type": "",
            "content_length": "",
            "saved_path": "",
            "request_status": "",
            "error": "",
        }

        try:
            response = session.get(url, timeout=90)
            row["status_code"] = response.status_code
            row["content_type"] = response.headers.get("content-type", "")
            row["content_length"] = response.headers.get("content-length", "")

            if response.status_code == 200:
                out_path = safe_out_path(url, out_dir)
                out_path.write_bytes(response.content)
                row["saved_path"] = str(out_path)
                row["request_status"] = "downloaded"
                row.update(inspect_saved_file(out_path))
            else:
                row["request_status"] = "not_available"

        except Exception as exc:
            row["request_status"] = "failed"
            row["error"] = str(exc)

        rows.append(row)
        if sleep_seconds > 0:
            time.sleep(sleep_seconds)

    return pd.DataFrame(rows)


def main():
    parser = argparse.ArgumentParser(
        description="Probe historical NDIS Explore Data Tool static JSON endpoints for 2021-22 SA2/SA3 data."
    )
    parser.add_argument(
        "--periods",
        default=",".join(DEFAULT_TARGET_PERIODS),
        help="Comma-separated period tokens, e.g. 2122_q4,2122_q3",
    )
    parser.add_argument(
        "--capture-dir",
        default="",
        help="Optional specific Explore Data Tool capture folder to scan. Defaults to latest capture folder.",
    )
    parser.add_argument(
        "--sleep-seconds",
        type=float,
        default=0.15,
        help="Delay between endpoint probes.",
    )
    parser.add_argument(
        "--max-probes",
        type=int,
        default=0,
        help="Optional cap on number of URLs to probe for testing. 0 means all.",
    )
    args = parser.parse_args()

    target_periods = [normalise_period(p) for p in args.periods.split(",") if p.strip()]

    AUDIT_DIR.mkdir(parents=True, exist_ok=True)
    RAW_OUT.mkdir(parents=True, exist_ok=True)

    if args.capture_dir:
        capture_dirs = [Path(args.capture_dir)]
    else:
        latest = latest_capture_dir()
        if latest is None:
            raise FileNotFoundError(f"No capture folders found under {CAPTURE_ROOT}")
        capture_dirs = [latest]

    print("Scanning capture folders:")
    for d in capture_dirs:
        print(f"  {d}")

    files = collect_source_files(capture_dirs)
    print(f"Source files scanned: {len(files)}")

    discovered_paths = set()
    discovered_periods = set()
    source_rows = []

    for f in files:
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except Exception:
            try:
                text = f.read_text(encoding="cp1252", errors="replace")
            except Exception as exc:
                source_rows.append({"file_path": str(f), "status": "fail", "error": str(exc)})
                continue

        periods = extract_period_tokens(text)
        paths = extract_candidate_paths(text)
        score, hits = score_text(text[:500000])

        discovered_periods.update(periods)
        discovered_paths.update(paths)

        source_rows.append(
            {
                "file_path": str(f),
                "status": "pass",
                "file_size_bytes": f.stat().st_size,
                "period_tokens_found": " | ".join(sorted(periods)),
                "candidate_path_count": len(paths),
                "target_score": score,
                "target_hits_json": json.dumps(hits, ensure_ascii=False),
            }
        )

    source_audit = pd.DataFrame(source_rows)
    source_audit_path = AUDIT_DIR / "ndia_explore_historical_probe_source_scan.csv"
    source_audit.to_csv(source_audit_path, index=False, encoding="utf-8-sig")

    full_urls = set()
    for path in discovered_paths:
        url = to_full_url(path)
        if url:
            full_urls.add(url)

    expanded = expand_period_candidates(full_urls, target_periods, discovered_periods)

    # Prioritise target period URLs, JSON, and likely data endpoints.
    def priority(url: str):
        u = url.lower()
        period_hit = any(p in u for p in target_periods)
        json_hit = u.endswith(".json")
        data_hit = "/build/data/" in u
        geo_hit = any(g in u for g in ["sa2", "sa3", "region", "lga"])
        return (not period_hit, not data_hit, not json_hit, not geo_hit, u)

    probe_urls_list = sorted(expanded, key=priority)

    if args.max_probes and args.max_probes > 0:
        probe_urls_list = probe_urls_list[: args.max_probes]

    candidate_url_audit = pd.DataFrame(
        [{"url": u, "target_period_url": any(p in u.lower() for p in target_periods)} for u in probe_urls_list]
    )
    candidate_url_path = AUDIT_DIR / "ndia_explore_historical_probe_candidate_urls.csv"
    candidate_url_audit.to_csv(candidate_url_path, index=False, encoding="utf-8-sig")

    run_label = datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + "_".join(target_periods[:3])
    run_out_dir = RAW_OUT / run_label

    print(f"Discovered period tokens in captured app/logs: {', '.join(sorted(discovered_periods)) or 'none'}")
    print(f"Target periods: {', '.join(target_periods)}")
    print(f"Candidate URLs to probe: {len(probe_urls_list)}")
    print(f"Download folder: {run_out_dir}")

    probe_audit = probe_urls(probe_urls_list, run_out_dir, args.sleep_seconds)
    probe_audit_path = AUDIT_DIR / "ndia_explore_historical_probe_results.csv"
    probe_audit.to_csv(probe_audit_path, index=False, encoding="utf-8-sig")

    hits = probe_audit[
        probe_audit["request_status"].eq("downloaded")
        & probe_audit["url"].str.lower().apply(lambda x: any(p in x for p in target_periods))
    ].copy()

    if "content_target_score" in hits.columns:
        hits["content_target_score_num"] = pd.to_numeric(hits["content_target_score"], errors="coerce").fillna(0)
        hits = hits.sort_values(["content_target_score_num", "url"], ascending=[False, True])

    hits_path = AUDIT_DIR / "ndia_explore_historical_probe_target_period_hits.csv"
    hits.to_csv(hits_path, index=False, encoding="utf-8-sig")

    print("\nCreated audits:")
    print(f"  {source_audit_path}")
    print(f"  {candidate_url_path}")
    print(f"  {probe_audit_path}")
    print(f"  {hits_path}")

    print("\nTarget-period downloads:")
    if hits.empty:
        print("  None found. The current app build may not expose 2021-22 data files by static JSON URL.")
    else:
        show_cols = [
            "status_code", "content_type", "content_target_score", "url", "saved_path",
            "json_type", "top_level_keys", "top_level_list_len", "first_row_keys",
        ]
        existing_cols = [c for c in show_cols if c in hits.columns]
        print(hits[existing_cols].head(40).to_string(index=False))

    print("\nNext step:")
    print(f"  Paste or upload: {hits_path}")
    print("  If target-period hits exist, we can write the processor for those JSON files.")


if __name__ == "__main__":
    main()
