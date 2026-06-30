import datetime as _datetime
import logging
import os
import shutil as _shutil
import re as _re
import time
from pymongo import MongoClient

import requests
from pathlib import Path as _Path

import nedrexdb
from nedrexdb.logger import logger
from nedrexdb import config as _config
from nedrexdb import mconfig as _mconfig
from nedrexdb.common import Downloader
from nedrexdb.db import MongoInstance
from nedrexdb.db.parsers import unichem
from nedrexdb.downloaders.biogrid import download_biogrid as _download_biogrid, get_latest_biogrid_version
from nedrexdb.downloaders.chembl import download_chembl as _download_chembl, get_latest_chembl_version
from nedrexdb.downloaders.ncg import download_ncg as _download_ncg
from nedrexdb.downloaders.intogen import download_intogen as _download_intogen
from nedrexdb.downloaders.orphanet import download_orphanet as _download_orphanet
from nedrexdb.downloaders.opentargets import download_opentargets as _download_opentargets
from nedrexdb.downloaders.rxnorm import download_rxnorm as _download_rxnorm
from nedrexdb.exceptions import (
    ProcessError as _ProcessError,
)


class Version:
    def __init__(self, string):
        self.major, self.minor, self.patch = [int(i) for i in string.split(".")]

    def increment(self, level):
        if level == "major":
            self.major += 1
        elif level == "minor":
            self.minor += 1
        elif level == "patch":
            self.patch += 1

    def __repr__(self):
        return f"{self.major}.{self.minor}.{self.patch}"

def update_version(name, source_url, unique_pattern, mode="date", skip_digits=0):
    try:
        response = requests.get(source_url)
        if response.status_code != 200:
            raise _ProcessError(f"got non-zero status code while updating metadata.\n source:{name}\n URL: {source_url}")
        result = _re.findall(unique_pattern, response.text)
        text = str(result[0])
        if mode == "date":
            # create version number from date
            version = "".join(_re.findall(r"\d+", text))
            version = version[skip_digits:]
            version = f"{version[0:4]}-{version[4:6]}-{version[6:]}"
        else:
            version = "".join(_re.findall(r"\d+", text))
            version = version[skip_digits:]
            version = f"{version[0:2]}.{version[2:]}"
    except:
        version = "N/A"
    date = _datetime.datetime.now().date()
    logger.debug(f"{name}: date: {date}, version: {version}")
    return {"date": f"{date}", "version": version}

def download_all(force=False, ignored_sources=set(), no_download_meta={}):
    base = _Path(_config["db.root_directory"])
    download_dir = base / _config["sources.directory"]

    if force and (download_dir).exists():
        _shutil.rmtree(download_dir)
    download_dir.mkdir(exist_ok=True, parents=True)

    sources = _config["sources"]
    # Remove the source keys (in filter)
    exclude_keys = {"directory", "username", "password", "default_version", "version",
                    "version_url", "version_pattern", "version_mode", "skip_digits"}
    exclude_keys.update(ignored_sources)

    logger.debug(f"ignore sources for download: {ignored_sources}")
    
    if "opentargets" not in ignored_sources:
        if "opentargets" not in no_download_meta:
            _download_opentargets()
        else:
            logger.debug("opentargets is already up-to-date")
    if "ncg" not in ignored_sources:
        if "ncg" not in no_download_meta:
            _download_ncg()
        else:
            logger.debug("ncg is already up-to-date")
    if "intogen" not in ignored_sources:
        if "intogen" not in no_download_meta:
            _download_intogen()
        else:
            logger.debug("intogen is already up-to-date")
    if "orphanet" not in ignored_sources:
        if "orphanet" not in no_download_meta:
            _download_orphanet()
        else:
            logger.debug("orphanet is already up-to-date")
    if "chembl" not in ignored_sources:
        if "chembl" not in no_download_meta:
            _download_chembl()
        else:
            logger.debug("chembl is already up-to-date")
    if "biogrid" not in ignored_sources:
        if "biogrid" not in no_download_meta:
            _download_biogrid()
        else:
            logger.debug("biogrid is already up-to-date")
    if "rxnorm" not in ignored_sources:
        if "rxnorm" not in no_download_meta:
            _download_rxnorm()
        else:
            logger.debug("rxnorm is already up-to-date")

    for source in filter(lambda i: i not in exclude_keys, sources):

        # Catch case to skip sources with bespoke downloaders entirely.
        if source in {
            "biogrid",
            "chembl",
            "ncg",
            "opentargets",
            "cosmic",
            "intogen",
            "hippie",
            "sider",
            "rxnorm"
        }:
            continue

        # Catch case to skip sources with bespoke downloaders after setting metadata.
        if source in {
            "drugbank",
            "disgenet",
        }:
            continue

        # only download if necessary (by checking previous metadata)
        if source not in no_download_meta:
            (download_dir / source).mkdir(exist_ok=True)

            data = sources[source]
            username = data.get("username")
            password = data.get("password")

            for _, download in filter(lambda i: i[0] not in exclude_keys, data.items()):
                url = download.get("url")
                filename = download.get("filename")
                if url is None:
                    continue
                if filename is None:
                    filename = url.rsplit("/", 1)[1]

                d = Downloader(
                    url=url,
                    target=download_dir / source / filename,
                    username=username,
                    password=password,
                )
                validated = False
                retries = 3
                timeout = 30
                while not validated and retries > 0:
                    d.download()
                    validated = validate_download(download_dir / source / filename, source)
                    if not validated:
                        logging.error(f"failed to verify download of {filename} for {source}! Retrying in {timeout} seconds.")
                        retries -= 1
                        time.sleep(timeout)
        else:
            logger.debug(f"{source} is already up-to-date")

