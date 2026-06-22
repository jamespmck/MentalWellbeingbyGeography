from pathlib import Path
from urllib.parse import urljoin, unquote
import zipfile
import re
import sys
import subprocess

import pandas as pd
import requests
from bs4 import BeautifulSoup

PROJECT_ROOT = Path(r"D:\Good Measure\MentalWellbeingbyGeography")

SOURCE_PAGE = "https://www.aihw.gov.au/nmhspf/support-material/regional-activity-data"

RAW_DIR = PROJECT_ROOT / "data" / "raw" / "aihw" / "regional_activity_data_downloads"
ZIP_DIR = RAW_DIR / "zips"
EXTRACT_DIR = RAW_DIR / "extracted"

AUDIT_DIR = PROJECT_ROOT / "outputs" / "audits"

DOWNLOAD_MANIFEST = AUDIT_DIR / "aihw_regional_activity_data_download_manifest.csv"
FILE_INVENTORY = AUDIT_DIR / "aihw_regional_activity_data_file_inventory.csv"
SA3_CANDIDATE_FILES = AUDIT_DIR / "aihw_regional_activity_data_sa3_candidate_files.csv"
WORKBOOK_SHEET_AUDIT = AUDIT_DIR / "aihw_regional_activity_data_workbook_sheet_audit.csv"


def ensure_excel_dependencies():
    try:
        import openpyxl  # noqa: F401
    except ImportError:
        print("openpyxl is not installed. Installing now.")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "openpyxl"])


def clean_text(value) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    text = re.sub(r"\s+", " ", text)
    return text


def safe_filename_from_url(url: str) -> str:
    name = Path(unquote(url.split("?")[0].split("#")[0])).name
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", name)
    name = re.sub(r"_+", "_", name).strip("_")
    return name or "aihw_download.zip"


def discover_zip_links() -> pd.DataFrame:
    print("Scanning AIHW regional activity data page:")
    print(SOURCE_PAGE)

    response = requests.get(
        SOURCE_PAGE,
        timeout=90,
        headers={"User-Agent": "Mozilla/5.0"},
    )
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")

    rows = []

    for a in soup.find_all("a", href=True):
        href = a.get("href", "").strip()
        text = a.get_text(" ", strip=True)
        url = urljoin(SOURCE_PAGE, href)

        combined = f"{text} {url}".lower()

        if ".zip" not in combined:
            continue

        if not any(
            term in combined
            for term in [
                "medicare",
                "prescription",
                "admitted",
                "community mental",
                "emergency",
                "workforce",
                "mental health",
                "data tables",
            ]
        ):
            continue

        rows.append(
            {
                "link_text": text,
                "download_url": url,
                "filename": safe_filename_from_url(url),
            }
        )

    df = pd.DataFrame(rows).drop_duplicates(subset=["download_url"])

    if df.empty:
        raise RuntimeError("No AIHW regional activity ZIP links found.")

    return df.reset_index(drop=True)


def download_zips(links: pd.DataFrame) -> pd.DataFrame:
    ZIP_DIR.mkdir(parents=True, exist_ok=True)

    rows = []

    for i, row in links.iterrows():
        url = row["download_url"]
        filename = row["filename"]
        out_path = ZIP_DIR / filename

        print(f"[{i + 1}/{len(links)}] {filename}")

        status = "downloaded"
        error = ""

        try:
            if out_path.exists() and out_path.stat().st_size > 1000:
                status = "cached"
            else:
                response = requests.get(
                    url,
                    timeout=180,
                    headers={"User-Agent": "Mozilla/5.0"},
                )
                response.raise_for_status()
                out_path.write_bytes(response.content)

        except Exception as exc:
            status = "failed"
            error = str(exc)

        rows.append(
            {
                "link_text": row["link_text"],
                "download_url": url,
                "filename": filename,
                "local_path": str(out_path),
                "size_bytes": out_path.stat().st_size if out_path.exists() else 0,
                "status": status,
                "error": error,
            }
        )

    return pd.DataFrame(rows)


