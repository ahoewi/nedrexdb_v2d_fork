import time
import time as _time
from abc import ABC as _ABC, abstractmethod as _abstractmethod

import docker as _docker
from docker.errors import NotFound, APIError
from subprocess import run, CalledProcessError

from nedrexdb import config as _config
from nedrexdb.logger import logger

_client = _docker.from_env()


def get_mongo_image():
    return _config["db.mongo_image"]


def get_mongo_express_image():
    return _config["db.mongo_express_image"]


def get_neo4j_image():
    return _config["db.neo4j_image"]


def generate_mongo_volume_name():
    timestamp = _time.time_ns() // 1_000_000  # time in ms
    volume_name = f"{_config['db.volume_root']}_mongo_{timestamp}"
    return volume_name


def generate_new_mongo_volume():
    name = generate_mongo_volume_name()
    _client.volumes.create(name=name)
    return name


def get_mongo_volumes():
    volume_root = _config["db.volume_root"]
    volumes = _client.volumes.list()
    volumes = [vol for vol in volumes if vol.name.startswith(f"{volume_root}_mongo")]
    volumes.sort(key=lambda i: i.name, reverse=True)
    return volumes

def update_neo4j_image_version():
    command = ["docker", "pull", _config["db.neo4j_image"]]
    result = run(
        command,
        capture_output=True,
        text=True
    )
    logger.debug(result.stdout)
    logger.warning(result.stderr)


def generate_neo4j_volume_name():
    timestamp = _time.time_ns() // 1_000_000  # time in ms
    volume_name = f"{_config['db.volume_root']}_neo4j_{timestamp}"
    return volume_name


def generate_new_neo4j_volume():
    name = generate_neo4j_volume_name()
    _client.volumes.create(name=name)
    return name


def get_neo4j_volumes():
    volume_root = _config["db.volume_root"]
    volumes = _client.volumes.list()
    volumes = [vol for vol in volumes if vol.name.startswith(f"{volume_root}_neo4j")]
    volumes.sort(key=lambda i: i.name, reverse=True)
    return volumes


class _NeDRexInstance(_ABC):
    @_abstractmethod
    def set_up(self):
        pass

    @_abstractmethod
    def remove(self):
        pass