def validate_download(file, source):
    if source == "unichem":
        return unichem.validate_file(file)
    return True

def update_versions(ignored_sources=set(), default_version=None):
    sources = _config["sources"]
    # Remove the source keys (in filter)
    exclude_keys = {"directory", "username", "password", "default_version", "version",
                    "version_url", "version_pattern", "version_mode", "skip_digits"}
    exclude_keys.update(ignored_sources)

    metadata = {"source_databases": {}}

    logger.debug(f"ignore sources for versions: {ignored_sources}")

    if "chembl" not in ignored_sources:
        chembl_date = _datetime.datetime.now().date()
        chembl_version = get_latest_chembl_version()
        metadata["source_databases"]["chembl"] = {"date": f"{chembl_date}", "version": chembl_version}
        # Catch case to skip sources with bespoke version grabbers entirely.
        exclude_keys.add("chembl")

    if "biogrid" not in ignored_sources:
        biogrid_date = _datetime.datetime.now().date()
        biogrid_version = get_latest_biogrid_version()
        metadata["source_databases"]["biogrid"] = {"date": f"{biogrid_date}", "version": biogrid_version}
        # Catch case to skip sources with bespoke version grabbers entirely.
        exclude_keys.add("biogrid")

    for source in filter(lambda i: i not in exclude_keys, sources):

        logger.info(f"checking version of {source}")

        # update metadata
        meta = sources[source]
        if "version_url" in meta.keys():
            version_url = meta["version_url"]
            version_pattern = rf"{meta['version_pattern']}"
            skip_digits = meta["skip_digits"] if "skip_digits" in meta.keys() else 0
            version_mode = meta["version_mode"] if "version_mode" in meta.keys() else "date"
            metadata["source_databases"][source] = update_version(name=source,
                                                                  source_url=version_url,
                                                                  unique_pattern=version_pattern,
                                                                  mode=version_mode,
                                                                  skip_digits=skip_digits)
        else:
            date = _datetime.datetime.now().date()
            version = meta["version"] if "version" in meta.keys() else f"{date}"
            metadata["source_databases"][source] = {"date": f"{date}", "version": version}

    try:
        docs = list(MongoInstance.DB["metadata"].find())
    except:
        docs = []

    if len(docs) == 1:
        version = docs[0]["version"]
    elif len(docs) == 0:
        if default_version is None:
            default_version = sources["default_version"]
        version = default_version
    else:
        raise Exception("should only be one document in the metadata collection")

    if os.environ.get("FORCE_VERSION_OVERRIDE", "0") == "1":
        if "default_version" in sources.keys():
            version = sources["default_version"]

    v = Version(version)
    v.increment("patch")

    metadata["version"] = f"{v}"
    return metadata

    # metadata debugging file. Use to check metadata if DB does not work as intended.
    #with open("./metadata.txt", "w") as f:
    #    f.write(f"Last Download: {_datetime.datetime.now().date()}\n\n")
    #    f.write("Current metadata: \n")
    #    f.write(f"version:\t{metadata['version']}\n")
    #    f.write("source_databases:\n")
    #    for key in metadata["source_databases"].keys():
    #        f.write(f"V\t{key}:\t{metadata['source_databases'][key]}\n")
    # can be parsed with this code:
    #metadata = {"source_databases": {}}
    #with open("./metadata.txt") as f:
    #    for line in f:
    #        if line.startswith("V"):
    #            sd_split = line.rstrip().split("\t")
    #            metadata["source_databases"][sd_split[1][:-1]] = ast.literal_eval(sd_split[2])
    #        elif line.startswith("version"):
    #            metadata["version"] = line.rstrip().split("\t")[1]


def get_versions(no_download):
    increment = False
    # no_download is either "true" or path to metadata config -> mconfig

    # so in this case no_download contains the path to the licensed_config
    if not no_download == "true":
        licensed_config = no_download
        nedrexdb.parse_mconfig(licensed_config)
        mconfig = _mconfig

    else:
        increment = True
        mconfig = _config

    version = "live"

    mongo_port = 27017
    mongo_host = mconfig["db"][version.lower()]["mongo_name"]
    db_name = mconfig["db"]["mongo_db"]

    try:
        client = MongoClient(port=mongo_port, host=mongo_host)
        db = client[db_name]

        metadata = db["metadata"].find_one()
    except:
        metadata = None
    metadata = metadata if metadata is not None else {"version": "0.0.0"}
    if "source_databases" not in metadata:
        metadata["source_databases"] = {}

    if increment:
        v = Version(metadata["version"])
        v.increment("patch")
        metadata["version"] = f"{v}"

    metadata_keys = {k for k in metadata["source_databases"].keys()}
    for source in metadata_keys:
        if source not in _config["sources"]:
            del metadata["source_databases"][source]

    return metadata

