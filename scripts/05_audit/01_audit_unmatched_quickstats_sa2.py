from pathlib import Path
import pandas as pd

root = Path(r"D:\Good Measure\MentalWellbeingbyGeography")

master_path = root / "data" / "processed" / "integrated" / "sa2_predictor_universe_v01.parquet"
parse_audit_path = root / "outputs" / "audits" / "sa2_census_2021_quickstats_parse_audit.csv"
download_audit_path = root / "outputs" / "audits" / "abs_census_2021_quickstats_sa2_download_audit.csv"

out_path = root / "outputs" / "audits" / "sa2_census_2021_quickstats_unmatched_sa2_audit.csv"

master = pd.read_parquet(master_path)
parse_audit = pd.read_csv(parse_audit_path, dtype=str)
download_audit = pd.read_csv(download_audit_path, dtype=str)

flag = "source_census_quickstats_2021_present_flag"

unmatched = master.loc[
    ~master[flag].fillna(False).astype(bool),
    [
        "sa2_code_2021",
        "sa2_name_2021",
        "sa3_code_2021",
        "sa3_name_2021",
        "sa4_code_2021",
        "sa4_name_2021",
        "state_code_2021",
        "state_name_2021",
    ]
].copy()

unmatched["sa2_code_2021"] = unmatched["sa2_code_2021"].astype(str)
parse_audit["sa2_code_2021"] = parse_audit["sa2_code_2021"].astype(str)
download_audit["sa2_code_2021"] = download_audit["sa2_code_2021"].astype(str)

unmatched = unmatched.merge(
    download_audit,
    on="sa2_code_2021",
    how="left"
)

unmatched = unmatched.merge(
    parse_audit,
    on="sa2_code_2021",
    how="left",
    suffixes=("_download", "_parse")
)

unmatched.to_csv(out_path, index=False, encoding="utf-8-sig")

print("Created unmatched QuickStats audit:")
print(out_path)

print("\nUnmatched SA2 count:")
print(len(unmatched))

print("\nDownload status counts:")
print(unmatched["download_status"].value_counts(dropna=False).to_string())

print("\nParse status counts:")
print(unmatched["status"].value_counts(dropna=False).to_string())

print("\nUnmatched by state:")
print(unmatched["state_name_2021"].value_counts(dropna=False).to_string())

print("\nFirst 80 unmatched SA2s:")
cols = [
    "sa2_code_2021",
    "sa2_name_2021",
    "sa3_name_2021",
    "state_name_2021",
    "download_status",
    "status",
    "notes",
]
print(unmatched[cols].head(80).to_string(index=False))
