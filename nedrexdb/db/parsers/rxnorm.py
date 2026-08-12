from collections import defaultdict
from itertools import chain

from more_itertools import chunked
from tqdm import tqdm

from nedrexdb.db import MongoInstance
from nedrexdb.db.parsers import _get_file_location_factory
from nedrexdb.db.models.nodes.drug import Drug
from nedrexdb.logger import logger
from pathlib import PurePosixPath
from urllib.parse import parse_qs, urlparse
from io import TextIOWrapper
from zipfile import ZipFile

get_file_location = _get_file_location_factory("rxnorm")

# TTYs that should participate in matching
MATCH_TTYS = {
    "IN",
    "PIN",
    "BN",
    "SY",
}


def parse_rxnorm():
    filename = get_file_location("full")

    # Remove "&apiKey={}" suffix if present
    filename = filename.with_name(filename.name.split("&")[0])

    logger.debug(f"RxNorm filename: {filename}")
    logger.info("Parsing RxNorm...")
    #download_url = parse_qs(urlparse(filename).query)["url"][0]
    #zip_name = PurePosixPath(urlparse(download_url).path).name

    #filename = get_file_location(zip_name)

    # ------------------------------------------------------------------
    # Build
    #
    # RXCUI -> set(names)
    # RXCUI -> DrugBank ID
    # ------------------------------------------------------------------

    names_by_rxcui = defaultdict(set)
    drugbank_by_rxcui = {}

    with ZipFile(filename) as zf:

        # Locate RXNCONSO.RRF inside the archive
        rxnconso = next(
            name
            for name in zf.namelist()
            if name.endswith("RXNCONSO.RRF")
        )

        with zf.open(rxnconso) as raw:
            f = TextIOWrapper(raw, encoding="utf-8")

            for line in tqdm(f, desc="Reading RXNCONSO.RRF"):

                cols = line.rstrip("\n").split("|")

                # Expected columns
                #
                # 0  RXCUI
                # 11 SAB
                # 12 TTY
                # 13 CODE
                # 14 STR

                if len(cols) < 15:
                    continue

                rxcui = cols[0]
                sab = cols[11]
                tty = cols[12]
                code = cols[13]
                string = cols[14].strip()

                if not string:
                    continue

                if sab == "RXNORM":
                    if tty in MATCH_TTYS:
                        names_by_rxcui[rxcui].add(string.lower())

                elif sab == "DRUGBANK":
                    drugbank_by_rxcui[rxcui] = f"drugbank.{code}"

    print(f"{len(names_by_rxcui):,} RXCUIs with names")
    print(f"{len(drugbank_by_rxcui):,} RXCUIs mapped to DrugBank")

    # ------------------------------------------------------------------
    # Build
    #
    # DrugBank ID -> RxNorm names
    # ------------------------------------------------------------------

    names_by_drugbank = defaultdict(set)

    for rxcui, dbid in drugbank_by_rxcui.items():
        if rxcui not in names_by_rxcui:
            continue

        names_by_drugbank[dbid].update(names_by_rxcui[rxcui])

    print(f"{len(names_by_drugbank):,} DrugBank drugs enriched")

    # ------------------------------------------------------------------
    # Load existing drugs
    # ------------------------------------------------------------------

    drugs = {
        drug["primaryDomainId"]: drug
        for drug in Drug.find(MongoInstance.DB)
    }

    updates = []

    for dbid, rxnames in tqdm(
        names_by_drugbank.items(),
        desc="Updating drugs",
    ):

        if dbid not in drugs:
            continue

        drug = Drug.parse_obj(drugs[dbid])

        existing = {drug.displayName.lower()}
        existing.update(s.lower() for s in drug.synonyms)

        # keep only genuinely new names
        rxnames = sorted(rxnames - existing)

        drug.rxnormNames = rxnames

        updates.append(drug.generate_update())

    print(f"{len(updates):,} drugs will be updated")

    for chunk in chunked(updates, 1000):
        MongoInstance.DB[Drug.collection_name].bulk_write(chunk)

    print("Finished RxNorm.")