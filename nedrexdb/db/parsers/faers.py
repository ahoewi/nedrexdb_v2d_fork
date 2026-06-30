import zipfile
import xml.etree.ElementTree as ET
from collections import defaultdict
from more_itertools import chunked
from tqdm import tqdm

from nedrexdb.db import MongoInstance
from nedrexdb.db.models.nodes.drug import Drug
from nedrexdb.db.models.nodes.side_effect import SideEffect
from nedrexdb.db.models.edges.drug_has_adverse_reaction import DrugHasAdverseReaction
from nedrexdb.db.parsers import _get_file_location_factory
from nedrexdb.logger import logger

get_file_location = _get_file_location_factory("faers")


def build_drug_name_map():
    """lowercase displayName -> primaryDomainId"""
    d = {}
    for drug in Drug.find(MongoInstance.DB):
        name = drug.get("displayName", "").lower().strip()
        if name:
            d[name] = drug["primaryDomainId"]
    return d


def build_side_effect_name_map():
    """lowercase displayName -> primaryDomainId"""
    d = {}
    for se in SideEffect.find(MongoInstance.DB):
        name = se.get("displayName", "").lower().strip()
        if name:
            d[name] = se["primaryDomainId"]
    return d


def get_text(element, tag):
    child = element.find(tag)
    return child.text.strip() if child is not None and child.text else None


def parse_faers():
    logger.info("Parsing FAERS")

    drug_map = build_drug_name_map()
    se_map = build_side_effect_name_map()

    logger.debug(f"Drug name map: {len(drug_map)} entries")
    logger.debug(f"Side effect name map: {len(se_map)} entries")

    # drug_id -> se_id -> aggregated data
    pair_data = defaultdict(lambda: {
        "report_count": 0,
        "serious_count": 0,
        "fatal_count": 0,
        "report_ids": set(),
        "countries": set(),
    })

    fname = get_file_location("adverse_reactions")

    with zipfile.ZipFile(fname, "r") as z:
        xml_files = [f for f in z.namelist() if f.endswith(".xml")]

        for xml_file in xml_files:
            logger.info(f"Parsing {xml_file}...")
            with z.open(xml_file) as f:
                context = ET.iterparse(f, events=("end",))
                for event, elem in tqdm(context, leave=False):
                    if elem.tag != "safetyreport":
                        elem.clear()
                        continue

                    report_id = get_text(elem, "safetyreportid")
                    serious = get_text(elem, "serious") == "1"
                    fatal = get_text(elem, "seriousnessdeath") == "1"
                    country = get_text(elem, "occurcountry")

                    patient = elem.find("patient")
                    if patient is None:
                        elem.clear()
                        continue

                    # collect matched drug IDs
                    drug_ids = set()
                    for drug in patient.findall("drug"):
                        name = (get_text(drug, "medicinalproduct") or "").lower().strip()
                        drug_id = drug_map.get(name)
                        if drug_id:
                            drug_ids.add(drug_id)

                    # collect matched side effect IDs
                    se_ids = set()
                    for reaction in patient.findall("reaction"):
                        name = (get_text(reaction, "reactionmeddrapt") or "").lower().strip()
                        se_id = se_map.get(name)
                        if se_id:
                            se_ids.add(se_id)

                    # aggregate
                    for drug_id in drug_ids:
                        for se_id in se_ids:
                            key = (drug_id, se_id)
                            pair_data[key]["report_count"] += 1
                            if serious:
                                pair_data[key]["serious_count"] += 1
                            if fatal:
                                pair_data[key]["fatal_count"] += 1
                            if report_id:
                                pair_data[key]["report_ids"].add(report_id)
                            if country:
                                pair_data[key]["countries"].add(country)

                    elem.clear()

    logger.debug(f"Identified {len(pair_data)} DrugHasAdverseReaction edges")

    updates = []
    for (drug_id, se_id), data in pair_data.items():
        edge = DrugHasAdverseReaction(
            sourceDomainId=drug_id,
            targetDomainId=se_id,
            report_count=data["report_count"],
            serious_count=data["serious_count"],
            fatal_count=data["fatal_count"],
            report_ids=list(data["report_ids"]),
            countries=list(data["countries"]),
            dataSources=["faers"],
        )
        updates.append(edge.generate_update())

    for chunk in tqdm(chunked(updates, 1_000), leave=False):
        MongoInstance.DB[DrugHasAdverseReaction.collection_name].bulk_write(chunk)

    logger.info("FAERS parsing complete")