from pathlib import Path
from urllib.parse import urljoin, unquote
import zipfile
import re
import sys
import subprocess
from datetime import datetime, timezone

import pandas as pd
import requests
from bs4 import BeautifulSoup

PROJECT_ROOT = Path(r"D:\Good Measure\MentalWellbeingbyGeography")

RAW_DIR = PROJECT_ROOT / "data" / "raw" / "ndia" / "public_data_downloads"
DOWNLOAD_DIR = RAW_DIR / "downloads"
EXTRACT_DIR = RAW_DIR / "extracted"

AUDIT_DIR = PROJECT_ROOT / "outputs" / "audits"

DOWNLOAD_MANIFEST = AUDIT_DIR / "ndia_public_data_download_manifest.csv"
LINK_DISCOVERY_AUDIT = AUDIT_DIR / "ndia_public_data_link_discovery_audit.csv"
FILE_INVENTORY = AUDIT_DIR / "ndia_public_data_file_inventory.csv"
ACTIVE_CANDIDATES = AUDIT_DIR / "ndia_sa2_sa3_active_candidate_sources.csv"
HELD_ASIDE = AUDIT_DIR / "ndia_lga_service_district_phn_held_aside_sources.csv"
REJECTED = AUDIT_DIR / "ndia_rejected_non_granular_sources.csv"
WORKBOOK_SHEET_AUDIT = AUDIT_DIR / "ndia_workbook_sheet_inventory.csv"
SUMMARY_AUDIT = AUDIT_DIR / "ndia_sa2_sa3_discovery_summary.csv"

SOURCE_PAGES = {
    "participant_datasets": "https://dataresearch.ndis.gov.au/datasets/participant-datasets",
    "provider_datasets": "https://dataresearch.ndis.gov.au/datasets/provider-datasets",
    "payments_datasets": "https://dataresearch.ndis.gov.au/datasets/payments-datasets",
}

# Discovery is deliberately broad. Final inclusion is controlled by geography checks below.
TARGET_FAMILY_HINTS = {
    "participants_by_sa2": ["participants by sa2"],
    "participants_by_sa3": ["participants by sa3"],
    "participants_by_lga": ["participants by lga"],
    "participant_numbers_plan_budgets": ["participant numbers and plan budgets", "participant numbers", "plan budgets"],
    "utilisation_plan_budgets": ["utilisation of plan budgets", "utilization of plan budgets", "plan budget utilisation", "plan budget utilization"],
    "participants_count_by_diagnosis": ["participants count by diagnosis", "diagnosis"],
    "plan_management_types": ["plan management"],
    "sda_participants": ["sda participants", "specialist disability accommodation participants"],
    "sda_dwellings_demand": ["sda enrolled dwellings", "ndis demand"],
    "sil_participants": ["sil participants", "supported independent living"],
    "active_providers": ["active providers"],
    "market_concentration": ["market concentration"],
    "payments": ["payments data", "payment data"],
}

DOWNLOAD_EXTENSION_RE = re.compile(r"\.(csv|zip|xlsx|xlsm|xls)(\?|#|$)", flags=re.IGNORECASE)


def ensure_excel_dependencies():
    try:
        import openpyxl  # noqa: F401
    except ImportError:
        print("openpyxl is not installed. Installing now.")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "openpyxl"])


def clean_text(value) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except Exception:
        pass
    text = str(value).strip()
    text = re.sub(r"\s+", " ", text)
    return text


