from pathlib import Path
import argparse
import json
import re
import subprocess
import sys
import time
from datetime import datetime, timezone

import pandas as pd

PROJECT_ROOT = Path(r"D:\Good Measure\MentalWellbeingbyGeography")

AIHW_PAGE_URL = "https://www.aihw.gov.au/mental-health/monitoring/regional-profiles"
TARGET_YEARS_DEFAULT = ["2021-22"]

RAW_DIR = PROJECT_ROOT / "data" / "raw" / "aihw" / "regional_profiles_sa3_embedding_api"
INDIVIDUAL_DIR = RAW_DIR / "individual"
PROCESSED_SOURCE_DIR = PROJECT_ROOT / "data" / "processed" / "sources"
AUDIT_DIR = PROJECT_ROOT / "outputs" / "audits"
DICT_DIR = PROJECT_ROOT / "docs" / "data_dictionaries"

SPINE_PARQUET = PROJECT_ROOT / "data" / "processed" / "spines" / "sa2_2021_spine.parquet"
SPINE_CSV = PROJECT_ROOT / "data" / "processed" / "spines" / "sa2_2021_spine.csv"

LONG_CSV = PROCESSED_SOURCE_DIR / "sa3_aihw_regional_profiles_long_2021_22.csv"
LONG_PARQUET = PROCESSED_SOURCE_DIR / "sa3_aihw_regional_profiles_long_2021_22.parquet"

SELECTED_CSV = PROCESSED_SOURCE_DIR / "sa3_aihw_regional_profiles_selected_measures_2021_22.csv"
SELECTED_PARQUET = PROCESSED_SOURCE_DIR / "sa3_aihw_regional_profiles_selected_measures_2021_22.parquet"

EXTRACTION_LOG = AUDIT_DIR / "aihw_sa3_embedding_extraction_log_2021_22.csv"
COVERAGE_SUMMARY = AUDIT_DIR / "aihw_sa3_embedding_extraction_coverage_summary_2021_22.csv"
FAILURES_CSV = AUDIT_DIR / "aihw_sa3_embedding_extraction_failures_2021_22.csv"
PROBE_JSON = AUDIT_DIR / "aihw_embedding_api_probe_2021_22.json"
SELECTED_DICTIONARY = DICT_DIR / "sa3_aihw_regional_profiles_selected_measure_dictionary_2021_22.csv"

DATA_WORKSHEET_NAME = "Regional profiles data table"
DOWNLOAD_SHEET_NAME = "Download"

SPECIAL_SA3_NAME_RE = re.compile(
    r"no usual address|migratory|offshore|shipping|outside australia",
    flags=re.IGNORECASE,
)

