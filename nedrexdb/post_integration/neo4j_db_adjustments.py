from langchain_neo4j import Neo4jGraph
from nedrexdb import config as _config
import time
from nedrexdb.logger import logger
from nedrexdb.post_integration.embedding_config import NODE_EMBEDDING_CONFIG, EDGE_EMBEDDING_CONFIG

node_keys = {key.lower(): key for key in NODE_EMBEDDING_CONFIG.keys()}
edge_keys = {key.lower(): key for key in EDGE_EMBEDDING_CONFIG.keys()}

open_con = None


def get_kg_connection() -> Neo4jGraph:
    global open_con
    NEO4J_URI = f'bolt://{_config["db.dev.neo4j_name"]}:7687'

    retry = 10
    while retry > 0:
        try:
            if open_con is None:
                logger.debug(f"Opening connection to {NEO4J_URI}")
                open_con = Neo4jGraph(
                    url=NEO4J_URI, username="", password="", database='neo4j'
                )
            if open_con is not None:
                return open_con
        except Exception:
            retry -= 1
            if retry == 0:
                logger.error(f"Failed to connect to Neo4j at {NEO4J_URI} after {10} retries!")
                try:
                    import docker as _docker
                    client = _docker.from_env()
                    container = client.containers.get(_config["db.dev.neo4j_name"])
                    logger.error(f"Neo4j container status: {container.status}")
                    logger.error("Last 20 lines of container logs:")
                    logger.error(container.logs(tail=20).decode('utf-8'))
                except Exception as docker_err:
                    logger.error(f"Could not retrieve container status/logs: {docker_err}")
                return None
        time.sleep(30)


def close_kg_connection():
    global open_con
    if open_con is not None:
        open_con.close()
        open_con = None


def create_unique_node_constraint(con, node_type, attribute):
    query = f"CREATE CONSTRAINT {node_type.lower()}_{attribute.lower()}_unique FOR (n:{node_type}) REQUIRE n.{attribute} IS UNIQUE"
    con.query(query)

def create_constraints():
    try:
        if not _config.get("db.set_unique_constraints"):
            return
    except:
        return

    dev_nodes = None

    logger.info("Creating unique constraints for IDs")
    kg = get_kg_connection()
    if kg is None:
        raise RuntimeError(f"Could not connect to Neo4j at bolt://{_config['db.dev.neo4j_name']}:7687 to create unique constraints.")

    # fetch existing constraints once
    existing = kg.query("""
        SHOW CONSTRAINTS YIELD labelsOrTypes, properties
        RETURN labelsOrTypes AS labels, properties
    """)
    existing_set = {
        (
            tuple(e["labels"]) if e["labels"] is not None else (),
            tuple(e["properties"]) if e["properties"] is not None else ()
        )
        for e in existing
    }
    logger.debug(f"Found existing constraints (next line): \n{existing_set}")

    results = kg.query("CALL db.labels()")
    node_types = [r["label"] for r in results]
    node_list = node_types if dev_nodes is None else dev_nodes

    for node in node_list:
        # skip if constraint already exists
        if ((node,), ("primaryDomainId",)) in existing_set:
            continue

        create_unique_node_constraint(kg, node, "primaryDomainId")

    close_kg_connection()


