#!/usr/bin/env python

import click
import os
import subprocess
import time
from pymongo.errors import PyMongoError

import nedrexdb
from nedrexdb import config, downloaders
from nedrexdb.control.docker import NeDRexDevInstance, NeDRexLiveInstance, update_neo4j_image_version
from nedrexdb.control.embeddings import EmbeddingController
from nedrexdb.db import MongoInstance, mongo_to_neo, collection_stats
from nedrexdb.db.import_embeddings import fetch_embeddings, upsert_embeddings
from nedrexdb.db.parsers import (
    biogrid,
    disgenet,
    ctd,
    drugbank,
    drug_central,
    hpo,
    hpa,
    iid,
    intact,
    uniprot,
    uniprot_signatures,
    ncbi,
    mondo,
    omim,
    reactome,
    go,
    clinvar,
    chembl,
    unichem,
    bioontology,
    sider,
    uberon,
    repotrial,
    cosmic,
    ncg,
    intogen,
    orphanet,
    opentargets,
    hippie
)
from nedrexdb.downloaders import get_versions, update_versions
from nedrexdb.post_integration import (trim_uberon, drop_empty_collections)
from nedrexdb.post_integration.neo4j_db_adjustments import create_constraints, create_vector_indices
from nedrexdb.logger import logger


@click.group()
def cli():
    pass


def _gather_live_metadata(embedding_controller, download, rebuild):
    prev_metadata = {}
    try:
        MongoInstance.connect("live")
        
        # Centralized in EmbeddingController
        embedding_controller.gather_live_state(MongoInstance.DB)
        
        if download or rebuild:
            # needed for metadata comparisons
            prev_metadata_list = list(MongoInstance.DB["metadata"].find())
            prev_metadata = {} if not prev_metadata_list else prev_metadata_list[0].get("source_databases", {})
            logger.debug("Gathering previous metadata:")
            for source in prev_metadata:
                logger.debug(f"{source}:\t{prev_metadata[source]['version']}"
                             f" [{prev_metadata[source]['date']}]")
    except Exception as e:
        logger.warning(f"No previous metadata found/failed Mongo live connection: {e}")
    return prev_metadata


def _perform_downloads(download, rebuild, version_update, prev_metadata, ignored_sources):
    nedrex_versions = None
    no_download = []
    current_metadata = {}

    if download or rebuild:
        # update metadata
        # fallback version is rarely needed. Do not change that file, only use the config!
        default_version = get_fallback_version()
        nedrex_versions = update_versions(ignored_sources=ignored_sources, default_version=default_version)
        save_fallback_version(f"{nedrex_versions['version']}")

        # do the download
        logger.debug("Download: ON")
        current_metadata = nedrex_versions["source_databases"]
        # already up-to-date data
        no_download = [key for key in prev_metadata if key in current_metadata and
                       prev_metadata[key]['version'] == current_metadata[key]['version']]
        if rebuild:
            no_download = []
            logger.info(
                "Skipping download for: [] because of rebuild flag. This can be disabled by setting FORCE_REBUILD=0")
        static_download = [key for key in ["bioontology", "drugbank", "disgenet", "repotrial",
                                           "hippie", "sider", "cosmic", "intogen", "ncg"] if key not in no_download and
                           key not in ignored_sources]

        loglevel_info_or_debug = os.environ.get("LOG_LEVEL", "INFO") in ["DEBUG", "INFO"]
        if static_download:
            logger.info("Starting dump downloads")
            subprocess.run(["./setup_data.sh", "/data/nedrex_files", "1" if loglevel_info_or_debug else "0"])
        downloaders.download_all(ignored_sources=ignored_sources,
                                 no_download_meta=no_download)

    if version_update:
        nedrex_versions = get_versions(version_update)

    return nedrex_versions, no_download, current_metadata


def _prepare_dev_environment(embedding_controller):
    embedding_controller.prepare_reusable_embeddings()

    # prepare neo4j for import from mongoDB
    embedding_controller.dev_instance.remove(neo4j_mode="import")
    embedding_controller.dev_instance.set_up(use_existing_volume=False, neo4j_mode="import")

    # MongoDB data download & import
    MongoInstance.connect("dev")
    MongoInstance.set_indexes()