def extract_zips(manifest: pd.DataFrame) -> list[Path]:
    EXTRACT_DIR.mkdir(parents=True, exist_ok=True)

    extracted_files = []

    for _, row in manifest.iterrows():
        if row["status"] not in {"downloaded", "cached"}:
            continue

        zip_path = Path(row["local_path"])

        if not zip_path.exists():
            continue

        target_dir = EXTRACT_DIR / zip_path.stem
        marker = target_dir / ".extracted"

        target_dir.mkdir(parents=True, exist_ok=True)

        if not marker.exists():
            print(f"Extracting {zip_path.name}")
            with zipfile.ZipFile(zip_path, "r") as zf:
                zf.extractall(target_dir)
            marker.write_text("extracted", encoding="utf-8")

        for p in target_dir.rglob("*"):
            if p.is_file() and p.name != ".extracted":
                extracted_files.append(p)

    return extracted_files


def read_csv_sample(path: Path) -> tuple[list[str], int, str]:
    try:
        df = pd.read_csv(path, dtype=str, nrows=200, low_memory=False)
        columns = list(df.columns)
        return columns, len(df), ""
    except Exception as exc:
        return [], 0, str(exc)


def read_excel_sheet_samples(path: Path) -> list[dict]:
    rows = []

    try:
        xl = pd.ExcelFile(path)
    except Exception as exc:
        return [
            {
                "sheet_name": "",
                "columns": [],
                "sample_row_count": 0,
                "error": str(exc),
            }
        ]

    for sheet_name in xl.sheet_names:
        try:
            df = pd.read_excel(path, sheet_name=sheet_name, dtype=str, nrows=200)
            rows.append(
                {
                    "sheet_name": sheet_name,
                    "columns": list(df.columns),
                    "sample_row_count": len(df),
                    "error": "",
                }
            )
        except Exception as exc:
            rows.append(
                {
                    "sheet_name": sheet_name,
                    "columns": [],
                    "sample_row_count": 0,
                    "error": str(exc),
                }
            )

    return rows


def classify_geography(columns: list[str], path: Path, sheet_name: str = "") -> dict:
    joined = " | ".join(str(c).lower() for c in columns)
    file_text = f"{path.name} {sheet_name}".lower()

    has_sa3 = bool(re.search(r"\bsa3\b|statistical area 3|statistical area level 3", joined + " " + file_text))
    has_sa4 = bool(re.search(r"\bsa4\b|statistical area 4|statistical area level 4", joined + " " + file_text))
    has_phn = bool(re.search(r"\bphn\b|primary health network", joined + " " + file_text))

    likely_region_cols = [
        c for c in columns
        if any(term in str(c).lower() for term in ["region", "geography", "area", "sa3", "sa4", "phn"])
    ]

    likely_measure_cols = [
        c for c in columns
        if any(term in str(c).lower() for term in ["rate", "count", "number", "patients", "services", "contacts", "presentations", "hospitalisations", "prescriptions", "per capita", "benefit", "fee"])
    ]

    likely_year_cols = [
        c for c in columns
        if any(term in str(c).lower() for term in ["year", "financial", "period"])
    ]

    return {
        "has_sa3_signal": has_sa3,
        "has_sa4_signal": has_sa4,
        "has_phn_signal": has_phn,
        "likely_region_columns": " | ".join(map(str, likely_region_cols[:30])),
        "likely_measure_columns": " | ".join(map(str, likely_measure_cols[:30])),
        "likely_year_columns": " | ".join(map(str, likely_year_cols[:30])),
    }