def clean_slug(value, max_len=100) -> str:
    text = clean_text(value).lower()
    text = text.replace("&", " and ")
    text = re.sub(r"\(.*?\)", " ", text)
    text = re.sub(r"[^a-z0-9]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    return (text or "unnamed")[:max_len].strip("_")


def safe_filename(value: str, suffix_hint: str = "") -> str:
    name = unquote(str(value))
    name = name.split("?")[0].split("#")[0]
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", name)
    name = re.sub(r"_+", "_", name).strip("_")
    if not name:
        name = "ndia_download"
    if suffix_hint and not name.lower().endswith(suffix_hint.lower()):
        name = f"{name}{suffix_hint}"
    return name


def infer_suffix_from_text(text: str) -> str:
    t = text.lower()
    if ".xlsx" in t or "xlsx" in t:
        return ".xlsx"
    if ".xlsm" in t or "xlsm" in t:
        return ".xlsm"
    if ".xls" in t or "xls" in t:
        return ".xls"
    if ".zip" in t or "zip" in t:
        return ".zip"
    if ".csv" in t or "csv" in t:
        return ".csv"
    return ""


def infer_family(link_text: str, url: str) -> str:
    combined = f"{link_text} {url}".lower()
    combined_clean = re.sub(r"[^a-z0-9]+", " ", combined)

    for family, hints in TARGET_FAMILY_HINTS.items():
        for hint in hints:
            if hint.lower() in combined_clean:
                return family

    if "sa2" in combined_clean and "participant" in combined_clean:
        return "participants_by_sa2"
    if "sa3" in combined_clean and "participant" in combined_clean:
        return "participants_by_sa3"
    if "lga" in combined_clean and "participant" in combined_clean:
        return "participants_by_lga"
    if "provider" in combined_clean:
        return "provider_other"
    if "market" in combined_clean:
        return "market_other"
    if "payment" in combined_clean:
        return "payments"
    if "participant" in combined_clean:
        return "participant_other"

    return "other_download"


def link_looks_downloadable(link_text: str, url: str) -> bool:
    combined = f"{link_text} {url}".lower()

    if DOWNLOAD_EXTENSION_RE.search(url):
        return True

    if "/media/" in url.lower() and any(x in combined for x in ["csv", "zip", "xlsx", "xls"]):
        return True

    if any(x in combined for x in ["csv", "zip", "xlsx", "xls"]):
        if any(x in combined for x in ["participant", "provider", "market", "payment", "utilisation", "utilization", "budget", "diagnosis", "sa2", "sa3", "lga"]):
            return True

    return False


def discover_download_links() -> pd.DataFrame:
    rows = []

    for page_key, page_url in SOURCE_PAGES.items():
        print(f"Scanning {page_key}:")
        print(page_url)

        response = requests.get(
            page_url,
            timeout=90,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        response.raise_for_status()

        soup = BeautifulSoup(response.text, "html.parser")

        for a in soup.find_all("a", href=True):
            link_text = clean_text(a.get_text(" ", strip=True))
            href = a.get("href", "").strip()
            url = urljoin(page_url, href)

            if not link_looks_downloadable(link_text, url):
                continue

            family = infer_family(link_text, url)
            suffix_hint = infer_suffix_from_text(f"{link_text} {url}")

            rows.append(
                {
                    "source_page_key": page_key,
                    "source_page_url": page_url,
                    "source_family": family,
                    "link_text": link_text,
                    "download_url": url,
                    "suffix_hint": suffix_hint,
                }
            )

    links = pd.DataFrame(rows)

    if links.empty:
        raise RuntimeError("No NDIA download links discovered from target pages.")

    links = links.drop_duplicates(subset=["download_url"]).reset_index(drop=True)
    links["discovered_at_utc"] = datetime.now(timezone.utc).isoformat()

    return links


def filename_from_response_or_link(response, link_text, url, suffix_hint):
    cd = response.headers.get("content-disposition", "")

    match = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)"?', cd, flags=re.IGNORECASE)
    if match:
        return safe_filename(match.group(1), suffix_hint="")

    url_name = Path(unquote(url.split("?")[0].split("#")[0])).name

    if url_name and "." in url_name:
        return safe_filename(url_name, suffix_hint="")

    return safe_filename(link_text, suffix_hint=suffix_hint or ".dat")


def download_files(links: pd.DataFrame) -> pd.DataFrame:
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

    rows = []

    for i, row in links.iterrows():
        source_family = row["source_family"]
        url = row["download_url"]
        link_text = row["link_text"]
        suffix_hint = row["suffix_hint"]

        print(f"[{i + 1}/{len(links)}] Downloading {source_family}: {link_text or url}")

        status = "downloaded"
        error = ""
        local_path = ""
        final_url = url
        content_type = ""

        try:
            response = requests.get(
                url,
                timeout=240,
                headers={"User-Agent": "Mozilla/5.0"},
                allow_redirects=True,
            )
            response.raise_for_status()
            final_url = response.url
            content_type = response.headers.get("content-type", "")

            filename = filename_from_response_or_link(response, link_text, final_url, suffix_hint)
            filename = f"{clean_slug(source_family, 60)}__{filename}"
            out_path = DOWNLOAD_DIR / filename

            if out_path.exists() and out_path.stat().st_size > 1000:
                status = "cached"
            else:
                out_path.write_bytes(response.content)

            local_path = str(out_path)

        except Exception as exc:
            status = "failed"
            error = str(exc)

        rows.append(
            {
                **row.to_dict(),
                "final_url": final_url,
                "content_type": content_type,
                "local_path": local_path,
                "status": status,
                "error": error,
                "size_bytes": Path(local_path).stat().st_size if local_path and Path(local_path).exists() else 0,
            }
        )

    return pd.DataFrame(rows)


def extract_zip_files(manifest: pd.DataFrame) -> list[Path]:
    EXTRACT_DIR.mkdir(parents=True, exist_ok=True)

    files = []

    for _, row in manifest.iterrows():
        if row["status"] not in {"downloaded", "cached"}:
            continue

        path = Path(row["local_path"])

        if not path.exists():
            continue

        suffix = path.suffix.lower()

        if suffix == ".zip":
            target_dir = EXTRACT_DIR / path.stem
            marker = target_dir / ".extracted"

            target_dir.mkdir(parents=True, exist_ok=True)

            if not marker.exists():
                print(f"Extracting {path.name}")
                with zipfile.ZipFile(path, "r") as zf:
                    zf.extractall(target_dir)
                marker.write_text("extracted", encoding="utf-8")

            for p in target_dir.rglob("*"):
                if p.is_file() and p.name != ".extracted":
                    files.append(p)

        else:
            files.append(path)

    return sorted(files)


def read_csv_sample(path: Path) -> tuple[list[str], int, str]:
    try:
        df = pd.read_csv(path, dtype=str, nrows=500, low_memory=False)
        return list(df.columns), len(df), ""
    except Exception as exc:
        return [], 0, str(exc)


def read_excel_sheet_samples(path: Path) -> list[dict]:
    try:
        xl = pd.ExcelFile(path)
    except Exception as exc:
        return [{
            "sheet_name": "",
            "columns": [],
            "sample_row_count": 0,
            "error": str(exc),
        }]

    rows = []

    for sheet in xl.sheet_names:
        try:
            df = pd.read_excel(path, sheet_name=sheet, dtype=str, nrows=500)
            rows.append({
                "sheet_name": sheet,
                "columns": list(df.columns),
                "sample_row_count": len(df),
                "error": "",
            })
        except Exception as exc:
            rows.append({
                "sheet_name": sheet,
                "columns": [],
                "sample_row_count": 0,
                "error": str(exc),
            })

    return rows


def infer_source_family_from_path(path: Path, manifest: pd.DataFrame) -> str:
    path_str = str(path)

    for _, row in manifest.iterrows():
        local_path = str(row.get("local_path", ""))
        family = str(row.get("source_family", ""))

        if local_path and (local_path in path_str or Path(local_path).stem in path_str):
            return family

    text = path.name.lower()

    for family in TARGET_FAMILY_HINTS:
        family_compact = family.replace("_", "")
        file_compact = re.sub(r"[^a-z0-9]+", "", text)
        if family_compact in file_compact:
            return family

    return "unknown"


def classify_columns(columns: list[str], path: Path, sheet_name: str, source_family: str) -> dict:
    joined = " | ".join(str(c).lower() for c in columns)
    file_text = f"{path.name} {sheet_name} {source_family}".lower()
    text = joined + " " + file_text

    has_sa2 = bool(re.search(r"\bsa2\b|statistical area 2|statistical area level 2", text))
    has_sa3 = bool(re.search(r"\bsa3\b|statistical area 3|statistical area level 3", text))
    has_sa4 = bool(re.search(r"\bsa4\b|statistical area 4|statistical area level 4", text))
    has_lga = bool(re.search(r"\blga\b|local government area", text))
    has_service_district = bool(re.search(r"service district|\bdistrict\b", text))
    has_state = bool(re.search(r"state|territory|jurisdiction", text))
    has_phn = bool(re.search(r"\bphn\b|primary health network", text))
    has_postcode = bool(re.search(r"postcode|postal area", text))

    has_psychosocial = bool(re.search(r"psychosocial|psycho-social|mental health|psych", text))
    has_disability = bool(re.search(r"disability|diagnosis|primary disability|disability group", text))

    has_plan_budget = bool(re.search(r"plan budget|committed support|annualised committed|annualized committed|support budget|budget", text))
    has_utilisation = bool(re.search(r"utilisation|utilization|utilised|utilized", text))
    has_payment = bool(re.search(r"payment|paid|payments", text))
    has_provider = bool(re.search(r"provider|providers", text))
    has_market = bool(re.search(r"market|concentration|top 10|largest providers", text))
    has_participant = bool(re.search(r"participant|participants", text))
    has_sda_sil = bool(re.search(r"\bsda\b|specialist disability accommodation|\bsil\b|supported independent living", text))
    has_support_category = bool(re.search(r"support category|support class|support item|support type", text))

    likely_geo_cols = [
        str(c) for c in columns
        if any(term in str(c).lower() for term in [
            "sa2", "sa3", "sa4", "lga", "service district", "district", "state", "territory", "region", "area", "postcode", "phn"
        ])
    ]

    likely_measure_cols = [
        str(c) for c in columns
        if any(term in str(c).lower() for term in [
            "count", "number", "participants", "budget", "utilisation", "utilization", "payment",
            "provider", "market", "concentration", "rate", "percentage", "amount", "total", "average",
            "committed", "paid"
        ])
    ]

    likely_disability_cols = [
        str(c) for c in columns
        if any(term in str(c).lower() for term in [
            "disability", "diagnosis", "psychosocial", "condition", "function"
        ])
    ]

    if has_sa2 or has_sa3:
        active_status = "active_sa2_sa3_candidate"
        active_reason = "Contains SA2 and/or SA3 geography signal."
    elif has_lga or has_service_district or has_phn or has_postcode:
        active_status = "held_aside_bridge_required"
        active_reason = "Contains LGA/service-district/PHN/postcode geography but no SA2/SA3 signal. Hold until bridge is validated."
    elif has_sa4 or has_state:
        active_status = "rejected_not_granular_enough"
        active_reason = "Contains SA4/state signal only or no finer geography signal."
    else:
        active_status = "manual_review_required"
        active_reason = "No clear geography signal detected."

    return {
        "has_sa2_signal": has_sa2,
        "has_sa3_signal": has_sa3,
        "has_sa4_signal": has_sa4,
        "has_lga_signal": has_lga,
        "has_service_district_signal": has_service_district,
        "has_phn_signal": has_phn,
        "has_postcode_signal": has_postcode,
        "has_state_signal": has_state,
        "has_psychosocial_signal": has_psychosocial,
        "has_disability_signal": has_disability,
        "has_plan_budget_signal": has_plan_budget,
        "has_utilisation_signal": has_utilisation,
        "has_payment_signal": has_payment,
        "has_provider_signal": has_provider,
        "has_market_signal": has_market,
        "has_participant_signal": has_participant,
        "has_sda_sil_signal": has_sda_sil,
        "has_support_category_signal": has_support_category,
        "likely_geography_columns": " | ".join(likely_geo_cols[:40]),
        "likely_measure_columns": " | ".join(likely_measure_cols[:40]),
        "likely_disability_columns": " | ".join(likely_disability_cols[:40]),
        "active_status": active_status,
        "active_reason": active_reason,
    }


def inventory_files(files: list[Path], manifest: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    inventory_rows = []
    workbook_rows = []

    for path in files:
        suffix = path.suffix.lower()
        source_family = infer_source_family_from_path(path, manifest)

        if suffix == ".csv":
            columns, sample_rows, error = read_csv_sample(path)
            cls = classify_columns(columns, path, "", source_family)

            inventory_rows.append({
                "source_family": source_family,
                "file_path": str(path),
                "file_name": path.name,
                "file_type": "csv",
                "sheet_name": "",
                "sample_row_count": sample_rows,
                "column_count": len(columns),
                "columns": " | ".join(map(str, columns)),
                "status": "pass" if not error else "fail",
                "error": error,
                **cls,
            })

        elif suffix in {".xlsx", ".xlsm", ".xls"}:
            sheet_samples = read_excel_sheet_samples(path)

            for sample in sheet_samples:
                columns = sample["columns"]
                error = sample["error"]
                sheet_name = sample["sheet_name"]
                cls = classify_columns(columns, path, sheet_name, source_family)

                row = {
                    "source_family": source_family,
                    "file_path": str(path),
                    "file_name": path.name,
                    "file_type": suffix.replace(".", ""),
                    "sheet_name": sheet_name,
                    "sample_row_count": sample["sample_row_count"],
                    "column_count": len(columns),
                    "columns": " | ".join(map(str, columns)),
                    "status": "pass" if not error else "fail",
                    "error": error,
                    **cls,
                }

                inventory_rows.append(row)
                workbook_rows.append(row)

        else:
            inventory_rows.append({
                "source_family": source_family,
                "file_path": str(path),
                "file_name": path.name,
                "file_type": suffix.replace(".", ""),
                "sheet_name": "",
                "sample_row_count": "",
                "column_count": "",
                "columns": "",
                "status": "skipped",
                "error": "Unsupported file type.",
                "active_status": "manual_review_required",
                "active_reason": "Unsupported file type.",
            })

    return pd.DataFrame(inventory_rows), pd.DataFrame(workbook_rows)


def print_subset(title: str, df: pd.DataFrame, cols: list[str], empty_text: str = "None."):
    print(f"\n{title}")
    if df.empty:
        print(empty_text)
        return
    existing = [c for c in cols if c in df.columns]
    print(df[existing].to_string(index=False))


def main():
    ensure_excel_dependencies()

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    EXTRACT_DIR.mkdir(parents=True, exist_ok=True)
    AUDIT_DIR.mkdir(parents=True, exist_ok=True)

    links = discover_download_links()
    links.to_csv(LINK_DISCOVERY_AUDIT, index=False, encoding="utf-8-sig")

    print("\nDiscovered target NDIA links:")
    print(links[["source_page_key", "source_family", "link_text", "download_url"]].to_string(index=False))

    manifest = download_files(links)
    manifest.to_csv(DOWNLOAD_MANIFEST, index=False, encoding="utf-8-sig")

    print("\nCreated download manifest:")
    print(DOWNLOAD_MANIFEST)

    files = extract_zip_files(manifest)

    print(f"\nFiles available for inspection: {len(files)}")

    inventory, workbook_sheet_audit = inventory_files(files, manifest)

    inventory.to_csv(FILE_INVENTORY, index=False, encoding="utf-8-sig")
    workbook_sheet_audit.to_csv(WORKBOOK_SHEET_AUDIT, index=False, encoding="utf-8-sig")

    active = inventory[
        inventory["active_status"].eq("active_sa2_sa3_candidate")
        & inventory["status"].eq("pass")
    ].copy()

    held = inventory[
        inventory["active_status"].eq("held_aside_bridge_required")
        & inventory["status"].eq("pass")
    ].copy()

    rejected = inventory[
        inventory["active_status"].eq("rejected_not_granular_enough")
        & inventory["status"].eq("pass")
    ].copy()

    manual_review = inventory[
        inventory["active_status"].eq("manual_review_required")
        & inventory["status"].eq("pass")
    ].copy()

    active.to_csv(ACTIVE_CANDIDATES, index=False, encoding="utf-8-sig")
    held.to_csv(HELD_ASIDE, index=False, encoding="utf-8-sig")
    rejected.to_csv(REJECTED, index=False, encoding="utf-8-sig")

    summary = (
        inventory
        .groupby(["source_family", "active_status"], dropna=False)
        .size()
        .reset_index(name="file_or_sheet_count")
        .sort_values(["source_family", "active_status"])
    )
    summary.to_csv(SUMMARY_AUDIT, index=False, encoding="utf-8-sig")

    print("\nCreated inventory outputs:")
    print(FILE_INVENTORY)
    print(ACTIVE_CANDIDATES)
    print(HELD_ASIDE)
    print(REJECTED)
    print(WORKBOOK_SHEET_AUDIT)
    print(SUMMARY_AUDIT)

    print("\nSource-family summary:")
    print(summary.to_string(index=False))

    active_cols = [
        "source_family",
        "file_name",
        "sheet_name",
        "sample_row_count",
        "column_count",
        "has_sa2_signal",
        "has_sa3_signal",
        "has_psychosocial_signal",
        "has_disability_signal",
        "has_plan_budget_signal",
        "has_utilisation_signal",
        "has_payment_signal",
        "has_provider_signal",
        "has_market_signal",
        "has_support_category_signal",
        "likely_geography_columns",
        "likely_disability_columns",
        "likely_measure_columns",
    ]

    print_subset(
        "Active SA2/SA3 candidates:",
        active,
        active_cols,
        "None found. Review inventory for possible column-name detection failure.",
    )

    print_subset(
        "Held aside because bridge required:",
        held,
        [
            "source_family",
            "file_name",
            "sheet_name",
            "has_lga_signal",
            "has_service_district_signal",
            "has_phn_signal",
            "has_postcode_signal",
            "likely_geography_columns",
        ],
    )

    print_subset(
        "Rejected as not granular enough:",
        rejected,
        [
            "source_family",
            "file_name",
            "sheet_name",
            "has_sa4_signal",
            "has_state_signal",
            "likely_geography_columns",
        ],
    )

    print_subset(
        "Manual review required:",
        manual_review,
        [
            "source_family",
            "file_name",
            "sheet_name",
            "sample_row_count",
            "column_count",
            "likely_geography_columns",
            "columns",
        ],
    )

    print("\nNDIA SA2/SA3 discovery complete.")


if __name__ == "__main__":
    main()
