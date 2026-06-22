from pathlib import Path
import requests

PROJECT_ROOT = Path(r"D:\Good Measure\MentalWellbeingbyGeography")

RAW_SEIFA_DIR = PROJECT_ROOT / "data" / "raw" / "abs" / "seifa"

URL = (
    "https://www.abs.gov.au/statistics/people/people-and-communities/"
    "socio-economic-indexes-areas-seifa-australia/2021/"
    "Statistical%20Area%20Level%202%2C%20Indexes%2C%20SEIFA%202021.xlsx"
)

OUTPUT_PATH = RAW_SEIFA_DIR / "Statistical_Area_Level_2_Indexes_SEIFA_2021.xlsx"


def main() -> None:
    RAW_SEIFA_DIR.mkdir(parents=True, exist_ok=True)

    print("Downloading ABS SEIFA 2021 SA2 indexes workbook:")
    print(URL)

    response = requests.get(
        URL,
        timeout=180,
        headers={"User-Agent": "Mozilla/5.0"},
    )
    response.raise_for_status()

    OUTPUT_PATH.write_bytes(response.content)

    print("\nDownloaded:")
    print(OUTPUT_PATH)
    print(f"Size: {OUTPUT_PATH.stat().st_size:,} bytes")


if __name__ == "__main__":
    main()