def inventory_files(files: list[Path]) -> tuple[pd.DataFrame, pd.DataFrame]:
    inventory_rows = []
    workbook_rows = []

    for path in sorted(files):
        suffix = path.suffix.lower()
        rel = path.relative_to(EXTRACT_DIR)

        if suffix == ".csv":
            columns, sample_rows, error = read_csv_sample(path)
            geo = classify_geography(columns, path)

            inventory_rows.append(
                {
                    "relative_path": str(rel),
                    "file_path": str(path),
                    "file_type": "csv",
                    "sheet_name": "",
                    "sample_row_count": sample_rows,
                    "column_count": len(columns),
                    "columns": " | ".join(map(str, columns)),
                    "status": "pass" if not error else "fail",
                    "error": error,
                    **geo,
                }
            )

        elif suffix in {".xlsx", ".xlsm", ".xls"}:
            sheet_samples = read_excel_sheet_samples(path)

            for sample in sheet_samples:
                columns = sample["columns"]
                error = sample["error"]
                sheet_name = sample["sheet_name"]
                geo = classify_geography(columns, path, sheet_name)

                row = {
                    "relative_path": str(rel),
                    "file_path": str(path),
                    "file_type": suffix.replace(".", ""),
                    "sheet_name": sheet_name,
                    "sample_row_count": sample["sample_row_count"],
                    "column_count": len(columns),
                    "columns": " | ".join(map(str, columns)),
                    "status": "pass" if not error else "fail",
                    "error": error,
                    **geo,
                }

                inventory_rows.append(row)
                workbook_rows.append(row)

        else:
            inventory_rows.append(
                {
                    "relative_path": str(rel),
                    "file_path": str(path),
                    "file_type": suffix.replace(".", ""),
                    "sheet_name": "",
                    "sample_row_count": "",
                    "column_count": "",
                    "columns": "",
                    "status": "skipped",
                    "error": "Unsupported file type for tabular inspection.",
                    "has_sa3_signal": False,
                    "has_sa4_signal": False,
                    "has_phn_signal": False,
                    "likely_region_columns": "",
                    "likely_measure_columns": "",
                    "likely_year_columns": "",
                }
            )

    return pd.DataFrame(inventory_rows), pd.DataFrame(workbook_rows)


def main():
    ensure_excel_dependencies()

    AUDIT_DIR.mkdir(parents=True, exist_ok=True)
    RAW_DIR.mkdir(parents=True, exist_ok=True)

    links = discover_zip_links()

    print(f"\nDiscovered {len(links)} ZIP links.")

    manifest = download_zips(links)
    manifest.to_csv(DOWNLOAD_MANIFEST, index=False, encoding="utf-8-sig")

    print(f"\nCreated download manifest:")
    print(DOWNLOAD_MANIFEST)

    files = extract_zips(manifest)

    print(f"\nExtracted/located {len(files)} files.")

    inventory, workbook_sheet_audit = inventory_files(files)

    inventory.to_csv(FILE_INVENTORY, index=False, encoding="utf-8-sig")
    workbook_sheet_audit.to_csv(WORKBOOK_SHEET_AUDIT, index=False, encoding="utf-8-sig")

    sa3_candidates = inventory[
        inventory["has_sa3_signal"].fillna(False)
        & inventory["status"].eq("pass")
    ].copy()

    sa3_candidates.to_csv(SA3_CANDIDATE_FILES, index=False, encoding="utf-8-sig")

    print("\nCreated file inventory:")
    print(FILE_INVENTORY)

    print("\nCreated workbook sheet audit:")
    print(WORKBOOK_SHEET_AUDIT)

    print("\nCreated SA3 candidate file audit:")
    print(SA3_CANDIDATE_FILES)

    print("\nGeography signal summary:")
    print(
        inventory[
            ["has_sa3_signal", "has_sa4_signal", "has_phn_signal"]
        ].sum(numeric_only=False).to_string()
    )

    print("\nSA3 candidate files:")
    if sa3_candidates.empty:
        print("No SA3 candidate files found in the downloadable ZIP tables.")
        print("That means the official ZIP downloads likely do not contain the SA3 Regional Profiles data.")
        print("We would then need to use the interactive tool manually or automate a browser session.")
    else:
        print(
            sa3_candidates[
                [
                    "relative_path",
                    "sheet_name",
                    "sample_row_count",
                    "column_count",
                    "likely_region_columns",
                    "likely_measure_columns",
                    "likely_year_columns",
                ]
            ].to_string(index=False)
        )

    print("\nAIHW regional activity download discovery complete.")


if __name__ == "__main__":
    main()
