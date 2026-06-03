# NeDRexDB - Dockerized


# Downloaders, Models and Parsers for NeDRex - version 2


* [Downloader](#downloader)
  * [Single file download](#single-file-download)
  * [Specialized download scripts](#specialized-download-scripts)
* [Models](#models)
* [Parsers](#parsers)
  * [Specialized data](#specialized-data)
  * [Integrated data](#integrated-data)



## Downloader


### Single file download:

Data sources are downloaded in one of two ways. The first, and simplest, is to add a section in the config TOML file. **The TOML files are the master config files of the database and API services and the default files can be downloaded from here**: [https://cloud.uni-hamburg.de/s/zBT3j8Srn6pJXHc](https://cloud.uni-hamburg.de/s/zBT3j8Srn6pJXHc)

The simplest example of this is given below but you will see many more in the .toml files (create config via editing config_template.toml in a text editor):


```toml
[sources]
[sources.go]
[sources.go.go_core_owl]
url = "http://purl.obolibrary.org/obo/go.owl"
```


The sources section of the config is formatted as `[sources.<database>.<description>]`. URL is the only required field for the downloader to be able to download the file. In cases where the default download name (go.owl, in this case) is undesirable, you can add `filename=<val>` to have the filename changed. 

Additionally, if a username and password are required to access the file, these too can be added to the config (using `username=` and `password=`). Where a username and password are passed, then downloads are carried out using the Python `requests` library. If a username and password are not carried out, then the download is carried out using `wget` via a subprocess call. 

**_NOTE FOR CONTRIBUTORS: Download the TOML file from the [nextcloud](https://cloud.uni-hamburg.de/s/zBT3j8Srn6pJXHc) folder, add your lines and send them to [me](mailto:andreas.maier-1@uni-hamburg.de) with whatever additional information necessary to test and add this to the live version!_**

<span style="color:red">**Note for future developers**: The BioGRID downloader will likely break at some point in the future – for example, if BioGRID changes the layout of their statistics page.</span>

<span style="color:red">**Note for future developers**: The logic handling decompressing files varies in location throughout the source code, and should probably be moved to the same place for consistency. For example, ChEMBL is a tar.gz archive, but the decompression of that happens in the parser.</span>

<span style="color:red">**Note for future developers**: Files that are just gzipped aren’t decompressed – these just use the Python `gzip` library to decompress during parsing.</span>


### Specialized download scripts:

**The second is to write a bespoke downloader and then call that in the <code>[download_all() ](https://github.com/repotrial/nedrexdb_v2d/blob/master/nedrexdb/downloaders/__init__.py)</code>function.** In the current code base, this is done for DrugBank and BioGRID, as can be seen [here](https://github.com/repotrial/nedrexdb_v2d/tree/master/nedrexdb/downloaders). 

In the case of BioGRID, this is because there is no static URL to use to download the latest version. Consequently, there is some logic required to determine the existing version (web scraping) and form the URL. 

Additionally, back when we were using the DrugBank XML download, there was a custom downloader to handle unzipping the database.

As of now, the download_all() function has to be adjusted in two places:



1. Skipping automatic download based on the TOML configuration for selected sources

```python
# Catch case to skip sources with bespoke downloaders.
if source in {"biogrid","drugbank",}:
continue
```


2. Import and call the bespoke downloader

```python
from nedrexdb.downloaders.biogrid import download_biogrid as _download_biogrid
_download_biogrid()
```


A path where to place downloaded files” manually” can be constructed as follows:


```python
biogrid_dir = _Path(_config.get("db.root_directory")) / _config.get("sources.directory") / "biogrid"

biogrid_dir.mkdir(exist_ok=True, parents=True)
```


**_NOTE FOR CONTRIBUTORS: If you do not have access to the [nedrexdb](https://github.com/repotrial/nedrexdb_v2d) and/or [nedrexapi](https://github.com/repotrial/api_v2d) GitHub repositories to see and contribute to them, [contact me](mailto:andreas.maier-1@uni-hamburg.de)_** **and tell me your GitHub user/email. A branch for you might be created and/or you can create a pull request to add your downloading script to the project and the [download_all](https://github.com/repotrial/nedrexdb_v2d/blob/master/nedrexdb/downloaders/__init__.py) function.**


## Models

When adding a data source, consider whether you are adding a new _type_ of edge or node to the database. Additionally, consider whether you require new properties to be added to an existing node or edge.

_In general_, every node and edge type has a corresponding file and class in <code>[nedrexdb/db/models/nodes](https://github.com/repotrial/nedrexdb_v2d/tree/master/nedrexdb/db/models/nodes)</code> or <code>[nedrexdb/db/models/edges](https://github.com/repotrial/nedrexdb_v2d/tree/master/nedrexdb/db/models/edges)</code>. For example, there is a <code>Protein</code> class in <code>[nedrexdb/db/models/nodes/protein.py](https://github.com/repotrial/nedrexdb_v2d/blob/master/nedrexdb/db/models/nodes/protein.py)</code>. The purpose of these classes is to represent a document in a DB collection as a Python object. Additionally, these classes typically implement a <code>.generate_update()<sup>$</sup></code> method which generates an upsert statement to insert new nodes/edges into MongoDB. I’ve chosen to use `pymongo` as the way of interfacing with MongoDB using Python - for more details on `pymongo`, please see [this link](https://pymongo.readthedocs.io/en/stable/).

Note, too, that these models aren’t used for updating/adding properties of existing nodes in the database (in practice, this happens rarely). For example, in the open version, the DrugBank CC0 data is parsed to get Drug names & IDs, followed by ChEBI being parsed to get drug group information (approval status). These ChEBI updates are made using a PyMongo update directly (i.e., not via the Drug model class).

In the implementation of models, each file has a `<Type>Base` class in addition to the `<Type>` class. The `<Type>Base` class inherits from the `models.MongoMixin` class, which provides two class methods to assist with querying. A base class then provides two class attributes – `collection_name` (the name of the collection in MongoDB) and `node_type` (the value to set for the “type” attribute on the nodes) – and one method –.

<sup>$</sup>This is not required/enforced (e.g., using abstract base classes) – ultimately, the choice of using the node or edge class abstraction is up to you. For example, the <code>[nedrexdb/db/models/edges/protein_has_signature.py](https://github.com/repotrial/nedrexdb_v2d/blob/master/nedrexdb/db/models/edges/protein_has_signature.py)</code> file is empty, and the logic generating of a PyMongo update statement is handled in the <code>[uniprot_signatures.py](https://github.com/repotrial/nedrexdb_v2d/blob/master/nedrexdb/db/parsers/uniprot_signatures.py)</code> parser.

**_NOTE FOR CONTRIBUTORS: If you do not have access to the [nedrexdb](https://github.com/repotrial/nedrexdb_v2d) and/or [nedrexapi](https://github.com/repotrial/api_v2d) GitHub repositories to see and contribute to them, [contact me](mailto:andreas.maier-1@uni-hamburg.de)_** **and tell me your GitHub user/email. A branch for you might be created, and/or you can create a pull request to add your models to the project and to adjust the available fields or [update()](https://github.com/repotrial/nedrexdb_v2d/blob/master/build.py) function.**


## Parsers

### Specialized data

For data, that is not integrable into the generalistic knowledge graph, because the scope is e.g. limited to a specific group of diseases and this data only exists for those and cannot be regarded in a holistic fashion (so cancer specific cellline studies or information about virus-host protein interactions) have to be regarded separately. A guideline for those will be added once the setup for this is tested and examples are implemented. There will be a way to write those data to the MongoDB without integrating them into the Neo4j knowledge graph. \
As a result these data require purpose-built API routes that handle the query, query the MongoDB and format the output. Instructions on that will follow!


### Integrated data

Parsers will be executed after all files are downloaded and all of them are located [here](https://github.com/repotrial/nedrexdb_v2d/tree/master/nedrexdb/db/parsers). To find the downloaded files, the following helper function can be used: \



```python
from nedrexdb.db.parsers import _get_file_location_factory
get_file_location = _get_file_location_factory("biogrid")
```


Our convention is that each script has some kind of a central `parse()` function, that is then called from the master [build.py](https://github.com/repotrial/nedrexdb_v2d/blob/master/build.py) script. So once a new parser is added, it has to be called from this script.

The parsers directly read, update and insert entries from and into the database:


```python
def parse(self):
   proteins = {i["primaryDomasterId"] for i in Protein.find(MongoInstance.DB)}

    with open(self._f, "r") as f:
        reader = _DictReader(f, fieldnames=self.fieldnames,delimiter="\t")
        members = (BioGridRow(row).parse(proteins_allowed=proteins) for row in reader)

        for chunk in _tqdm(_chunked(members, 1_000), leave=False, desc="Parsing BioGRID"):
            updates = [ppi.generate_update() for ppi in _chain(*chunk)]
            if updates:
               MongoInstance.DB[ProteinInteractsWithProtein.collection_name].bulk_write(updates)
```


The models defining the attributes of nodes like [Protein](https://github.com/repotrial/nedrexdb_v2d/blob/master/nedrexdb/db/models/nodes/protein.py) and edges like [ProteinInteractsWithProtein](https://github.com/repotrial/nedrexdb_v2d/blob/master/nedrexdb/db/models/edges/protein_interacts_with_protein.py) can be found in the [models directory](https://github.com/repotrial/nedrexdb_v2d/tree/master/nedrexdb/db/models). Also see the [models](#models) section below for more info. We use `_tqdm()` to visually present the progress being made and using data chunks together with the MongoDB option `bulk_write()` to optimize write speed. The Neo4j database will be constructed from the MongoDB database at a later point in the update routine.

**_NOTE FOR CONTRIBUTORS: If you do not have access to the [nedrexdb](https://github.com/repotrial/nedrexdb_v2d) and/or [nedrexapi](https://github.com/repotrial/api_v2d) GitHub repositories to see and contribute to them, [contact me](mailto:andreas.maier-1@uni-hamburg.de)_** **and tell me your GitHub user/email. A branch for you might be created and/or you can create a pull request to add your parsers and models to the project and to adjust the [update()](https://github.com/repotrial/nedrexdb_v2d/blob/master/build.py) function in the build.py file.**


