"""Download the raw nutrition datasets into data/raw/ (git-ignored; never committed).

- IFCT 2017: the digitized composition table from github.com/nodef/ifct2017, pinned to one
  commit. IFCT 2017 is published by ICMR-NIN; confirm its reuse terms before serving it
  (implementation plan, open decision 4).
- USDA FoodData Central: SR Legacy and FNDDS survey foods CSVs (public domain).

Usage (from backend/):  uv run python -m scripts.ingest.fetch_raw
"""

import io
import urllib.request
import zipfile
from pathlib import Path

from scripts.ingest.common import IFCT_CSV, USDA_DIR

IFCT_COMMIT = "b65621ddcf07115e7adba50809d4977402a9fd29"
IFCT_URL = f"https://raw.githubusercontent.com/nodef/ifct2017/{IFCT_COMMIT}/compositions/index.csv"
USDA_ZIPS = [
    "https://fdc.nal.usda.gov/fdc-datasets/FoodData_Central_sr_legacy_food_csv_2018-04.zip",
    "https://fdc.nal.usda.gov/fdc-datasets/FoodData_Central_survey_food_csv_2024-10-31.zip",
]
# Only these files are needed from each USDA download.
USDA_FILES = {"food.csv", "nutrient.csv", "food_nutrient.csv", "survey_fndds_food.csv"}


def _get(url: str) -> bytes:
    print(f"downloading {url}")
    with urllib.request.urlopen(url, timeout=120) as response:  # noqa: S310 - fixed https URLs
        data: bytes = response.read()
    return data


def fetch_ifct(dest: Path = IFCT_CSV) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(_get(IFCT_URL))
    print(f"  → {dest}")


def fetch_usda(dest: Path = USDA_DIR) -> None:
    for url in USDA_ZIPS:
        folder = dest / Path(url).stem
        folder.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(io.BytesIO(_get(url))) as archive:
            for member in archive.namelist():
                name = Path(member).name
                if name in USDA_FILES:
                    (folder / name).write_bytes(archive.read(member))
        print(f"  → {folder}")


if __name__ == "__main__":
    fetch_ifct()
    fetch_usda()
