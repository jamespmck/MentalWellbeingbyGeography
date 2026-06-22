from pathlib import Path
from urllib.parse import urljoin, unquote
import re
import requests
import pandas as pd
from bs4 import BeautifulSoup

PROJECT_ROOT = Path(r"D:\Good Measure\MentalWellbeingbyGeography")

RAW_DIR = PROJECT_ROOT / "data" / "raw" / "abs" / "nsmhw"
AUDIT_DIR = PROJECT_ROOT / "outputs" / "audits"

MANIFEST_PATH = AUDIT_DIR / "abs_nsmhw_sa2_modelled_estimates_download_manifest.csv"

ABS_NSMHW_PAGE = (
    "https://www.abs.gov.au/statistics/health/mental-health/"
    "national-study-mental-health-and-wellbeing/latest-release"
)


def safe_filename_from_url(url: str) -> str:
    name = Path(unquote(url.split("?")[0].split("#")[0])).name
    name = name.replace("%20", "_")
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", name)
    name = re.sub(r"_+", "_", name)
    return name.strip("_")


def slug_from_filename(filename: str) -> str:
    stem = Path(filename).stem.lower()
    stem = stem.replace("12-month", "12_month")
    stem = re.sub(r"[^a-z0-9]+", "_", stem)
    stem = re.sub(r"_+", "_", stem)
    return stem.strip("_")


def discover_sa2_modelled_links() -> pd.DataFrame:
    response = requests.get(
        ABS_NSMHW_PAGE,
        timeout=60,
        headers={"User-Agent": "Mozilla/5.0"},
    )
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")

    rows = []

    for a in soup.find_all("a", href=True):
        href = a.get("href", "").strip()
        url = urljoin(ABS_NSMHW_PAGE, href)

        lower_url = unquote(url).lower()

        if not lower_url.endswith(".xlsx"):
            continue

        if "modelled" not in lower_url:
            continue

        if "sa2" not in lower_url:
            continue

        filename = safe_filename_from_url(url)

        rows.append(
            {
                "source_id": "SRC002",
                "source_page": ABS_NSMHW_PAGE,
                "download_url": url,
                "filename": filename,
                "source_slug": slug_from_filename(filename),
                "downloaded_path": str(RAW_DIR / filename),
                "download_status": "not_attempted",
                "size_bytes": "",
            }
        )

    out = pd.DataFrame(rows).drop_duplicates(subset=["download_url"])

    if out.empty:
        return out

    return out.sort_values("filename").reset_index(drop=True)


def download_one(url: str, output_path: Path) -> int:
    response = requests.get(
        url,
        timeout=180,
        headers={"User-Agent": "Mozilla/5.0"},
    )
    response.raise_for_status()

    output_path.write_bytes(response.content)

    return output_path.stat().st_size


def main() -> None:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    AUDIT_DIR.mkdir(parents=True, exist_ok=True)

    links = discover_sa2_modelled_links()

    if links.empty:
        links.to_csv(MANIFEST_PATH, index=False, encoding="utf-8-sig")
        raise RuntimeError(
            "No SA2 modelled NSMHW xlsx links found on ABS page. "
            f"Review {MANIFEST_PATH}"
        )

    print("Discovered SA2 modelled NSMHW downloads:")
    print(links[["filename", "source_slug"]].to_string(index=False))

    rows = []

    for _, row in links.iterrows():
        output_path = Path(row["downloaded_path"])
        out_row = row.to_dict()

        try:
            size = download_one(row["download_url"], output_path)
            out_row["download_status"] = "downloaded"
            out_row["size_bytes"] = size

            print(f"\nDownloaded: {output_path}")
            print(f"  Size: {size:,} bytes")

        except Exception as exc:
            out_row["download_status"] = f"error: {exc}"
            print(f"\nERROR: {row['download_url']}")
            print(f"  {exc}")

        rows.append(out_row)

    manifest = pd.DataFrame(rows)
    manifest.to_csv(MANIFEST_PATH, index=False, encoding="utf-8-sig")

    print("\nCreated manifest:")
    print(MANIFEST_PATH)

    failed = manifest[~manifest["download_status"].eq("downloaded")]

    if not failed.empty:
        raise RuntimeError(
            "One or more NSMHW SA2 files failed to download. "
            f"Review {MANIFEST_PATH}"
        )

    print("\nAll NSMHW SA2 modelled estimate files downloaded.")


if __name__ == "__main__":
    main()
