from collections import defaultdict
from io import TextIOWrapper
from zipfile import ZipFile

from more_itertools import chunked
from pymongo import UpdateOne
from tqdm import tqdm

from nedrexdb.db import MongoInstance
from nedrexdb.db.models.nodes.drug import Drug
from nedrexdb.db.parsers import _get_file_location_factory
from nedrexdb.logger import logger


get_file_location = _get_file_location_factory("rxnorm")


# RxNorm term types that should be used as drug names.
MATCH_TTYS = {
    "IN",   # Ingredient
    "PIN",  # Precise Ingredient
    "BN",   # Brand Name
    "SY",   # Synonym
}


def parse_rxnorm():
    filename = get_file_location("full")

    # Remove "&apiKey={}" suffix if present.
    filename = filename.with_name(filename.name.split("&")[0])

    logger.debug(f"RxNorm filename: {filename}")
    logger.info("Parsing RxNorm...")

    # ------------------------------------------------------------------
    # Build:
    #
    # RXCUI -> set(RxNorm names)
    # RXCUI -> DrugBank ID
    # ------------------------------------------------------------------

    names_by_rxcui = defaultdict(set)
    drugbank_by_rxcui = {}

    with ZipFile(filename) as zf:

        # Locate RXNCONSO.RRF inside the archive.
        rxnconso = next(
            name
            for name in zf.namelist()
            if name.endswith("RXNCONSO.RRF")
        )

        logger.debug(f"Using {rxnconso} from RxNorm archive")

        with zf.open(rxnconso) as raw:
            with TextIOWrapper(raw, encoding="utf-8") as f:

                for line in tqdm(
                    f,
                    desc="Reading RXNCONSO.RRF",
                ):
                    cols = line.rstrip("\n").split("|")

                    # RXNCONSO.RRF columns:
                    #
                    # 0  RXCUI
                    # ...
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

                    # --------------------------------------------------
                    # RxNorm vocabulary
                    # --------------------------------------------------

                    if sab == "RXNORM" and tty in MATCH_TTYS:
                        names_by_rxcui[rxcui].add(string)

                    # --------------------------------------------------
                    # DrugBank vocabulary
                    #
                    # RxNorm contains DrugBank cross-references where
                    # CODE is the DrugBank identifier.
                    # --------------------------------------------------

                    elif sab == "DRUGBANK":
                        drugbank_by_rxcui[rxcui] = f"drugbank.{code}"

    logger.info(
        f"{len(names_by_rxcui):,} RXCUIs with names"
    )

    logger.info(
        f"{len(drugbank_by_rxcui):,} RXCUIs mapped to DrugBank"
    )

    # ------------------------------------------------------------------
    # Build:
    #
    # DrugBank ID -> set(all RxNorm names)
    # ------------------------------------------------------------------

    names_by_drugbank = defaultdict(set)

    for rxcui, drugbank_id in drugbank_by_rxcui.items():

        names = names_by_rxcui.get(rxcui)

        if not names:
            continue

        names_by_drugbank[drugbank_id].update(names)

    logger.info(
        f"{len(names_by_drugbank):,} DrugBank drugs enriched"
    )

    # ------------------------------------------------------------------
    # Load existing Drug nodes.
    # ------------------------------------------------------------------

    drugs = {
        drug["primaryDomainId"]: drug
        for drug in Drug.find(MongoInstance.DB)
    }

    # ------------------------------------------------------------------
    # Create MongoDB updates.
    #
    # IMPORTANT:
    #
    # rxnormNames intentionally contains ALL matched RxNorm names.
    # We do NOT remove names that also occur in displayName/synonyms.
    #
    # This field represents the RxNorm vocabulary independently and
    # will later be used for FAERS name matching.
    # ------------------------------------------------------------------

    updates = []

    for drugbank_id, rxnames in tqdm(
        names_by_drugbank.items(),
        desc="Updating drugs",
    ):
        if drugbank_id not in drugs:
            continue

        # Sort for deterministic database output.
        rxnames = sorted(rxnames)

        updates.append(
            UpdateOne(
                {"primaryDomainId": drugbank_id},
                {
                    "$set": {
                        "rxnormNames": rxnames,
                    }
                },
            )
        )

    logger.info(
        f"{len(updates):,} drugs will be updated"
    )

    # ------------------------------------------------------------------
    # Write updates.
    # ------------------------------------------------------------------

    for chunk in chunked(updates, 1000):
        MongoInstance.DB[Drug.collection_name].bulk_write(chunk)

    logger.info("Finished RxNorm.")