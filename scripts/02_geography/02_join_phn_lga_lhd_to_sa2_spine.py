from pathlib import Path
import re
import pandas as pd

PROJECT_ROOT = Path(r"D:\Good Measure\MentalWellbeingbyGeography")

SPINE_INPUT = PROJECT_ROOT / "data" / "processed" / "spines" / "sa2_2021_spine.parquet"

BRIDGE_DIRS = [
    PROJECT_ROOT / "data" / "raw" / "abs" / "geography",
    PROJECT_ROOT / "data" / "interim" / "geography",
    PROJECT_ROOT / "data" / "processed" / "spines",
]

OUTPUT_CSV = PROJECT_ROOT / "data" / "processed" / "spines" / "sa2_2021_geography_spine.csv"
OUTPUT_PARQUET = PROJECT_ROOT / "data" / "processed" / "spines" / "sa2_2021_geography_spine.parquet"
AUDIT_OUTPUT = PROJECT_ROOT / "outputs" / "audits" / "sa2_geography_spine_join_audit.csv"


def clean_col_name(col: str) -> str:
    col = str(col).strip().lower()
    col = re.sub(r"[^a-z0-9]+", "_", col)
    col = re.sub(r"_+", "_", col)
    return col.strip("_")


def normalise_code(value):
    if pd.isna(value):
        return pd.NA

    value = str(value).strip()

    if value == "" or value.lower() in {"nan", "none", "null"}:
        return pd.NA

    if re.fullmatch(r"\d+\.0", value):
        value = value[:-2]

    return value


def read_table(path: Path) -> pd.DataFrame:
    suffix = path.suffix.lower()

    if suffix == ".csv":
        return pd.read_csv(path, dtype=str, low_memory=False)

    if suffix in {".xlsx", ".xls"}:
        return pd.read_excel(path, dtype=str)

    if suffix == ".parquet":
        return pd.read_parquet(path)

    raise ValueError(f"Unsupported file type: {path}")


