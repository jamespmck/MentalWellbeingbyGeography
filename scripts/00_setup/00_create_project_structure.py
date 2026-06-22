from pathlib import Path

PROJECT_ROOT = Path(r"D:\Good Measure\MentalWellbeingbyGeography")

FOLDERS = [
    "data",
    "data/raw",
    "data/raw/abs",
    "data/raw/aihw",
    "data/raw/dss",
    "data/raw/ndia",
    "data/raw/phidu",
    "data/raw/aedc",
    "data/raw/state_health",
    "data/external",

    "data/interim",
    "data/interim/geography",
    "data/interim/sources",
    "data/interim/checks",

    "data/processed",
    "data/processed/spines",
    "data/processed/integrated",

    "data/published",

    "docs",
    "docs/briefs",
    "docs/source_registers",
    "docs/methodology",
    "docs/data_dictionaries",

    "scripts",
    "scripts/00_setup",
    "scripts/01_registers",
    "scripts/02_geography",
    "scripts/03_acquisition",
    "scripts/04_integration",
    "scripts/05_audit",
    "scripts/06_modelling",

    "outputs",
    "outputs/audits",
    "outputs/tables",
    "outputs/figures",

    "notebooks",
    "tests",
]


def main() -> None:
    """Create the standard project folder structure."""

    print(f"Project root: {PROJECT_ROOT}")

    if not PROJECT_ROOT.exists():
        raise FileNotFoundError(
            f"Project root does not exist: {PROJECT_ROOT}"
        )

    for folder in FOLDERS:
        folder_path = PROJECT_ROOT / folder
        folder_path.mkdir(parents=True, exist_ok=True)
        print(f"OK: {folder_path}")

    print("\nProject folder structure checked and created where needed.")


if __name__ == "__main__":
    main()
