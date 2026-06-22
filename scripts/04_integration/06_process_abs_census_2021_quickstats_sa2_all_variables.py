from pathlib import Path
from io import StringIO
import re
import time
import shutil
import warnings

import pandas as pd
import requests
from bs4 import BeautifulSoup

warnings.filterwarnings("ignore", category=UserWarning, module="pandas")

PROJECT_ROOT = Path(r"D:\Good Measure\MentalWellbeingbyGeography")

PRE_CENSUS_CLEAN = PROJECT_ROOT / "data" / "processed" / "integrated" / "sa2_predictor_universe_pre_census_v01_clean.parquet"
PRE_CENSUS_RAW = PROJECT_ROOT / "data" / "processed" / "integrated" / "sa2_predictor_universe_pre_census_v01.parquet"

RAW_DIR = PROJECT_ROOT / "data" / "raw" / "abs" / "census_2021_quickstats_sa2"
HTML_CACHE_DIR = RAW_DIR / "html"

SOURCE_DIR = PROJECT_ROOT / "data" / "processed" / "sources"
INTEGRATED_DIR = PROJECT_ROOT / "data" / "processed" / "integrated"
AUDIT_DIR = PROJECT_ROOT / "outputs" / "audits"
DICT_DIR = PROJECT_ROOT / "docs" / "data_dictionaries"
ARCHIVE_DIR = SOURCE_DIR / "discarded_or_archived"

QUICKSTATS_SOURCE_CSV = SOURCE_DIR / "sa2_census_2021_quickstats_variables.csv"
QUICKSTATS_SOURCE_PARQUET = SOURCE_DIR / "sa2_census_2021_quickstats_variables.parquet"

MASTER_OUTPUT_CSV = INTEGRATED_DIR / "sa2_predictor_universe_v01.csv"
MASTER_OUTPUT_PARQUET = INTEGRATED_DIR / "sa2_predictor_universe_v01.parquet"

DOWNLOAD_AUDIT = AUDIT_DIR / "abs_census_2021_quickstats_sa2_download_audit.csv"
PARSE_AUDIT = AUDIT_DIR / "sa2_census_2021_quickstats_parse_audit.csv"
BUILD_AUDIT = AUDIT_DIR / "sa2_predictor_universe_v01_quickstats_census_build_audit.csv"
MISSINGNESS_AUDIT = AUDIT_DIR / "sa2_predictor_universe_v01_missingness.csv"
COLUMN_DICTIONARY = DICT_DIR / "sa2_census_2021_quickstats_column_dictionary.csv"

QUICKSTATS_BASE_URL = "https://www.abs.gov.au/census/find-census-data/quickstats/2021/{sa2_code}"

OLD_GCP_OUTPUTS = [
    SOURCE_DIR / "sa2_census_2021_gcp_all_variables_raw_wide.csv",
    SOURCE_DIR / "sa2_census_2021_gcp_all_variables_raw_wide.parquet",
]


def normalise_code(value):
    if pd.isna(value):
        return pd.NA

    value = str(value).strip()

    if value == "" or value.lower() in {"nan", "none", "null"}:
        return pd.NA

    if re.fullmatch(r"\d+\.0", value):
        value = value[:-2]

    return value


def clean_slug(value, max_len=110):
    value = "" if pd.isna(value) else str(value)
    value = value.strip().lower()
    value = value.replace("&", " and ")
    value = re.sub(r"\(.*?\)", " ", value)
    value = re.sub(r"[^a-z0-9]+", "_", value)
    value = re.sub(r"_+", "_", value).strip("_")

    if not value:
        value = "unnamed"

    return value[:max_len].strip("_")


def clean_numeric(value):
    if pd.isna(value):
        return pd.NA

    text = str(value).strip()

    if text == "":
        return pd.NA

    text_lower = text.lower()

    if text_lower in {"null", "nan", "none", "n/a", "na", "-", "—"}:
        return pd.NA

    text = text.replace("\u00a0", " ")
    text = text.replace(",", "")
    text = text.replace("$", "")
    text = text.replace("%", "")
    text = text.strip()

    if text == "":
        return pd.NA

    try:
        return float(text)
    except ValueError:
        return pd.NA


