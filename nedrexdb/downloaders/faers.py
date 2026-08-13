import os
import requests
from tqdm import tqdm
from datetime import datetime

BASE_URL = "https://fis.fda.gov/content/Exports/"
OUTPUT_DIR = "faers_downloads"

HEADERS = {
    "User-Agent": "FAERS-Downloader/1.0"
}

START_YEAR = 2013
END_YEAR = datetime.utcnow().year

QUARTERS = [1, 2, 3, 4]
TYPES = ["xml"] #, "ascii"]


def try_download(url, out_path):
    if os.path.exists(out_path):
        print(f"Skip exists: {out_path}")
        return True

    os.makedirs(os.path.dirname(out_path), exist_ok=True)

    try:
        with requests.get(url, stream=True, headers=HEADERS, timeout=60) as r:
            if r.status_code == 404:
                return False

            r.raise_for_status()

            total = int(r.headers.get("content-length", 0))

            with open(out_path, "wb") as f, tqdm(
                total=total,
                unit="B",
                unit_scale=True,
                desc=os.path.basename(out_path),
            ) as pbar:

                for chunk in r.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        f.write(chunk)
                        pbar.update(len(chunk))

        return True

    except Exception as e:
        print(f"Error: {url} -> {e}")
        return False


def build_candidates(year, q, file_type):
    """
    Handles Q/q inconsistency.
    """
    return [
        f"{BASE_URL}faers_{file_type}_{year}Q{q}.zip",
        f"{BASE_URL}faers_{file_type}_{year}q{q}.zip",
    ]


def download_one(year, q, file_type):
    out_path = os.path.join(
        OUTPUT_DIR,
        str(year),
        f"Q{q}",
        file_type,
        f"faers_{file_type}_{year}Q{q}.zip"
    )

    for url in build_candidates(year, q, file_type):
        print(f"Trying: {url}")

        if try_download(url, out_path):
            print(f"OK: {year} Q{q} {file_type}")
            return

    print(f"Missing: {year} Q{q} {file_type}")


def main():
    for year in range(START_YEAR, END_YEAR + 1):
        for q in QUARTERS:
            for file_type in TYPES:
                download_one(year, q, file_type)


if __name__ == "__main__":
    main()