def _ingest_data(version, nedrex_versions, ignored_sources):
    if nedrex_versions:
        MongoInstance.DB["metadata"].replace_one({}, nedrex_versions, upsert=True)

    # Run parser pipeline
    run_parsers(
        version=version,
        ignored_sources=ignored_sources
    )

    for src in ignored_sources:
        col = src.replace("-", "_")
        if col in MongoInstance.DB.list_collection_names():
            logger.info(f"Dropping collection for ignored source: {col}")
            MongoInstance.DB[col].drop()


def _post_process_data(dev_instance):
    # clean up for export
    drop_empty_collections.drop_empty_collections()

    # prepare neo4j for import from mongoDB
    dev_instance._remove_neo4j(remove_db_volume=True, neo4j_mode="import")
    dev_instance._set_up_neo4j(use_existing_volume=False, neo4j_mode="import")

    # export to Neo4j
    mongo_to_neo.mongo_to_neo(dev_instance, MongoInstance.DB)

    # Profile the collections
    collection_stats.profile_collections(MongoInstance.DB)

    collection_stats.verify_collections_after_profiling(MongoInstance.DB)


def _finalize_build(embedding_controller, no_download, current_metadata):
    embedding_controller.validate_and_finalize(MongoInstance.DB, no_download, current_metadata)

    live_instance = NeDRexLiveInstance()
    live_instance.remove()
    live_instance.set_up(use_existing_volume=True, neo4j_mode="db")

@click.option("--conf", required=True, type=click.Path(exists=True))
@click.option("--download", is_flag=True, default=False)
@click.option("--rebuild", is_flag=True, default=False)
@click.option("--version_update", is_flag=False, default="")
@click.option("--create_embeddings", is_flag=True, default=False)
@cli.command()
def update(conf, download, rebuild, version_update, create_embeddings):
    logger.debug(f"Config file: {conf}")
    logger.info(f"Download updates: {download}")
    logger.info(f"Update DB versions: {version_update}")
    logger.info(f"Force rebuild entire DB: {rebuild}")
    logger.info(f"Create embeddings: {create_embeddings}")

    nedrexdb.parse_config(conf)

    version = config["db.version"]

    if version not in ["open", "licensed"]:
        raise Exception(f"invalid version {version!r}")

    update_neo4j_image_version()

    # Determine ignored sources for minimal build
    ignored_sources = set()
    if os.environ.get("TEST_MINIMUM", 0) == '1':
        ignored_sources = {
            "go", "uberon", "clinvar", "hpo", "hpa", "reactome",
            "bioontology", "unichem", "intact", "ncg", "intogen", "uniprot",
            "opentargets", "orphanet", "ncbi", "ctd",
            "disgenet", "hippie", "sider", "cosmic"
        }

    # Initialize Embedding Controller
    dev_instance = NeDRexDevInstance()
    embedding_controller = EmbeddingController(
        dev_instance=dev_instance, 
        create_embeddings=create_embeddings, 
        rebuild=rebuild
    )

    # Stage 1: Gather metadata from live DB
    prev_metadata = _gather_live_metadata(
        embedding_controller, download, rebuild
    )

    # Stage 2: Perform downloads
    nedrex_versions, no_download, current_metadata = _perform_downloads(
        download, rebuild, version_update, prev_metadata, ignored_sources
    )

    # Stage 3: Prepare Dev environment
    _prepare_dev_environment(embedding_controller)

    # Stage 4: Ingest data into Mongo
    _ingest_data(version, nedrex_versions, ignored_sources)

    # Stage 5: Post-process (Mongo to Neo4j)
    _post_process_data(dev_instance)

    # Stage 6: Finalize Build (Promote to Live, generate embeddings)
    _finalize_build(
        embedding_controller, no_download, current_metadata
    )


