from pathlib import Path
from urllib.parse import urljoin
import re
import requests
import pandas as pd
from bs4 import BeautifulSoup

PROJECT_ROOT = Path(r"D:\Good Measure\MentalWellbeingbyGeography")

RAW_GEO_DIR = PROJECT_ROOT / "data" / "raw" / "abs" / "geography"
AUDIT_DIR = PROJECT_ROOT / "outputs" / "audits"

MANIFEST_PATH = AUDIT_DIR / "abs_sa2_sa3_download_manifest.csv"

ABS_PAGES = [
    "https://www.abs.gov.au/statistics/standards/australian-statistical-geography-standard-asgs/edition-3-july-2021-june-2026/access-and-downloads/allocation-files",
    "https://www.abs.gov.au/statistics/standards/australian-statistical-geography-standard-asgs/edition-3-july-2021-june-2026/access-and-downloads/correspondences",
]

DOWNLOAD_EXTENSIONS = (".xlsx", ".xls", ".csv", ".zip")

LIKELY_TERMS = [
    "allocation",
    "correspondence",
    "main",
    "structure",
    "sa2",
    "sa3",
    "2021",
    "asgs",
]


def normalise_filename(name: str) -> str:
    """Create a safe filename from a URL or link label."""
    name = name.split("?")[0].split("#")[0]
    name = name.strip().replace("%20", "_")
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", name)
    name = re.sub(r"_+", "_", name)
    return name.strip("_")


def link_score(url: str, text: str) -> int:
    """Score links by likelihood of containing SA2-SA3 hierarchy data."""
    combined = f"{url} {text}".lower()
    score = 0

    for term in LIKELY_TERMS:
        if term in combined:
            score += 1

    if "allocation" in combined:
        score += 3
    if "main" in combined and "structure" in combined:
        score += 3
    if "sa2" in combined and "sa3" in combined:
        score += 4
    if "2021" in combined:
        score += 2

    return score


def get_download_links(page_url: str) -> list[dict]:
    """Extract downloadable file links from an ABS page."""
    response = requests.get(page_url, timeout=60)
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")

    links = []
    for a in soup.find_all("a", href=True):
        href = a.get("href", "").strip()
        text = a.get_text(" ", strip=True)

        absolute_url = urljoin(page_url, href)
        lower_url = absolute_url.lower()

        if not lower_url.endswith(DOWNLOAD_EXTENSIONS):
            continue

        score = link_score(absolute_url, text)

        links.append(
            {
                "page_url": page_url,
                "link_text": text,
                "download_url": absolute_url,
                "score": score,
            }
        )

    return links


def download_file(url: str, output_dir: Path) -> Path:
    """Download one file."""
    output_dir.mkdir(parents=True, exist_ok=True)

    filename = normalise_filename(Path(url.split("?")[0]).name)

    if not filename:
        filename = "abs_download"

    output_path = output_dir / filename

    response = requests.get(url, timeout=120)
    response.raise_for_status()

    output_path.write_bytes(response.content)

    return output_path


def main() -> None:
    RAW_GEO_DIR.mkdir(parents=True, exist_ok=True)
    AUDIT_DIR.mkdir(parents=True, exist_ok=True)

    all_links = []

    for page_url in ABS_PAGES:
        print(f"Scanning ABS page: {page_url}")
        try:
            links = get_download_links(page_url)
            all_links.extend(links)
            print(f"  Found downloadable links: {len(links)}")
        except Exception as exc:
            print(f"  ERROR scanning page: {exc}")

    if not all_links:
        raise RuntimeError("No downloadable ABS links found on the configured pages.")

    links_df = pd.DataFrame(all_links)
    links_df = links_df.sort_values(
        by=["score", "download_url"],
        ascending=[False, True],
    ).drop_duplicates(subset=["download_url"])

    # Download the strongest candidates only.
    # This avoids pulling every ABS file if the page has many unrelated downloads.
    candidates = links_df.query("score >= 3").copy()

    if candidates.empty:
        print("No high-scoring candidates found. Saving link manifest only.")
        links_df["downloaded_path"] = ""
        links_df["download_status"] = "not_downloaded_low_score"
        links_df.to_csv(MANIFEST_PATH, index=False, encoding="utf-8-sig")
        print(f"Manifest saved: {MANIFEST_PATH}")
        return

    downloaded_rows = []

    print("\nDownloading candidate files:")
    for _, row in candidates.iterrows():
        url = row["download_url"]
        print(f"  Score {row['score']}: {url}")

        try:
            downloaded_path = download_file(url, RAW_GEO_DIR)
            status = "downloaded"
            print(f"    Saved: {downloaded_path}")
        except Exception as exc:
            downloaded_path = ""
            status = f"error: {exc}"
            print(f"    ERROR: {exc}")

        out_row = row.to_dict()
        out_row["downloaded_path"] = str(downloaded_path)
        out_row["download_status"] = status
        downloaded_rows.append(out_row)

    manifest_df = pd.DataFrame(downloaded_rows)
    manifest_df.to_csv(MANIFEST_PATH, index=False, encoding="utf-8-sig")

    print("\nDone.")
    print(f"Downloaded files saved to: {RAW_GEO_DIR}")
    print(f"Manifest saved to: {MANIFEST_PATH}")
    print("\nNext: inspect the downloaded files and run the SA2-SA3 spine build script.")


if __name__ == "__main__":
    main()