def create_vector_indices(tobuild=set()):
    if not tobuild:
        return

    # only building embeddings for dev nodes and edges, except they are None.
    dev_nodes = []
    dev_edges = []

    for embedding in tobuild:
        if embedding in node_keys:
            dev_nodes.append(node_keys[embedding])
        elif embedding in edge_keys:
            dev_edges.append(edge_keys[embedding])

    logger.info("Starting indexing")

    kg = get_kg_connection()
    if kg is None:
        raise RuntimeError(f"Could not connect to Neo4j at bolt://{_config['db.dev.neo4j_name']}:7687 to create vector indices.")

    index_names = []

    node_list = NODE_EMBEDDING_CONFIG.keys() if dev_nodes is None else dev_nodes

    # Only building embeddings for specified nodes
    retry_list = []
    num_retries = 5
    while num_retries > 0:
        for node in node_list:
            if not fill_vector_index(kg, "NODE", node):
                retry_list.append(node)
            else:
                index_names.append(f"{node.lower()}Embeddings")
        node_list = [node for node in retry_list]
        retry_list = []
        num_retries -= 1

    if len(node_list) > 0:
        logger.warning(f"Could not create embeddings successfully for the following nodes: {node_list}")

    edge_list = EDGE_EMBEDDING_CONFIG.keys() if dev_edges is None else dev_edges
    retry_list = []
    num_retries = 5
    while num_retries > 0:
        for edge in edge_list:
            if not fill_vector_index(kg, "RELATIONSHIP", edge):
                retry_list.append(edge)
            else:
                index_names.append(f"{edge.lower()}Embeddings")
        edge_list = [edge for edge in retry_list]
        retry_list = []
        num_retries -= 1

    if len(edge_list) > 0:
        logger.warning(f"Could not create embeddings successfully for the following edges: {edge_list}")

    if wait_for_database_ready(kg, index_names):
        logger.info("Ready to switch to read-only mode")
    else:
        logger.error("Something went wrong with the index build")
    close_kg_connection()


def get_node_info_string(node_name, node_embedding_config):
    """
    Generates a Cypher string snippet for node embeddings in a more readable way.

    Args:
        node_name (str): The name of the node label (used to look up the config).
        node_embedding_config (dict): The configuration dictionary.

    Returns:
        str: A Cypher string for concatenating node properties.
    """
    config = node_embedding_config.get(node_name, {})
    parts = ["coalesce(x.type, '') + ' with ID ' + coalesce(x.primaryDomainId, '') + ':'"]

    for attribute, format_config in config.items():
        prefix = format_config.get('prefix', ' ')
        suffix = format_config.get('suffix', ' ')
        attribute_type = format_config.get('type', 'string')

        if attribute_type == "list":
            part = f"'{prefix}' + coalesce(apoc.text.join(x.{attribute}, ', '), '') + '{suffix};'"
        else:
            part = f"'{prefix}' + coalesce(toString(x.{attribute}), '') + '{suffix};'"
        parts.append(part)

    return " + ".join(parts)


def get_edge_info_string(edge_name, edge_embedding_config):
    """
    Generates a Cypher string snippet for edge embeddings and fixes a likely bug.

    Args:
        edge_name (str): The name of the edge type (used to look up the config).
        edge_embedding_config (dict): The configuration dictionary.

    Returns:
        str: A Cypher string for concatenating edge and node properties.
    """
    config = edge_embedding_config.get(edge_name, {})
    link_term = config.get("link_term", "is connected to")

    base_info = f"coalesce(entry.s.type, '') + ' ' + coalesce(entry.s.displayName, '') + ' with ID ' + coalesce(entry.s.primaryDomainId, '') + ' {link_term} ' + coalesce(entry.t.type, '') + ' ' + coalesce(entry.t.displayName, '') + ' with ID ' + coalesce(entry.t.primaryDomainId, '')"
    parts = [base_info]

    if "attributes" in config:
        parts.append("' and has properties:'")
        for attribute, format_config in config["attributes"].items():
            prefix = format_config.get('prefix', ' ')
            suffix = format_config.get('suffix', ' ')
            attribute_type = format_config.get('type', 'string')

            if attribute_type == "list":
                part = f"'{prefix}' + coalesce(apoc.text.join(entry.r.{attribute}, ', '), '') + '{suffix};'"
            else:
                part = f"'{prefix}' + coalesce(toString(entry.r.{attribute}), '') + '{suffix};'"
            parts.append(part)

    return " + ".join(parts)