# Unified parser pipeline used by both the full update() path and parse_dev().
def run_parsers(version, ignored_sources, hippie_method_scores=None):
    """
    Unified parser pipeline used by both the full update() path and parse_dev().
    Ordering does matter due to dependencies in the parsing process.
    Custom db build is possible with conditional execution based on ignored_sources.
    """

    # --- PRIMARY NODE SOURCES (must run first) ---
    if "go" not in ignored_sources:
        go.parse_go()
    if "mondo" not in ignored_sources:
        mondo.parse_mondo_json()  # disorder nodes
    if "ncbi" not in ignored_sources:
        ncbi.parse_gene_info()
        ncbi.parse_gene_summary()
    if "uberon" not in ignored_sources:
        uberon.parse()
    if "uniprot" not in ignored_sources:
        uniprot.parse_proteins()

    # --- NODE SOURCES THAT REQUIRE EXISTING NODES ---
    if "cosmic" not in ignored_sources:
        cosmic.parse_gene_disease_associations()
    if "clinvar" not in ignored_sources:
        clinvar.parse()
    if "drugbank" not in ignored_sources:
        if version == "licensed":
            drugbank._parse_drugbank()  # requires proteins
        else:
            drugbank.parse_drugbank()
    if "chembl" not in ignored_sources:
        chembl.parse_chembl()
    if "uniprot" not in ignored_sources:
        uniprot_signatures.parse()  # requires proteins
    if "hpo" not in ignored_sources:
        hpo.parse()  # requires disorders
    if "reactome" not in ignored_sources:
        reactome.parse()  # requires proteins
    if "bioontology" not in ignored_sources:
        bioontology.parse()  # requires phenotype

    # --- SOURCES ADDING DATA TO EXISTING NODES ---
    if "drug_central" not in ignored_sources:
        drug_central.parse_drug_central()
    if "unichem" not in ignored_sources:
        unichem.parse()
    if "repotrial" not in ignored_sources:
        repotrial.parse()

    # --- SCORE-RELATED EXTRACTION (hippie) ---
    if "hippie" not in ignored_sources:
        if hippie_method_scores is None:
            hippie_method_scores = hippie.parse_perplexity_techinque_scores()

    # --- EDGE SOURCES ---
    if "ctd" not in ignored_sources:
        ctd.parse()
    if "disgenet" not in ignored_sources:
        disgenet.parse_gene_disease_associations()
    if "intogen" not in ignored_sources:
        intogen.parse_gene_disease_associations()
    if "orphanet" not in ignored_sources:
        orphanet.parse_gene_disease_associations()
    if "opentargets" not in ignored_sources:
        opentargets.parse_gene_disease_associations()
    if "ncg" not in ignored_sources:
        ncg.parse_gene_disease_associations()

    # GO annotations
    if "go" not in ignored_sources:
        go.parse_goa()

    # Edges requiring hippie scores
    if "hpa" not in ignored_sources:
        hpa.parse_hpa()
    if "biogrid" not in ignored_sources and "hippie" not in ignored_sources:
        biogrid.parse_ppis(hippie_method_scores)
    if "iid" not in ignored_sources and "hippie" not in ignored_sources:
        iid.parse_ppis(hippie_method_scores)
    if "intact" not in ignored_sources and "hippie" not in ignored_sources:
        intact.parse(hippie_method_scores)

    # omim is licensed-only
    if version == "licensed" and "omim" not in ignored_sources:
        omim.parse_gene_disease_associations()

    if "sider" not in ignored_sources:
        sider.parse()

    if "uniprot" not in ignored_sources:
        uniprot.parse_idmap()

    if "repotrial" not in ignored_sources:
        from nedrexdb.analyses import molecule_similarity
        molecule_similarity.run()

    if "uberon" not in ignored_sources:
        trim_uberon.trim_uberon()

def get_fallback_version(fallback_path="/data/nedrex_files/nedrex_data/fallback_version"):
    default_version = None
    if os.path.exists(fallback_path):
        with open(fallback_path) as f:
            default_version = f.readline().rstrip()
    return default_version

def save_fallback_version(version, fallback_path="/data/nedrex_files/nedrex_data/fallback_version"):
    try:
        with open(fallback_path, "w") as f:
            f.write(str(version))
    except:
        logger.info("No fallback version file found. Initial setup?")


