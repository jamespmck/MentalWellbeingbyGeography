from pathlib import Path
import re
import pandas as pd

PROJECT_ROOT = Path(r"D:\Good Measure\MentalWellbeingbyGeography")

SPINE_PATH = PROJECT_ROOT / "data" / "processed" / "spines" / "sa2_2021_spine.parquet"

RAW_REMOTENESS_DIR = PROJECT_ROOT / "data" / "raw" / "abs" / "remoteness"

SA1_FILE = RAW_REMOTENESS_DIR / "SA1_2021_AUST.xlsx"
RA_FILE = RAW_REMOTENESS_DIR / "RA_2021_AUST.xlsx"

PROCESSED_SOURCE_DIR = PROJECT_ROOT / "data" / "processed" / "sources"
AUDIT_DIR = PROJECT_ROOT / "outputs" / "audits"

OUTPUT_CSV = PROCESSED_SOURCE_DIR / "sa2_remoteness_2021.csv"
OUTPUT_PARQUET = PROCESSED_SOURCE_DIR / "sa2_remoteness_2021.parquet"
PROCESSING_AUDIT = AUDIT_DIR / "sa2_remoteness_2021_processing_audit.csv"
DISTRIBUTION_OUTPUT = AUDIT_DIR / "sa2_remoteness_2021_distribution.csv"

RA_ORDINAL = {
    "Major Cities of Australia": 1,
    "Inner Regional Australia": 2,
    "Outer Regional Australia": 3,
    "Remote Australia": 4,
    "Very Remote Australia": 5,
    "Migratory - Offshore - Shipping": pd.NA,
}


def clean_text(value):
    if pd.isna(value):
        return pd.NA

    text = str(value).strip()
    text = re.sub(r"\s+", " ", text)

    if text == "" or text.lower() in {"nan", "none", "null"}:
        return pd.NA

    return text


def normalise_code(value):
    if pd.isna(value):
        return pd.NA

    value = str(value).strip()

    if value == "" or value.lower() in {"nan", "none", "null"}:
        return pd.NA

    if re.fullmatch(r"\d+\.0", value):
        value = value[:-2]

    return value


def require_file(path: Path) -> None:
    if not path.exists():
        raise FileNotFoundError(
            f"Required file not found: {path}\n"
            "The previous download step should have created this file."
        )


def load_spine() -> pd.DataFrame:
    require_file(SPINE_PATH)

    spine = pd.read_parquet(SPINE_PATH)

    required = ["sa2_code_2021", "sa2_name_2021"]

    missing = [col for col in required if col not in spine.columns]
    if missing:
        raise ValueError(f"Spine missing required columns: {missing}")

    out = spine[required].copy()
    out["sa2_code_2021"] = out["sa2_code_2021"].map(normalise_code).astype("string")
    out["sa2_name_2021"] = out["sa2_name_2021"].map(clean_text).astype("string")

    if out["sa2_code_2021"].duplicated().any():
        raise ValueError("SA2 spine contains duplicate SA2 codes.")

    return out


def load_sa1_to_sa2() -> pd.DataFrame:
    require_file(SA1_FILE)

    print(f"Reading SA1 structure file: {SA1_FILE}")

    df = pd.read_excel(
        SA1_FILE,
        sheet_name=0,
        dtype=str,
        usecols=[
            "SA1_CODE_2021",
            "SA2_CODE_2021",
            "SA2_NAME_2021",
            "AREA_ALBERS_SQKM",
        ],
    )

    df = df.rename(
        columns={
            "SA1_CODE_2021": "sa1_code_2021",
            "SA2_CODE_2021": "sa2_code_2021",
            "SA2_NAME_2021": "sa2_name_2021",
            "AREA_ALBERS_SQKM": "sa1_area_albers_sqkm",
        }
    )

    df["sa1_code_2021"] = df["sa1_code_2021"].map(normalise_code).astype("string")
    df["sa2_code_2021"] = df["sa2_code_2021"].map(normalise_code).astype("string")
    df["sa2_name_2021"] = df["sa2_name_2021"].map(clean_text).astype("string")
    df["sa1_area_albers_sqkm"] = pd.to_numeric(df["sa1_area_albers_sqkm"], errors="coerce")

    df = df.dropna(subset=["sa1_code_2021", "sa2_code_2021"])

    return df