def fill_vector_index(con, entityType, name) -> bool:
    try:
        start = time.time()
        from nedrexdb.llm import (_LLM_API_KEY, _LLM_BASE, _LLM_path, _LLM_model, _LLM_embedding_length, _LLM_parallel)
        create_vector_index(con, entityType, name,_LLM_embedding_length)
        params = {"api_key": _LLM_API_KEY, "llm_base": _LLM_BASE, "llm_path": _LLM_path, "llm_model": _LLM_model}
        info_string = get_info_string(entityType, name, NODE_EMBEDDING_CONFIG, EDGE_EMBEDDING_CONFIG)
        if entityType == "NODE":
            query = create_node_vector_query(info_string, name, _LLM_parallel)
        else:
            source_name = EDGE_EMBEDDING_CONFIG[name]["source"]
            target_name = EDGE_EMBEDDING_CONFIG[name]["target"]
            query = create_edge_vector_query(info_string, source_name, name, target_name, _LLM_parallel)
        
        MAX_STALL_ATTEMPTS = 3
        prev_remaining = None
        stall_count = 0

        while True:
            if entityType == "NODE":
                count_query = f"MATCH (x:{name}) WHERE x.embedding IS NULL RETURN count(x) AS count"
            else:
                count_query = f"MATCH ()-[r:{name}]->() WHERE r.embedding IS NULL RETURN count(r) AS count"

            res = con.query(count_query)
            remaining = res[0]["count"] if res else 0
            if remaining == 0:
                break

            logger.info(f"Remaining {name} elements to embed: {remaining}")

            if remaining == prev_remaining:
                stall_count += 1
                if stall_count >= MAX_STALL_ATTEMPTS:
                    raise RuntimeError(
                        f"Embedding stalled: {name} count stuck at {remaining} after "
                        f"{stall_count} consecutive no-progress iterations"
                    )
            else:
                stall_count = 0
            prev_remaining = remaining

            retries = 5
            while retries > 0:
                retries -= 1
                try:
                    con.query(query, params=params)
                    break
                except Exception as e:
                    print(e)
                    logger.error(f"Encountered an issue! Retry {6 - retries} retrying in 60s...")
                    if retries == 0:
                        raise e
                    time.sleep(60)
        duration = time.time() - start
        logger.info(f"Building {name} embedding indexes finished after {duration} seconds")
        return True
    except Exception as e:
        print(e)
        logger.error("Could not create vector index for " + name)
        return False

def get_info_string(element_type, name, node_config, edge_config):
    """
    Dispatcher function to get the correct info string for a node or edge.
    This prevents calling the wrong generator with the wrong config by
    routing the request based on the element_type.

    Args:
        element_type (str): The type of element, either "NODE" or "EDGE".
        name (str): The name of the node label or edge type.
        node_config (dict): The complete node embedding configuration dictionary.
        edge_config (dict): The complete edge embedding configuration dictionary.

    Returns:
        str: The generated Cypher string snippet.
    """
    if element_type.upper() == "NODE":
        return get_node_info_string(name, node_config)
    elif element_type.upper() == "RELATIONSHIP":
        return get_edge_info_string(name, edge_config)
    else:
        raise ValueError(f"Unknown element_type: {element_type}. Must be 'NODE' or 'EDGE'.")

