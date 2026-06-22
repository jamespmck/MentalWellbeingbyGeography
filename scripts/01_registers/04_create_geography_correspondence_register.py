from pathlib import Path
import pandas as pd

PROJECT_ROOT = Path(r"D:\Good Measure\MentalWellbeingbyGeography")

OUTPUT_PATH = (
    PROJECT_ROOT
    / "docs"
    / "source_registers"
    / "geography_correspondence_register.csv"
)

COLUMNS = [
    "bridge_id",
    "from_geography",
    "to_geography",
    "reference_year",
    "publisher",
    "correspondence_file",
    "source_url_or_access_path",
    "weight_available",
    "weight_type",
    "allocation_method",
    "dominant_region_rule",
    "validation_status",
    "notes",
]

ROWS = [
    {
        "bridge_id": "GEO001",
        "from_geography": "SA2 2021",
        "to_geography": "SA3 2021",
        "reference_year": "2021",
        "publisher": "ABS",
        "correspondence_file": "",
        "source_url_or_access_path": "",
        "weight_available": "yes",
        "weight_type": "official ABS correspondence weight if available",
        "allocation_method": "official ASGS hierarchy or official correspondence",
        "dominant_region_rule": "not applicable",
        "validation_status": "not_started",
        "notes": "Required for joining AIHW SA3 service-activity variables onto SA2 rows.",
    },
    {
        "bridge_id": "GEO002",
        "from_geography": "SA2 2021",
        "to_geography": "PHN",
        "reference_year": "2021",
        "publisher": "ABS or Australian Government Department of Health and Aged Care",
        "correspondence_file": "",
        "source_url_or_access_path": "",
        "weight_available": "check",
        "weight_type": "population weight preferred; area weight acceptable only if documented",
        "allocation_method": "official correspondence or documented allocation",
        "dominant_region_rule": "use dominant PHN only if weighted allocation is unavailable or analytically unsuitable",
        "validation_status": "not_started",
        "notes": "Required before adding PHN-level contextual variables. Do not infer PHN from SA3.",
    },
    {
        "bridge_id": "GEO003",
        "from_geography": "SA2 2021",
        "to_geography": "LGA 2021",
        "reference_year": "2021",
        "publisher": "ABS",
        "correspondence_file": "",
        "source_url_or_access_path": "",
        "weight_available": "yes",
        "weight_type": "official ABS correspondence weight if available",
        "allocation_method": "official correspondence",
        "dominant_region_rule": "use dominant LGA only where one-to-many allocation must be collapsed",
        "validation_status": "not_started",
        "notes": "Required before adding LGA-level contextual variables.",
    },
    {
        "bridge_id": "GEO004",
        "from_geography": "SA2 2021",
        "to_geography": "LHD or equivalent health service region",
        "reference_year": "2021 or closest available",
        "publisher": "state and territory health departments",
        "correspondence_file": "",
        "source_url_or_access_path": "",
        "weight_available": "check",
        "weight_type": "population weight preferred; area weight acceptable only if documented",
        "allocation_method": "state-specific documented correspondence or allocation",
        "dominant_region_rule": "use dominant LHD only if required and documented",
        "validation_status": "not_started",
        "notes": "LHD structures are state-specific. Do not assume national comparability.",
    },
    {
        "bridge_id": "GEO005",
        "from_geography": "SA2 2021",
        "to_geography": "State/Territory",
        "reference_year": "2021",
        "publisher": "ABS",
        "correspondence_file": "",
        "source_url_or_access_path": "",
        "weight_available": "yes",
        "weight_type": "standard ASGS hierarchy",
        "allocation_method": "direct ASGS hierarchy",
        "dominant_region_rule": "not applicable",
        "validation_status": "not_started",
        "notes": "Required jurisdictional identifier.",
    },
]


def main() -> None:
    """Create the geography correspondence register."""

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)

    df = pd.DataFrame(ROWS, columns=COLUMNS)

    if df["bridge_id"].duplicated().any():
        duplicates = df.loc[df["bridge_id"].duplicated(), "bridge_id"].tolist()
        raise ValueError(f"Duplicate bridge_id values found: {duplicates}")

    allowed_statuses = {
        "not_started",
        "requires_validation",
        "part_validated",
        "validated_for_acquisition",
        "validated_for_integration",
        "exclude",
    }

    invalid_statuses = sorted(set(df["validation_status"]) - allowed_statuses)
    if invalid_statuses:
        raise ValueError(f"Invalid validation_status values found: {invalid_statuses}")

    df.to_csv(OUTPUT_PATH, index=False, encoding="utf-8-sig")

    print("Created geography correspondence register:")
    print(OUTPUT_PATH)
    print(f"Rows: {len(df)}")
    print(f"Columns: {len(df.columns)}")


if __name__ == "__main__":
    main()
