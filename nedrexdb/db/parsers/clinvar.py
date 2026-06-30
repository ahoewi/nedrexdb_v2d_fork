import gzip as _gzip
from itertools import chain
from lxml import etree as _let
from collections import defaultdict as _defaultdict
from csv import DictReader as _DictReader
from functools import lru_cache as _lru_cache

from more_itertools import chunked as _chunked
from tqdm import tqdm as _tqdm

from nedrexdb.db import MongoInstance
from nedrexdb.db.models.edges.variant_affects_gene import VariantAffectsGene
from nedrexdb.db.models.edges.variant_associated_with_disorder import VariantAssociatedWithDisorder
from nedrexdb.db.models.nodes.disorder import Disorder
from nedrexdb.db.models.nodes.gene import Gene
from nedrexdb.db.models.nodes.genomic_variant import GenomicVariant
from nedrexdb.db.parsers import _get_file_location_factory
from nedrexdb.logger import logger

get_file_location = _get_file_location_factory("clinvar")

already_reported_missing_handlers = set()

def xml_disorder_mapper(id, db):
    if db == "MONDO":
        return f"mondo.{id.replace('MONDO:', '')}"
    elif db == "OMIM":
        return f"omim.{id}"
    elif db == "Orphanet":
        return f"orhanet.{id}"
    elif db == "MeSH":
        return f"mesh.{id}"
    elif db in {"Human Phenotype Ontology", "EFO", "Gene", "MedGen"}:
        return None
    elif db in already_reported_missing_handlers:
        already_reported_missing_handlers.add(db)
        logger.warning(f"database given without handler: {db!r}")


def disorder_domain_id_to_primary_id_map():
    d = _defaultdict(list)
    for doc in Disorder.find(MongoInstance.DB):
        for domain_id in doc["domainIds"]:
            d[domain_id].append(doc["primaryDomainId"])
    return d


def get_variant_list():
    variants = {doc["primaryDomainId"] for doc in GenomicVariant.find(MongoInstance.DB)}
    return variants


@_lru_cache(maxsize=None)
def get_disorder_by_domain_id(domain_id: str):
    query = {"domainIds": domain_id}
    return [doc["primaryDomainId"] for doc in Disorder.find(MongoInstance.DB, query)]


@_lru_cache(maxsize=None)
def get_variant_by_primary_domain_id(pdid: str):
    query = {"primaryDomainId": pdid}
    return GenomicVariant.find_one(MongoInstance.DB, query)


class ClinVarXMLParser:
    def __init__(self, fname):
        self.fname = fname

    def iter_parse(self):
        variant_ids = get_variant_list()
        disorder_domain_id_map = disorder_domain_id_to_primary_id_map()

        assert None not in variant_ids

        with _gzip.open(self.fname, "rb") as f:
            for _, elem in _let.iterparse(f, events=("end",), tag="VariationArchive"):
                if elem.get('VariationID') is not None:
                    variant_pdid = f"clinvar.{elem.get('VariationID')}"
                else:
                    variant_pdid = None
                if variant_pdid in variant_ids:
                    classified_record = elem.find("ClassifiedRecord")

                    if classified_record is not None:

                        clinical_assertion_list = classified_record.find("ClinicalAssertionList")

                        if clinical_assertion_list is not None:
                            clinical_assertions = clinical_assertion_list.findall("ClinicalAssertion")

                            for clinical_assertion in clinical_assertions:

                                trait_set = clinical_assertion.find("TraitSet")
                                if trait_set is not None:
                                    traits = trait_set.findall("Trait")
                                    traits = chain(
                                        *[
                                            [xref.attrib for xref in trait.findall("XRef")]
                                            for trait in traits
                                            if trait.get("Type") == "Disease"
                                        ]
                                    )
                                    traits = {xml_disorder_mapper(item["ID"], item["DB"]) for item in traits}
                                    traits = set(
                                        chain(
                                            *[disorder_domain_id_map.get(domain_id, []) for domain_id in traits if
                                              domain_id]
                                        )
                                    )
                                    traits.discard(None)

                                classification = clinical_assertion.find("Classification")
                                if classification is not None:
                                    effects_elem = classification.find(
                                        "GermlineClassification")
                                    review_status_elem = classification.find("ReviewStatus")

                                    effects = [
                                        effects_elem.text] if effects_elem is not None and effects_elem.text else []
                                    review_status = review_status_elem.text if review_status_elem is not None else "Unknown"

                                clinvar_accession = clinical_assertion.find("ClinVarAccession")
                                if clinvar_accession is not None:
                                    acc = clinvar_accession.get("Accession")
                                else:
                                    acc = None

                                if traits and acc:
                                    for trait in traits:
                                        vawd = VariantAssociatedWithDisorder(
                                            sourceDomainId=variant_pdid,
                                            targetDomainId=trait,
                                            accession=acc,
                                            effects=effects,
                                            reviewStatus=review_status,
                                            dataSources=["clinvar"],
                                        )
                                        yield vawd

                elem.clear()


