FROM neo4j:2026.04-enterprise-bullseye

# Return to the original working directory
RUN mkdir /app
WORKDIR /app

ENV APOC_VERSION=2026.04.0
ENV APOC_PROC_VERSION=2026.04.0
ENV NEO4J_USERNAME=neo4j
ENV NEO4J_PASSWORD=password
ENV NEO4J_AUTH=$NEO4J_USERNAME/$NEO4J_PASSWORD
ENV NEO4J_ACCEPT_LICENSE_AGREEMENT=yes

RUN apt-get update && apt-get install -y --fix-missing \
    build-essential \
    libffi-dev \
    libgdbm-dev \
    libc6-dev \
    libssl-dev \
    zlib1g-dev \
    libbz2-dev \
    libreadline-dev \
    libsqlite3-dev \
    wget \
    curl \
    llvm \
    libncurses5-dev \
    libncursesw5-dev \
    xz-utils \
    tk-dev \
    libxml2-dev \
    libxmlsec1-dev \
    liblzma-dev \
    unzip

# Installiere APOC Plugin
RUN wget -O /var/lib/neo4j/plugins/apoc-${APOC_VERSION}-core.jar https://github.com/neo4j/apoc/releases/download/${APOC_VERSION}/apoc-${APOC_VERSION}-core.jar
RUN wget -O /var/lib/neo4j/plugins/apoc-${APOC_PROC_VERSION}-extended.jar https://github.com/neo4j-contrib/neo4j-apoc-procedures/releases/download/${APOC_PROC_VERSION}/apoc-${APOC_PROC_VERSION}-extended.jar

ENV NEO4JLABS_PLUGINS='["apoc"]'
ENV NEO4J_dbms_security_procedures_unrestricted=apoc.* \
    NEO4J_dbms_security_procedures_allowlist=apoc.*


# Copy any scripts if necessary
COPY modify_neo4j_conf.sh /app/scripts/
# Make scripts executable
RUN chmod +x /app/scripts/*

# Instanciate the Neo4j database
RUN neo4j-admin dbms set-initial-password $NEO4J_PASSWORD

# Run the modify_neo4j_conf script
RUN /app/scripts/modify_neo4j_conf.sh
RUN echo "configuration of neo4j ready"

WORKDIR /var/lib/neo4j
RUN mv products/neo4j-genai-* plugins/

# # Expose the ports Neo4j and Jupyter Lab uses
EXPOSE 7687
EXPOSE 7474

#ENTRYPOINT ["/startup/docker-entrypoint.sh"]

#CMD ["neo4j-admin"]