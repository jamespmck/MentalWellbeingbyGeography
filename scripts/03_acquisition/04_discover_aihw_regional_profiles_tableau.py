from pathlib import Path
import sys
import subprocess
import re
import pandas as pd
import requests
from bs4 import BeautifulSoup

PROJECT_ROOT = Path(r"D:\Good Measure\MentalWellbeingbyGeography")

RAW_DIR = PROJECT_ROOT / "data" / "raw" / "aihw" / "regional_profiles_sa3_tableau"
AUDIT_DIR = PROJECT_ROOT / "outputs" / "audits"

AIHW_PAGE = "https://www.aihw.gov.au/mental-health/monitoring/regional-profiles"

# Current known Tableau workbook path from the AIHW Regional Profiles page.
KNOWN_WORKBOOK_URLS = [
    "https://viz.aihw.gov.au/t/Public/views/MHOR_regional_profiles_2324_24022026/Overview?:showVizHome=no",
    "https://viz.aihw.gov.au/t/Public/views/MHOR_regional_profiles_2324_24022026/Overview",
]

WORKSHEET_AUDIT = AUDIT_DIR / "aihw_regional_profiles_tableau_worksheet_discovery.csv"
LOAD_ATTEMPT_AUDIT = AUDIT_DIR / "aihw_regional_profiles_tableau_load_attempts.csv"
WORKSHEET_SAMPLE_DIR = RAW_DIR / "worksheet_samples"


def ensure_tableauscraper():
    try:
        import tableauscraper  # noqa: F401
        return
    except ImportError:
        print("tableauscraper is not installed. Installing now.")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "tableauscraper"])


def clean_filename(value: str, max_len: int = 90) -> str:
    value = str(value)
    value = re.sub(r"[^A-Za-z0-9._-]+", "_", value)
    value = re.sub(r"_+", "_", value).strip("_")
    return value[:max_len] or "worksheet"