class _NeDRexBaseInstance(_NeDRexInstance):
    GRACEFUL_SHUTDOWN_TIMEOUT = 1200

    @property
    def restart_policy(self):
        return {"Name": "always"}

    @property
    def mongo_container_name(self):
        return _config[f"db.{self.version}.container_name"]

    @property
    def neo4j_container_name(self):
        return f'{_config[f"db.{self.version}.container_name"]}_neo4j'

    @property
    def db_mode(self):
        return f'{_config["api.mode"]}'

    @property
    def neo4j_http_port(self):
        return _config[f"db.{self.version}.neo4j_http_port"]

    @property
    def neo4j_name(self):
        return _config[f"db.{self.version}.neo4j_name"]

    @property
    def neo4j_bolt_port(self):
        return _config[f"db.{self.version}.neo4j_bolt_port"]

    @property
    def mongo_port(self):
        return _config[f"db.{self.version}.mongo_port"]

    # @property
    # def neo4j_max_mem(self):
    #     max_mem = _config[f"db.{self.version}.neo4j_memory_max"]
    #     return max_mem if max_mem is not None else "16g"

    @property
    def network_name(self):
        # return _config[f"db.{self.version}.network"]
        return "nedrexdb_default"

    @property
    def express_port(self):
        return _config[f"db.{self.version}.mongo_express_port"]

    @property
    def express_container_name(self):
        return _config[f"db.{self.version}.express_container_name"]

    @property
    def mongo_container(self):
        try:
            return _client.containers.get(self.mongo_container_name)
        except _docker.errors.NotFound:
            return None

    @property
    def express_container(self):
        try:
            return _client.containers.get(self.express_container_name)
        except _docker.errors.NotFound:
            return None

    @property
    def neo4j_container(self):
        try:
            return _client.containers.get(self.neo4j_container_name)
        except _docker.errors.NotFound:
            return None

    def _set_up_network(self):
        try:
            _client.networks.get(self.network_name)
        except _docker.errors.NotFound:
            _client.networks.create(self.network_name)

    # def _set_up_neo4j(self, neo4j_mode, use_existing_volume):
    #     if self.neo4j_container:
    #         return
    #
    #     if use_existing_volume:
    #         volumes = get_neo4j_volumes()
    #         if not volumes:
    #             raise ValueError("use_existing_volume set to True but no volume already exists")
    #         volume = volumes[0].name
    #     else:
    #         volume = generate_neo4j_volume_name()
    #
    #     max_mem = _config[f"db.{self.version}.neo4j_memory_max"]
    #     pagecache_mem = _config[f"db.{self.version}.neo4j_pagecache_max"]
    #     max_mem = max_mem if max_mem is not None else "16G"
    #     pagecache_mem = pagecache_mem if pagecache_mem is not None else "4G"
    #
    #     kwargs = {
    #         "image": get_neo4j_image(),
    #         "detach": True,
    #         "name": self.neo4j_container_name,
    #         "volumes": {volume: {"bind": "/data", "mode": "rw"}},
    #         "ports": {7474: ("127.0.0.1", self.neo4j_http_port), 7687: ("127.0.0.1", self.neo4j_bolt_port)},
    #         "environment": {
    #             "NEO4J_AUTH": "none",
    #             "NEO4J_PLUGINS": '["apoc"]',
    #             "NEO4J_ACCEPT_LICENSE_AGREEMENT": "yes",
    #             "NEO4J_server_config_strict__validation_enabled": "false",
    #             "NEO4J_server_memory_heap_initial__size": max_mem,
    #             "NEO4J_server_memory_heap_max__size": max_mem,
    #             "NEO4J_server_memory_pagecache_size": pagecache_mem,
    #         },
    #         "network": self.network_name,
    #         "remove": False,
    #         "restart_policy": {"Name": "always"}
    #     }
    #
    #     if self.db_mode == "open":
    #         kwargs["ports"][7474] = self.neo4j_http_port
    #         kwargs["ports"][7687] = self.neo4j_bolt_port
    #
    #     if neo4j_mode == "import":
    #         kwargs["volumes"].update({"/tmp/nedrexdb_v2": {"bind": "/import", "mode": "ro"}})
    #         kwargs["environment"]["NEO4J_server_memory_heap_initial__size"] = "4G"
    #         kwargs["environment"]["NEO4J_server_memory_heap_max__size"] = "4G"
    #         kwargs["environment"]["NEO4J_server_memory_pagecache_size"] = "4G"
    #         kwargs["stdin_open"] = True
    #         kwargs["tty"] = True
    #         kwargs["entrypoint"] = "/bin/bash"
    #
    #     elif neo4j_mode == "db":
    #         kwargs["environment"]["NEO4J_server_databases_read__only"] = "true"
    #         kwargs["environment"]["NEO4J_server_databases_default__to__read__only"] = "true"
    #         kwargs["environment"]["NEO4J_db_transaction_timeout"] = "60s"
    #         # kwargs["environment"]["NEO4J_dbms_memory_heap_max__size"] = max_mem.upper()
    #         # kwargs["environment"]["NEO4J_server_memory_pagecache_size"] = "4G"
    #     elif neo4j_mode == "db-write":
    #         kwargs["environment"]["NEO4J_server_databases_read__only"] = "false"
    #         kwargs["environment"]["NEO4J_server_databases_default__to__read__only"] = "false"
    #     else:
    #         raise Exception(f"neo4j_mode {neo4j_mode!r} is invalid")
    #     _client.containers.run(**kwargs)

    def _set_up_neo4j(self, neo4j_mode, use_existing_volume):
        if self.neo4j_container:
            return

        if use_existing_volume:
            volumes = get_neo4j_volumes()
            if not volumes:
                raise ValueError("use_existing_volume set to True but no volume already exists")
            volume = volumes[0].name
        else:
            volume = generate_neo4j_volume_name()

        max_mem = _config[f"db.{self.version}.neo4j_memory_max"]
        max_mem = max_mem if max_mem is not None else "16G"
        pagecache_mem = _config[f"db.{self.version}.neo4j_pagecache_max"]
        pagecache_mem = pagecache_mem if pagecache_mem is not None else "16G"

        kwargs = {
            "image": get_neo4j_image(),
            "detach": True,
            "name": self.neo4j_container_name,
            "volumes": {volume: {"bind": "/data", "mode": "rw"}},
            "ports": {7474: ("127.0.0.1", self.neo4j_http_port), 7687: ("127.0.0.1", self.neo4j_bolt_port)},
            "environment": {
                "NEO4J_AUTH": "none",
                "NEO4J_PLUGINS": '["apoc"]',
                "NEO4J_ACCEPT_LICENSE_AGREEMENT": "yes",
                "NEO4J_server_config_strict__validation_enabled": "false",
                "NEO4J_server_memory_heap_initial__size": max_mem,
                "NEO4J_server_memory_heap_max__size": max_mem,
                "NEO4J_server_memory_pagecache_size": pagecache_mem,

            },
            "network": self.network_name,
            "remove": False,
        }
        if self.restart_policy:
            kwargs["restart_policy"] = self.restart_policy

        if self.db_mode == "open":
            kwargs["ports"][7474] = self.neo4j_http_port
            kwargs["ports"][7687] = self.neo4j_bolt_port

        if neo4j_mode == "import":
            kwargs["volumes"].update({"/tmp/nedrexdb_v2": {"bind": "/import", "mode": "ro"}})
            kwargs["environment"]["NEO4J_server_memory_heap_initial__size"] = "7G"
            kwargs["environment"]["NEO4J_server_memory_heap_max__size"] = "7G"
            kwargs["environment"]["NEO4J_server_memory_pagecache_size"] = "8G"
            kwargs["stdin_open"] = True
            kwargs["tty"] = True
            kwargs["entrypoint"] = "/bin/bash"

        elif neo4j_mode == "db":
            kwargs["environment"]["NEO4J_server_databases_read__only"] = "true"
            kwargs["environment"]["NEO4J_server_databases_default__to__read__only"] = "true"
            kwargs["environment"]["NEO4J_db_transaction_timeout"] = "60s"
            # kwargs["environment"]["NEO4J_dbms_memory_heap_max__size"] = max_mem.upper()
            # kwargs["environment"]["NEO4J_dbms_memory_pagecache_size"] = "4G"
        elif neo4j_mode == "db-write":
            kwargs["environment"]["NEO4J_server_databases_read__only"] = "false"
            kwargs["environment"]["NEO4J_server_databases_default__to__read__only"] = "false"

        else:
            raise Exception(f"neo4j_mode {neo4j_mode!r} is invalid")
        _client.containers.run(**kwargs)

    def _set_up_mongo(self, use_existing_volume):
        if self.mongo_container:  # if the container already exists, nothing to do
            return

        if use_existing_volume:
            volumes = get_mongo_volumes()
            if not volumes:
                raise ValueError("use_existing_volume set to True but no volume already exists")
            volume = volumes[0].name
        else:
            volume = generate_new_mongo_volume()

        mongo_kwargs = {
            "image": get_mongo_image(),
            "detach": True,
            "name": self.mongo_container_name,
            "volumes": {volume: {"mode": "rw", "bind": "/data/db"}},
            "ports": {27017: ("127.0.0.1", self.mongo_port)},
            "network": self.network_name,
            "remove": False,
        }
        if self.restart_policy:
            mongo_kwargs["restart_policy"] = self.restart_policy

        _client.containers.run(**mongo_kwargs)

    def _set_up_express(self):
        if self.express_container:  # if the container already exists, nothing to do
            return

        express_kwargs = {
            "image": get_mongo_express_image(),
            "detach": True,
            "name": self.express_container_name,
            "ports": {_config["db.dev.mongo_express_port"]: ("127.0.0.1", self.express_port)},
            "network": self.network_name,
            "environment": {"ME_CONFIG_MONGODB_SERVER": self.mongo_container_name},
            "remove": False,
        }
        if self.restart_policy:
            express_kwargs["restart_policy"] = self.restart_policy

        _client.containers.run(**express_kwargs)




    def shutdown_neo4j_container(self) -> bool:
        """
        Gracefully shut down and remove the Neo4j container.

        Returns:
            bool: True if shutdown was successful, False otherwise
        """
        if not self._stop_neo4j_process():
            logger.warning("Failed to gracefully stop Neo4j process")
            # try:
            #     self.neo4j_container.remove()
            # except Exception:
            #     pass

        return self._remove_neo4_container()

    def _stop_neo4j_process(self) -> bool:
        """Attempt to gracefully stop the Neo4j process within the container."""
        logger.debug("Attempting to gracefully stop Neo4j process")
        update_command = ["docker", "update", "--restart=no", self.neo4j_container_name]
        update = run(update_command)
        update.check_returncode()
        try:
            result = run(
                ["docker", "exec", self.neo4j_container_name, "neo4j", "stop"],
                capture_output=True,
                text=True,
                timeout=self.GRACEFUL_SHUTDOWN_TIMEOUT
            )
            if result.returncode not in [0, 137]:
                logger.warning(f"neo4j stop command returned non-zero code: {result.returncode}")
                return False

            logger.debug("Sent stop signal to Neo4j. Waiting for container to stop...")
            
            # Poll the container status until it has exited (up to 120 seconds)
            for _ in range(120):
                try:
                    self.neo4j_container.reload()
                    if self.neo4j_container.status == "exited":
                        logger.debug("Neo4j container has exited gracefully")
                        return True
                except Exception:
                    # Container might already be removed or missing
                    return True
                time.sleep(1)

            logger.warning("Timeout waiting for Neo4j container to exit gracefully")
            return False

        except (CalledProcessError, TimeoutError) as e:
            logger.warning(f"Failed to stop Neo4j process: {str(e)}")
            return False

    def _stop_neo4j_container(self) -> bool:
        """Stop the Docker container."""
        try:
            self.neo4j_container.stop(timeout=self.GRACEFUL_SHUTDOWN_TIMEOUT)
            return True

        except (NotFound, APIError) as e:
            logger.warning(f"Failed to stop container: {str(e)}")
            return False

    def _remove_neo4_container(self) -> bool:
        """Remove the Docker container."""
        try:
            self.neo4j_container.remove(force=True)
            return True

        except (NotFound, APIError) as e:
            logger.warning(f"Failed to remove container: {str(e)}")
            return False

    def _remove_neo4j(self, remove_db_volume=False, neo4j_mode="db"):
        if not self.neo4j_container:
            return

        mounts = self.neo4j_container.attrs["Mounts"]
        volumes_to_remove = ["/logs"]
        if remove_db_volume:
            volumes_to_remove.append("/data")

        volumes_to_remove = [
            mount["Name"] for mount in mounts if mount["Type"] == "volume" and mount["Destination"] in volumes_to_remove
        ]
        if neo4j_mode=='import':
            self.neo4j_container.remove(force=True)
        else:
            self.shutdown_neo4j_container()

        for vol_name in volumes_to_remove:
            _client.volumes.get(vol_name).remove(force=True)

    def _remove_mongo(self, remove_db_volume=False, remove_configdb_volume=True):
        if not self.mongo_container:
            return

        mounts = self.mongo_container.attrs["Mounts"]

        volumes_to_remove = []
        if remove_configdb_volume:
            volumes_to_remove.append("/data/configdb")
        if remove_db_volume:
            volumes_to_remove.append("/data/db")

        volumes_to_remove = [
            mount["Name"] for mount in mounts if mount["Type"] == "volume" and mount["Destination"] in volumes_to_remove
        ]

        self.mongo_container.remove(force=True)

        for vol_name in volumes_to_remove:
            _client.volumes.get(vol_name).remove(force=True)

    def _remove_express(self):
        if not self.express_container:
            return

        self.express_container.remove(force=True)

    def _remove_network(self):
        try:
            _client.networks.get(self.network_name).remove()
        except _docker.errors.NotFound:
            pass

    def set_up(self, use_existing_volume=True, neo4j_mode="db"):
        if neo4j_mode != "db-write":
            logger.info(f"Setting up {self.db_mode} NeDRex instance in not-running & write mode...")
        else:
            logger.info(f"Setting up {self.db_mode} NeDRex instance in running & write mode...")
        self._set_up_neo4j(use_existing_volume=use_existing_volume, neo4j_mode=neo4j_mode)
        if neo4j_mode != "db-write":
            self._set_up_mongo(use_existing_volume=use_existing_volume)
            self._set_up_express()

    def remove(self, remove_db_volume=False, remove_configdb_volume=True, neo4j_mode="db"):
        self._remove_mongo(
            remove_db_volume=remove_db_volume,
            remove_configdb_volume=remove_configdb_volume,
        )
        self._remove_express()
        self._remove_neo4j(remove_db_volume=remove_db_volume, neo4j_mode=neo4j_mode)


class NeDRexLiveInstance(_NeDRexBaseInstance):
    @property
    def version(self):
        return "live"


class NeDRexDevInstance(_NeDRexBaseInstance):
    @property
    def version(self):
        return "dev"

    @property
    def restart_policy(self):
        return None
