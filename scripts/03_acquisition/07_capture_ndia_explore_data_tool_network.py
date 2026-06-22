from pathlib import Path
from datetime import datetime, timezone
import argparse
import csv
import hashlib
import json
import re
import subprocess
import sys
import time
from urllib.parse import urlparse, unquote

import pandas as pd

PROJECT_ROOT = Path(r"D:\Good Measure\MentalWellbeingbyGeography")

EXPLORE_URL = "https://dataresearch.ndis.gov.au/explore-data"

RAW_ROOT = PROJECT_ROOT / "data" / "raw" / "ndia" / "explore_data_tool_capture"
AUDIT_DIR = PROJECT_ROOT / "outputs" / "audits"

TARGET_TERMS = {
    "year_period": [
        "2021-22",
        "2021/22",
        "fy21/22",
        "fy 21/22",
        "q3 fy21/22",
        "q4 fy21/22",
        "q3 fy 21/22",
        "q4 fy 21/22",
        "31 march 2022",
        "30 june 2022",
        "march 2022",
        "june 2022",
        "2021",
        "2022",
    ],
    "geography": [
        "sa2",
        "sa3",
        "statistical area 2",
        "statistical area 3",
        "service district",
        "lga",
        "local government area",
    ],
    "psychosocial": [
        "psychosocial",
        "psycho-social",
        "mental health",
        "primary disability",
        "disability type",
        "disability group",
        "diagnosis",
    ],
    "participant_plan": [
        "active participants",
        "participant count",
        "participants",
        "plans",
        "plan budget",
        "committed supports",
        "average committed",
        "total committed",
        "support category",
        "support class",
    ],
    "utilisation_payment": [
        "utilisation",
        "utilization",
        "payments",
        "average payments",
        "total payments",
        "paid supports",
        "claims",
    ],
    "provider_market": [
        "provider",
        "providers",
        "active providers",
        "market",
        "market concentration",
        "concentration",
        "top 10",
        "participants per provider",
        "provider growth",
        "provider shrinkage",
    ],
    "download_api": [
        "download",
        "csv",
        "xlsx",
        "json",
        "api",
        "graphql",
        "query",
        "export",
    ],
}

SAVE_CONTENT_TYPES = [
    "application/json",
    "text/json",
    "text/csv",
    "application/csv",
    "application/vnd.ms-excel",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/zip",
    "text/plain",
    "application/javascript",
    "text/javascript",
]

SAVE_EXTENSIONS = [
    ".json",
    ".csv",
    ".xlsx",
    ".xls",
    ".zip",
    ".js",
    ".txt",
]

MAX_CAPTURE_BYTES = 60_000_000


def ensure_playwright():
    try:
        import playwright  # noqa: F401
    except ImportError:
        print("playwright is not installed. Installing now.")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "playwright"])

    try:
        from playwright.sync_api import sync_playwright  # noqa: F401
    except Exception:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "playwright"])

    try:
        subprocess.check_call([sys.executable, "-m", "playwright", "install", "chromium"])
    except Exception as exc:
        print(f"Warning: playwright browser install returned an error: {exc}")


def clean_text(value) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    text = re.sub(r"\s+", " ", text)
    return text


