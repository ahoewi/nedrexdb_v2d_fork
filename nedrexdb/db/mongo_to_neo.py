import os as _os
import subprocess as _subprocess
from collections.abc import MutableMapping as _MutableMapping
from pathlib import Path as _Path

import numpy as _np
import pandas as _pd
import time as _time

from nedrexdb import config as _config
from nedrexdb.logger import logger

_TYPE_MAP = {bool: "boolean", int: "int", float: "double", str: "string"}


def flatten(d, parent_key="", sep="."):
    items = []
    for k, v in d.items():
        new_key = f"{parent_key}{sep}{k}" if parent_key else k
        if isinstance(v, _MutableMapping):
            items.extend(flatten(v, new_key, sep).items())
        else:
            items.append((new_key, v))
    return dict(items)


def determine_series_type(series_main):
    series = series_main.copy()
    series = series.dropna()

    s = set()
    for item in series:
        # skip items with no content
        if not item:
            continue

        # is the item a container?
        if isinstance(item, list):
            q = set(type(i) for i in item)
            s.add(f"{_TYPE_MAP[q.pop()]}[]")
        else:
            s.add(_TYPE_MAP[type(item)])

    if len(s) == 1:
        return s.pop()
    else:
        return False


def mongo_to_neo(nedrex_instance, db):
    collections = db.list_collection_names()

    nodes = [node for node in _config["api.node_collections"] if node in collections]
    edges = [edge for edge in _config["api.edge_collections"] if edge in collections]

    delimiter = "|"

    workdir = _Path("/tmp")

    for node in nodes:
        logger.debug(node)
        cursor = db[node].find()
        df = _pd.DataFrame(flatten(i) for i in cursor)
        # replace NaN with empty strings
        df = df.replace(_np.nan, "", regex=True)
        for key in ["_id", "_cls", "created", "updated"]:
            if key in df.columns:
                del df[key]

        for col in df.columns:
            if col == "primaryDomainId":
                df = df.rename(columns={col: f"{col}:ID"})
            elif col == "type":
                df["type:string"] = df["type"]
                df = df.rename(columns={col: ":LABEL"})
            else:
                data_type = determine_series_type(df[col])
                if data_type is False:
                    df.drop(columns=[col], inplace=True)
                else:
                    if data_type.endswith("[]"):
                        df[col] = df[col].apply(delimiter.join)
                    df = df.rename(columns={col: f"{col}:{data_type}"})

        df.to_csv(f"{workdir}/{node}.csv", index=False)

    for edge in edges:
        logger.debug(edge)
        cursor = db[edge].find()
        df = _pd.DataFrame(flatten(i) for i in cursor)
        # replace NaN with empty strings
        df = df.replace(_np.nan, "", regex=True)
        for key in ["_id", "created", "updated"]:
            if key in df.columns:
                del df[key]

        for col in df.columns:
            if col in {"sourceDomainId", "memberOne"}:
                df = df.rename(columns={col: f"{col}:START_ID"})
            elif col in {"targetDomainId", "memberTwo"}:
                df = df.rename(columns={col: f"{col}:END_ID"})
            elif col == "type":
                df["type:string"] = df["type"]
                df = df.rename(columns={col: ":TYPE"})
            else:
                data_type = determine_series_type(df[col])
                if data_type is False:
                    df.drop(columns=[col], inplace=True)
                else:
                    if data_type.endswith("[]"):
                        df[col] = df[col].apply(delimiter.join)

                    df = df.rename(columns={col: f"{col}:{data_type}"})

        cols = list(df.columns)
        cols.remove(":TYPE")
        cols.append(":TYPE")
        df.to_csv(f"{workdir}/{edge}.csv", columns=cols, index=False)

    #logger.debug(
    #    f"calling 'docker exec {nedrex_instance.neo4j_container_name} chown -R neo4j:neo4j /data /import /logs "
    #    f"/var/lib/neo4j/plugins /app'")
    _subprocess.call([
        "docker", "exec", nedrex_instance.neo4j_container_name,
        "chown", "-R", "neo4j:neo4j", "/data", "/logs", "/var/lib/neo4j/plugins", "/app"
    ])
    _time.sleep(30)
    command = [
        "docker",
        "exec",
        "-u",
        "neo4j",
        nedrex_instance.neo4j_container_name,
        "neo4j-admin",
        "database",
        "import",
        "full",
        "neo4j",
        f"--array-delimiter={delimiter}",
        "--multiline-fields=true",
        "--overwrite-destination=true",
        "--ignore-empty-strings=true",
        "--skip-bad-relationships=true",
        "--skip-duplicate-nodes=true",
    ]
    for node in nodes:
        command += ["--nodes=/import/" + node + ".csv"]
    for edge in edges:
        command += ["--relationships=/import/" + edge + ".csv"]
    # command += ["--database=nedrex"]

    logger.info("Importing files into Neo4j...")
    logger.debug("Running: "+" ".join(command))
    _subprocess.call(command)
    logger.info("Waiting 60s for Neo4j to run internal processes...")
    _time.sleep(60)
    # clean up
    for node in nodes:
       _os.remove(f"{workdir}/{node}.csv")
    for edge in edges:
       _os.remove(f"{workdir}/{edge}.csv")
    logger.info("Neo4j import done!")