PRIMARY_MEASURES = [
    {
        "variable_stem": "aihw_admitted_hospitalisations_rate_per_10000",
        "topic": "Admitted patient care hospitalisations",
        "practitioner": "",
        "age_group": "Total",
        "measure": "Hospitalisations",
        "metric": "Rate per 10,000 population",
    },
    {
        "variable_stem": "aihw_community_care_contacts_rate_per_10000",
        "topic": "Community care contacts",
        "practitioner": "",
        "age_group": "Total",
        "measure": "Contacts",
        "metric": "Rate per 10,000 population",
    },
    {
        "variable_stem": "aihw_emergency_department_presentations_rate_per_10000",
        "topic": "Emergency department presentations",
        "practitioner": "",
        "age_group": "Total",
        "measure": "Presentations",
        "metric": "Rate per 10,000 population",
    },
    {
        "variable_stem": "aihw_medicare_patients_rate_per_10000_all_providers",
        "topic": "Medicare services delivered",
        "practitioner": "All providers",
        "age_group": "Total",
        "measure": "Patients",
        "metric": "Rate per 10,000 population",
    },
    {
        "variable_stem": "aihw_medicare_services_rate_per_10000_all_providers",
        "topic": "Medicare services delivered",
        "practitioner": "All providers",
        "age_group": "Total",
        "measure": "Services",
        "metric": "Rate per 10,000 population",
    },
    {
        "variable_stem": "aihw_medicare_benefit_paid_per_capita_all_providers",
        "topic": "Medicare services delivered",
        "practitioner": "All providers",
        "age_group": "Total",
        "measure": "Benefits paid ($)",
        "metric": "Benefits per capita (current price)",
    },
    {
        "variable_stem": "aihw_medicare_fee_charged_per_capita_all_providers",
        "topic": "Medicare services delivered",
        "practitioner": "All providers",
        "age_group": "Total",
        "measure": "Fees charged ($)",
        "metric": "Fees per capita (current price)",
    },
    {
        "variable_stem": "aihw_prescriptions_patients_rate_per_10000",
        "topic": "Prescriptions dispensed",
        "practitioner": "",
        "age_group": "Total",
        "measure": "Patients",
        "metric": "Rate per 10,000 population",
    },
    {
        "variable_stem": "aihw_prescriptions_dispensed_rate_per_10000",
        "topic": "Prescriptions dispensed",
        "practitioner": "",
        "age_group": "Total",
        "measure": "Prescriptions",
        "metric": "Rate per 10,000 population",
    },
    {
        "variable_stem": "aihw_prescriptions_benefit_subsidised_per_capita",
        "topic": "Prescriptions dispensed",
        "practitioner": "",
        "age_group": "Total",
        "measure": "Benefits subsidised ($)",
        "metric": "Benefits per capita (current price)",
    },
]


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
    try:
        if pd.isna(value):
            return ""
    except Exception:
        pass
    text = str(value).strip()
    text = re.sub(r"\s+", " ", text)
    return text