class ClinVarVCFParser:
    fieldnames = (
        "CHROM",
        "POS",
        "ID",
        "REF",
        "ALT",
        "QUAL",
        "FILTER",
        "INFO",
    )

    def __init__(self, fname):
        self.fname = fname

    def iter_rows(self):
        with _gzip.open(self.fname, "rt") as f:
            f = (line for line in f if not line.startswith("#"))
            reader = _DictReader(f, fieldnames=self.fieldnames, delimiter="\t")
            for row in reader:
                row["INFO"] = {k: v for k, v in [i.split("=", 1) for i in row["INFO"].split(";")]}
                yield row


class ClinVarRow:
    def __init__(self, row):
        self._row = row

    @property
    def identifier(self):
        return f"clinvar.{self._row['ID']}"

    def get_rs(self):
        if self._row["INFO"].get("RS"):
            return [f"dbsnp.{i}" for i in self._row["INFO"]["RS"].split("|")]
        else:
            return []

    @property
    def chromosome(self):
        return self._row["CHROM"]

    @property
    def position(self):
        return int(self._row["POS"])

    @property
    def reference(self):
        return self._row["REF"]

    @property
    def alternative(self):
        return self._row["ALT"]

    @property
    def variant_type(self):
        variant_type = self._row["INFO"].get("CLNVC")
        return variant_type.replace("_", " ").title()

    @property
    def associated_genes(self):
        gene_info = self._row["INFO"].get("GENEINFO")
        if not gene_info:
            return []

        gene_info = [info.split(":")[1] for info in gene_info.split("|")]
        return [f"entrez.{entrez_id}" for entrez_id in gene_info]

    def parse_variant(self):
        return GenomicVariant(
            primaryDomainId=self.identifier,
            domainIds=[self.identifier] + self.get_rs(),
            chromosome=self.chromosome,
            position=self.position,
            referenceSequence=self.reference,
            alternativeSequence=self.alternative,
            variantType=self.variant_type,
            dataSources=["clinvar"],
        )

    def parse_variant_gene_relationships(self):
        for gene in self.associated_genes:
            yield VariantAffectsGene(sourceDomainId=self.identifier, targetDomainId=gene, dataSources=["clinvar"])


def parse():
    fname = get_file_location("human_data")
    parser = ClinVarVCFParser(fname)

    updates = (ClinVarRow(i).parse_variant().generate_update() for i in parser.iter_rows())
    for chunk in _tqdm(_chunked(updates, 10_000), desc="Parsing ClinVar genomic variants", leave=False):
        MongoInstance.DB[GenomicVariant.collection_name].bulk_write(chunk)

    def iter_variant_gene_relationships():
        for row in parser.iter_rows():
            yield from ClinVarRow(row).parse_variant_gene_relationships()

    gene_ids = {doc["primaryDomainId"] for doc in Gene.find(MongoInstance.DB)}

    updates = (vgr.generate_update() for vgr in iter_variant_gene_relationships() if vgr.targetDomainId in gene_ids)
    for chunk in _tqdm(
        _chunked(updates, 10_000), desc="Parsing ClinVar genomic variant-gene relationships", leave=False
    ):
        MongoInstance.DB[VariantAffectsGene.collection_name].bulk_write(chunk)


    fname = get_file_location("human_data_xml")

    parser = ClinVarXMLParser(fname)
    updates = (i.generate_update() for i in parser.iter_parse())
    for chunk in _tqdm(
        _chunked(updates, 10_000), desc="Parsing ClinVar genomic variant-disorder relationships", leave=False
    ):
        MongoInstance.DB[VariantAssociatedWithDisorder.collection_name].bulk_write(chunk)

    db = MongoInstance.DB
    coll = VariantAssociatedWithDisorder.collection_name
    doc_count = db[coll].count_documents({})
    sample_doc = db[coll].find_one()

    if sample_doc:
        attr_counts = {attr: db[coll].count_documents({attr: {"$exists": True}})
                       for attr in sample_doc.keys()}
        db["_collections"].replace_one(
            {"collection": coll},
            {
                "collection": coll,
                "document_count": doc_count,
                "unique_attributes": list(attr_counts.keys()),
                "attribute_counts": attr_counts
            },
            upsert=True
        )
