from pathlib import Path
from urllib.parse import urljoin
import re
import requests
import pandas as pd
from bs4 import BeautifulSoup

PROJECT_ROOT = Path(r"D:\Good Measure\MentalWellbeingbyGeography")

RAW_SEIFA_DIR = PROJECT_ROOT / "data" / "raw" / "abs" / "seifa"
AUDIT_DIR = PROJECT_ROOT / "outputs" / "audits"
MANIFEST_PATH = AUDIT_DIR / "abs_seifa_2021_download_manifest.csv"

ABS_SEIFA_PAGE = (
    "https://www.abs.gov.au/statistics/people/people-and-communities/"
    "socio-economic-indexes-areas-seifa-australia/latest-release"
)

DOWNLOAD_EXTENSIONS = (".xlsx", ".xls", ".csv", ".zip")

LIKELY_TERMS = [
    "seifa",
    "2021",
    "sa2",
    "statistical area level 2",
    "index",
    "indexes",
    "irsd",
    "irsad",
    "ier",
    "ieo",
    "data",
    "datapack",
    "data cube",
    "datacube",
]


def normalise_filename(name: str) -> str:
    """Create a safe filename from a URL path."""
    name = name.split("?")[0].split("#")[0]
    name = name.strip().replace("%20", "_")
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", name)
    name = re.sub(r"_+", "_", name)
    return name.strip("_")


def link_score(url: str, text: str) -> int:
    """Score SEIFA download candidates."""
    combined = f"{url} {text}".lower()
    score = 0

    for term in LIKELY_TERMS:
        if term in combined:
            score += 1

    if "seifa" in combined:
        score += 8
    if "2021" in combined:
        score += 5
    if "sa2" in combined or "statistical area level 2" in combined:
        score += 5
    if "data" in combined or "cube" in combined or "datacube" in combined:
        score += 3
    if any(index in combined for index in ["irsd", "irsad", "ier", "ieo"]):
        score += 4

    # Prefer actual downloadable data files over images, docs or links with vague names.
    if combined.endswith((".xlsx", ".xls", ".csv", ".zip")):
        score += 3

    return score


def get_download_links(page_url: str) -> pd.DataFrame:
    """Extract downloadable file links from the ABS SEIFA page."""
    response = requests.get(
        page_url,
        timeout=60,
        headers={"User-Agent": "Mozilla/5.0"},
    )
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")

    rows = []

    for a in soup.find_all("a", href=True):
        href = a.get("href", "").strip()
        text = a.get_text(" ", strip=True)
        absolute_url = urljoin(page_url, href)
        lower_url = absolute_url.lower().split("?")[0].split("#")[0]

        if not lower_url.endswith(DOWNLOAD_EXTENSIONS):
            continue

        rows.append(
            {
                "page_url": page_url,
                "link_text": text,
                "download_url": absolute_url,
                "score": link_score(absolute_url, text),
            }
        )

    if not rows:
        return pd.DataFrame(
            columns=[
                "page_url",
                "link_text",
                "download_url",
                "score",
            ]
        )

    return (
        pd.DataFrame(rows)
        .drop_duplicates(subset=["download_url"])
        .sort_values(by=["score", "download_url"], ascending=[False, True])
    )


def download_file(url: str, output_dir: Path) -> Path:
    """Download one file."""
    output_dir.mkdir(parents=True, exist_ok=True)

    filename = normalise_filename(Path(url.split("?")[0]).name)

    if not filename:
        filename = "abs_seifa_2021_download"

    output_path = output_dir / filename

    response = requests.get(
        url,
        timeout=180,
        headers={"User-Agent": "Mozilla/5.0"},
    )
    response.raise_for_status()

    output_path.write_bytes(response.content)

    return output_path


def main() -> None:
    RAW_SEIFA_DIR.mkdir(parents=True, exist_ok=True)
    AUDIT_DIR.mkdir(parents=True, exist_ok=True)

    print(f"Scanning ABS SEIFA page:")
    print(ABS_SEIFA_PAGE)

    links_df = get_download_links(ABS_SEIFA_PAGE)

    if links_df.empty:
        links_df["downloaded_path"] = ""
        links_df["download_status"] = "no_download_links_found"
        links_df.to_csv(MANIFEST_PATH, index=False, encoding="utf-8-sig")
        raise RuntimeError(
            "No downloadable CSV/XLSX/XLS/ZIP links found on the ABS SEIFA page. "
            f"Manifest written to {MANIFEST_PATH}"
        )

    # Download all clearly relevant candidates. Keep this threshold permissive
    # because ABS filenames and link labels can vary.
    candidates = links_df.query("score >= 5").copy()

    if candidates.empty:
        candidates = links_df.head(10).copy()
        print("No high-scoring candidates found. Downloading top 10 links for inspection.")

    rows = []

    print("\nDownloading SEIFA candidate files:")
    for _, row in candidates.iterrows():
        url = row["download_url"]
        print(f"  Score {row['score']}: {url}")

        out_row = row.to_dict()

        try:
            downloaded_path = download_file(url, RAW_SEIFA_DIR)
            out_row["downloaded_path"] = str(downloaded_path)
            out_row["download_status"] = "downloaded"
            print(f"    Saved: {downloaded_path}")
        except Exception as exc:
            out_row["downloaded_path"] = ""
            out_row["download_status"] = f"error: {exc}"
            print(f"    ERROR: {exc}")

        rows.append(out_row)

    manifest_df = pd.DataFrame(rows)
    manifest_df.to_csv(MANIFEST_PATH, index=False, encoding="utf-8-sig")

    print("\nDone.")
    print(f"Raw SEIFA files saved to: {RAW_SEIFA_DIR}")
    print(f"Manifest saved to: {MANIFEST_PATH}")
    print("\nNext step: inspect and process the SA2-level SEIFA file.")


if __name__ == "__main__":
    main()