def clean_columns(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out.columns = [str(c).strip() for c in out.columns]
    return out


def discover_tableau_urls_from_aihw_page() -> list[str]:
    urls = []

    print("Scanning AIHW Regional Profiles page for Tableau URLs:")
    print(AIHW_PAGE)

    try:
        response = requests.get(
            AIHW_PAGE,
            timeout=60,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        response.raise_for_status()

        html = response.text

        # Direct viz.aihw.gov.au Tableau links in href/src text.
        matches = re.findall(
            r"https://viz\.aihw\.gov\.au/[^\s\"'<>]+",
            html,
            flags=re.IGNORECASE,
        )

        for url in matches:
            cleaned = url.replace("&amp;", "&")
            if "views" in cleaned.lower() and "regional" in cleaned.lower():
                urls.append(cleaned)

        # Sometimes Tableau URLs are embedded in iframe src attributes.
        soup = BeautifulSoup(html, "html.parser")

        for tag in soup.find_all(["iframe", "a", "script"], src=True):
            src = tag.get("src", "")
            if "viz.aihw.gov.au" in src and "views" in src:
                urls.append(src.replace("&amp;", "&"))

        for tag in soup.find_all("a", href=True):
            href = tag.get("href", "")
            if "viz.aihw.gov.au" in href and "views" in href:
                urls.append(href.replace("&amp;", "&"))

    except Exception as exc:
        print(f"Could not scan AIHW page for Tableau URLs: {exc}")

    # Keep order but deduplicate.
    deduped = []
    seen = set()

    for url in urls:
        if url not in seen:
            seen.add(url)
            deduped.append(url)

    return deduped


def candidate_workbook_urls() -> list[str]:
    urls = []

    urls.extend(KNOWN_WORKBOOK_URLS)
    urls.extend(discover_tableau_urls_from_aihw_page())

    # Add showVizHome=no variants.
    expanded = []

    for url in urls:
        expanded.append(url)

        if "?:showVizHome" not in url and "showVizHome" not in url:
            if "?" in url:
                expanded.append(url + "&:showVizHome=no")
            else:
                expanded.append(url + "?:showVizHome=no")

    deduped = []
    seen = set()

    for url in expanded:
        if url not in seen:
            seen.add(url)
            deduped.append(url)

    return deduped


def load_workbook():
    from tableauscraper import TableauScraper

    attempts = []
    urls = candidate_workbook_urls()

    if not urls:
        raise RuntimeError("No Tableau workbook URL candidates found.")

    for i, url in enumerate(urls, start=1):
        print(f"\nAttempting Tableau workbook load {i}/{len(urls)}:")
        print(url)

        try:
            ts = TableauScraper()

            # Correct method for URL loading.
            ts.load(url)

            workbook = ts.getWorkbook()
            worksheet_count = len(workbook.worksheets)

            attempts.append(
                {
                    "attempt_index": i,
                    "workbook_url": url,
                    "status": "pass" if worksheet_count > 0 else "review",
                    "worksheet_count": worksheet_count,
                    "notes": "" if worksheet_count > 0 else "Loaded but no worksheets found.",
                }
            )

            if worksheet_count > 0:
                pd.DataFrame(attempts).to_csv(
                    LOAD_ATTEMPT_AUDIT,
                    index=False,
                    encoding="utf-8-sig",
                )
                return workbook, url

        except Exception as exc:
            attempts.append(
                {
                    "attempt_index": i,
                    "workbook_url": url,
                    "status": "fail",
                    "worksheet_count": 0,
                    "notes": str(exc),
                }
            )

    pd.DataFrame(attempts).to_csv(
        LOAD_ATTEMPT_AUDIT,
        index=False,
        encoding="utf-8-sig",
    )

    raise RuntimeError(
        "Could not load any AIHW Tableau workbook candidate. "
        f"Review load attempts: {LOAD_ATTEMPT_AUDIT}"
    )


def inspect_worksheets(workbook, successful_url: str) -> pd.DataFrame:
    rows = []
    worksheets = workbook.worksheets

    print(f"\nSuccessful workbook URL:")
    print(successful_url)
    print(f"\nWorksheets found: {len(worksheets)}")

    for i, ws in enumerate(worksheets, start=1):
        ws_name = ws.name

        print(f"[{i}/{len(worksheets)}] Reading worksheet: {ws_name}")

        try:
            df = ws.data
            df = clean_columns(df)

            sample_path = WORKSHEET_SAMPLE_DIR / f"{i:03d}_{clean_filename(ws_name)}.csv"
            df.head(300).to_csv(sample_path, index=False, encoding="utf-8-sig")

            columns = list(df.columns)
            lower_cols = " | ".join(c.lower() for c in columns)

            likely_region = any(
                term in lower_cols
                for term in [
                    "sa3",
                    "phn",
                    "region",
                    "region code",
                    "region name",
                    "statistical area",
                    "geography",
                ]
            )

            likely_year = any(
                term in lower_cols
                for term in [
                    "year",
                    "financial",
                    "period",
                    "time",
                ]
            )

            likely_measure = any(
                term in lower_cols
                for term in [
                    "measure",
                    "topic",
                    "rate",
                    "patients",
                    "services",
                    "contacts",
                    "presentations",
                    "hospitalisations",
                    "hospitalizations",
                    "prescriptions",
                    "admitted",
                    "emergency",
                    "medicare",
                    "community",
                ]
            )

            rows.append(
                {
                    "worksheet_index": i,
                    "worksheet_name": ws_name,
                    "row_count": len(df),
                    "column_count": len(df.columns),
                    "likely_region_data": likely_region,
                    "likely_year_data": likely_year,
                    "likely_measure_data": likely_measure,
                    "columns": " | ".join(columns),
                    "sample_file": str(sample_path),
                    "successful_workbook_url": successful_url,
                    "status": "pass",
                    "notes": "",
                }
            )

        except Exception as exc:
            rows.append(
                {
                    "worksheet_index": i,
                    "worksheet_name": ws_name,
                    "row_count": "",
                    "column_count": "",
                    "likely_region_data": False,
                    "likely_year_data": False,
                    "likely_measure_data": False,
                    "columns": "",
                    "sample_file": "",
                    "successful_workbook_url": successful_url,
                    "status": "fail",
                    "notes": str(exc),
                }
            )

    return pd.DataFrame(rows)


def main():
    ensure_tableauscraper()

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    AUDIT_DIR.mkdir(parents=True, exist_ok=True)
    WORKSHEET_SAMPLE_DIR.mkdir(parents=True, exist_ok=True)

    workbook, successful_url = load_workbook()

    audit = inspect_worksheets(workbook, successful_url)
    audit.to_csv(WORKSHEET_AUDIT, index=False, encoding="utf-8-sig")

    print("\nCreated worksheet discovery audit:")
    print(WORKSHEET_AUDIT)

    print("\nCreated load attempt audit:")
    print(LOAD_ATTEMPT_AUDIT)

    useful = audit[
        audit["likely_region_data"].fillna(False)
        & audit["likely_measure_data"].fillna(False)
    ].copy()

    print("\nMost likely useful worksheets:")

    if useful.empty:
        print("No obvious region/measure worksheet found. Review all sample files:")
        print(WORKSHEET_SAMPLE_DIR)
    else:
        print(
            useful[
                [
                    "worksheet_index",
                    "worksheet_name",
                    "row_count",
                    "column_count",
                    "likely_year_data",
                    "sample_file",
                ]
            ].to_string(index=False)
        )

    print("\nDiscovery complete. Paste the 'Most likely useful worksheets' output here.")


if __name__ == "__main__":
    main()
