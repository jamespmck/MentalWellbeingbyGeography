from pathlib import Path
import json
import pandas as pd

PROJECT_ROOT = Path(r"D:\Good Measure\MentalWellbeingbyGeography")

PROBE_JSON = PROJECT_ROOT / "outputs" / "audits" / "aihw_embedding_api_probe_2021_22.json"
LOG_CSV = PROJECT_ROOT / "outputs" / "audits" / "aihw_sa3_embedding_extraction_log_2021_22.csv"
RAW_DIR = PROJECT_ROOT / "data" / "raw" / "aihw" / "regional_profiles_sa3_embedding_api" / "individual"

OUT_CSV = PROJECT_ROOT / "outputs" / "audits" / "aihw_empty_extract_raw_file_diagnostic.csv"


def main():
    print("Checking AIHW empty extract diagnostics")

    print("\nProbe JSON:")
    print(PROBE_JSON)

    if PROBE_JSON.exists():
        try:
            probe = json.loads(PROBE_JSON.read_text(encoding="utf-8"))
            print(json.dumps(probe, indent=2)[:5000])
        except Exception as exc:
            print(f"Could not read probe JSON: {exc}")
    else:
        print("Probe JSON does not exist.")

    print("\nExtraction log:")
    print(LOG_CSV)

    if LOG_CSV.exists():
        log = pd.read_csv(LOG_CSV, dtype=str)
        print(log.to_string(index=False))
    else:
        print("Extraction log does not exist.")

    print("\nRaw individual folder:")
    print(RAW_DIR)

    if not RAW_DIR.exists():
        print("Raw folder does not exist.")
        return

    files = sorted(RAW_DIR.glob("*.csv"))

    if not files:
        print("No raw individual CSVs found.")
        return

    rows = []

    for file in files:
        print(f"\n--- {file.name} ---")

        try:
            df = pd.read_csv(file, dtype=str)
            print(f"shape: {df.shape}")
            print("columns:")
            print(list(df.columns))

            sample = df.head(5)
            print("\nfirst rows:")
            print(sample.to_string(index=False))

            expected_cols = [
                "Year",
                "Topics",
                "Practitioner",
                "Age Group",
                "Measure",
                "Metric",
                "Geographic Area Name",
                "Geographic Area Type",
                "SUM(Values)",
                "Values",
                "Value",
                "AGG(Values)",
            ]

            present_expected = [c for c in expected_cols if c in df.columns]

            rows.append(
                {
                    "file_name": file.name,
                    "row_count": len(df),
                    "column_count": len(df.columns),
                    "present_expected_columns": " | ".join(present_expected),
                    "columns": " | ".join(map(str, df.columns)),
                    "year_values": " | ".join(sorted(df["Year"].dropna().astype(str).unique())[:20]) if "Year" in df.columns else "",
                    "topic_values": " | ".join(sorted(df["Topics"].dropna().astype(str).unique())[:20]) if "Topics" in df.columns else "",
                    "measure_values": " | ".join(sorted(df["Measure"].dropna().astype(str).unique())[:20]) if "Measure" in df.columns else "",
                    "metric_values": " | ".join(sorted(df["Metric"].dropna().astype(str).unique())[:20]) if "Metric" in df.columns else "",
                    "geographic_area_name_values": " | ".join(sorted(df["Geographic Area Name"].dropna().astype(str).unique())[:20]) if "Geographic Area Name" in df.columns else "",
                    "geographic_area_type_values": " | ".join(sorted(df["Geographic Area Type"].dropna().astype(str).unique())[:20]) if "Geographic Area Type" in df.columns else "",
                }
            )

        except Exception as exc:
            print(f"FAILED to inspect file: {exc}")
            rows.append(
                {
                    "file_name": file.name,
                    "row_count": "",
                    "column_count": "",
                    "present_expected_columns": "",
                    "columns": "",
                    "year_values": "",
                    "topic_values": "",
                    "measure_values": "",
                    "metric_values": "",
                    "geographic_area_name_values": "",
                    "geographic_area_type_values": "",
                    "error": str(exc),
                }
            )

    diagnostic = pd.DataFrame(rows)
    diagnostic.to_csv(OUT_CSV, index=False, encoding="utf-8-sig")

    print("\nCreated diagnostic audit:")
    print(OUT_CSV)


if __name__ == "__main__":
    main()
