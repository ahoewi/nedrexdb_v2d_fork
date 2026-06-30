from nedrexdb.post_integration.neo4j_db_adjustments import (
    get_node_info_string,
    get_edge_info_string,
    create_node_vector_query,
    create_edge_vector_query
)

def test_get_node_info_string():
    config = {
        "Gene": {
            "symbol": {"prefix": "symbol ", "suffix": ""},
            "description": {"prefix": " described as ", "suffix": "."}
        }
    }
    result = get_node_info_string("Gene", config)
    assert "symbol " in result
    assert "described as " in result
    assert "x.primaryDomainId" in result

def test_get_edge_info_string():
    config = {
        "INTERACTS_WITH": {
            "link_term": "interacts with",
            "attributes": {
                "score": {"prefix": " with score ", "suffix": ""},
                "methods": {"prefix": " by methods ", "suffix": "", "type": "list"}
            },
            "source": "Protein",
            "target": "Protein"
        }
    }
    result = get_edge_info_string("INTERACTS_WITH", config)
    assert "interacts with" in result
    assert "entry.s.primaryDomainId" in result
    assert "entry.t.primaryDomainId" in result
    assert "apoc.text.join(entry.r.methods, ', ')" in result

def test_create_node_vector_query():
    info_str = "x.displayName"
    query = create_node_vector_query(info_str, "Gene")
    assert "MATCH (x:Gene) WHERE x.embedding IS NULL" in query
    assert "LIMIT 50000" in query
    assert "MATCH (x:Gene) WHERE id(x) = id" in query
    assert "db.create.setNodeVectorProperty(node, \"embedding\", embedding)" in query

def test_create_edge_vector_query():
    info_str = "entry.s.displayName"
    query = create_edge_vector_query(info_str, "Gene", "GeneAssociatedWithDisorder", "Disorder")
    # Outer query should match directed, label-free relationships of the type
    assert "MATCH ()-[r:GeneAssociatedWithDisorder]->() WHERE r.embedding IS NULL" in query
    assert "LIMIT 50000" in query
    # Inner CALL block match should also be directed
    assert "MATCH (s:Gene)-[r:GeneAssociatedWithDisorder]->(t:Disorder) WHERE id(r) = id" in query
    assert "db.create.setRelationshipVectorProperty(rel, \"embedding\", embedding)" in query