def read_table(path: Path) -> pd.DataFrame:
    if path.exists() and path.suffix.lower() == ".parquet":
        return pd.read_parquet(path)

    if path.exists() and path.suffix.lower() == ".csv":
        return pd.read_csv(path, dtype=str, low_memory=False)

    csv_path = path.with_suffix(".csv")
    parquet_path = path.with_suffix(".parquet")

    if parquet_path.exists():
        return pd.read_parquet(parquet_path)

    if csv_path.exists():
        return pd.read_csv(csv_path, dtype=str, low_memory=False)

    raise FileNotFoundError(f"Input table not found: {path}")


def load_pre_census_master():
    if PRE_CENSUS_CLEAN.exists():
        source = PRE_CENSUS_CLEAN
    else:
        source = PRE_CENSUS_RAW

    master = read_table(source)

    if "sa2_code_2021" not in master.columns:
        raise ValueError(f"Pre-Census master missing sa2_code_2021: {source}")

    master = master.copy()
    master["sa2_code_2021"] = master["sa2_code_2021"].map(normalise_code).astype("string")

    duplicate_count = int(master["sa2_code_2021"].duplicated().sum())

    if duplicate_count > 0:
        raise ValueError(f"Pre-Census master has duplicate SA2 rows: {duplicate_count}")

    return master, source


def archive_old_gcp_outputs():
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)

    moved = []

    for path in OLD_GCP_OUTPUTS:
        if path.exists():
            target = ARCHIVE_DIR / path.name.replace(
                "sa2_census_2021_gcp_all_variables_raw_wide",
                "sa2_census_2021_gcp_published_cells_wide_ARCHIVED",
            )
            shutil.move(str(path), str(target))
            moved.append((str(path), str(target)))

    return moved


