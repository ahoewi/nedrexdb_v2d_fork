from collections import defaultdict
from itertools import chain

from more_itertools import chunked
from tqdm import tqdm

from nedrexdb.db import MongoInstance
from nedrexdb.db.parsers import _get_file_location_factory
from nedrexdb.db.models.nodes.drug import Drug

get_file_location = _get_file_location_factory("rxnorm")

# TTYs that should participate in matching
MATCH_TTYS = {
    "IN",
    "PIN",
    "BN",
    "SY",
}


def parse_rxnorm():
    filename = get_file_location("rrf")

    print("Parsing RxNorm...")

    # ------------------------------------------------------------------
    # Build
    #
    # RXCUI -> set(names)
    # RXCUI -> DrugBank ID
    # ------------------------------------------------------------------

    names_by_rxcui = defaultdict(set)
    drugbank_by_rxcui = {}

    with open(filename, "r", encoding="utf8") as f:
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