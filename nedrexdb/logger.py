import sys as _sys
import os
import logging

from loguru import logger

# Remove default logger
logger.remove()
log_format = (
    "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
    "<lvl>{level}</lvl> |  "
    "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
    "<level>{message}</level>"
)
os.environ["LOG_LEVEL"] = os.environ.get("LOG_LEVEL", "INFO")
logger.add(_sys.stderr, format=log_format, level=os.environ.get("LOG_LEVEL"))

# Suppress verbose deprecation warnings and notifications from Neo4j driver
logging.getLogger("neo4j").setLevel(logging.ERROR)
