from pathlib import Path
import re
import pandas as pd

PROJECT_ROOT = Path(r"D:\Good Measure\MentalWellbeingbyGeography")

SEARCH_DIRS = [
    PROJECT_ROOT / "data" / "raw" / "abs" / "geography",
    PROJECT_ROOT / "data" / "interim" / "geography",
    PROJECT_ROOT / "data" / "processed" / "spines",
]

OUTPUT = PROJECT_ROOT / "outputs" / "audits" / "phn_lga_lhd_bridge_column_diagnostic.csv"


def clean_col_name(col: str) -> str:
    col = str(col).strip().lower()
    col = re.sub(r"[^a-z0-9]+", "_", col)
    col = re.sub(r"_+", "_", col)
    return col.strip("_")


def read_columns(path: Path) -> list[str]:
    suffix = path.suffix.lower()

    if suffix == ".csv":
        df = pd.read_csv(path, dtype=str, nrows=3, low_memory=False)
        return list(df.columns)

    if suffix in {".xlsx", ".xls"}:
        df = pd.read_excel(path, dtype=str, nrows=3)
        return list(df.columns)

    if suffix == ".parquet":
        df = pd.read_parquet(path)
        return list(df.columns)

    return []


def score_file(path: Path, cleaned_cols: list[str], kind: str) -> int:
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

    for col in cleaned_cols:
        if kind.lower() in col:
            score += 5
        if "sa2" in col:
            score += 2
        if "code" in col:
            score += 1
        if "name" in col:
            score += 1

    return score


def main() -> None:
    rows = []

    files = []
    for directory in SEARCH_DIRS:
        if not directory.exists():
            continue

        for pattern in ["*.csv", "*.xlsx", "*.xls", "*.parquet"]:
            files.extend(directory.glob(pattern))

    for path in sorted(files):
        try:
            original_cols = read_columns(path)
            cleaned_cols = [clean_col_name(c) for c in original_cols]
        except Exception as exc:
            rows.append(
                {
                    "file_path": str(path),
                    "file_name": path.name,
                    "read_status": f"error: {exc}",
                    "phn_score": "",
                    "lga_score": "",
                    "lhd_score": "",
                    "original_columns": "",
                    "cleaned_columns": "",
                }
            )
            continue

        rows.append(
            {
                "file_path": str(path),
                "file_name": path.name,
                "read_status": "read_ok",
                "phn_score": score_file(path, cleaned_cols, "PHN"),
                "lga_score": score_file(path, cleaned_cols, "LGA"),
                "lhd_score": score_file(path, cleaned_cols, "LHD"),
                "original_columns": " | ".join(map(str, original_cols)),
                "cleaned_columns": " | ".join(cleaned_cols),
            }
        )

    out = pd.DataFrame(rows)
    out = out.sort_values(
        by=["phn_score", "lga_score", "lhd_score", "file_name"],
        ascending=[False, False, False, True],
    )

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(OUTPUT, index=False, encoding="utf-8-sig")

    print(f"Created diagnostic file: {OUTPUT}")
    print("\nTop candidate files:")
    print(
        out[
            [
                "file_name",
                "phn_score",
                "lga_score",
                "lhd_score",
                "read_status",
                "cleaned_columns",
            ]
        ]
        .head(10)
        .to_string(index=False)
    )


if __name__ == "__main__":
    main()
