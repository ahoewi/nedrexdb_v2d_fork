from nedrexdb import config as _config

_LLM_embeddings = _config["embeddings"]
_LLM_BASE=_LLM_embeddings["server_base"]
_LLM_model=_LLM_embeddings["model"]
_LLM_path=_LLM_embeddings["path"]
_LLM_embedding_length=_LLM_embeddings.get("embedding_length",1024)
_LLM_parallel=_LLM_embeddings.get("parallel", False)

_LLM_API_KEY=_LLM_embeddings.get("api_key", "no-key")