def slug(value, max_len=100) -> str:
    text = clean_text(value).lower()
    text = text.replace("&", " and ")
    text = text.replace("–", "-").replace("—", "-")
    text = re.sub(r"[^a-z0-9]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    return (text or "blank")[:max_len].strip("_")


def normalise_year_token(value) -> str:
    """Normalise financial-year display tokens for comparisons."""
    return clean_text(value).replace("–", "-").replace("—", "-")


def parse_years(value: str) -> list[str]:
    """
    Return Tableau request year tokens.

    Keep financial years as ASCII hyphen, e.g. 2021-22.
    The old successful AIHW scrape used 2021-22 in requests.
    The Tableau display may return 2021–22; matching is normalised later.
    """
    if not value:
        return TARGET_YEARS_DEFAULT

    out = []

    for part in value.split(","):
        part = part.strip()

        if not part:
            continue

        part = part.replace("–", "-").replace("—", "-")
        out.append(part)

    return out or TARGET_YEARS_DEFAULT


def load_sa3_targets(include_special: bool) -> pd.DataFrame:
    if SPINE_PARQUET.exists():
        spine = pd.read_parquet(SPINE_PARQUET)
    elif SPINE_CSV.exists():
        spine = pd.read_csv(SPINE_CSV, dtype=str)
    else:
        raise FileNotFoundError(f"Could not find SA2 spine at {SPINE_PARQUET} or {SPINE_CSV}")

    required = ["sa3_code_2021", "sa3_name_2021", "state_name_2021"]
    missing = [c for c in required if c not in spine.columns]

    if missing:
        raise ValueError(f"SA2 spine missing required columns: {missing}")

    targets = spine[required].drop_duplicates().copy()

    targets["sa3_code_2021"] = (
        targets["sa3_code_2021"]
        .astype(str)
        .str.strip()
        .str.replace(r"\.0$", "", regex=True)
    )

    targets["sa3_name_2021"] = targets["sa3_name_2021"].map(clean_text)
    targets["state_name_2021"] = targets["state_name_2021"].map(clean_text)

    if not include_special:
        targets = targets[
            ~targets["sa3_name_2021"].str.contains(SPECIAL_SA3_NAME_RE, na=False)
        ].copy()

    return targets.sort_values(["state_name_2021", "sa3_code_2021"]).reset_index(drop=True)


def normalise_value_raw(value) -> str:
    text = clean_text(value)

    if text.lower() in {"", "nan", "none", "null"}:
        return ""

    return text


def to_numeric(value):
    text = normalise_value_raw(value)

    if text == "":
        return pd.NA

    lowered = text.lower().replace(".", "").replace(" ", "")

    if lowered in {"np", "na", "n/a", "notpublished", "notavailable"}:
        return pd.NA

    text = text.replace(",", "").replace("$", "")

    try:
        return float(text)
    except Exception:
        return pd.NA


def publication_status(value):
    text = normalise_value_raw(value).lower().replace(".", "").replace(" ", "")

    if text in {"np", "notpublished"}:
        return "not_published"

    if text in {"na", "n/a", "notavailable", ""}:
        return "not_available"

    if pd.notna(to_numeric(value)):
        return "published_numeric"

    return "published_non_numeric"


def normalise_extracted_table(df: pd.DataFrame, target: dict, year: str) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()

    column_lookup = {clean_text(c).lower(): c for c in df.columns}

    def pick(*names):
        for name in names:
            key = name.lower()
            if key in column_lookup:
                return column_lookup[key]

        for col in df.columns:
            c = clean_text(col).lower()
            if any(name.lower() in c for name in names):
                return col

        return None

    cols = {
        "year_source": pick("Year"),
        "aihw_topic": pick("Topics", "Topic"),
        "aihw_practitioner": pick("Practitioner", "Provider type", "Provider"),
        "aihw_age_group": pick("Age Group", "Age"),
        "aihw_measure": pick("Measure"),
        "aihw_metric": pick("Metric"),
        "geographic_area_name": pick("Geographic Area Name"),
        "geographic_area_type": pick("Geographic Area Type"),
        "aihw_value_raw": pick("SUM(Values)", "Values", "Value", "AGG(Values)"),
    }

    missing = [
        k for k, v in cols.items()
        if v is None and k not in {"geographic_area_name", "geographic_area_type"}
    ]

    if missing:
        raise ValueError(
            f"Returned data missing expected AIHW columns: {missing}; "
            f"returned columns: {list(df.columns)}"
        )

    out = pd.DataFrame(index=df.index)

    out["sa3_code_2021"] = str(target["sa3_code_2021"])
    out["sa3_name_2021"] = str(target["sa3_name_2021"])
    out["state_name_2021"] = str(target["state_name_2021"])

    out["geographic_area_name"] = (
        df[cols["geographic_area_name"]].map(clean_text)
        if cols["geographic_area_name"]
        else str(target["sa3_name_2021"])
    )

    out["geographic_area_type"] = (
        df[cols["geographic_area_type"]].map(clean_text)
        if cols["geographic_area_type"]
        else "SA3"
    )

    out["year_source"] = df[cols["year_source"]].map(clean_text).replace("", year)
    out["aihw_topic"] = df[cols["aihw_topic"]].map(clean_text)
    out["aihw_practitioner"] = df[cols["aihw_practitioner"]].map(clean_text)
    out["aihw_age_group"] = df[cols["aihw_age_group"]].map(clean_text)
    out["aihw_measure"] = df[cols["aihw_measure"]].map(clean_text)
    out["aihw_metric"] = df[cols["aihw_metric"]].map(clean_text)
    out["aihw_value_raw"] = df[cols["aihw_value_raw"]].map(normalise_value_raw)
    out["aihw_value_numeric"] = out["aihw_value_raw"].map(to_numeric)
    out["aihw_publication_status"] = out["aihw_value_raw"].map(publication_status)

    out = out[
        (out["aihw_topic"] != "")
        & (out["aihw_measure"] != "")
    ].copy()

    return out


def extract_tableau_data_for_region(page, target: dict, year: str, timeout_ms: int) -> pd.DataFrame:
    result = page.evaluate(
        """
        async ({target, year, dataWorksheetName, downloadSheetName, timeoutMs}) => {
            const sleep = (ms) => new Promise(resolve => setTimeout(resolve, ms));
            const started = Date.now();

            async function waitForViz() {
                while ((Date.now() - started) < timeoutMs) {
                    const viz =
                        document.querySelector('tableau-viz') ||
                        document.querySelector('tableau-authoring-viz') ||
                        Array.from(document.querySelectorAll('*')).find(
                            el => String(el.tagName).toLowerCase().includes('tableau')
                        );

                    if (viz && viz.workbook) return viz;
                    await sleep(500);
                }

                throw new Error('Timed out waiting for tableau-viz workbook object.');
            }

            async function waitForWorkbook(viz) {
                if (viz.workbook) return viz.workbook;

                await new Promise((resolve) => {
                    const timer = setTimeout(resolve, timeoutMs);
                    viz.addEventListener(
                        'firstinteractive',
                        () => {
                            clearTimeout(timer);
                            resolve();
                        },
                        {once: true}
                    );
                });

                if (!viz.workbook) {
                    throw new Error('tableau-viz exists but workbook is unavailable.');
                }

                return viz.workbook;
            }

            function getReplaceEnum() {
                if (
                    window.tableau &&
                    window.tableau.FilterUpdateType &&
                    window.tableau.FilterUpdateType.Replace
                ) {
                    return window.tableau.FilterUpdateType.Replace;
                }

                return 'replace';
            }

            async function activateDownloadSheet(workbook) {
                try {
                    await workbook.activateSheetAsync(downloadSheetName);
                } catch (err) {
                    // Some embedded views already expose the dashboard as active sheet.
                }

                await sleep(750);
                return workbook.activeSheet;
            }

            function collectWorksheets(sheet) {
                if (!sheet) return [];
                if (sheet.worksheets) return Array.from(sheet.worksheets);
                return [sheet];
            }

            function chooseWorksheet(worksheets) {
                let ws = worksheets.find(w => w.name === dataWorksheetName);

                if (ws) return ws;

                ws = worksheets.find(
                    w => String(w.name || '').toLowerCase().includes('regional profiles data')
                );

                if (ws) return ws;

                ws = worksheets.find(
                    w => String(w.name || '').toLowerCase().includes('data table')
                );

                if (ws) return ws;

                if (worksheets.length === 1) return worksheets[0];

                throw new Error(
                    'Could not find data worksheet. Worksheets: '
                    + worksheets.map(w => w.name).join(' | ')
                );
            }

            async function applyFilterSafe(ws, fieldCandidates, values) {
                const updateType = getReplaceEnum();
                let lastErr = null;

                for (const field of fieldCandidates) {
                    try {
                        await ws.applyFilterAsync(
                            field,
                            Array.isArray(values) ? values : [values],
                            updateType
                        );
                        await sleep(250);
                        return field;
                    } catch (err) {
                        lastErr = err;
                    }
                }

                throw new Error(
                    'Could not apply filter. Candidates='
                    + fieldCandidates.join(' | ')
                    + '; last error='
                    + (lastErr ? lastErr.message : 'unknown')
                );
            }

            async function getSummaryData(ws) {
                let dataTable;

                try {
                    dataTable = await ws.getSummaryDataAsync({maxRows: 10000});
                } catch (err1) {
                    try {
                        dataTable = await ws.getSummaryDataAsync();
                    } catch (err2) {
                        throw new Error('getSummaryDataAsync failed: ' + err2.message);
                    }
                }

                const columns = Array.from(dataTable.columns || []).map(
                    c => c.fieldName || c.name || c.caption || c._fieldName || String(c)
                );

                const rows = Array.from(dataTable.data || []).map(row => {
                    return Array.from(row).map(cell => {
                        if (cell == null) return '';
                        if (cell.formattedValue !== undefined) return cell.formattedValue;
                        if (cell.value !== undefined) return cell.value;
                        if (cell._formattedValue !== undefined) return cell._formattedValue;
                        if (cell._value !== undefined) return cell._value;
                        return String(cell);
                    });
                });

                return {columns, rows};
            }

            const viz = await waitForViz();
            const workbook = await waitForWorkbook(viz);
            const activeSheet = await activateDownloadSheet(workbook);
            const worksheets = collectWorksheets(activeSheet);
            const worksheet = chooseWorksheet(worksheets);

            const applied = {};

            applied.regionTypeField = await applyFilterSafe(
                worksheet,
                ['Action (Geographic Area Type)', 'Geographic Area Type'],
                'SA3'
            );

            applied.stateField = await applyFilterSafe(
                worksheet,
                ['Action (State Name)', 'State Name', 'State/Territory'],
                target.state_name_2021
            );

            applied.areaField = await applyFilterSafe(
                worksheet,
                ['Action (Geographic Area Name)', 'Geographic Area Name', 'Region name'],
                target.sa3_name_2021
            );

            applied.yearField = await applyFilterSafe(
                worksheet,
                ['Year', 'Financial year'],
                year
            );

            await sleep(1200);

            let table = await getSummaryData(worksheet);
            let yearUsed = year;

            // AIHW/Tableau financial-year filters may accept either ASCII hyphen
            // (2021-22) or an en dash display token (2021–22), depending on the
            // workbook version. Try the alternate token if the first request
            // returns no rows.
            const yearAlternates = [];
            if (String(year).includes('-')) {
                yearAlternates.push(String(year).replace('-', '–'));
            }
            if (String(year).includes('–')) {
                yearAlternates.push(String(year).replace('–', '-'));
            }

            if (table.rows.length === 0) {
                for (const altYear of yearAlternates) {
                    if (altYear === yearUsed) continue;

                    try {
                        applied.yearField = await applyFilterSafe(
                            worksheet,
                            ['Year', 'Financial year'],
                            altYear
                        );
                        await sleep(1200);
                        const altTable = await getSummaryData(worksheet);

                        if (altTable.rows.length > 0) {
                            table = altTable;
                            yearUsed = altYear;
                            break;
                        }
                    } catch (err) {
                        // Continue trying alternatives.
                    }
                }
            }

            return {
                ok: true,
                target,
                year,
                yearUsed,
                activeSheetName: activeSheet ? activeSheet.name : null,
                worksheetName: worksheet.name,
                appliedFilters: applied,
                columns: table.columns,
                rows: table.rows,
                rowCount: table.rows.length
            };
        }
        """,
        {
            "target": target,
            "year": year,
            "dataWorksheetName": DATA_WORKSHEET_NAME,
            "downloadSheetName": DOWNLOAD_SHEET_NAME,
            "timeoutMs": timeout_ms,
        },
    )

    if not result.get("ok"):
        raise RuntimeError(json.dumps(result, indent=2))

    return pd.DataFrame(result["rows"], columns=result["columns"])


def write_probe(page, timeout_ms: int):
    result = page.evaluate(
        """
        async ({timeoutMs}) => {
            const sleep = (ms) => new Promise(resolve => setTimeout(resolve, ms));
            const started = Date.now();

            while ((Date.now() - started) < timeoutMs) {
                const viz =
                    document.querySelector('tableau-viz') ||
                    document.querySelector('tableau-authoring-viz') ||
                    Array.from(document.querySelectorAll('*')).find(
                        el => String(el.tagName).toLowerCase().includes('tableau')
                    );

                if (viz && viz.workbook) {
                    const workbook = viz.workbook;

                    let published = [];

                    try {
                        published = Array.from(workbook.publishedSheetsInfo || []).map(
                            s => ({name: s.name, sheetType: String(s.sheetType)})
                        );
                    } catch (err) {}

                    let active = null;

                    try {
                        active = workbook.activeSheet
                            ? {
                                name: workbook.activeSheet.name,
                                sheetType: String(workbook.activeSheet.sheetType)
                              }
                            : null;
                    } catch (err) {}

                    return {
                        ok: true,
                        publishedSheetsInfo: published,
                        activeSheet: active
                    };
                }

                await sleep(500);
            }

            return {ok: false, error: 'No tableau-viz workbook found.'};
        }
        """,
        {"timeoutMs": timeout_ms},
    )

    PROBE_JSON.write_text(json.dumps(result, indent=2), encoding="utf-8")


def save_individual_raw(df: pd.DataFrame, target: dict, year: str):
    filename = (
        f"aihw_regional_profiles_sa3_"
        f"{target['sa3_code_2021']}_"
        f"{slug(target['sa3_name_2021'], 70)}_"
        f"{slug(year)}.csv"
    )

    path = INDIVIDUAL_DIR / filename
    df.to_csv(path, index=False, encoding="utf-8-sig")
    return path


def individual_raw_path(target: dict, year: str) -> Path:
    filename = (
        f"aihw_regional_profiles_sa3_"
        f"{target['sa3_code_2021']}_"
        f"{slug(target['sa3_name_2021'], 70)}_"
        f"{slug(year)}.csv"
    )
    return INDIVIDUAL_DIR / filename


def build_selected_measures(long: pd.DataFrame, years: list[str]) -> pd.DataFrame:
    base = (
        long[["sa3_code_2021", "sa3_name_2021", "state_name_2021"]]
        .drop_duplicates()
        .copy()
    )

    base["sa3_code_2021"] = base["sa3_code_2021"].astype(str)
    out = base.copy()
    dictionary_rows = []

    for year in years:
        suffix = slug(year)

        for spec in PRIMARY_MEASURES:
            var = f"{spec['variable_stem']}_{suffix}"
            source_var = f"{var}_source_value"
            status_var = f"{var}_publication_status"

            mask = (
                long["year_source"].map(normalise_year_token).eq(normalise_year_token(year))
                & long["aihw_topic"].eq(spec["topic"])
                & long["aihw_age_group"].eq(spec["age_group"])
                & long["aihw_measure"].eq(spec["measure"])
                & long["aihw_metric"].eq(spec["metric"])
            )

            if spec["practitioner"] == "":
                mask = mask & long["aihw_practitioner"].fillna("").str.strip().eq("")
            else:
                mask = mask & long["aihw_practitioner"].eq(spec["practitioner"])

            selected = long.loc[
                mask,
                [
                    "sa3_code_2021",
                    "aihw_value_numeric",
                    "aihw_value_raw",
                    "aihw_publication_status",
                ],
            ].copy()

            if selected.empty:
                out[var] = pd.NA
                out[source_var] = pd.NA
                out[status_var] = "not_found_in_extract"
            else:
                selected = selected.drop_duplicates(
                    subset=["sa3_code_2021"],
                    keep="first",
                )

                selected = selected.rename(
                    columns={
                        "aihw_value_numeric": var,
                        "aihw_value_raw": source_var,
                        "aihw_publication_status": status_var,
                    }
                )

                out = out.merge(selected, on="sa3_code_2021", how="left")

            dictionary_rows.append(
                {
                    "column_name": var,
                    "source": "AIHW Regional profiles of mental health service activity",
                    "native_geography": "SA3",
                    "analysis_year": year,
                    "topic": spec["topic"],
                    "practitioner": spec["practitioner"] or "Not applicable",
                    "age_group": spec["age_group"],
                    "measure": spec["measure"],
                    "metric": spec["metric"],
                    "field_role": "candidate_predictor_aihw_sa3_service_activity",
                    "modelling_warning": (
                        "SA3-level predictor repeated across SA2 rows after SA3-to-SA2 join. "
                        "Use grouped validation by SA3."
                    ),
                }
            )

    numeric_cols = [
        c for c in out.columns
        if c.startswith("aihw_")
        and not c.endswith("_source_value")
        and not c.endswith("_publication_status")
    ]

    last_suffix = slug(years[-1])

    out[f"aihw_primary_measures_numeric_count_{last_suffix}"] = (
        out[numeric_cols].notna().sum(axis=1)
    )

    out[f"aihw_primary_measures_complete_numeric_{last_suffix}"] = (
        out[numeric_cols].notna().sum(axis=1).eq(len(numeric_cols))
    )

    out["has_aihw_sa3_regional_profile_extract"] = True

    pd.DataFrame(dictionary_rows).to_csv(
        SELECTED_DICTIONARY,
        index=False,
        encoding="utf-8-sig",
    )

    return out


def main():
    parser = argparse.ArgumentParser(
        description="Extract AIHW Regional Profiles SA3 data through the embedded Tableau API."
    )

    parser.add_argument(
        "--years",
        default="2021-22",
        help="Comma-separated financial years, e.g. 2021-22 or 2021–22,2023–24",
    )

    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run Chromium headless. Default is visible browser.",
    )

    parser.add_argument(
        "--include-special-sa3",
        action="store_true",
        help="Include no-usual-address/migratory/offshore SA3 records in target list.",
    )

    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Optional test limit for SA3 target count.",
    )

    parser.add_argument(
        "--resume",
        action="store_true",
        help="Skip existing individual raw CSV files.",
    )

    parser.add_argument(
        "--slowmo",
        type=int,
        default=0,
        help="Playwright slow_mo milliseconds for debugging.",
    )

    parser.add_argument(
        "--timeout-ms",
        type=int,
        default=90000,
        help="Tableau wait timeout per operation in milliseconds.",
    )

    args = parser.parse_args()
    years = parse_years(args.years)

    ensure_playwright()

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    INDIVIDUAL_DIR.mkdir(parents=True, exist_ok=True)
    PROCESSED_SOURCE_DIR.mkdir(parents=True, exist_ok=True)
    AUDIT_DIR.mkdir(parents=True, exist_ok=True)
    DICT_DIR.mkdir(parents=True, exist_ok=True)

    targets = load_sa3_targets(include_special=args.include_special_sa3)

    if args.limit and args.limit > 0:
        targets = targets.head(args.limit).copy()

    print(f"Target SA3 regions: {len(targets)}")
    print(f"Target years: {', '.join(years)}")
    print(f"Headless: {args.headless}")

    from playwright.sync_api import sync_playwright

    log_rows = []
    long_parts = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=args.headless, slow_mo=args.slowmo)

        context = browser.new_context(
            viewport={"width": 1600, "height": 1000},
            accept_downloads=True,
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/126 Safari/537.36"
            ),
        )

        page = context.new_page()
        page.set_default_timeout(args.timeout_ms)

        print("Opening AIHW Regional Profiles page:")
        print(AIHW_PAGE_URL)

        page.goto(AIHW_PAGE_URL, wait_until="load", timeout=args.timeout_ms)

        # Do not wait for "networkidle". The AIHW/Tableau page keeps background
        # requests open, so Playwright can time out even after the page has loaded.
        try:
            page.wait_for_selector(
                "tableau-viz, tableau-authoring-viz, iframe[src*='viz.aihw.gov.au']",
                timeout=args.timeout_ms,
            )
        except Exception:
            print("Warning: Tableau element/iframe was not detected by selector. Continuing to API probe.")

        time.sleep(8)

        write_probe(page, args.timeout_ms)
        print(f"Created probe: {PROBE_JSON}")

        total = len(targets) * len(years)
        counter = 0

        for year in years:
            for _, t in targets.iterrows():
                counter += 1

                target = {
                    "sa3_code_2021": str(t["sa3_code_2021"]),
                    "sa3_name_2021": str(t["sa3_name_2021"]),
                    "state_name_2021": str(t["state_name_2021"]),
                }

                existing_raw_path = individual_raw_path(target, year)

                print(
                    f"[{counter}/{total}] {year} | "
                    f"{target['state_name_2021']} | "
                    f"{target['sa3_code_2021']} {target['sa3_name_2021']}"
                )

                if args.resume and existing_raw_path.exists():
                    try:
                        raw = pd.read_csv(existing_raw_path, dtype=str)
                        norm = normalise_extracted_table(raw, target, year)
                        long_parts.append(norm)

                        log_rows.append(
                            {
                                "request_timestamp_utc": datetime.now(timezone.utc).isoformat(),
                                **target,
                                "year_requested": year,
                                "status": "cached",
                                "records_retrieved": len(norm),
                                "raw_file": str(existing_raw_path),
                                "error": "",
                            }
                        )

                        continue

                    except Exception as exc:
                        print(f"  Cached file could not be reused; refetching. Reason: {exc}")

                try:
                    raw = extract_tableau_data_for_region(page, target, year, args.timeout_ms)
                    saved = save_individual_raw(raw, target, year)
                    norm = normalise_extracted_table(raw, target, year)

                    long_parts.append(norm)

                    status = "success" if len(norm) > 0 else "success_no_profile_rows"

                    log_rows.append(
                        {
                            "request_timestamp_utc": datetime.now(timezone.utc).isoformat(),
                            **target,
                            "year_requested": year,
                            "status": status,
                            "records_retrieved": len(norm),
                            "raw_file": str(saved),
                            "error": "",
                        }
                    )

                    print(f"  records: {len(norm)}")

                except Exception as exc:
                    log_rows.append(
                        {
                            "request_timestamp_utc": datetime.now(timezone.utc).isoformat(),
                            **target,
                            "year_requested": year,
                            "status": "fail",
                            "records_retrieved": 0,
                            "raw_file": "",
                            "error": str(exc),
                        }
                    )

                    print(f"  FAILED: {exc}")

                time.sleep(1.0)

        browser.close()

    log = pd.DataFrame(log_rows)
    log.to_csv(EXTRACTION_LOG, index=False, encoding="utf-8-sig")

    failures = log[log["status"].eq("fail")].copy()
    failures.to_csv(FAILURES_CSV, index=False, encoding="utf-8-sig")

    if long_parts:
        long = pd.concat(long_parts, ignore_index=True)
    else:
        long = pd.DataFrame()

    if long.empty:
        raise RuntimeError(f"No AIHW rows extracted. Review log: {EXTRACTION_LOG}")

    long = long.drop_duplicates().reset_index(drop=True)

    long.to_csv(LONG_CSV, index=False, encoding="utf-8-sig")
    long.to_parquet(LONG_PARQUET, index=False)

    selected = build_selected_measures(long, years)

    selected.to_csv(SELECTED_CSV, index=False, encoding="utf-8-sig")
    selected.to_parquet(SELECTED_PARQUET, index=False)

    summary = pd.DataFrame(
        [
            {
                "run_timestamp_utc": datetime.now(timezone.utc).isoformat(),
                "sa3_targets_requested": targets["sa3_code_2021"].nunique(),
                "years_requested": ", ".join(years),
                "region_year_requests": len(log),
                "region_years_successfully_extracted": int(
                    log["status"].isin(["success", "cached"]).sum()
                ),
                "region_years_successfully_extracted_no_profile_rows": int(
                    log["status"].eq("success_no_profile_rows").sum()
                ),
                "failed_region_year_requests": int(log["status"].eq("fail").sum()),
                "sa3_regions_with_any_successful_extraction": int(
                    long["sa3_code_2021"].nunique()
                ),
                "long_format_records_retrieved": len(long),
                "selected_measure_rows": len(selected),
                "source_method": "AIHW official page embedded Tableau API v3 via Playwright",
                "notes": (
                    "Raw formatted source values retained. n.p./n.a. converted to missing "
                    "numeric values and publication status retained."
                ),
            }
        ]
    )

    summary.to_csv(COVERAGE_SUMMARY, index=False, encoding="utf-8-sig")

    print("\nCreated outputs:")
    print(LONG_CSV)
    print(LONG_PARQUET)
    print(SELECTED_CSV)
    print(SELECTED_PARQUET)
    print(EXTRACTION_LOG)
    print(COVERAGE_SUMMARY)
    print(FAILURES_CSV)
    print(SELECTED_DICTIONARY)

    print("\nCoverage summary:")
    print(summary.to_string(index=False))


if __name__ == "__main__":
    main()