def create_node_vector_query(node_info_string, name, parallel=False):
    query = f"""
    MATCH (x:{name}) WHERE x.embedding IS NULL
    WITH x LIMIT 50000
    WITH id(x) AS id
    WITH collect(id) AS ids
    UNWIND range(0, size(ids) - 1, 100) AS i
    WITH ids[i..i+100] AS id_batch
    CALL {{
         WITH id_batch
         UNWIND id_batch AS id
         MATCH (x:{name}) WHERE id(x) = id
         WITH x, {node_info_string} AS text
         WITH x, CASE WHEN text IS NULL OR trim(text) = "" THEN "unknown" ELSE trim(text) END AS final_text
         WITH collect(x) AS batchNodes, collect(final_text) AS batchTexts
         WHERE size(batchTexts) > 0
          CALL apoc.ml.openai.embedding(
              batchTexts,
              $api_key,
              {{
                  endpoint: $llm_base,
                  path: $llm_path,
                  model: $llm_model,
                  enableBackOffRetries: true,
                  backOffRetries: 20,
                  exponentialBackoff: true
              }}
          ) YIELD index, embedding
          WITH batchNodes[index] as node, embedding
          CALL db.create.setNodeVectorProperty(node, "embedding", embedding)
    }} IN TRANSACTIONS OF 10 ROWS
    """
    return query


def create_edge_vector_query(edge_info_string, source_name, name, target_name, parallel=False):
    query = f"""
      MATCH ()-[r:{name}]->() WHERE r.embedding IS NULL
      WITH r LIMIT 50000
      WITH id(r) AS id
      WITH collect(id) AS ids
      UNWIND range(0, size(ids) - 1, 100) AS i
      WITH ids[i..i+100] AS id_batch
      CALL {{
           WITH id_batch
           UNWIND id_batch AS id
           MATCH (s:{source_name})-[r:{name}]->(t:{target_name}) WHERE id(r) = id
           WITH r, {{s: s, r: r, t: t}} AS entry
           WITH r, {edge_info_string} AS text
           WITH r, CASE WHEN text IS NULL OR trim(text) = "" THEN "unknown" ELSE trim(text) END AS final_text
           WITH collect(r) AS batchRelationships, collect(final_text) AS batchTexts
           WHERE size(batchTexts) > 0
          CALL apoc.ml.openai.embedding(
              batchTexts, 
              $api_key, 
              {{
                  endpoint: $llm_base,
                  path: $llm_path,
                  model: $llm_model,
                  enableBackOffRetries: true,
                  backOffRetries: 20,
                  exponentialBackoff: true
              }}
          ) YIELD index, embedding
          WITH batchRelationships[index] as rel, embedding 
          CALL db.create.setRelationshipVectorProperty(rel, "embedding", embedding)
      }} IN TRANSACTIONS OF 10 ROWS
    """
    return query

def create_vector_index(con, entityType, name, length=1024):
    props = {"index_name": f"{name.lower()}Embeddings"}
    if entityType == "NODE":
        con.query("""CREATE VECTOR INDEX $index_name IF NOT EXISTS
        FOR (d: """ + name + """) ON (d.embedding) 
        OPTIONS { indexConfig: {
          `vector.dimensions`: """+str(length)+""",
          `vector.similarity_function`: 'cosine'
        }}""", params=props)
    else:
        con.query("""CREATE VECTOR INDEX $index_name IF NOT EXISTS
               FOR ()-[r:""" + name + """]-() ON (r.embedding) 
               OPTIONS { indexConfig: {
                 `vector.dimensions`: """+str(length)+""",
                 `vector.similarity_function`: 'cosine'
               }}""", params=props)


def wait_for_database_ready(con, index_names=['bad_default']):
    for index_name in index_names:
        try:
            result = list(con.query("""
                SHOW INDEXES
                YIELD name, state, type, labelsOrTypes, properties
                WHERE name = $name
                RETURN *
            """, {"name": index_name}))

            if result:
                index = result[0]
                logger.debug(f"\nIndex details:")
                logger.debug(f"- Name: {index['name']}")
                logger.debug(f"- State: {index['state']}")
                logger.debug(f"- Type: {index['type']}")
                logger.debug(f"- Labels: {index['labelsOrTypes']}")
                logger.debug(f"- Properties: {index['properties']}")
                return index['state'] == 'ONLINE'
            else:
                logger.warning(f"\nNo index found with name {index_name}")
                return False

        except Exception as e:
            logger.error(f"\nError checking index: {str(e)}")
            return False