def load_sa1_to_ra() -> pd.DataFrame:
    require_file(RA_FILE)

    print(f"Reading RA allocation file: {RA_FILE}")

    df = pd.read_excel(
        RA_FILE,
        sheet_name=0,
        dtype=str,
        usecols=[
            "SA1_CODE_2021",
            "RA_CODE_2021",
            "RA_NAME_2021",
            "AREA_ALBERS_SQKM",
        ],
    )

    df = df.rename(
        columns={
            "SA1_CODE_2021": "sa1_code_2021",
            "RA_CODE_2021": "remoteness_area_code_2021",
            "RA_NAME_2021": "remoteness_area_name_2021",
            "AREA_ALBERS_SQKM": "ra_sa1_area_albers_sqkm",
        }
    )

    df["sa1_code_2021"] = df["sa1_code_2021"].map(normalise_code).astype("string")
    df["remoteness_area_code_2021"] = df["remoteness_area_code_2021"].map(normalise_code).astype("string")
    df["remoteness_area_name_2021"] = df["remoteness_area_name_2021"].map(clean_text).astype("string")
    df["ra_sa1_area_albers_sqkm"] = pd.to_numeric(df["ra_sa1_area_albers_sqkm"], errors="coerce")

    df = df.dropna(subset=["sa1_code_2021", "remoteness_area_name_2021"])

    return df


def build_sa2_remoteness() -> pd.DataFrame:
    spine = load_spine()
    sa1_to_sa2 = load_sa1_to_sa2()
    sa1_to_ra = load_sa1_to_ra()

    print("Joining SA1 → SA2 to SA1 → Remoteness Area.")

    joined = sa1_to_sa2.merge(
        sa1_to_ra,
        on="sa1_code_2021",
        how="left",
        validate="one_to_one",
    )

    missing_ra_at_sa1 = int(joined["remoteness_area_name_2021"].isna().sum())
    if missing_ra_at_sa1 > 0:
        print(f"WARNING: SA1 rows without remoteness after join: {missing_ra_at_sa1}")

    # Use RA file area where available; otherwise use SA1 structure area.
    joined["allocation_area_sqkm"] = joined["ra_sa1_area_albers_sqkm"].fillna(
        joined["sa1_area_albers_sqkm"]
    )

    joined["allocation_area_sqkm"] = joined["allocation_area_sqkm"].fillna(0)

    grouped = (
        joined
        .dropna(subset=["sa2_code_2021", "remoteness_area_name_2021"])
        .groupby(
            [
                "sa2_code_2021",
                "remoteness_area_code_2021",
                "remoteness_area_name_2021",
            ],
            dropna=False,
        )
        .agg(
            remoteness_sa1_count=("sa1_code_2021", "count"),
            remoteness_area_sqkm=("allocation_area_sqkm", "sum"),
        )
        .reset_index()
    )

    totals = (
        grouped
        .groupby("sa2_code_2021")
        .agg(
            sa2_total_ra_area_sqkm=("remoteness_area_sqkm", "sum"),
            sa2_ra_category_count=("remoteness_area_name_2021", "nunique"),
        )
        .reset_index()
    )

    grouped = grouped.merge(totals, on="sa2_code_2021", how="left")

    grouped["remoteness_area_share_of_sa2"] = (
        grouped["remoteness_area_sqkm"] / grouped["sa2_total_ra_area_sqkm"]
    )

    grouped = grouped.sort_values(
        by=[
            "sa2_code_2021",
            "remoteness_area_share_of_sa2",
            "remoteness_sa1_count",
            "remoteness_area_code_2021",
        ],
        ascending=[True, False, False, True],
    )

    dominant = grouped.drop_duplicates(subset=["sa2_code_2021"], keep="first").copy()

    dominant["remoteness_area_ordinal_2021"] = (
        dominant["remoteness_area_name_2021"]
        .map(RA_ORDINAL)
        .astype("Int64")
    )

    dominant["remoteness_area_short_2021"] = (
        dominant["remoteness_area_name_2021"]
        .astype("string")
        .str.replace(" of Australia", "", regex=False)
        .str.replace(" Australia", "", regex=False)
    )

    dominant["remoteness_assignment_method"] = "dominant_ra_by_sa1_area_within_sa2"

    keep_cols = [
        "sa2_code_2021",
        "remoteness_area_code_2021",
        "remoteness_area_name_2021",
        "remoteness_area_short_2021",
        "remoteness_area_ordinal_2021",
        "remoteness_area_share_of_sa2",
        "remoteness_sa1_count",
        "sa2_ra_category_count",
        "remoteness_assignment_method",
    ]

    remoteness = dominant[keep_cols].copy()

    out = spine.merge(
        remoteness,
        on="sa2_code_2021",
        how="left",
        validate="one_to_one",
    )

    return out