def slug(value, max_len=90) -> str:
    text = clean_text(value).lower()
    text = text.replace("&", " and ")
    text = re.sub(r"[^a-z0-9]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    return (text or "blank")[:max_len].strip("_")


def now_utc():
    return datetime.now(timezone.utc).isoformat()


def make_run_dir(run_label: str | None) -> Path:
    if run_label:
        label = slug(run_label, 80)
    else:
        label = datetime.now().strftime("%Y%m%d_%H%M%S")

    run_dir = RAW_ROOT / label
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def normalise_for_search(value: str) -> str:
    text = clean_text(value).lower()
    text = text.replace("–", "-").replace("—", "-")
    return text


def score_text(value: str) -> tuple[int, dict]:
    text = normalise_for_search(value)

    hits = {}
    score = 0

    for group, terms in TARGET_TERMS.items():
        group_hits = []

        for term in terms:
            if normalise_for_search(term) in text:
                group_hits.append(term)

        if group_hits:
            hits[group] = sorted(set(group_hits))
            score += len(group_hits)

    return score, hits


def safe_filename_from_url(url: str, content_type: str = "", index: int = 0) -> str:
    parsed = urlparse(url)
    name = Path(unquote(parsed.path)).name

    if not name or "." not in name:
        h = hashlib.sha256(url.encode("utf-8")).hexdigest()[:12]
        ext = ".txt"

        ct = content_type.lower()

        if "json" in ct:
            ext = ".json"
        elif "csv" in ct:
            ext = ".csv"
        elif "spreadsheet" in ct or "excel" in ct:
            ext = ".xlsx"
        elif "zip" in ct:
            ext = ".zip"
        elif "javascript" in ct:
            ext = ".js"

        name = f"response_{index:05d}_{h}{ext}"

    name = re.sub(r"[^A-Za-z0-9._-]+", "_", name)
    name = re.sub(r"_+", "_", name).strip("_")

    if len(name) > 160:
        stem = Path(name).stem[:120]
        suffix = Path(name).suffix[:20]
        h = hashlib.sha256(url.encode("utf-8")).hexdigest()[:10]
        name = f"{stem}_{h}{suffix}"

    return name


def content_length_from_headers(headers: dict) -> int | None:
    value = headers.get("content-length") or headers.get("Content-Length")

    if not value:
        return None

    try:
        return int(value)
    except Exception:
        return None


def should_capture_response(url: str, content_type: str, headers: dict) -> bool:
    lower_url = url.lower()
    lower_ct = content_type.lower()

    if any(ext in lower_url for ext in SAVE_EXTENSIONS):
        return True

    if any(ct in lower_ct for ct in SAVE_CONTENT_TYPES):
        return True

    if any(term in lower_url for term in ["api", "download", "export", "graphql", "data", "market"]):
        return True

    length = content_length_from_headers(headers)

    if length is not None and length > MAX_CAPTURE_BYTES:
        return False

    return False


def decode_body(body: bytes) -> str:
    for enc in ["utf-8-sig", "utf-8", "cp1252", "latin-1"]:
        try:
            return body.decode(enc)
        except Exception:
            continue
    return ""


def summarise_dom(page) -> dict:
    return page.evaluate(
        """
        () => {
            const visibleText = document.body ? document.body.innerText : "";

            const anchors = Array.from(document.querySelectorAll("a")).map((a, i) => ({
                index: i,
                text: (a.innerText || a.textContent || "").trim(),
                href: a.href || "",
                ariaLabel: a.getAttribute("aria-label") || ""
            }));

            const buttons = Array.from(document.querySelectorAll("button")).map((b, i) => ({
                index: i,
                text: (b.innerText || b.textContent || "").trim(),
                ariaLabel: b.getAttribute("aria-label") || "",
                title: b.getAttribute("title") || "",
                type: b.getAttribute("type") || ""
            }));

            const inputs = Array.from(document.querySelectorAll("input, select, textarea")).map((el, i) => ({
                index: i,
                tag: el.tagName,
                type: el.getAttribute("type") || "",
                name: el.getAttribute("name") || "",
                id: el.getAttribute("id") || "",
                placeholder: el.getAttribute("placeholder") || "",
                ariaLabel: el.getAttribute("aria-label") || "",
                value: el.value || ""
            }));

            const scripts = Array.from(document.querySelectorAll("script[src]")).map((s, i) => ({
                index: i,
                src: s.src
            }));

            return {
                url: window.location.href,
                title: document.title,
                visibleTextPreview: visibleText.slice(0, 20000),
                anchors,
                buttons,
                inputs,
                scripts
            };
        }
        """
    )


def write_csv(path: Path, rows: list[dict]):
    if not rows:
        pd.DataFrame().to_csv(path, index=False, encoding="utf-8-sig")
        return

    pd.DataFrame(rows).to_csv(path, index=False, encoding="utf-8-sig")


def main():
    parser = argparse.ArgumentParser(
        description="Capture NDIS Explore Data Tool network/API traffic for SA2/SA3 2021-22 source discovery."
    )

    parser.add_argument(
        "--capture-seconds",
        type=int,
        default=420,
        help="How long to keep browser open for manual interaction.",
    )

    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run browser headless. Not recommended for manual capture.",
    )

    parser.add_argument(
        "--run-label",
        default="",
        help="Optional folder label for this capture run.",
    )

    parser.add_argument(
        "--slowmo",
        type=int,
        default=0,
        help="Playwright slow motion milliseconds.",
    )

    parser.add_argument(
        "--timeout-ms",
        type=int,
        default=120000,
        help="Page load timeout.",
    )

    args = parser.parse_args()

    ensure_playwright()

    run_dir = make_run_dir(args.run_label or None)

    responses_dir = run_dir / "responses"
    downloads_dir = run_dir / "downloads"
    screenshots_dir = run_dir / "screenshots"

    responses_dir.mkdir(parents=True, exist_ok=True)
    downloads_dir.mkdir(parents=True, exist_ok=True)
    screenshots_dir.mkdir(parents=True, exist_ok=True)
    AUDIT_DIR.mkdir(parents=True, exist_ok=True)

    requests_rows = []
    responses_rows = []
    candidate_rows = []
    candidate_hit_rows = []
    download_rows = []

    response_counter = {"n": 0}

    print("\nNDIS Explore Data Tool capture")
    print(f"Run folder: {run_dir}")
    print("\nWhen the browser opens, use the Explore Data Tool manually.")
    print("Target selections to try:")
    print("  Period: Q4 FY21/22 or Q3 FY21/22")
    print("  Geography: SA2 first, SA3 second")
    print("  Data: participant, market, provider")
    print("  Terms to look for: psychosocial, support class/category, utilisation, payments, providers")
    print("\nThe script will capture network/API responses and downloads while you interact.\n")

    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=args.headless, slow_mo=args.slowmo)

        context = browser.new_context(
            viewport={"width": 1700, "height": 1100},
            accept_downloads=True,
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/126 Safari/537.36"
            ),
        )

        page = context.new_page()
        page.set_default_timeout(args.timeout_ms)

        def on_request(request):
            try:
                post_data = request.post_data or ""
                score, hits = score_text(f"{request.url} {post_data}")

                requests_rows.append(
                    {
                        "timestamp_utc": now_utc(),
                        "method": request.method,
                        "url": request.url,
                        "resource_type": request.resource_type,
                        "post_data_preview": post_data[:5000],
                        "target_score": score,
                        "target_hits_json": json.dumps(hits, ensure_ascii=False),
                    }
                )
            except Exception as exc:
                requests_rows.append(
                    {
                        "timestamp_utc": now_utc(),
                        "method": "",
                        "url": getattr(request, "url", ""),
                        "resource_type": "",
                        "post_data_preview": "",
                        "target_score": "",
                        "target_hits_json": "",
                        "error": str(exc),
                    }
                )

        def on_response(response):
            response_counter["n"] += 1
            idx = response_counter["n"]

            try:
                url = response.url
                status = response.status
                headers = response.headers
                content_type = headers.get("content-type", "")
                request = response.request
                resource_type = request.resource_type if request else ""

                base_text = f"{url} {content_type}"
                score, hits = score_text(base_text)

                saved_path = ""
                body_text_preview = ""
                body_size = ""
                body_score = 0
                body_hits = {}
                capture_status = "not_captured"
                capture_error = ""

                if should_capture_response(url, content_type, headers):
                    try:
                        length = content_length_from_headers(headers)

                        if length is not None and length > MAX_CAPTURE_BYTES:
                            capture_status = "skipped_too_large"
                        else:
                            body = response.body()
                            body_size = len(body)

                            if body_size <= MAX_CAPTURE_BYTES:
                                filename = safe_filename_from_url(url, content_type, idx)
                                out_path = responses_dir / filename
                                out_path.write_bytes(body)
                                saved_path = str(out_path)
                                capture_status = "captured"

                                text = decode_body(body)
                                body_text_preview = text[:5000]
                                body_score, body_hits = score_text(text)

                                score += body_score

                                merged_hits = dict(hits)
                                for k, v in body_hits.items():
                                    merged_hits.setdefault(k, [])
                                    merged_hits[k] = sorted(set(merged_hits[k] + v))
                                hits = merged_hits
                            else:
                                capture_status = "skipped_too_large_after_read"

                    except Exception as exc:
                        capture_status = "capture_failed"
                        capture_error = str(exc)

                responses_rows.append(
                    {
                        "timestamp_utc": now_utc(),
                        "response_index": idx,
                        "status": status,
                        "url": url,
                        "resource_type": resource_type,
                        "content_type": content_type,
                        "content_length_header": content_length_from_headers(headers),
                        "body_size_bytes": body_size,
                        "saved_path": saved_path,
                        "capture_status": capture_status,
                        "capture_error": capture_error,
                        "target_score": score,
                        "target_hits_json": json.dumps(hits, ensure_ascii=False),
                        "body_text_preview": body_text_preview,
                    }
                )

                if score > 0 or saved_path:
                    candidate_rows.append(
                        {
                            "timestamp_utc": now_utc(),
                            "response_index": idx,
                            "status": status,
                            "url": url,
                            "resource_type": resource_type,
                            "content_type": content_type,
                            "saved_path": saved_path,
                            "capture_status": capture_status,
                            "target_score": score,
                            "target_hits_json": json.dumps(hits, ensure_ascii=False),
                        }
                    )

                    for group, terms in hits.items():
                        candidate_hit_rows.append(
                            {
                                "response_index": idx,
                                "url": url,
                                "hit_group": group,
                                "hit_terms": " | ".join(terms),
                                "saved_path": saved_path,
                            }
                        )

            except Exception as exc:
                responses_rows.append(
                    {
                        "timestamp_utc": now_utc(),
                        "response_index": idx,
                        "status": "",
                        "url": getattr(response, "url", ""),
                        "resource_type": "",
                        "content_type": "",
                        "content_length_header": "",
                        "body_size_bytes": "",
                        "saved_path": "",
                        "capture_status": "handler_failed",
                        "capture_error": str(exc),
                        "target_score": "",
                        "target_hits_json": "",
                        "body_text_preview": "",
                    }
                )

        def on_download(download):
            try:
                suggested = download.suggested_filename
                filename = safe_filename_from_url(suggested or download.url, index=len(download_rows) + 1)
                out_path = downloads_dir / filename
                download.save_as(out_path)

                score, hits = score_text(f"{download.url} {suggested}")

                download_rows.append(
                    {
                        "timestamp_utc": now_utc(),
                        "url": download.url,
                        "suggested_filename": suggested,
                        "saved_path": str(out_path),
                        "target_score": score,
                        "target_hits_json": json.dumps(hits, ensure_ascii=False),
                    }
                )

                print(f"Downloaded: {out_path}")

            except Exception as exc:
                download_rows.append(
                    {
                        "timestamp_utc": now_utc(),
                        "url": getattr(download, "url", ""),
                        "suggested_filename": "",
                        "saved_path": "",
                        "target_score": "",
                        "target_hits_json": "",
                        "error": str(exc),
                    }
                )

        page.on("request", on_request)
        page.on("response", on_response)
        page.on("download", on_download)

        print(f"Opening: {EXPLORE_URL}")

        page.goto(EXPLORE_URL, wait_until="load", timeout=args.timeout_ms)

        try:
            page.wait_for_load_state("domcontentloaded", timeout=args.timeout_ms)
        except Exception:
            pass

        time.sleep(5)

        try:
            dom = summarise_dom(page)
            (run_dir / "ndia_explore_dom_initial.json").write_text(
                json.dumps(dom, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception as exc:
            print(f"Could not save initial DOM summary: {exc}")

        try:
            page.screenshot(
                path=screenshots_dir / "ndia_explore_initial.png",
                full_page=True,
            )
        except Exception as exc:
            print(f"Could not save initial screenshot: {exc}")

        print("\nCapture is running.")
        print(f"Interact with the browser for {args.capture_seconds} seconds.")
        print("When finished early, you can close the browser window; the script will still write logs.\n")

        started = time.time()

        try:
            while time.time() - started < args.capture_seconds:
                remaining = int(args.capture_seconds - (time.time() - started))

                if remaining % 30 == 0:
                    print(f"Capture remaining: {remaining} seconds")

                time.sleep(1)

                if page.is_closed():
                    break

        except KeyboardInterrupt:
            print("Interrupted by user. Writing captured logs.")

        if not page.is_closed():
            try:
                dom = summarise_dom(page)
                (run_dir / "ndia_explore_dom_final.json").write_text(
                    json.dumps(dom, indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )
            except Exception as exc:
                print(f"Could not save final DOM summary: {exc}")

            try:
                page.screenshot(
                    path=screenshots_dir / "ndia_explore_final.png",
                    full_page=True,
                )
            except Exception as exc:
                print(f"Could not save final screenshot: {exc}")

        try:
            browser.close()
        except Exception:
            pass

    requests_csv = run_dir / "ndia_explore_network_requests.csv"
    responses_csv = run_dir / "ndia_explore_network_responses.csv"
    candidates_csv = run_dir / "ndia_explore_candidate_responses.csv"
    hits_csv = run_dir / "ndia_explore_candidate_hits.csv"
    downloads_csv = run_dir / "ndia_explore_downloads.csv"

    write_csv(requests_csv, requests_rows)
    write_csv(responses_csv, responses_rows)
    write_csv(candidates_csv, candidate_rows)
    write_csv(hits_csv, candidate_hit_rows)
    write_csv(downloads_csv, download_rows)

    latest_summary_csv = AUDIT_DIR / "ndia_explore_latest_capture_candidate_summary.csv"

    candidates = pd.DataFrame(candidate_rows)

    if not candidates.empty:
        candidates = candidates.sort_values(
            ["target_score", "status"],
            ascending=[False, True],
        )
        candidates.to_csv(latest_summary_csv, index=False, encoding="utf-8-sig")
    else:
        pd.DataFrame().to_csv(latest_summary_csv, index=False, encoding="utf-8-sig")

    print("\nCreated NDIA Explore Data Tool capture outputs:")
    print(f"  Run folder: {run_dir}")
    print(f"  Requests:   {requests_csv}")
    print(f"  Responses:  {responses_csv}")
    print(f"  Candidates: {candidates_csv}")
    print(f"  Hits:       {hits_csv}")
    print(f"  Downloads:  {downloads_csv}")
    print(f"  Latest summary: {latest_summary_csv}")

    print("\nCandidate response summary:")
    if candidates.empty:
        print("  No candidate responses detected. Try running again and interact with the Download Manager.")
    else:
        show_cols = [
            "response_index",
            "status",
            "resource_type",
            "content_type",
            "target_score",
            "url",
            "saved_path",
        ]

        print(candidates[show_cols].head(30).to_string(index=False))

    print("\nNext step:")
    print("  Paste the Candidate response summary, or upload:")
    print(f"  {latest_summary_csv}")


if __name__ == "__main__":
    main()
