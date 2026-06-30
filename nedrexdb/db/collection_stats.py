from collections import defaultdict

from tqdm import tqdm

from nedrexdb import config as _config
from nedrexdb.logger import logger
import time as _time


def profile_collections(db):
    nodes = _config["api.node_collections"]
    edges = _config["api.edge_collections"]

    collections = nodes + edges
    logger.info("Starting collection profiling...")
    for coll in collections:
        if coll not in db.list_collection_names():
            logger.warning(f"Collection '{coll}' does not exist in database, skipping...")
            continue

        logger.info(f"Profiling collection: {coll}")
        doc_count = 0
        attr_counts = defaultdict(int)

        try:
            total_docs = db[coll].count_documents({})
            if total_docs == 0:
                logger.warning(f"Collection '{coll}' is empty")
                continue

            for doc in tqdm(db[coll].find(), total=total_docs, desc=f"Processing {coll}", leave=False):
                doc_count += 1
                for attr in doc.keys():
                    attr_counts[attr] += 1

            unique_attrs = list(attr_counts.keys())

            db["_collections"].replace_one(
                {"collection": coll},
                {
                    "collection": coll,
                    "document_count": doc_count,
                    "unique_attributes": unique_attrs,
                    "attribute_counts": dict(attr_counts),
                },
                upsert=True
            )
            logger.info(f"Successfully profiled {coll}: {doc_count} documents")
        except Exception as e:
            logger.error(f"Error profiling {coll}: {str(e)}")
            raise


def verify_collections_after_profiling(db):
    actual_collections = set(coll for coll in db.list_collection_names()
                             if not coll.startswith("system.")
                             and coll not in ["_collections", "metadata"])
    metadata_collections = set(doc["collection"] for doc in db["_collections"].find())

    missing = actual_collections - metadata_collections
    if missing:
        logger.error(f"Collections missing from metadata after profiling: {missing}")
        raise Exception(f"Collections missing from metadata after profiling: {missing}")