def download_quickstats_html(sa2_code, session, sleep_seconds=0.05):
    HTML_CACHE_DIR.mkdir(parents=True, exist_ok=True)

    cache_path = HTML_CACHE_DIR / f"{sa2_code}.html"
    url = QUICKSTATS_BASE_URL.format(sa2_code=sa2_code)

    if cache_path.exists() and cache_path.stat().st_size > 1000:
        return cache_path, url, "cached", ""

    try:
        response = session.get(
            url,
            timeout=60,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        response.raise_for_status()

        cache_path.write_text(response.text, encoding="utf-8")

        time.sleep(sleep_seconds)

        return cache_path, url, "downloaded", ""

    except Exception as exc:
        return cache_path, url, "failed", str(exc)


def find_local_value_columns(df):
    """
    QuickStats tables usually have:
      label column
      local count/value column
      local percent column
      state count/value column
      state percent column
      Australia count/value column
      Australia percent column

    This function takes only the first local count/value and first local percent.
    """
    if df.empty or len(df.columns) < 2:
        return None, None, None

    label_col = df.columns[0]

    count_col = None
    pct_col = None

    for col in df.columns[1:]:
        col_text = str(col).lower()

        if pct_col is None and "%" in col_text:
            pct_col = col
            continue

        if count_col is None and "%" not in col_text:
            count_col = col

        if count_col is not None and pct_col is not None:
            break

    return label_col, count_col, pct_col


def parse_html_tables_for_sa2(sa2_code, html_text):
    soup = BeautifulSoup(html_text, "html.parser")

    page_title = soup.find("h1")
    area_name = page_title.get_text(" ", strip=True) if page_title else ""

    # Track nearby headings to create stable, interpretable variable names.
    current_h2 = ""
    current_h3 = ""
    current_h4 = ""

    records = []
    table_counter = 0
    parsed_table_count = 0

    for element in soup.find_all(["h2", "h3", "h4", "table"]):
        tag_name = element.name.lower()

        if tag_name == "h2":
            current_h2 = element.get_text(" ", strip=True)
            current_h3 = ""
            current_h4 = ""
            continue

        if tag_name == "h3":
            current_h3 = element.get_text(" ", strip=True)
            current_h4 = ""
            continue

        if tag_name == "h4":
            current_h4 = element.get_text(" ", strip=True)
            continue

        if tag_name != "table":
            continue

        table_counter += 1

        try:
            tables = pd.read_html(StringIO(str(element)))
        except Exception:
            continue

        if not tables:
            continue

        df = tables[0]

        if df.empty or len(df.columns) < 2:
            continue

        # Remove completely blank rows.
        df = df.dropna(how="all")

        if df.empty:
            continue

        label_col, count_col, pct_col = find_local_value_columns(df)

        if label_col is None or count_col is None:
            continue

        parsed_table_count += 1

        context_parts = [
            current_h2,
            current_h3,
            current_h4,
            str(label_col),
        ]

        context = "_".join(
            clean_slug(part, max_len=35)
            for part in context_parts
            if clean_slug(part, max_len=35)
        )

        if not context:
            context = f"table_{table_counter}"

        for _, row in df.iterrows():
            row_label = row.get(label_col)

            if pd.isna(row_label):
                continue

            row_label_text = str(row_label).strip()

            if not row_label_text or row_label_text.lower() in {"nan", "null"}:
                continue

            row_slug = clean_slug(row_label_text, max_len=55)

            # Skip repeated notes or malformed rows that are not data.
            if row_slug in {"note", "notes", "more_information_on"}:
                continue

            count_value = clean_numeric(row.get(count_col))
            pct_value = clean_numeric(row.get(pct_col)) if pct_col is not None else pd.NA

            if pd.isna(count_value) and pd.isna(pct_value):
                continue

            base_name = f"census_qs_{context}_{row_slug}"
            base_name = clean_slug(base_name, max_len=180)

            if not pd.isna(count_value):
                records.append(
                    {
                        "sa2_code_2021": sa2_code,
                        "variable_name": f"{base_name}_count",
                        "value": count_value,
                        "area_name_from_page": area_name,
                        "quickstats_section": current_h2,
                        "quickstats_topic": current_h3 or current_h4,
                        "quickstats_row_label": row_label_text,
                        "measure_type": "count_or_value",
                    }
                )

            if not pd.isna(pct_value):
                records.append(
                    {
                        "sa2_code_2021": sa2_code,
                        "variable_name": f"{base_name}_pct",
                        "value": pct_value,
                        "area_name_from_page": area_name,
                        "quickstats_section": current_h2,
                        "quickstats_topic": current_h3 or current_h4,
                        "quickstats_row_label": row_label_text,
                        "measure_type": "percent",
                    }
                )

    parse_summary = {
        "sa2_code_2021": sa2_code,
        "area_name_from_page": area_name,
        "html_table_count": table_counter,
        "parsed_table_count": parsed_table_count,
        "long_record_count": len(records),
        "status": "pass" if len(records) > 0 else "review",
        "notes": "" if len(records) > 0 else "No QuickStats table values parsed.",
    }

    return records, parse_summary


def build_quickstats_source(sa2_codes):
    session = requests.Session()

    download_rows = []
    parse_rows = []
    all_records = []

    total = len(sa2_codes)

    for i, sa2_code in enumerate(sa2_codes, start=1):
        if i == 1 or i % 25 == 0 or i == total:
            print(f"Processing QuickStats SA2 {i:,}/{total:,}: {sa2_code}")

        cache_path, url, download_status, download_error = download_quickstats_html(sa2_code, session)

        download_rows.append(
            {
                "sa2_code_2021": sa2_code,
                "quickstats_url": url,
                "cache_path": str(cache_path),
                "download_status": download_status,
                "download_error": download_error,
            }
        )

        if download_status == "failed":
            parse_rows.append(
                {
                    "sa2_code_2021": sa2_code,
                    "area_name_from_page": "",
                    "html_table_count": 0,
                    "parsed_table_count": 0,
                    "long_record_count": 0,
                    "status": "fail",
                    "notes": download_error,
                }
            )
            continue

        try:
            html_text = cache_path.read_text(encoding="utf-8")
            records, parse_summary = parse_html_tables_for_sa2(sa2_code, html_text)
            all_records.extend(records)
            parse_rows.append(parse_summary)

        except Exception as exc:
            parse_rows.append(
                {
                    "sa2_code_2021": sa2_code,
                    "area_name_from_page": "",
                    "html_table_count": 0,
                    "parsed_table_count": 0,
                    "long_record_count": 0,
                    "status": "fail",
                    "notes": str(exc),
                }
            )

    download_audit = pd.DataFrame(download_rows)
    parse_audit = pd.DataFrame(parse_rows)

    if not all_records:
        raise RuntimeError("No QuickStats records parsed. Review download and parse audits.")

    long = pd.DataFrame(all_records)

    # Handle rare duplicate variable names within an SA2 by keeping the first.
    duplicate_long = int(long.duplicated(subset=["sa2_code_2021", "variable_name"]).sum())

    if duplicate_long > 0:
        long = long.drop_duplicates(subset=["sa2_code_2021", "variable_name"], keep="first")

    wide = (
        long
        .pivot(index="sa2_code_2021", columns="variable_name", values="value")
        .reset_index()
    )

    wide["sa2_code_2021"] = wide["sa2_code_2021"].map(normalise_code).astype("string")

    column_dictionary = (
        long[[
            "variable_name",
            "quickstats_section",
            "quickstats_topic",
            "quickstats_row_label",
            "measure_type",
        ]]
        .drop_duplicates()
        .sort_values("variable_name")
        .reset_index(drop=True)
    )

    column_dictionary = column_dictionary.rename(columns={"variable_name": "column_name"})
    column_dictionary["source"] = "ABS 2021 Census QuickStats, All persons, SA2"
    column_dictionary["initial_field_role"] = "census_quickstats_variable"
    column_dictionary["initial_modelling_use"] = "candidate_predictor_after_review"
    column_dictionary["notes"] = (
        "QuickStats Census value parsed from public ABS SA2 QuickStats page. "
        "These are not full GCP cross-tab cells."
    )

    parse_audit["duplicate_long_records_removed_total"] = duplicate_long

    return wide, download_audit, parse_audit, column_dictionary


def join_to_master(master, quickstats):
    before_rows = len(master)

    overlap = [
        col for col in quickstats.columns
        if col in master.columns and col != "sa2_code_2021"
    ]

    if overlap:
        raise ValueError(f"Column collision before QuickStats join: {overlap[:50]}")

    quickstats = quickstats.copy()
    quickstats["source_census_quickstats_2021_present_flag"] = True

    integrated = master.merge(
        quickstats,
        on="sa2_code_2021",
        how="left",
        validate="one_to_one",
    )

    integrated["source_census_quickstats_2021_present_flag"] = (
        integrated["source_census_quickstats_2021_present_flag"]
        .fillna(False)
        .astype(bool)
    )

    matched = int(integrated["source_census_quickstats_2021_present_flag"].sum())
    unmatched = int(len(integrated) - matched)

    audit = pd.DataFrame(
        [
            {
                "check_name": "master_rows_before_join",
                "value": before_rows,
                "status": "pass" if before_rows == 2472 else "review",
                "notes": "Expected 2472 SA2 rows from pre-Census master.",
            },
            {
                "check_name": "quickstats_source_rows",
                "value": len(quickstats),
                "status": "pass" if len(quickstats) > 0 else "fail",
                "notes": "Rows in QuickStats SA2 source table.",
            },
            {
                "check_name": "master_rows_after_join",
                "value": len(integrated),
                "status": "pass" if len(integrated) == before_rows else "fail",
                "notes": "Join must preserve one row per SA2.",
            },
            {
                "check_name": "duplicate_sa2_rows_after_join",
                "value": int(integrated["sa2_code_2021"].duplicated().sum()),
                "status": "pass" if int(integrated["sa2_code_2021"].duplicated().sum()) == 0 else "fail",
                "notes": "No duplicate SA2 rows allowed.",
            },
            {
                "check_name": "quickstats_matched_sa2_rows",
                "value": matched,
                "status": "pass" if matched == before_rows else "review",
                "notes": "SA2 rows with parsed QuickStats values.",
            },
            {
                "check_name": "quickstats_unmatched_sa2_rows",
                "value": unmatched,
                "status": "pass" if unmatched == 0 else "review",
                "notes": "SA2 rows retained in master without parsed QuickStats values.",
            },
            {
                "check_name": "quickstats_variable_columns",
                "value": len([col for col in quickstats.columns if col.startswith("census_qs_")]),
                "status": "pass" if len([col for col in quickstats.columns if col.startswith("census_qs_")]) > 0 else "fail",
                "notes": "Number of Census QuickStats variable columns added.",
            },
            {
                "check_name": "final_column_count",
                "value": len(integrated.columns),
                "status": "info",
                "notes": "Final integrated master column count.",
            },
        ]
    )

    return integrated, audit


def build_missingness(df):
    rows = []

    for col in df.columns:
        rows.append(
            {
                "column_name": col,
                "missing_count": int(df[col].isna().sum()),
                "missing_pct": round(float(df[col].isna().mean() * 100), 3),
                "non_missing_count": int(df[col].notna().sum()),
                "dtype": str(df[col].dtype),
            }
        )

    return (
        pd.DataFrame(rows)
        .sort_values(["missing_pct", "column_name"], ascending=[False, True])
        .reset_index(drop=True)
    )


def main():
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    HTML_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    SOURCE_DIR.mkdir(parents=True, exist_ok=True)
    INTEGRATED_DIR.mkdir(parents=True, exist_ok=True)
    AUDIT_DIR.mkdir(parents=True, exist_ok=True)
    DICT_DIR.mkdir(parents=True, exist_ok=True)

    print("Archiving previous 17k GCP-wide outputs if present.")
    moved = archive_old_gcp_outputs()

    if moved:
        print("Archived old GCP-wide files:")
        for old, new in moved:
            print(f"  {old} -> {new}")
    else:
        print("No old GCP-wide source outputs found to archive.")

    print("\nLoading latest pre-Census master table.")
    master, master_source = load_pre_census_master()

    print(f"Using pre-Census master: {master_source}")
    print(f"Pre-Census master shape: {len(master)} rows x {len(master.columns)} columns")

    sa2_codes = (
        master["sa2_code_2021"]
        .dropna()
        .astype(str)
        .drop_duplicates()
        .sort_values()
        .tolist()
    )

    print(f"\nBuilding ABS 2021 Census QuickStats SA2 source table for {len(sa2_codes):,} SA2s.")
    quickstats, download_audit, parse_audit, column_dictionary = build_quickstats_source(sa2_codes)

    print("\nJoining QuickStats source to pre-Census master.")
    integrated, build_audit = join_to_master(master, quickstats)

    missingness = build_missingness(integrated)

    print("\nWriting outputs.")

    quickstats.to_csv(QUICKSTATS_SOURCE_CSV, index=False, encoding="utf-8-sig")

    quickstats_parquet_status = "written"
    try:
        quickstats.to_parquet(QUICKSTATS_SOURCE_PARQUET, index=False)
    except Exception as exc:
        quickstats_parquet_status = f"not written: {exc}"

    integrated.to_csv(MASTER_OUTPUT_CSV, index=False, encoding="utf-8-sig")

    master_parquet_status = "written"
    try:
        integrated.to_parquet(MASTER_OUTPUT_PARQUET, index=False)
    except Exception as exc:
        master_parquet_status = f"not written: {exc}"

    download_audit.to_csv(DOWNLOAD_AUDIT, index=False, encoding="utf-8-sig")
    parse_audit.to_csv(PARSE_AUDIT, index=False, encoding="utf-8-sig")
    build_audit.to_csv(BUILD_AUDIT, index=False, encoding="utf-8-sig")
    missingness.to_csv(MISSINGNESS_AUDIT, index=False, encoding="utf-8-sig")
    column_dictionary.to_csv(COLUMN_DICTIONARY, index=False, encoding="utf-8-sig")

    print("\nCreated Census QuickStats source table:")
    print(f"  {QUICKSTATS_SOURCE_CSV}")
    print(f"  {QUICKSTATS_SOURCE_PARQUET} ({quickstats_parquet_status})")

    print("\nCreated integrated master table:")
    print(f"  {MASTER_OUTPUT_CSV}")
    print(f"  {MASTER_OUTPUT_PARQUET} ({master_parquet_status})")

    print("\nCreated audits and dictionary:")
    print(f"  {DOWNLOAD_AUDIT}")
    print(f"  {PARSE_AUDIT}")
    print(f"  {BUILD_AUDIT}")
    print(f"  {MISSINGNESS_AUDIT}")
    print(f"  {COLUMN_DICTIONARY}")

    print("\nBuild audit:")
    print(build_audit.to_string(index=False))

    print("\nFinal shapes:")
    print(f"  QuickStats source: {len(quickstats)} rows x {len(quickstats.columns)} columns")
    print(f"  Integrated master: {len(integrated)} rows x {len(integrated.columns)} columns")

    failed = build_audit.query("status == 'fail'")
    if not failed.empty:
        raise RuntimeError(f"QuickStats Census build failed. Review {BUILD_AUDIT}")

    print("\nCensus QuickStats SA2 variable layer created and joined to the master file.")


if __name__ == "__main__":
    main()