def standardise_columns(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out.columns = [clean_col_name(c) for c in out.columns]

    rename_candidates = {
        "sa2_code_2021": [
            "sa2_code_2021",
            "sa2_maincode_2021",
            "sa2_main_code_2021",
            "sa2_code",
            "sa2_maincode",
            "sa2_main_code",
        ],
        "phn_code": [
            "phn_code",
            "phn_code_2021",
            "phn_code_2017",
            "primary_health_network_code",
            "primary_health_network_code_2021",
        ],
        "phn_name": [
            "phn_name",
            "phn_name_2021",
            "phn_name_2017",
            "primary_health_network_name",
            "primary_health_network_name_2021",
        ],
        "lga_code_2021": [
            "lga_code_2021",
            "lga_code",
            "lga_code_2022",
            "lga_code_2023",
            "local_government_area_code",
        ],
        "lga_name_2021": [
            "lga_name_2021",
            "lga_name",
            "lga_name_2022",
            "lga_name_2023",
            "local_government_area_name",
        ],
        "lhd_code": [
            "lhd_code",
            "lhd_code_2021",
            "local_health_district_code",
            "health_district_code",
        ],
        "lhd_name": [
            "lhd_name",
            "lhd_name_2021",
            "local_health_district_name",
            "health_district_name",
        ],
        "allocation_weight": [
            "allocation_weight",
            "weight",
            "ratio",
            "population_ratio",
            "area_ratio",
            "percentage",
            "percent",
            "proportion",
            "proportion_allocated",
        ],
    }

    rename_map = {}

    for standard_name, possible_names in rename_candidates.items():
        for possible_name in possible_names:
            if possible_name in out.columns:
                rename_map[possible_name] = standard_name
                break

    return out.rename(columns=rename_map)


def find_bridge_file(kind: str) -> Path | None:
    files = []

    for directory in BRIDGE_DIRS:
        if not directory.exists():
            continue

        for pattern in ["*.csv", "*.xlsx", "*.xls", "*.parquet"]:
            files.extend(directory.glob(pattern))

    scored = []

    for path in files:
        name = path.name.lower()
        score = 0

        if kind.lower() in name:
            score += 10
        if "sa2" in name:
            score += 5
        if "2021" in name:
            score += 2
        if "correspondence" in name or "allocation" in name or "bridge" in name:
            score += 3

        scored.append((score, path))

    scored = sorted(scored, key=lambda x: x[0], reverse=True)

    if not scored or scored[0][0] == 0:
        return None

    return scored[0][1]


def collapse_to_one_row_per_sa2(df: pd.DataFrame, region_cols: list[str], kind: str) -> pd.DataFrame:
    if "sa2_code_2021" not in df.columns:
        raise ValueError(f"{kind} bridge does not contain sa2_code_2021 after standardisation.")

    missing_region_cols = [col for col in region_cols if col not in df.columns]
    if missing_region_cols:
        raise ValueError(f"{kind} bridge missing expected region columns: {missing_region_cols}")

    keep_cols = ["sa2_code_2021"] + region_cols

    if "allocation_weight" in df.columns:
        keep_cols.append("allocation_weight")

    out = df[keep_cols].copy()

    for col in out.columns:
        out[col] = out[col].map(normalise_code).astype("string")

    out = out.dropna(subset=["sa2_code_2021"])
    out = out.drop_duplicates()

    if "allocation_weight" in out.columns:
        numeric_weight = pd.to_numeric(out["allocation_weight"], errors="coerce")
        out["_weight_numeric"] = numeric_weight
        out = out.sort_values(
            by=["sa2_code_2021", "_weight_numeric"],
            ascending=[True, False],
        )
        out[f"{kind.lower()}_allocation_method"] = "dominant_by_available_weight"
        out[f"{kind.lower()}_allocation_weight"] = out["allocation_weight"]
        out = out.drop(columns=["allocation_weight", "_weight_numeric"])
    else:
        out[f"{kind.lower()}_allocation_method"] = "single_or_first_record_no_weight_found"
        out[f"{kind.lower()}_allocation_weight"] = pd.NA
        out = out.sort_values(by=["sa2_code_2021"])

    one_row = out.drop_duplicates(subset=["sa2_code_2021"], keep="first").copy()

    return one_row


def join_bridge(spine: pd.DataFrame, kind: str, region_cols: list[str]) -> tuple[pd.DataFrame, dict]:
    bridge_path = find_bridge_file(kind)

    if bridge_path is None:
        audit = {
            "join_name": kind,
            "bridge_file": "",
            "status": "not_found",
            "spine_rows_before": len(spine),
            "spine_rows_after": len(spine),
            "matched_rows": 0,
            "unmatched_rows": len(spine),
            "notes": f"No likely {kind} bridge file found.",
        }
        return spine, audit

    bridge_raw = read_table(bridge_path)
    bridge = standardise_columns(bridge_raw)
    bridge_one = collapse_to_one_row_per_sa2(bridge, region_cols, kind)

    spine_before = len(spine)

    existing_cols = [col for col in bridge_one.columns if col in spine.columns and col != "sa2_code_2021"]
    spine = spine.drop(columns=existing_cols)

    merged = spine.merge(
        bridge_one,
        on="sa2_code_2021",
        how="left",
        validate="one_to_one",
    )

    matched = int(merged[region_cols[0]].notna().sum())
    unmatched = int(merged[region_cols[0]].isna().sum())

    audit = {
        "join_name": kind,
        "bridge_file": str(bridge_path),
        "status": "pass" if unmatched == 0 and len(merged) == spine_before else "review",
        "spine_rows_before": spine_before,
        "spine_rows_after": len(merged),
        "matched_rows": matched,
        "unmatched_rows": unmatched,
        "notes": f"Joined {kind} fields using dominant one-row-per-SA2 bridge.",
    }

    return merged, audit


def validate_final_spine(df: pd.DataFrame) -> list[dict]:
    rows = []

    rows.append(
        {
            "join_name": "final_validation",
            "bridge_file": "",
            "status": "pass" if df["sa2_code_2021"].isna().sum() == 0 else "fail",
            "spine_rows_before": "",
            "spine_rows_after": len(df),
            "matched_rows": "",
            "unmatched_rows": int(df["sa2_code_2021"].isna().sum()),
            "notes": "SA2 code must not be missing.",
        }
    )

    rows.append(
        {
            "join_name": "final_validation",
            "bridge_file": "",
            "status": "pass" if df["sa2_code_2021"].duplicated().sum() == 0 else "fail",
            "spine_rows_before": "",
            "spine_rows_after": len(df),
            "matched_rows": "",
            "unmatched_rows": int(df["sa2_code_2021"].duplicated().sum()),
            "notes": "Final geography spine must remain one row per SA2.",
        }
    )

    return rows


def main() -> None:
    if not SPINE_INPUT.exists():
        raise FileNotFoundError(f"SA2 spine not found: {SPINE_INPUT}")

    OUTPUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    AUDIT_OUTPUT.parent.mkdir(parents=True, exist_ok=True)

    spine = pd.read_parquet(SPINE_INPUT)

    for col in spine.columns:
        if "code" in col.lower() or "name" in col.lower():
            spine[col] = spine[col].map(normalise_code).astype("string")

    audit_rows = []

    joins = [
        ("PHN", ["phn_code", "phn_name"]),
        ("LGA", ["lga_code_2021", "lga_name_2021"]),
        ("LHD", ["lhd_code", "lhd_name"]),
    ]

    for kind, region_cols in joins:
        spine, audit = join_bridge(spine, kind, region_cols)
        audit_rows.append(audit)

    audit_rows.extend(validate_final_spine(spine))

    audit_df = pd.DataFrame(audit_rows)

    spine.to_csv(OUTPUT_CSV, index=False, encoding="utf-8-sig")
    spine.to_parquet(OUTPUT_PARQUET, index=False)
    audit_df.to_csv(AUDIT_OUTPUT, index=False, encoding="utf-8-sig")

    print("Created final geography spine:")
    print(OUTPUT_CSV)
    print(OUTPUT_PARQUET)

    print("\nCreated audit:")
    print(AUDIT_OUTPUT)

    print("\nAudit:")
    print(audit_df.to_string(index=False))

    review_rows = audit_df[~audit_df["status"].isin(["pass"])]
    if not review_rows.empty:
        print("\nWARNING: Some joins require review.")
        print("This may be acceptable if a bridge file has not yet been supplied or if some SA2s legitimately lack a mapping.")


if __name__ == "__main__":
    main()