def parse_dev(version, download, rebuild, version_update, prev_metadata,
              distinct_per_collection, dev_instance, create_embeddings):
    # control source downloads
    ignored_sources = {"chembl",
                       "biogrid",
                       "go",
                       "uberon",
                       "clinvar",
                       "hpo",
                       "hpa",
                       "uniprot",
                       "reactome",
                       "bioontology",
                       "drug_central",
                       "unichem",
                       "repotrial",
                       "iid",
                       "intact",
                        "omim",
                       "ncg",
                       "intogen",
                       "opentargets",
                       "orphanet",
                       #"ncbi",
                       "drugbank", #temp
                       "ctd",
                       "disgenet",
                       "hippie",
                       "sider",
                       "cosmic",
                       }
    nedrex_versions = None
    no_download = None
    embeddings = None
    tobuild_embeddings = None
    current_metadata = None
    if download or rebuild:
        # fallback version is rarely needed. Do not change that file, only use the config!
        default_version = get_fallback_version()
        nedrex_versions = update_versions(ignored_sources=ignored_sources, default_version=default_version)
        save_fallback_version(f"{nedrex_versions['version']}")

        # do the download
        logger.debug("Download: ON")
        current_metadata = nedrex_versions["source_databases"]
        # already up-to-date data
        no_download = [key for key in prev_metadata if key in current_metadata and
                       prev_metadata[key]['version'] == current_metadata[key]['version']]
        if rebuild:
            no_download = []
            logger.info(
                f"Skipping download for: {no_download} because of rebuild flag. This can be disabled by setting FORCE_REBUILD=0")
        
        static_download = [key for key in ["bioontology", "drugbank", "disgenet", "repotrial",
                                           "hippie", "sider", "cosmic", "intogen", "ncg"] if key not in no_download and
                           key not in ignored_sources]

        loglevel_info_or_debug = os.environ.get("LOG_LEVEL", "INFO") in ["DEBUG", "INFO"]
        if static_download:
            logger.info("Starting dump downloads")
            subprocess.run(["./setup_data.sh", "/data/nedrex_files", "1" if loglevel_info_or_debug else "0"])
        downloaders.download_all(ignored_sources=ignored_sources,
                                 no_download_meta=no_download)
    if version_update:
        nedrex_versions = get_versions(version_update)

    if create_embeddings:
        embeddings, tobuild_embeddings = manage_embeddings(dev_instance=dev_instance,
                                                           distinct_per_collection=distinct_per_collection,
                                                           rebuild=rebuild)

    # prepare neo4j for import from mongoDB
    dev_instance.remove(neo4j_mode="import")
    dev_instance.set_up(use_existing_volume=False, neo4j_mode="import")

    # MongoDB data download & import
    MongoInstance.connect("dev")
    MongoInstance.set_indexes()

    MongoInstance.DB["metadata"].replace_one({}, nedrex_versions, upsert=True)

    # Run parser pipeline
    run_parsers(
        version=version,
        ignored_sources=ignored_sources
    )

    for src in ignored_sources:
        col = src.replace("-", "_")
        if col in MongoInstance.DB.list_collection_names():
            logger.info(f"Minimal Build: Dropping collection for ignored source: {col}")
            MongoInstance.DB[col].drop()

    return embeddings, tobuild_embeddings, no_download, current_metadata


@click.option("--conf", required=True, type=click.Path(exists=True))
@cli.command()
def embed_only(conf):
    logger.info("Creating embeddings only")
    logger.debug(f"Config file: {conf}")
    nedrexdb.parse_config(conf)
    live_instance = NeDRexLiveInstance()
    live_instance.remove()
    live_instance.set_up(use_existing_volume=True, neo4j_mode="db-write")

    time.sleep(60)
    create_constraints()


    # create embeddings
    # try:
    create_vector_indices()
    # except Exception as e:
    #     print(e)
    #     logger.warning("Failed to create vector indices")

    live_instance = NeDRexLiveInstance()
    live_instance.remove()
    live_instance.set_up(use_existing_volume=True, neo4j_mode="db")


@click.option("--conf", required=True, type=click.Path(exists=True))
# @click.option("--create_embeddings", is_flag=True, default=False)
@cli.command()
def restart_live(conf):
    logger.debug(f"Config file: {conf}")
    nedrexdb.parse_config(conf)

    live_instance = NeDRexLiveInstance()
    live_instance.remove()
    live_instance.set_up(use_existing_volume=True, neo4j_mode="db")


if __name__ == "__main__":
    cli()