def build_audit(df: pd.DataFrame) -> pd.DataFrame:
    rows = []

    rows.extend(
        [
            {
                "check_name": "source_sa1_file",
                "value": str(SA1_FILE),
                "status": "info",
                "notes": "SA1-to-SA2 ASGS file used.",
            },
            {
                "check_name": "source_ra_file",
                "value": str(RA_FILE),
                "status": "info",
                "notes": "SA1-to-Remoteness Area ASGS file used.",
            },
            {
                "check_name": "row_count",
                "value": len(df),
                "status": "info",
                "notes": "Rows in processed SA2 remoteness source table.",
            },
            {
                "check_name": "unique_sa2_count",
                "value": df["sa2_code_2021"].nunique(dropna=True),
                "status": "info",
                "notes": "Unique SA2 codes.",
            },
            {
                "check_name": "missing_sa2_code",
                "value": int(df["sa2_code_2021"].isna().sum()),
                "status": "pass" if int(df["sa2_code_2021"].isna().sum()) == 0 else "fail",
                "notes": "SA2 code must not be missing.",
            },
            {
                "check_name": "duplicate_sa2_rows",
                "value": int(df["sa2_code_2021"].duplicated().sum()),
                "status": "pass" if int(df["sa2_code_2021"].duplicated().sum()) == 0 else "fail",
                "notes": "Output must remain one row per SA2.",
            },
            {
                "check_name": "missing_remoteness_name",
                "value": int(df["remoteness_area_name_2021"].isna().sum()),
                "status": "pass" if int(df["remoteness_area_name_2021"].isna().sum()) == 0 else "review",
                "notes": "SA2 rows without a remoteness category.",
            },
            {
                "check_name": "multi_ra_sa2_count",
                "value": int((df["sa2_ra_category_count"] > 1).sum()),
                "status": "info",
                "notes": "SA2s crossing more than one remoteness category; dominant category assigned.",
            },
            {
                "check_name": "lowest_dominant_share",
                "value": round(float(df["remoteness_area_share_of_sa2"].min()), 6),
                "status": "info",
                "notes": "Lowest dominant RA area share across SA2s.",
            },
        ]
    )

    counts = (
        df["remoteness_area_name_2021"]
        .fillna("MISSING")
        .value_counts(dropna=False)
        .reset_index()
    )
    counts.columns = ["remoteness_area_name_2021", "count"]

    for _, row in counts.iterrows():
        rows.append(
            {
                "check_name": f"count__{row['remoteness_area_name_2021']}",
                "value": int(row["count"]),
                "status": "info",
                "notes": "SA2 count by assigned remoteness category.",
            }
        )

    return pd.DataFrame(rows)


def main() -> None:
    PROCESSED_SOURCE_DIR.mkdir(parents=True, exist_ok=True)
    AUDIT_DIR.mkdir(parents=True, exist_ok=True)

    remoteness = build_sa2_remoteness()
    audit = build_audit(remoteness)

    distribution = (
        remoteness
        .groupby(
            [
                "remoteness_area_code_2021",
                "remoteness_area_name_2021",
                "remoteness_area_short_2021",
                "remoteness_area_ordinal_2021",
            ],
            dropna=False,
        )
        .agg(sa2_count=("sa2_code_2021", "count"))
        .reset_index()
        .sort_values("remoteness_area_ordinal_2021", na_position="last")
    )

    remoteness.to_csv(OUTPUT_CSV, index=False, encoding="utf-8-sig")

    parquet_status = "written"
    try:
        remoteness.to_parquet(OUTPUT_PARQUET, index=False)
    except Exception as exc:
        parquet_status = f"not written: {exc}"

    audit.to_csv(PROCESSING_AUDIT, index=False, encoding="utf-8-sig")
    distribution.to_csv(DISTRIBUTION_OUTPUT, index=False, encoding="utf-8-sig")

    print("Created SA2 remoteness source table:")
    print(f"  {OUTPUT_CSV}")
    print(f"  {OUTPUT_PARQUET} ({parquet_status})")

    print("\nCreated audits:")
    print(f"  {PROCESSING_AUDIT}")
    print(f"  {DISTRIBUTION_OUTPUT}")

    print("\nProcessing audit:")
    print(audit.to_string(index=False))

    print("\nDistribution:")
    print(distribution.to_string(index=False))

    failed = audit.query("status == 'fail'")
    if not failed.empty:
        raise RuntimeError(f"Remoteness validation failed. Review {PROCESSING_AUDIT}")

    print("\nSA2 remoteness 2021 source table created.")


if __name__ == "__main__":
    main()
