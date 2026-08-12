from pathlib import Path as _Path
import requests
import time

from nedrexdb import config as _config
from nedrexdb.logger import logger

def download_rxnorm():
    #api_key = _config["sources.rxnorm.api_key"]
    # zwischenzeitlich:
    from nedrexdb.downloaders.helper import rxnorm_api_key
    api_key = rxnorm_api_key
    url = _config["sources.rxnorm.full.url"].format(api_key)

    rxnorm_dir = _Path(_config.get("db.root_directory")) / _config.get("sources.directory") / "rxnorm"
    rxnorm_dir.mkdir(exist_ok=True, parents=True)

    fname = "RxNorm_full_current.zip"
    logger.info(f"Downloading RxNorm from {url}")

    retries = 3
    for attempt in range(retries):
        try:
            with requests.get(url, stream=True) as r:
                r.raise_for_status()
                with open(rxnorm_dir / fname, "wb") as f:
                    for chunk in r.iter_content(chunk_size=1024 * 1024):
                        f.write(chunk)
            logger.info("RxNorm download complete.")
            return
        except Exception as e:
            logger.warning(f"RxNorm download failed (attempt {attempt+1}/{retries}): {e}")
            if attempt < retries - 1:
                time.sleep(5 * (attempt + 1))

    raise RuntimeError(f"RxNorm download failed after {retries} retries")